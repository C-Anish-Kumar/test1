#!/usr/bin/env bash
# Set up a local TradingAgents environment: venv, dependencies, .env, tests.
set -euo pipefail
cd "$(dirname "$0")/.."

if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.13 .venv
  uv pip install --python .venv/bin/python -e ".[dev]"
else
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -e ".[dev]"
fi

[ -f .env ] || cp .env.example .env

.venv/bin/ruff check . || echo "WARNING: lint reported issues (install is still complete)"
.venv/bin/pytest -q || echo "WARNING: some tests failed (install is still complete)"

echo
echo "Setup complete. Next:"
echo "  1. Add your LLM API key to .env (e.g. OPENAI_API_KEY or ANTHROPIC_API_KEY)"
echo "  2. source .venv/bin/activate"
echo "  3. tradingagents            # interactive CLI"
