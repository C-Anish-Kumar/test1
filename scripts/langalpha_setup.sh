#!/usr/bin/env bash
# Set up LangAlpha (https://github.com/ginlix-ai/LangAlpha) next to this repo.
# See docs/LANGALPHA_SETUP.md for what each step does.
#
#   scripts/langalpha_setup.sh                 # host dev: deps, Postgres+Redis, backend + web
#   scripts/langalpha_setup.sh --validate      # ...and then lint, type check and test suites
#   scripts/langalpha_setup.sh --mode compose  # everything in Docker (upstream `make up`)
#   scripts/langalpha_setup.sh --stop          # stop what this script started
set -euo pipefail

REPO_URL="https://github.com/ginlix-ai/LangAlpha.git"
# Last commit this guide was validated against. Use --ref main for the latest.
DEFAULT_REF="96a3161"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="${LANGALPHA_DIR:-$(dirname "$(dirname "$SCRIPT_DIR")")/LangAlpha}"
REF="$DEFAULT_REF"
MODE="dev"
VALIDATE=0
STOP=0
CLAUDE_CODE_EXTRA=0

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  cat <<EOF

Options:
  --dir PATH        where to clone (default: $DIR, or \$LANGALPHA_DIR)
  --ref REF         git ref to check out (default: $DEFAULT_REF; use 'main' for latest)
  --mode dev|compose
  --claude-code     also install the claude-code extra (Claude Code runtime, ~290 MB)
  --validate        run lint, type check, backend and frontend tests after setup
  --stop            stop the backend, web app and database containers
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;;
    --ref) REF="$2"; shift 2 ;;
    --mode) MODE="$2"; shift 2 ;;
    --claude-code) CLAUDE_CODE_EXTRA=1; shift ;;
    --validate) VALIDATE=1; shift ;;
    --stop) STOP=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
done

info() { printf '\033[0;32m[langalpha]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[langalpha]\033[0m %s\n' "$*"; }
die()  { printf '\033[0;31m[langalpha]\033[0m %s\n' "$*" >&2; exit 1; }

LOG_DIR="$DIR/.local-run"

# --------------------------------------------------------------------------
# --stop
# --------------------------------------------------------------------------
if [[ $STOP -eq 1 ]]; then
  # Each was started in its own session, so its pid is also its process group.
  # vite runs in a group of its own under pnpm, so also match anything running
  # from the clone itself, and fall back to SIGKILL for whatever ignores TERM.
  pids=()
  for name in backend web; do
    pidfile="$LOG_DIR/$name.pid"
    [[ -f "$pidfile" ]] && pids+=("$(cat "$pidfile")")
    rm -f "$pidfile"
  done
  match="$DIR/(\.venv|web/node_modules)/"
  for sig in TERM KILL; do
    for pid in "${pids[@]}"; do kill "-$sig" -- "-$pid" 2>/dev/null || true; done
    pkill "-$sig" -f "$match" 2>/dev/null || true
    for _ in $(seq 1 20); do
      pgrep -f "$match" >/dev/null || break
      sleep 0.5
    done
    pgrep -f "$match" >/dev/null || break
  done
  if pgrep -f "$match" >/dev/null; then
    warn "some processes are still running: $(pgrep -f "$match" | tr '\n' ' ')"
  else
    info "stopped backend and web app"
  fi
  if [[ -d "$DIR" ]]; then
    (cd "$DIR" && docker compose down 2>/dev/null) || true
  fi
  docker stop langalpha-postgresql langalpha-redis >/dev/null 2>&1 && info "stopped Postgres and Redis" || true
  exit 0
fi

# --------------------------------------------------------------------------
# Prerequisites
# --------------------------------------------------------------------------
need() { command -v "$1" >/dev/null 2>&1 || die "missing '$1': $2"; }
need git    "install git"
need docker "install Docker (https://docs.docker.com/get-docker/)"
docker info >/dev/null 2>&1 || die "Docker daemon is not reachable. Start Docker Desktop, or run 'sudo dockerd &' on Linux."

if [[ "$MODE" == "dev" ]]; then
  need uv   "curl -LsSf https://astral.sh/uv/install.sh | sh"
  need node "install Node.js 24 (CI uses 24; 22 works but 3 frontend tests fail on it)"
  need pnpm "npm install -g pnpm"
  node_major="$(node -p 'process.versions.node.split(".")[0]')"
  if (( node_major < 24 )); then
    warn "Node $node_major found; upstream CI uses Node 24. The app runs, but 3 vitest tests fail on older Node."
  fi
elif [[ "$MODE" != "compose" ]]; then
  die "--mode must be 'dev' or 'compose'"
fi

# --------------------------------------------------------------------------
# Clone / update
# --------------------------------------------------------------------------
if [[ ! -d "$DIR/.git" ]]; then
  info "cloning $REPO_URL into $DIR"
  git clone "$REPO_URL" "$DIR"
fi
cd "$DIR"
git fetch --quiet origin || warn "git fetch failed; using the local copy"
git checkout --quiet "$REF" 2>/dev/null || git checkout --quiet "origin/$REF" \
  || die "cannot check out '$REF'"
info "at $(git log -1 --format='%h %s')"

# --------------------------------------------------------------------------
# .env
# --------------------------------------------------------------------------
set_env() {  # set_env KEY VALUE — set or uncomment KEY in .env
  local key="$1" value="$2"
  if grep -qE "^#? *${key}=" .env; then
    sed -i.bak -E "s|^#? *${key}=.*|${key}=${value}|" .env && rm -f .env.bak
  else
    printf '%s=%s\n' "$key" "$value" >> .env
  fi
}

if [[ ! -f .env ]]; then
  cp .env.example .env
  info "created .env from .env.example"
