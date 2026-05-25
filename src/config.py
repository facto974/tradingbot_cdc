"""Chargement de configuration (.env + config.yaml)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


load_dotenv()


@dataclass
class Settings:
    raw: dict[str, Any] = field(default_factory=dict)

    # Env
    gemini_api_key: str = ""
    gemini_api_secret: str = ""
    gemini_sandbox: bool = True
    groq_api_key: str = ""
    openrouter_api_key: str = ""
    openrouter_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    finnhub_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "gemini-trader/0.1"
    telegram_token: str = ""
    telegram_chat_id: str = ""
    metrics_port: int = 8000
    sqlite_path: str = "./data/trader.db"

    # YAML
    mode: str = "paper"
    universe: list[str] = field(default_factory=lambda: [
        "BTC-USD",   # Bitcoin
        "ETH-USD",   # Ethereum
        "SOL-USD",   # Solana
        "BNB-USD",   # BNB
        "XRP-USD",   # Ripple
        "ADA-USD",   # Cardano
        "DOGE-USD",  # Dogecoin
        "MATIC-USD", # Polygon
        "DOT-USD",   # Polkadot
        "AVAX-USD",  # Avalanche
    ])
    loop_interval: int = 60

    @classmethod
    def load(cls, path: str | Path = "config.yaml") -> "Settings":
        cfg_path = Path(path)
        cfg: dict[str, Any] = {}
        if cfg_path.exists():
            with cfg_path.open("r", encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
        else:
            example = Path("config.example.yaml")
            if example.exists():
                with example.open("r", encoding="utf-8") as f:
                    cfg = yaml.safe_load(f) or {}

        s = cls(raw=cfg)
        s.gemini_api_key = os.getenv("GEMINI_API_KEY", "")
        s.gemini_api_secret = os.getenv("GEMINI_API_SECRET", "")
        s.gemini_sandbox = os.getenv("GEMINI_SANDBOX", "true").lower() == "true"
        s.groq_api_key = os.getenv("GROQ_API_KEY", "")
        s.openrouter_api_key = os.getenv("OPENROUTER_API_KEY", "")
        s.openrouter_model = os.getenv("OPENROUTER_MODEL", s.openrouter_model)
        s.finnhub_api_key = os.getenv("FINNHUB_API_KEY", "")
        s.reddit_client_id = os.getenv("REDDIT_CLIENT_ID", "")
        s.reddit_client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        s.reddit_user_agent = os.getenv("REDDIT_USER_AGENT", s.reddit_user_agent)
        s.telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        s.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        # Load reddit subs with weights from config
        s.reddit_subs = cfg.get("strategy", {}).get("sentiment", {}).get("reddit_subs", {})
        # Load reddit limit for sentiment aggregation
        s.reddit_limit = int(cfg.get("strategy", {}).get("sentiment", {}).get("reddit_limit", 50))
        # Force enable Reddit client for debugging
        if s.reddit_client_id and s.reddit_client_secret:
            s.reddit_client_id = s.reddit_client_id
            s.reddit_client_secret = s.reddit_client_secret
        s.metrics_port = int(os.getenv("METRICS_PORT", str(cfg.get("metrics", {}).get("port", 8000))))
        s.sqlite_path = os.getenv("SQLITE_PATH", s.sqlite_path)

        s.mode = cfg.get("mode", s.mode)
        s.universe = cfg.get("universe", s.universe)
        s.loop_interval = int(cfg.get("loop_interval_sec", s.loop_interval))

        # Load strategy parameters from yaml
        strat = cfg.get("strategy", {})
        s.strategy_weights = strat.get("weights", {})
        s.strategy_thresholds = strat.get("thresholds", {})
        s.strategy_momentum = strat.get("momentum", {})
        s.strategy_sentiment = strat.get("sentiment", {})
        s.strategy_risk = cfg.get("risk", {})

        # honor sandbox flag in yaml if explicit
        if "sandbox" in cfg:
            s.gemini_sandbox = bool(cfg["sandbox"])
        return s
