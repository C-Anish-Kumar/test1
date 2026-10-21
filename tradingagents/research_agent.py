"""A single-agent trading research assistant built on the Claude Agent SDK.

Where ``TradingAgentsGraph`` runs a fixed LangGraph pipeline of analysts,
researchers and a trader, this agent is one Claude Code loop that decides for
itself which data to pull. It reads market data through the same vendor router
the graph uses, so ``data_vendors`` in the config applies here too, and every
dated tool is clamped to the analysis date so a historical run never sees data
from after it.

Research only: the agent has no tool that places, stages or sizes an order, and
no shell or file-write access.

    pip install ".[agent]"
    python -m tradingagents.research_agent NVDA --date 2026-09-01
"""

import argparse
import asyncio
import sys
from datetime import date
from typing import Annotated, Any

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    SdkMcpTool,
    ToolUseBlock,
    create_sdk_mcp_server,
    query,
    tool,
)

from tradingagents.dataflows.date_window import as_of_window
from tradingagents.dataflows.errors import NoMarketDataError, VendorUnavailableError
from tradingagents.dataflows.router import no_data_available, route_to_vendor, vendor_unavailable
from tradingagents.dataflows.vendors.yahoo.snapshot import build_verified_market_snapshot

DEFAULT_MODEL = "claude-opus-5-5"
SERVER_NAME = "market"

SYSTEM_PROMPT = """\
You are an equity research analyst. Given a ticker and an analysis date, you \
produce a research brief a portfolio manager can act on.

The analysis date is {trade_date}. Treat it as "today": the market-data tools \
never return anything after it, and you must not use web results published \
after it either.

Work from evidence. Pull price action and indicators, fundamentals and \
statements, company and macro news, and insider activity, as far as each is \
relevant. Call get_verified_market_snapshot before stating any exact price, \
indicator value or level. When a tool answers NO_DATA_AVAILABLE or \
DATA_UNAVAILABLE, say the data is missing; never estimate or invent figures.

Finish with a Markdown brief containing:
1. Summary - two or three sentences.
2. Technicals, Fundamentals, News & sentiment - the findings, with numbers.
3. Bull case and bear case.
4. Key risks and what would change the view.
5. Rating - one of BUY, OVERWEIGHT, HOLD, UNDERWEIGHT, SELL, with a confidence \
of low, medium or high.

End with the line: "Research output, not financial advice."
"""


