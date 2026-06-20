"""Génération de graphiques colorés pour les notifications Telegram."""
from __future__ import annotations

import io
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import numpy as np
import pandas as pd

# Palette professionnelle
COLORS = {
    "green": "#00c853",
    "red": "#ff1744",
    "orange": "#ff9100",
    "blue": "#2979ff",
    "dark_bg": "#1a1a2e",
    "card_bg": "#16213e",
    "grid": "#0f3460",
    "text": "#e8e8e8",
}

plt.style.use("dark_background")
for param in ["figure.facecolor", "axes.facecolor", "axes.edgecolor"]:
    plt.rcParams[param] = COLORS["dark_bg"]
plt.rcParams["axes.labelcolor"] = COLORS["text"]
plt.rcParams["xtick.color"] = COLORS["text"]
plt.rcParams["ytick.color"] = COLORS["text"]
plt.rcParams["grid.color"] = COLORS["grid"]
plt.rcParams["grid.alpha"] = 0.3


def _emoji(val: float) -> str:
    if val > 0:
        return "🟢"
    elif val < 0:
        return "🔴"
    return "⚪"


def _color(val: float) -> str:
    if val > 0:
        return COLORS["green"]
    elif val < 0:
        return COLORS["red"]
    return COLORS["text"]


def equity_chart(
    equity_history: list[float],
    initial_capital: float,
    trades_count: int,
    win_rate: float,
    max_dd: float,
    sharpe: float,
) -> io.BytesIO:
    """Génère un graphique d'equity curve avec indicateurs de performance.

    Returns
    -------
    BytesIO prêt à être envoyé via Telegram (format PNG).
    """
    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), gridspec_kw={"height_ratios": [3, 1]})
    fig.suptitle("📊 TradingBot CDC — Performance", color=COLORS["text"],
                 fontsize=16, fontweight="bold", y=0.98)

    series = pd.Series(equity_history)
    x = np.arange(len(series))

    # ── Graphique 1 : Equity curve ──
    ret = series / initial_capital - 1
    colors = [COLORS["green"] if v >= 0 else COLORS["red"] for v in ret]
    ax1.fill_between(x, ret, 0, where=(ret >= 0), color=COLORS["green"],
                     alpha=0.15, interpolate=True)
    ax1.fill_between(x, ret, 0, where=(ret < 0), color=COLORS["red"],
                     alpha=0.15, interpolate=True)
    ax1.plot(x, ret, color=COLORS["blue"], linewidth=1.5, alpha=0.9)
    ax1.scatter(x[::max(1, len(x)//20)], ret[::max(1, len(x)//20)],
                c=[_color(v) for v in ret[::max(1, len(x)//20)]],
                s=20, alpha=0.6, zorder=5)

    # Ligne zéro
    ax1.axhline(y=0, color=COLORS["text"], linestyle="--", alpha=0.3, linewidth=0.8)

    # Annotations avec emojis
    final_ret = ret.iloc[-1]
    ax1.set_ylabel("Performance (%)", color=COLORS["text"])
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y*100:.1f}%" if abs(y) < 1 else f"{y:.1%}"))
    ax1.text(0.02, 0.95, f"{_emoji(final_ret)} {final_ret:+.2%}",
             transform=ax1.transAxes, fontsize=14, fontweight="bold",
             color=_color(final_ret), verticalalignment="top",
             bbox=dict(boxstyle="round,pad=0.3", facecolor=COLORS["card_bg"],
                       edgecolor=_color(final_ret), alpha=0.8))

    # ── Graphique 2 : Barres trades gagnants/perdants ──
    if trades_count > 0:
        wins = int(trades_count * win_rate)
        losses = trades_count - wins
        bar_colors = [COLORS["green"], COLORS["red"]]
        bars = ax2.barh([0, 1], [wins, losses], height=0.5,
                        color=bar_colors, alpha=0.8, edgecolor="white", linewidth=0.5)
        ax2.set_yticks([0, 1])
        ax2.set_yticklabels(["✅ Gagnants", "❌ Perdants"], color=COLORS["text"])
        for bar, val in zip(bars, [wins, losses]):
            ax2.text(bar.get_width() + 0.5, bar.get_y() + bar.get_height() / 2,
                     str(val), ha="left", va="center", color=COLORS["text"],
                     fontweight="bold", fontsize=11)

        # Métriques clés
        metrics_text = (
            f"Trades: {trades_count}  |  "
            f"Win Rate: {win_rate:.0%}  |  "
            f"Sharpe: {sharpe:.2f}  |  "
            f"Max DD: {max_dd:.1%}"
        )
        ax2.text(0.5, -0.4, metrics_text, transform=ax2.transAxes,
                 ha="center", color=COLORS["text"], fontsize=9,
                 bbox=dict(boxstyle="round,pad=0.3", facecolor=COLORS["card_bg"],
                           edgecolor=COLORS["grid"], alpha=0.8))
    else:
        ax2.text(0.5, 0.5, "Aucun trade pour le moment", ha="center",
                 va="center", color=COLORS["text"], fontsize=12)
    ax2.set_xlim(0, max(trades_count * 0.7, 5))
    ax2.invert_yaxis()
    ax2.set_axisbelow(True)
    ax2.grid(axis="x", alpha=0.3)

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=COLORS["dark_bg"])
    plt.close(fig)
    buf.seek(0)
    return buf


def signals_chart(
    scores: dict[str, float],
    threshold_long: float,
    threshold_short: float,
) -> io.BytesIO:
    """Génère un bar chart horizontal des scores (vert≥0, rouge<0) avec seuils.

    Returns
    -------
    BytesIO prêt à être envoyé via Telegram.
    """
    if not scores:
        return None

    # Trier par score
    items = sorted(scores.items(), key=lambda x: x[1])
    symbols = [s.split("-")[0] for s, _ in items]
    values = [v for _, v in items]

    fig, ax = plt.subplots(figsize=(10, max(6, len(symbols) * 0.3)))
    fig.suptitle("📡 Signaux en direct", color=COLORS["text"],
                 fontsize=14, fontweight="bold")

    y = np.arange(len(symbols))
    bar_colors = [COLORS["green"] if v >= 0 else COLORS["red"] for v in values]
    bars = ax.barh(y, values, height=0.7, color=bar_colors, alpha=0.85,
                   edgecolor="white", linewidth=0.3)

    # Seuils
    ax.axvline(x=threshold_long, color=COLORS["green"], linestyle="--",
               alpha=0.6, linewidth=1, label=f"Threshold LONG ({threshold_long:+.2f})")
    ax.axvline(x=threshold_short, color=COLORS["red"], linestyle="--",
               alpha=0.6, linewidth=1, label=f"Threshold SHORT ({threshold_short:+.2f})")
    ax.axvline(x=0, color=COLORS["text"], linestyle="-", alpha=0.3, linewidth=0.5)

    ax.set_yticks(y)
    ax.set_yticklabels(symbols, color=COLORS["text"], fontsize=8)
    ax.set_xlabel("Score", color=COLORS["text"])
    ax.legend(loc="lower right", fontsize=8, facecolor=COLORS["card_bg"],
              edgecolor=COLORS["grid"], labelcolor=COLORS["text"])

    # Valeurs sur les barres
    for bar, val in zip(bars, values):
        x_pos = bar.get_width() + 0.01 if val >= 0 else bar.get_width() - 0.06
        ha = "left" if val >= 0 else "right"
        ax.text(x_pos, bar.get_y() + bar.get_height() / 2,
                f"{val:+.3f}", ha=ha, va="center", fontsize=7,
                color=COLORS["text"])

    ax.set_xlim(min(values) - 0.15, max(values) + 0.15)
    ax.grid(axis="x", alpha=0.2)
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png", dpi=120, bbox_inches="tight",
                facecolor=COLORS["dark_bg"])
    plt.close(fig)
    buf.seek(0)
    return buf