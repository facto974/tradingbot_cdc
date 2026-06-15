"""Prometheus exporter — métriques globales de l'agent.
Importé silencieusement : si prometheus_client n'est pas installé,
les métriques sont des no-ops."""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram, start_http_server
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    logger.warning("prometheus_client non installe - les metriques Prometheus sont desactivees")
    # Stubs no-op
    class _Noop:
        def labels(self, **kw): return self
        def set(self, v): pass
        def inc(self, v=1): pass
        def observe(self, v): pass
    Counter = _Noop
    Gauge = _Noop
    Histogram = _Noop
    def start_http_server(port): pass
    _PROMETHEUS_AVAILABLE = False


# ---- Equity / PnL ----
EQUITY = Gauge("gmcp_equity_usd", "Equity totale (USD)")
REALIZED_PNL = Gauge("gmcp_realized_pnl_usd", "PnL réalisé cumulé (USD)")
UNREALIZED_PNL = Gauge("gmcp_unrealized_pnl_usd", "PnL non réalisé (USD)")
OPEN_POSITIONS = Gauge("gmcp_open_positions", "Nombre de positions ouvertes")
MAX_DD = Gauge("gmcp_max_drawdown_pct", "Drawdown maximum observé")
WIN_RATE = Gauge("gmcp_win_rate", "Taux de trades gagnants")
TRADES_TOTAL = Gauge("gmcp_trades_total", "Nombre total de trades fermés")

# ---- Marché / stratégie ----
PRICE = Gauge("gmcp_price_usd", "Dernier prix observé", ["symbol"])
SCORE = Gauge("gmcp_strategy_score", "Score composite stratégie", ["symbol"])

# ---- Sentiment ----
FEAR_GREED = Gauge("gmcp_fear_greed_index", "Fear & Greed Index (0-100)")
REDDIT_SENT = Gauge("gmcp_reddit_sentiment", "Score sentiment Reddit", ["symbol"])
FUTURES_LS_RATIO = Gauge("gmcp_futures_ls_ratio", "Binance Futures Long/Short ratio", ["symbol"])
COMPOSITE_SENT = Gauge("gmcp_composite_sentiment", "Sentiment composite", ["symbol"])

# ---- Activité ----
ORDERS = Counter("gmcp_orders_total", "Ordres envoyés", ["side", "symbol"])
ERRORS = Counter("gmcp_errors_total", "Erreurs runtime", ["component"])
LOOP_DURATION = Histogram("gmcp_loop_duration_seconds", "Durée d'une itération")
API_LATENCY = Histogram(
    "gmcp_api_latency_seconds",
    "Latence d'appels API",
    ["endpoint"],
    buckets=(0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
)


def start_metrics_server(port: int = 8000) -> None:
    """Démarre le serveur HTTP Prometheus en arrière-plan.
    Ne plante pas si le port est déjà utilisé."""
    if not _PROMETHEUS_AVAILABLE:
        logger.info("Prometheus desactive - demarrage serveur ignore")
        return
    try:
        start_http_server(port)
    except OSError:
        logger.warning("Impossible de demarrer le serveur metrics sur le port %s (deja utilise)", port)