fi

has_llm_key=0
for k in ANTHROPIC_API_KEY OPENAI_API_KEY GEMINI_API_KEY DEEPSEEK_API_KEY MINIMAX_API_KEY ZAI_API_KEY; do
  if [[ -n "${!k:-}" ]]; then
    set_env "$k" "${!k}"; info "copied $k from your shell into .env"
  fi
  if grep -qE "^${k}=.+" .env; then has_llm_key=1; fi
done

# --------------------------------------------------------------------------
# Compose mode: upstream `make up`, detached
# --------------------------------------------------------------------------
if [[ "$MODE" == "compose" ]]; then
  # Compose talks to the postgres/redis services by name, so undo any host-dev overrides.
  sed -i.bak -E 's|^DB_HOST=localhost|# DB_HOST=localhost|; s|^REDIS_URL=redis://:redis@localhost|# REDIS_URL=redis://:redis@localhost|' .env && rm -f .env.bak
  info "building sandbox image and starting the stack (first run takes several minutes)"
  docker build -q -f Dockerfile.sandbox -t langalpha-sandbox:latest .
  SANDBOX_PROVIDER=docker docker compose up --build -d
  BACKEND_URL="http://localhost:8000"
else
  # ------------------------------------------------------------------------
  # Dev mode: deps, infra, migrations, backend + web on the host
  # ------------------------------------------------------------------------
  extras=(--group dev --extra test)
  [[ $CLAUDE_CODE_EXTRA -eq 1 ]] && extras+=(--extra claude-code)
  info "installing Python dependencies (uv sync ${extras[*]})"
  uv sync "${extras[@]}"

  info "installing web and CLI dependencies"
  (cd web && pnpm install --frozen-lockfile)
  (cd cli && pnpm install --frozen-lockfile && pnpm build)

  set_env DB_HOST localhost
  set_env REDIS_URL "redis://:redis@localhost:6379/0"

  info "starting Postgres + Redis and running migrations"
  ./scripts/start_db.sh

  mkdir -p "$LOG_DIR"
  start_bg() {  # start_bg NAME DIR CMD...
    local name="$1" wd="$2"; shift 2
    local pidfile="$LOG_DIR/$name.pid"
    if [[ -f "$pidfile" ]] && kill -0 "$(cat "$pidfile")" 2>/dev/null; then
      info "$name already running (pid $(cat "$pidfile"))"; return
    fi
    # The subshell execs setsid, which (not being a group leader) execs the
    # command: $! is then the new session's leader and its process group, and
    # nothing is left holding this script's stdout open.
    (cd "$wd" || exit 1; exec setsid "$@") > "$LOG_DIR/$name.log" 2>&1 < /dev/null &
    echo $! > "$pidfile"
    info "started $name (log: $LOG_DIR/$name.log)"
  }
  start_bg backend "$DIR" uv run python server.py
  start_bg web "$DIR/web" env VITE_PROXY_BACKEND=http://localhost:8000 pnpm dev --host 0.0.0.0
  BACKEND_URL="http://localhost:8000"
fi

# --------------------------------------------------------------------------
# Health checks
# --------------------------------------------------------------------------
info "waiting for the backend"
for _ in $(seq 1 60); do
  curl -sf "$BACKEND_URL/health" >/dev/null 2>&1 && break
  sleep 3
done
curl -sf "$BACKEND_URL/health" >/dev/null || die "backend did not become healthy; see logs ($LOG_DIR/backend.log or 'docker compose logs backend')"
info "backend healthy at $BACKEND_URL (API docs: $BACKEND_URL/docs)"

for _ in $(seq 1 60); do
  curl -sf http://localhost:5173/ >/dev/null 2>&1 && break
  sleep 3
done
curl -sf http://localhost:5173/ >/dev/null && info "web app at http://localhost:5173" \
  || warn "web app not answering yet on :5173 (first start installs packages; check its log)"

# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------
if [[ $VALIDATE -eq 1 ]]; then
  [[ "$MODE" == "dev" ]] || die "--validate needs --mode dev (it runs the suites on the host)"
  failed=()
  # Use the ruff version pinned in uv.lock; a newer global ruff enables extra rules.
  ruff_ver="$(grep -A1 '^name = "ruff"' uv.lock | sed -nE 's/^version = "(.*)"/\1/p')"
  info "ruff $ruff_ver"
  uvx "ruff@${ruff_ver}" check src/ libs/market-protocol/ libs/chartkit/ || failed+=(ruff)
  info "web type check"
  (cd web && pnpm exec tsc -b --noEmit) || failed+=(tsc)
  info "frontend tests"
  (cd web && pnpm vitest run) > "$LOG_DIR/vitest.log" 2>&1 || failed+=(vitest)
  tail -4 "$LOG_DIR/vitest.log"
  info "backend unit tests (about 5 minutes)"
  uv run pytest tests/unit/ -n 4 --dist load -q -rfE --tb=line -p no:cacheprovider \
    > "$LOG_DIR/pytest.log" 2>&1 || failed+=(pytest)
  tail -1 "$LOG_DIR/pytest.log"
  if (( ${#failed[@]} )); then
    warn "failed: ${failed[*]} (logs in $LOG_DIR). See 'Known test failures' in docs/LANGALPHA_SETUP.md."
  else
    info "all checks passed"
  fi
fi

if [[ $has_llm_key -eq 0 ]]; then
  warn "no LLM API key in .env: agents can't answer until you add one (e.g. ANTHROPIC_API_KEY) or pick a model in the web setup wizard."
fi
warn "self-hosted LangAlpha has no login: keep it on your machine or a private network."
info "stop everything with: $0 --stop"
