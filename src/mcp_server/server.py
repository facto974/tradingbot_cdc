"""Serveur MCP — expose les skills trading à un agent IA externe (Cline, Claude Desktop…)."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from ..agent.trading_agent import TradingAgent
from ..backtest.engine import run as run_backtest
from ..config import Settings
from ..data.aggregator import DataAggregator
from ..data.finnhub_client import FinnhubClient
from ..data.reddit_client import RedditClient
from ..data.yfinance_client import fetch_ohlcv
from ..strategy.momentum_sentiment import (MomentumSentimentStrategy,
                                            StrategyConfig)


def _agent() -> TradingAgent:
    return TradingAgent(Settings.load())


def build_server() -> Server:
    server = Server("gemini-trader")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name="get_market_snapshot",
                 description="Snapshot prix + sentiment d'un symbole (ex BTC-USD).",
                 inputSchema={"type": "object",
                              "properties": {"symbol": {"type": "string"}},
                              "required": ["symbol"]}),
            Tool(name="get_signal",
                 description="Score & décision de la stratégie pour un symbole.",
                 inputSchema={"type": "object",
                              "properties": {"symbol": {"type": "string"}},
                              "required": ["symbol"]}),
            Tool(name="place_order",
                 description="Place un ordre sur Gemini (sandbox ou live selon config).",
                 inputSchema={"type": "object",
                              "properties": {"symbol": {"type": "string"},
                                             "side": {"type": "string", "enum": ["buy", "sell"]},
                                             "qty": {"type": "number"},
                                             "price": {"type": "number"}},
                              "required": ["symbol", "side", "qty", "price"]}),
            Tool(name="get_positions",
                 description="Liste les positions papier en cours.",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="get_pnl",
                 description="Renvoie equity, realized PnL et unrealized PnL (paper).",
                 inputSchema={"type": "object", "properties": {}}),
            Tool(name="run_backtest",
                 description="Backtest vectorisé sur yfinance.",
                 inputSchema={"type": "object",
                              "properties": {"symbol": {"type": "string"},
                                             "start": {"type": "string"},
                                             "end": {"type": "string"}},
                              "required": ["symbol"]}),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        s = Settings.load()
        if name == "get_market_snapshot":
            agg = DataAggregator(FinnhubClient(s.finnhub_api_key),
                                 RedditClient(s.reddit_client_id, s.reddit_client_secret,
                                              s.reddit_user_agent),
                                 ["CryptoCurrency"])
            snap = agg.snapshot(arguments["symbol"])
            payload = {"symbol": snap.symbol, "price": snap.price,
                       "reddit": snap.reddit, "futures_ls": snap.futures_ls,
                       "coingecko_social": snap.coingecko_social,
                       "fear_greed": snap.fear_greed}
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]

        if name == "get_signal":
            agent = _agent()
            agg = agent.aggregator
            snap = agg.snapshot(arguments["symbol"])
            sig = agent.strategy.evaluate(snap.ohlcv, snap.reddit, snap.futures_ls,
                                          snap.coingecko_social, snap.fear_greed)
            return [TextContent(type="text", text=json.dumps(sig.__dict__, indent=2))]

        if name == "place_order":
            agent = _agent()
            tr = agent._execute(arguments["symbol"], arguments["side"],
                                float(arguments["qty"]), float(arguments["price"]))
            return [TextContent(type="text", text=json.dumps(tr, default=str, indent=2))]

        if name == "get_positions":
            agent = _agent()
            payload = {sym: {"qty": p.qty, "avg_price": p.avg_price}
                       for sym, p in agent.paper.positions.items() if p.qty}
            return [TextContent(type="text", text=json.dumps(payload, indent=2))]

        if name == "get_pnl":
            agent = _agent()
            equity, unreal = agent.paper.equity({})
            return [TextContent(type="text", text=json.dumps({
                "equity": equity, "realized": agent.paper.realized_pnl,
                "unrealized": unreal,
            }, indent=2))]

        if name == "run_backtest":
            df = fetch_ohlcv(arguments["symbol"],
                             start=arguments.get("start"), end=arguments.get("end"),
                             interval="1h")
            strat = MomentumSentimentStrategy(StrategyConfig())
            res = run_backtest(df, strat)
            return [TextContent(type="text", text=json.dumps({
                "trades": res.trades, "sharpe": res.sharpe,
                "max_dd": res.max_dd, "total_return": res.total_return,
                "win_rate": res.win_rate,
            }, indent=2))]

        return [TextContent(type="text", text=f"unknown tool: {name}")]

    return server


async def main() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


def cli() -> None:
    asyncio.run(main())
