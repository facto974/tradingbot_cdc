"""Simulation de portefeuille multi-actifs avec mark-to-market a chaque barre."""
from __future__ import annotations
import pandas as pd
import numpy as np


def _is_night_trade(ts: pd.Timestamp) -> bool:
    """Miroir de trading_agent.py: _is_night_trade() — 20h-4h UTC."""
    hour = ts.hour if ts.tzinfo is None else ts.tz_convert("UTC").hour
    return hour >= 20 or hour < 4


def _realized_volatility(price_window: np.ndarray) -> float:
    """Miroir approximatif de _calculate_volatility() dans trading_agent.py:
    RMS des rendements successifs. Le live utilise la fenêtre OHLCV complète
    récupérée par l'agrégateur; ici on utilise les N dernières barres
    disponibles au moment de l'entrée (approximation raisonnable, à défaut
    d'avoir exactement le même historique que snap.ohlcv en live).
    """
    if len(price_window) < 2:
        return 0.05
    valid = price_window[price_window > 0]
    if len(valid) < 2:
        return 0.05
    returns = valid[1:] / valid[:-1] - 1
    if len(returns) == 0:
        return 0.05
    return float(np.sqrt(np.mean(returns ** 2)))


def live_position_size(
    equity: float,
    open_count: int,
    score: float,
    volatility: float,
    ts: pd.Timestamp,
    max_position_usd: float,
    price: float = 0.0,
    night_mult: float = 1.30,
) -> float:
    """Réplique EXACTEMENT trading_agent.py::_position_size() pour que le
    backtest et le live utilisent la même formule de dimensionnement.
    """
    # Risque cible = 1% de l'equity, converti en notionnel via le stop-loss
    risk_target = max(equity * 0.01, 0.0)
    stop_loss_pct = 0.04  # stop_loss_pct par défaut (config.yaml)
    stop_dist = price * max(stop_loss_pct, 1e-9)
    if volatility > 0:
        vol_adj = min(1.0, 0.05 / max(volatility, 1e-9))
        stop_dist = max(stop_dist, price * vol_adj)
    qty = risk_target / max(stop_dist, 1e-12)
    max_notional = min(equity * 0.25, max_position_usd)
    notional = min(qty * price, max_notional)
    # ── Night trading : +30% de taille pendant 20h-4h UTC (miroir du live) ──
    if _is_night_trade(ts) and night_mult > 1.0:
        notional = min(notional * night_mult, max_notional)
    return max(notional, 0.0)