def _text(body: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": body}]}


async def _call(method: str, fn, *args) -> dict[str, Any]:
    """Run a blocking data call off the event loop and turn data errors into text.

    An exception out of a tool would end the agent's turn; the typed data
    errors become the same sentinels the graph's tools return, and anything
    else is returned as a tool error the model can react to.
    """
    try:
        return _text(str(await asyncio.to_thread(fn, *args)))
    except VendorUnavailableError as exc:
        return _text(vendor_unavailable(method, exc))
    except NoMarketDataError as exc:
        return _text(no_data_available(exc))
    except Exception as exc:  # reported back to the model
        return {**_text(f"{method} failed: {exc}"), "is_error": True}


def market_tools(trade_date: str) -> list[SdkMcpTool]:
    """The market-data tools, each bounded by ``trade_date``."""

    @tool(
        "get_stock_data",
        "Daily OHLCV price history for a ticker between two dates (YYYY-MM-DD).",
        {
            "ticker": Annotated[str, "Ticker symbol, e.g. NVDA"],
            "start_date": Annotated[str, "Start date, YYYY-MM-DD"],
            "end_date": Annotated[str, "End date, YYYY-MM-DD"],
        },
    )
    async def get_stock_data(args):
        try:
            start, end = as_of_window(args["start_date"], args["end_date"], trade_date)
        except ValueError as exc:
            return {**_text(str(exc)), "is_error": True}
        return await _call(
            "get_stock_data", route_to_vendor, "get_stock_data", args["ticker"], start, end
        )

    @tool(
        "get_verified_market_snapshot",
        "Source of truth for exact market numbers: the latest OHLCV row, common "
        "indicators (moving averages, RSI, MACD, Bollinger bands) and recent closes. "
        "Call it before stating any exact price or indicator value.",
        {
            "ticker": Annotated[str, "Ticker symbol"],
            "look_back_days": Annotated[int, "Recent trading rows to include, e.g. 30"],
        },
    )
    async def get_verified_market_snapshot(args):
        return await _call(
            "get_verified_market_snapshot",
            build_verified_market_snapshot,
            args["ticker"],
            trade_date,
            args.get("look_back_days") or 30,
        )

    @tool(
        "get_indicators",
        "One or more technical indicators over a look-back window. Names such as "
        "rsi, macd, macds, boll, boll_ub, boll_lb, atr, close_50_sma, close_200_sma, "
        "close_10_ema, vwma; comma-separate several.",
        {
            "ticker": Annotated[str, "Ticker symbol"],
            "indicator": Annotated[str, "Indicator name(s), comma-separated"],
            "look_back_days": Annotated[int, "Days to look back, e.g. 30"],
        },
    )
    async def get_indicators(args):
        names = [n.strip().lower() for n in args["indicator"].split(",") if n.strip()]
        parts = []
        for name in names:
            result = await _call(
                "get_indicators",
                route_to_vendor,
                "get_indicators",
                args["ticker"],
                name,
                trade_date,
                args.get("look_back_days") or 30,
            )
            parts.append(result["content"][0]["text"])
        return _text("\n\n".join(parts))

    @tool(
        "get_fundamentals",
        "Company profile and key fundamental ratios as of the analysis date.",
        {"ticker": Annotated[str, "Ticker symbol"]},
    )
    async def get_fundamentals(args):
        return await _call(
            "get_fundamentals", route_to_vendor, "get_fundamentals", args["ticker"], trade_date
        )

    @tool(
        "get_financial_statement",
        "A financial statement reported on or before the analysis date.",
        {
            "type": "object",
            "properties": {
                "ticker": {"type": "string", "description": "Ticker symbol"},
                "statement": {
                    "type": "string",
                    "enum": ["balance_sheet", "cashflow", "income_statement"],
                },
                "freq": {"type": "string", "enum": ["quarterly", "annual"]},
            },
            "required": ["ticker", "statement"],
        },
    )
    async def get_financial_statement(args):
        method = f"get_{args['statement']}"
        return await _call(
            method,
            route_to_vendor,
            method,
            args["ticker"],
            args.get("freq") or "quarterly",
            trade_date,
        )

    @tool(
        "get_news",
        "Company-specific news articles between two dates (YYYY-MM-DD).",
        {
            "ticker": Annotated[str, "Ticker symbol"],
            "start_date": Annotated[str, "Start date, YYYY-MM-DD"],
            "end_date": Annotated[str, "End date, YYYY-MM-DD"],
        },
    )
    async def get_news(args):
        try:
            start, end = as_of_window(args["start_date"], args["end_date"], trade_date)
        except ValueError as exc:
            return {**_text(str(exc)), "is_error": True}
        return await _call("get_news", route_to_vendor, "get_news", args["ticker"], start, end)

    @tool(
        "get_global_news",
        "Macro and market-wide news in the days up to the analysis date.",
        {
            "look_back_days": Annotated[int, "Days to look back, e.g. 7"],
            "limit": Annotated[int, "Maximum number of articles, e.g. 10"],
        },
    )
    async def get_global_news(args):
        return await _call(
            "get_global_news",
            route_to_vendor,
            "get_global_news",
            trade_date,
            args.get("look_back_days"),
            args.get("limit"),
        )

    @tool(
        "get_insider_transactions",
        "Recent insider buys and sells for a company.",
        {"ticker": Annotated[str, "Ticker symbol"]},
    )
    async def get_insider_transactions(args):
        return await _call(
            "get_insider_transactions",
            route_to_vendor,
            "get_insider_transactions",
            args["ticker"],
            trade_date,
        )

    return [
        get_stock_data,
        get_verified_market_snapshot,
        get_indicators,
        get_fundamentals,
        get_financial_statement,
        get_news,
        get_global_news,
        get_insider_transactions,
    ]


def build_options(
    trade_date: str,
    *,
    model: str = DEFAULT_MODEL,
    web: bool = True,
    max_turns: int = 40,
    max_budget_usd: float | None = 2.0,
) -> ClaudeAgentOptions:
    """Agent options: the market tools, optionally web search, and nothing else.

    ``tools`` limits which built-in Claude Code tools exist at all, so the agent
    has no Bash, file editing or subagents; ``allowed_tools`` pre-approves the
    ones it does have so the run needs no permission prompts.
    """
    builtin = ["WebSearch", "WebFetch"] if web else []
    tools = market_tools(trade_date)
    return ClaudeAgentOptions(
        model=model,
        system_prompt=SYSTEM_PROMPT.format(trade_date=trade_date),
        mcp_servers={SERVER_NAME: create_sdk_mcp_server(name=SERVER_NAME, tools=tools)},
        tools=builtin,
        allowed_tools=[f"mcp__{SERVER_NAME}__{t.name}" for t in tools] + builtin,
        setting_sources=[],  # ignore the caller's CLAUDE.md, hooks and settings
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
    )


async def research(
    ticker: str,
    trade_date: str | None = None,
    *,
    question: str | None = None,
    verbose: bool = True,
    **option_overrides,
) -> ResultMessage:
    """Research ``ticker`` as of ``trade_date`` (default today) and return the result.

    The brief is ``result.result``; ``result.total_cost_usd`` is what the run cost.
    """
    trade_date = trade_date or date.today().isoformat()
    prompt = f"Research {ticker.upper()} as of {trade_date}."
    if question:
        prompt += f" Focus on this question: {question}"

    final: ResultMessage | None = None
    async for message in query(prompt=prompt, options=build_options(trade_date, **option_overrides)):
        if isinstance(message, AssistantMessage) and verbose:
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    name = block.name.removeprefix(f"mcp__{SERVER_NAME}__")
                    print(f"  -> {name} {block.input}", file=sys.stderr)
        elif isinstance(message, ResultMessage):
            final = message
    if final is None:
        raise RuntimeError("the agent ended without a result")
    return final


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Claude Agent SDK trading research agent")
    parser.add_argument("ticker", help="ticker symbol, e.g. NVDA")
    parser.add_argument("--date", help="analysis date YYYY-MM-DD (default: today)")
    parser.add_argument("--question", help="a specific question to focus the research on")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--no-web", action="store_true", help="disable web search and fetch")
    parser.add_argument("--max-turns", type=int, default=40)
    parser.add_argument("--max-budget", type=float, default=2.0, help="USD cap for the run")
    args = parser.parse_args(argv)

    result = asyncio.run(
        research(
            args.ticker,
            args.date,
            question=args.question,
            model=args.model,
            web=not args.no_web,
            max_turns=args.max_turns,
            max_budget_usd=args.max_budget,
        )
    )
    print(result.result or "")
    cost = f"${result.total_cost_usd:.2f}" if result.total_cost_usd is not None else "n/a"
    print(f"\n[{result.num_turns} turns, {cost}]", file=sys.stderr)
    return 1 if result.is_error else 0


if __name__ == "__main__":
    sys.exit(main())
