# LangAlpha setup guide

[LangAlpha](https://github.com/ginlix-ai/LangAlpha) (Apache-2.0) is an open-source harness for
agentic trading: AI agents that research markets, keep a thesis in a workspace, run automations,
and can place orders in a connected brokerage account within permissions you set. It is a
FastAPI/LangGraph backend (Python 3.13), a React/Vite web app, Postgres and Redis.

This guide was validated against upstream commit `96a3161` (2026-11-13). LangAlpha is not
vendored here; `scripts/langalpha_setup.sh` clones it next to this repo (`../LangAlpha`).

## Quick start

```bash
scripts/langalpha_setup.sh
```

Then open http://localhost:5173. The API is on http://localhost:8000 (docs at `/docs`).

To stop everything:

```bash
scripts/langalpha_setup.sh --stop
```

## Prerequisites

| Tool | Version | Needed for |
|---|---|---|
| git | any | cloning |
| Docker | running daemon | Postgres, Redis, sandboxes (all modes) |
| [uv](https://docs.astral.sh/uv/) | ≥ 0.6.15 | Python deps (dev mode); fetches Python 3.13 itself |
| Node.js | 24 (22 works) | web app and CLI (dev mode) |
| pnpm | any recent | web app and CLI (dev mode) |

Compose mode needs only git and Docker.

## Script options

| Option | What it does |
|---|---|
| `--mode dev` (default) | Runs the backend and web app on the host, Postgres + Redis in Docker. Best for development. |
| `--mode compose` | Upstream `make up`: everything in Docker, detached. Closest to a real self-host. |
| `--validate` | After setup, runs ruff, the web type check, frontend tests and backend unit tests (dev mode only). |
| `--claude-code` | Also installs the `claude-code` extra (~290 MB). CI installs it; tests for that runtime need it. |
| `--ref REF` | Git ref to check out. Default is the validated commit; `--ref main` for the latest. |
| `--dir PATH` | Clone location (or set `LANGALPHA_DIR`). |
| `--stop` | Stops the backend, web app and database containers. |

Any LLM key already exported in your shell (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`, `DEEPSEEK_API_KEY`, `MINIMAX_API_KEY`, `ZAI_API_KEY`) is copied into `.env`.

## What the script does (dev mode)

1. Clones LangAlpha and checks out the pinned commit.
2. Creates `.env` from `.env.example`, and generates a random `BYOK_ENCRYPTION_KEY` in it if
   there is none (as upstream `make config` does). Saved accounts, API keys and secrets are
   encrypted with it; an existing key is never replaced.
3. `uv sync --group dev --extra test` for the backend.
4. `pnpm install` in `web/`, and builds the `langalpha` CLI in `cli/`.
5. Sets `DB_HOST=localhost` and `REDIS_URL=redis://:redis@localhost:6379/0` in `.env`.
6. Runs upstream `scripts/start_db.sh`: Postgres 18 and Redis 7 containers
   (`langalpha-postgresql`, `langalpha-redis`) and all Alembic migrations.
7. Starts `uv run python server.py` and `pnpm dev` in the background. Logs and pid files go to
   `<LangAlpha>/.local-run/`.
8. Waits for `GET /health` and the web app to answer.

The manual equivalent, from the LangAlpha checkout:

```bash
cp .env.example .env    # then set DB_HOST=localhost and REDIS_URL as above
```

```bash
uv sync --group dev --extra test
```

```bash
(cd web && pnpm install) && (cd cli && pnpm install && pnpm build)
```

```bash
./scripts/start_db.sh
```

```bash
uv run python server.py
```

```bash
cd web && VITE_PROXY_BACKEND=http://localhost:8000 pnpm dev
```

## After it starts

- **Pick a model.** With no LLM key, the web app opens on a setup wizard (subscription sign-in,
  API key, or a local model). Until then agents cannot answer, and the backend log shows a
  `MissingProviderKeyError` from the background market-insight job. That error is expected.
- **Market data.** Without `FMP_API_KEY` it uses free Yahoo Finance data (delayed quotes).
- **Web search.** Set one of `TAVILY_API_KEY`, `SERPER_API_KEY`, `EXA_API_KEY` in `.env`.
- **Sandboxes.** With no Daytona/E2B key, agent code runs in local Docker containers; the image
  `langalpha-sandbox:latest` is built on first workspace creation (or by compose mode up front).
- Upstream's interactive wizard (`make config`) can fill in `.env` for you instead.

## Security notes

- **Self-hosted LangAlpha has no login.** Anyone who can reach ports 8000/5173 acts as you,
  including on any brokerage you connect. Both listen on all interfaces; keep the machine on a
  private network or put an authenticating proxy / VPN in front.
- The script generates a unique `BYOK_ENCRYPTION_KEY` in `.env`, so the public compose default
  (`langalpha-local-dev-encryption-key`) is never used. Keep `.env` private, and don't change the
  key once accounts are saved: what was encrypted with the old key can no longer be read.
- Connect brokerages read-only or paper first; live orders are opt-in per connection.

## Validation results (commit `96a3161`)

| Check | Result |
|---|---|
| Migrations (122) | apply cleanly on Postgres 18 |
| `GET /health`, `/docs` | healthy; 285 API routes |
| Web app | loads with no page errors, opens on the setup wizard |
| `--mode compose` | sandbox and backend images build; postgres, redis, backend and frontend come up, three report healthy (the frontend has no health check); `--stop` removes them |
| ruff 0.15.14 (pinned in `uv.lock`) | passes |
| Web `tsc` | passes |
| Frontend vitest | 10,970 tests; all pass on Node 24 |
| Backend unit tests (with `claude-code` extra) | 29,573 pass, 27 fail; see below |

### Known test failures

- **ruff reports ~9,600 errors**: you ran a newer global ruff (0.16+ enables more rules). The
  script uses the version pinned in `uv.lock`.
- **3 vitest failures on Node 22** (`currencyDisplay`, `ImageReply`): Node 22's `10 ** -4` is
  `0.00009999999999999999`, and its `Blob` lacks `stream()` under jsdom. They pass on Node 24,
  which upstream CI uses.
- **`ModuleNotFoundError: claude_agent_sdk`** (11 errors, ~70 failures): install with
  `--claude-code`, as CI does.
- **`tests/unit/ptc_agent/agent/claude_code/test_runtime_cli.py` auth failures**: these drive the
  real `claude` CLI against a local fake API. Running inside a Claude Code session or behind an
  HTTPS proxy redirects them; run the suite from a plain shell. Two tests (`never_signs_in_with
  _the_machines_own_login`, `no_credential_runs_signed_out`) also fail if the machine has its own
  Claude login (`~/.claude`).
- **`NoRoom: no room to make ...`** (~60 failures in `test_tree_runtime.py`,
  `test_folder_move_script.py`, `test_livefs_daemon.py`): the sandbox copy code keeps at least
  10% of the disk free, measured on the volume holding `/tmp`. On a 252 GB volume with 24 GB free
  these fail; with 26 GB free they pass. Free some space and rerun.
- **`tests/unit/ptc_agent/core/sandbox/test_livefs_daemon.py`**: tests that simulate folders the
  owner cannot open can fail when run as root, which ignores those permissions. Upstream CI runs
  as a normal user. This explanation is unconfirmed: in a normal run as root only
  `test_root_lays_the_links_as_the_folders_owner` failed, but 17 failed when the suite was run
  with a stripped environment (`env -i`), so the environment may matter too.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Docker daemon is not reachable` | Start Docker Desktop, or `sudo dockerd &` on Linux. |
| Port 5432/6379 in use | Another Postgres/Redis is running; stop it, or set `DB_PORT` / `REDIS_PORT` in `.env`. |
| Backend not healthy | `tail -50 ../LangAlpha/.local-run/backend.log` (dev) or `docker compose logs backend` (compose). |
| `checkpointer: not_configured` in `/health` | Normal for a fresh local run. |
| "Your account couldn't be saved" / "The key couldn't be saved" | `.env` has no `BYOK_ENCRYPTION_KEY`. Rerun the script (it adds one), or set `BYOK_ENCRYPTION_KEY=$(openssl rand -hex 32)` in `.env`, then `--stop` and start again. |
| Compose build fails with `curl: (60) SSL certificate problem: self-signed certificate` | You are behind a TLS-inspecting proxy (corporate network, some cloud sandboxes). The images' build steps don't trust its CA; add the CA to the Dockerfiles or build on another network. |
| Switching between dev and compose mode | Fine either way: dev mode sets `DB_HOST`/`REDIS_URL` to `localhost` in `.env`, compose mode comments them out again. |
| Reset the database | `scripts/langalpha_setup.sh --stop`, then `docker volume rm langalpha-postgresql-data`. |