def simulate_portfolio(
    prices: dict[str, pd.Series],
    signals: dict[str, pd.Series],
    scores: dict[str, pd.Series] | None = None,
    initial_capital: float = 100.0,
    max_positions: int = 3,
    fee_bps: float = 15.0,
    equity_stop_loss_pct: float | None = 0.10,
    position_frac: float | None = None,
    use_live_sizing: bool = False,
    max_position_usd: float = 30.0,
    vol_window: int = 20,
) -> tuple[float, float, float, float, float]:
    """Simule un portefeuille avec allocation dynamique et mark-to-market.

    Args:
        prices: Dict {symbol: Series de prix close}
        signals: Dict {symbol: Series de positions (0/1)}
        scores: Dict {symbol: Series de scores}, optionnel. Si fourni, les
            nouvelles positions sont priorisées par score décroissant
            (comme step()/_select_active_universe en live) au lieu de
            l'ordre arbitraire de la liste de symboles.
        initial_capital: Capital de depart
        max_positions: Nombre max de positions simultanees
        fee_bps: Frais par trade en points de base
        equity_stop_loss_pct: si défini, ferme TOUTES les positions dès que
            l'equity mark-to-market chute de plus de ce %, comme le fait
            trading_agent.py en live (equity_stop_loss_pct dans config.yaml).
            Mettre à None pour désactiver (non recommandé: rend les DD non
            comparables au bot réel).
        position_frac: fraction du capital allouée à CHAQUE position
            (défaut: None -> 1/max_positions). Ignoré si use_live_sizing=True.
        use_live_sizing: si True, utilise live_position_size() (réplique
            _position_size() de trading_agent.py: plafond en $ fixes,
            conviction par score, night trading ×1.30, facteur de
            volatilité, facteur de diversification) au lieu de position_frac.
            C'est la simulation la plus fidèle au bot réel.
        max_position_usd: plafond en dollars par position (config.yaml:
            risk.max_position_usd), utilisé seulement si use_live_sizing=True.
        vol_window: nombre de barres utilisées pour estimer la volatilité
            réalisée à l'entrée (approximation de _calculate_volatility()).

    Returns:
        (total_return, sharpe, max_dd, capital_final, win_rate)
    """
    all_symbols = list(prices.keys())
    ref_idx = next(iter(prices.values())).index

    n = len(ref_idx)
    price_arr = {}
    sig_arr = {}
    score_arr = {}
    for sym in all_symbols:
        price_arr[sym] = prices[sym].reindex(ref_idx, method='ffill').fillna(0).values
        sig_arr[sym] = signals[sym].reindex(ref_idx, method='ffill').fillna(0).astype(int).values
        if scores is not None and sym in scores:
            score_arr[sym] = scores[sym].reindex(ref_idx, method='ffill').fillna(0).values

    fee = fee_bps / 10000.0
    alloc_frac = position_frac if position_frac is not None else (1.0 / max_positions)

    capital = initial_capital
    open_pos: dict[str, dict] = {}
    equity_curve = [capital]
    stop_triggered_until_flat = False  # évite de re-déclencher en boucle
    wins = 0
    losses = 0

    def _mtm(t: int) -> float:
        val = capital
        for sym, pos in open_pos.items():
            current_price = price_arr[sym][t]
            if current_price > 0:
                val += (current_price - pos['entry']) * pos['qty']
        return val

    def _close_all(t: int) -> None:
        nonlocal capital, wins, losses
        for sym in list(open_pos.keys()):
            exit_price = price_arr[sym][t]
            if exit_price <= 0:
                continue
            pos = open_pos.pop(sym)
            pnl = (exit_price - pos['entry']) * pos['qty'] - pos['notional'] * fee
            capital += pnl
            wins += pnl > 0
            losses += pnl <= 0

    for t in range(1, n):
        # ── Kill-switch équité globale (miroir de trading_agent.py) ──────
        if equity_stop_loss_pct is not None:
            mtm_now = _mtm(t)
            eq_change = mtm_now / initial_capital - 1.0
            if eq_change < -equity_stop_loss_pct:
                if not stop_triggered_until_flat:
                    _close_all(t)
                    capital = _mtm(t)  # == capital après _close_all (positions vides)
                    stop_triggered_until_flat = True
                equity_curve.append(capital)
                continue
            else:
                stop_triggered_until_flat = False

        for sym in list(open_pos.keys()):
            if sig_arr[sym][t] == 1:
                continue
            exit_price = price_arr[sym][t]
            if exit_price <= 0:
                continue
            pos = open_pos.pop(sym)
            pnl = (exit_price - pos['entry']) * pos['qty'] - pos['notional'] * fee
            capital += pnl
            wins += pnl > 0
            losses += pnl <= 0

        remaining = max_positions - len(open_pos)
        if remaining > 0:
            new_long = [sym for sym in all_symbols
                       if sig_arr[sym][t] == 1 and sig_arr[sym][t-1] != 1]
            # Priorise par score décroissant si dispo (cohérent avec step()
            # en live), sinon retombe sur l'ordre de la liste (ancien comportement).
            if score_arr:
                new_long.sort(key=lambda s: score_arr.get(s, [0]*n)[t], reverse=True)
            for sym in new_long[:remaining]:
                entry_price = price_arr[sym][t]
                if entry_price <= 0:
                    continue
                if use_live_sizing:
                    equity_now = _mtm(t)
                    score_here = score_arr.get(sym, [0.0] * n)[t] if score_arr else 0.0
                    window = price_arr[sym][max(0, t - vol_window):t]
                    vol = _realized_volatility(window)
                    notional = live_position_size(
                        equity=equity_now, open_count=len(open_pos),
                        score=score_here, volatility=vol, ts=ref_idx[t],
                        max_position_usd=max_position_usd, price=entry_price,
                    )
                    notional = min(notional, capital)  # ne pas dépasser le cash dispo
                else:
                    notional = capital * alloc_frac
                qty = notional / entry_price
                capital -= notional * fee
                open_pos[sym] = {
                    'entry': entry_price,
                    'qty': qty,
                    'notional': notional
                }

        equity_curve.append(_mtm(t))

    # Fin de période: on NE force PAS la fermeture pour ne pas ajouter de
    # frais artificiels au dernier point. On rapporte l'équité mark-to-market
    # telle quelle (comme si le bot continuait, cohérent avec les points
    # précédents de la courbe).
    capital = equity_curve[-1]

    total_ret = capital / initial_capital - 1
    eq = pd.Series(equity_curve)
    rets = eq.pct_change().dropna()
    sharpe = float(np.sqrt(365 * 24) * rets.mean() / rets.std()) if len(rets) > 1 and rets.std() > 0 else 0.0
    dd = ((eq - eq.cummax()) / eq.cummax()).min()
    win_rate = wins / max(wins + losses, 1)

    return total_ret, sharpe, dd, capital, win_rate