# Setup notes

This repository vendors [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents)
at upstream commit `1394a3f` (v0.6.0, 2026-10-03), Apache-2.0 licensed (see `LICENSE`).

## Quick start

```bash
./scripts/setup.sh          # creates .venv, installs deps + dev tools, copies .env, runs lint + tests
# edit .env and set one LLM key, e.g. OPENAI_API_KEY=... or ANTHROPIC_API_KEY=...
source .venv/bin/activate
tradingagents               # interactive CLI
```

Non-interactive run (example with Anthropic):

```bash
export TRADINGAGENTS_LLM_PROVIDER=anthropic \
       TRADINGAGENTS_DEEP_THINK_LLM=claude-opus-5-5 \
       TRADINGAGENTS_QUICK_THINK_LLM=claude-sonnet-5 \
       TRADINGAGENTS_OUTPUT_LANGUAGE=English \
       TRADINGAGENTS_MAX_DEBATE_ROUNDS=1 TRADINGAGENTS_MAX_RISK_ROUNDS=1
tradingagents --ticker NVDA --date 2026-10-14 --analysts market,news,fundamentals --save --no-show
```

The default provider is OpenAI (`gpt-6-sol` / `gpt-6-luna`), so with no `TRADINGAGENTS_*`
overrides you need `OPENAI_API_KEY`. Results, the memory log and the cache go to
`~/.tradingagents/` unless overridden in `.env`.

## Verified

- Python 3.13, `pip install -e ".[dev]"`: installs cleanly
- `ruff check .`: clean
- `pytest -q`: 1262 passed, 1 skipped (Bedrock, optional `langchain-aws` extra)
- Live data path (Yahoo Finance, no key) returns NVDA OHLCV correctly
- A full agent run was **not** exercised: it needs an LLM API key

## Review notes

- Structure: LangGraph pipeline of analysts (market, sentiment, news, fundamentals) running in
  parallel, then a bull/bear debate, trader, risk debate and portfolio manager.
  Main entry points are `tradingagents/graph/trading_graph.py` and `cli/main.py`.
- Code quality is good: strict ruff config, ~1.2k unit tests, CI on Python 3.11–3.14, a
  clean-install smoke job, and a non-root Docker image.
- Security: no `eval`/`exec`/`pickle`/`shell=True`/disabled TLS in the package. Tickers used in
  file paths go through `safe_ticker_component` (`tradingagents/dataflows/symbols.py`).
  API keys come from env/`.env` only, and `.env` is gitignored.
- Cost: every run makes many LLM calls (4 analysts with up to 20 tool rounds each, plus debates).
  Start with `max_debate_rounds=1`, a few analysts and a cheaper quick-think model.
- Keyless data sources (Yahoo, SEC EDGAR, Polymarket) work out of the box. FRED macro data needs
  `FRED_API_KEY`. SEC asks for a contact via `SEC_EDGAR_USER_AGENT`.
- This is a research framework, not trading advice. It does not place real orders.
