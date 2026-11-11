#!/usr/bin/env bash
# ==============================================================================
# Vigil Assistant - Development Startup Script
#
# Starts the Website (React + Vite) and the Agents API (FastAPI) together,
# along with the background job worker for full agent loop execution.
#
# Usage:
#   ./start.sh [OPTIONS]
# ==============================================================================
set -euo pipefail

# ------------------------------------------------------------------------------
# Colors and formatting
# ------------------------------------------------------------------------------
if [[ -t 1 ]] && [[ -z "${NO_COLOR:-}" ]]; then
    C_RESET="\033[0m"
    C_BOLD="\033[1m"
    C_DIM="\033[2m"
    C_GREEN="\033[32m"
    C_CYAN="\033[36m"
    C_YELLOW="\033[33m"
    C_RED="\033[31m"
    C_MAGENTA="\033[35m"
    C_BLUE="\033[34m"
else
    C_RESET=""
    C_BOLD=""
    C_DIM=""
    C_GREEN=""
    C_CYAN=""
    C_YELLOW=""
    C_RED=""
    C_MAGENTA=""
    C_BLUE=""
fi

log_vigil()   { echo -e "${C_BOLD}${C_BLUE}[vigil]${C_RESET} $*"; }
log_info()    { echo -e "${C_BOLD}${C_BLUE}[vigil]${C_RESET} ${C_CYAN}INFO${C_RESET}  $*"; }
log_success() { echo -e "${C_BOLD}${C_BLUE}[vigil]${C_RESET} ${C_GREEN}OK${C_RESET}    $*"; }
log_warn()    { echo -e "${C_BOLD}${C_BLUE}[vigil]${C_RESET} ${C_YELLOW}WARN${C_RESET}  $*"; }
log_error()   { echo -e "${C_BOLD}${C_BLUE}[vigil]${C_RESET} ${C_RED}ERROR${C_RESET} $*" >&2; }

# Stream prefixer for child processes
prefix_stream() {
    local tag="$1"
    local color="$2"
    awk -v tag="$tag" -v color="$color" -v reset="$C_RESET" '{ print color "[" tag "]" reset " " $0; fflush() }'
}

# ------------------------------------------------------------------------------
# Root resolution
# ------------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -f "$SCRIPT_DIR/backend/pyproject.toml" && -f "$SCRIPT_DIR/web/package.json" ]]; then
    PROJECT_ROOT="$SCRIPT_DIR"
elif [[ -f "$SCRIPT_DIR/../backend/pyproject.toml" && -f "$SCRIPT_DIR/../web/package.json" ]]; then
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
else
    log_error "Cannot locate Vigil repository root from $SCRIPT_DIR"
    exit 1
fi

# ------------------------------------------------------------------------------
# Configuration & Defaults
# ------------------------------------------------------------------------------
ENABLE_API=true
ENABLE_WEB=true
ENABLE_WORKER=true
EMBED_WORKER=false
RUN_MIGRATIONS=true
MOCK_MODE=false
RAW_LOGS=false
API_HOST="127.0.0.1"
API_PORT="8000"
WEB_PORT="5173"

# Read defaults from .env if present
if [[ -f "$PROJECT_ROOT/backend/.env" ]]; then
    ENV_PORT=$(grep -E '^[[:space:]]*PORT=' "$PROJECT_ROOT/backend/.env" | cut -d '=' -f2- | tr -d ' "\r\n' || true)
    ENV_HOST=$(grep -E '^[[:space:]]*HOST=' "$PROJECT_ROOT/backend/.env" | cut -d '=' -f2- | tr -d ' "\r\n' || true)
    ENV_EMBED=$(grep -E '^[[:space:]]*EMBED_WORKER=' "$PROJECT_ROOT/backend/.env" | cut -d '=' -f2- | tr -d ' "\r\n' || true)
    [[ -n "$ENV_PORT" ]] && API_PORT="$ENV_PORT"
    [[ -n "$ENV_HOST" ]] && API_HOST="$ENV_HOST"
    if [[ "$ENV_EMBED" == "true" ]]; then
        EMBED_WORKER=true
        ENABLE_WORKER=false
    fi
elif [[ -f "$PROJECT_ROOT/.env" ]]; then
    ENV_PORT=$(grep -E '^[[:space:]]*PORT=' "$PROJECT_ROOT/.env" | cut -d '=' -f2- | tr -d ' "\r\n' || true)
    ENV_HOST=$(grep -E '^[[:space:]]*HOST=' "$PROJECT_ROOT/.env" | cut -d '=' -f2- | tr -d ' "\r\n' || true)
    ENV_EMBED=$(grep -E '^[[:space:]]*EMBED_WORKER=' "$PROJECT_ROOT/.env" | cut -d '=' -f2- | tr -d ' "\r\n' || true)
    [[ -n "$ENV_PORT" ]] && API_PORT="$ENV_PORT"
    [[ -n "$ENV_HOST" ]] && API_HOST="$ENV_HOST"
    if [[ "$ENV_EMBED" == "true" ]]; then
        EMBED_WORKER=true
        ENABLE_WORKER=false
    fi
fi

# ------------------------------------------------------------------------------
# Usage / Help
# ------------------------------------------------------------------------------
show_help() {
    echo -e "${C_BOLD}Vigil Assistant Startup Script${C_RESET}

Runs the React Web UI and the FastAPI Agents API together for local development,
including the background job worker so agent runs execute end-to-end.

${C_BOLD}USAGE:${C_RESET}
  ./start.sh [OPTIONS]

${C_BOLD}OPTIONS:${C_RESET}
  -h, --help            Show this help message and exit
  --no-worker           Run only Website and API (do not start the background worker)
  --worker              Explicitly enable separate background worker (default)
  --embed-worker        Run worker embedded inside the API process (EMBED_WORKER=true)
  --api-only            Start only the Agents API (plus worker, unless --no-worker)
  --web-only            Start only the Website frontend
  --mock                Run the Website in mock simulation mode (no backend needed)
  --no-migrate          Skip database migrations (alembic upgrade head)
  --raw-logs            Do not prefix logs with [api], [web], [worker] tags
  --api-port PORT       Port for the Agents API (default: ${API_PORT})
  --api-host HOST       Host address for the Agents API (default: ${API_HOST})
  --web-port PORT       Port for the Web frontend (default: ${WEB_PORT})

${C_BOLD}EXAMPLES:${C_RESET}
  ./start.sh                  # Start Website, Agents API, and Worker together
  ./start.sh --no-worker      # Start Website and Agents API only
  ./start.sh --api-port 8080  # Start with custom API port
  ./start.sh --web-only       # Start only the Vite frontend dev server
  ./start.sh --mock           # Run frontend with mock/fixture data
"
}

# ------------------------------------------------------------------------------
# Parse CLI arguments
# ------------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            show_help
            exit 0
            ;;
        --no-worker)
            ENABLE_WORKER=false
            shift
            ;;
        --worker)
            ENABLE_WORKER=true
            EMBED_WORKER=false
            shift
            ;;
        --embed-worker)
            EMBED_WORKER=true
            ENABLE_WORKER=false
            shift
            ;;
        --api-only)
            ENABLE_WEB=false
            shift
            ;;
        --web-only)
            ENABLE_API=false
            ENABLE_WORKER=false
            shift
            ;;
        --mock)
            MOCK_MODE=true
            shift
            ;;
        --no-migrate)
            RUN_MIGRATIONS=false
            shift
            ;;
        --raw-logs)
            RAW_LOGS=true
            shift
            ;;
        --api-port)
            if [[ -z "${2:-}" ]]; then
                log_error "--api-port requires a port number argument"
                exit 1
            fi
            API_PORT="$2"
            shift 2
            ;;
        --api-host)
            if [[ -z "${2:-}" ]]; then
                log_error "--api-host requires a host argument"
                exit 1
            fi
            API_HOST="$2"
            shift 2
            ;;
        --web-port)
            if [[ -z "${2:-}" ]]; then
                log_error "--web-port requires a port number argument"
                exit 1
            fi
            WEB_PORT="$2"
            shift 2
            ;;
        *)
            log_error "Unknown option: $1"
            echo "Use ./start.sh --help to see available options."
            exit 1
            ;;
    esac
done

# ------------------------------------------------------------------------------
# Process Management & Cleanup
# ------------------------------------------------------------------------------
PIDS=()
CLEANING_UP=false

kill_recursive() {
    local parent="$1"
    local children
    children=$(pgrep -P "$parent" 2>/dev/null || true)
    for child in $children; do
        kill_recursive "$child"
    done
    if kill -0 "$parent" 2>/dev/null; then
        kill -TERM "$parent" 2>/dev/null || true
    fi
}

cleanup() {
    # Guard against multiple executions
    if [[ "$CLEANING_UP" == true ]]; then
        return
    fi
    CLEANING_UP=true
    trap - SIGINT SIGTERM EXIT

    echo ""
    log_info "Shutting down Vigil services..."

    # Gracefully stop all recorded processes and their children
    for pid in "${PIDS[@]}"; do
        kill_recursive "$pid"
    done

    # Allow up to 2 seconds for graceful shutdown
    local waited=0
    while [[ $waited -lt 4 ]]; do
        local any_running=false
        for pid in "${PIDS[@]}"; do
            if kill -0 "$pid" 2>/dev/null; then
                any_running=true
                break
            fi
        done
        if [[ "$any_running" == false ]]; then
            break
        fi
        sleep 0.5
        waited=$((waited + 1))
    done

    # Force kill any remaining processes
    for pid in "${PIDS[@]}"; do
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
    done

    log_success "All services stopped."
    exit 0
}

trap cleanup SIGINT SIGTERM EXIT

# ------------------------------------------------------------------------------
# Pre-flight Checks
# ------------------------------------------------------------------------------
log_vigil "Initializing Vigil environment..."

# 1. Check uv package manager
if ! command -v uv >/dev/null 2>&1; then
    log_error "'uv' is required but not found in PATH."
    log_error "Install uv via: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
fi

# 2. Check Node & npm if web is enabled
if [[ "$ENABLE_WEB" == true ]]; then
    if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
        log_error "'node' and 'npm' are required to run the web client."
        log_error "Please install Node.js v20+ from https://nodejs.org/"
        exit 1
    fi
fi

# 3. Check environment configuration
if [[ ! -f "$PROJECT_ROOT/backend/.env" ]]; then
    if [[ -f "$PROJECT_ROOT/.env" ]]; then
        log_info "Copying root .env to backend/.env..."
        cp "$PROJECT_ROOT/.env" "$PROJECT_ROOT/backend/.env"
    elif [[ -f "$PROJECT_ROOT/backend/.env.example" ]]; then
        log_info "Creating backend/.env from backend/.env.example..."
        cp "$PROJECT_ROOT/backend/.env.example" "$PROJECT_ROOT/backend/.env"
    fi
fi

if [[ ! -f "$PROJECT_ROOT/.env" && -f "$PROJECT_ROOT/backend/.env" ]]; then
    cp "$PROJECT_ROOT/backend/.env" "$PROJECT_ROOT/.env"
fi

# 4. Check backend python virtualenv
if [[ ! -d "$PROJECT_ROOT/backend/.venv" ]]; then
    log_info "Syncing Python virtualenv (uv sync)..."
    (cd "$PROJECT_ROOT/backend" && uv sync --quiet)
    log_success "Python virtualenv initialized."
fi

# 5. Check web dependencies
if [[ "$ENABLE_WEB" == true && ! -d "$PROJECT_ROOT/web/node_modules" ]]; then
    log_info "Installing web dependencies (npm install)..."
    (cd "$PROJECT_ROOT/web" && npm install --silent)
    log_success "Web dependencies installed."
fi

# 6. Database connectivity & migrations (if API or Worker is enabled)
if [[ "$ENABLE_API" == true || "$ENABLE_WORKER" == true ]]; then
    log_info "Checking PostgreSQL connection..."
    check_db_cmd="
import asyncio, sys
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from src.config.settings import get_settings

async def check():
    settings = get_settings()
    engine = create_async_engine(settings.DATABASE_URL)
    try:
        async with engine.connect() as conn:
            await conn.execute(text('SELECT 1'))
        return True
    except Exception:
        return False
    finally:
        await engine.dispose()

if not asyncio.run(check()):
    sys.exit(1)
"
    if ! (cd "$PROJECT_ROOT/backend" && uv run python -c "$check_db_cmd" >/dev/null 2>&1); then
        log_warn "PostgreSQL is not responding on configured DATABASE_URL."

        # Check if docker container is present and stopped
        if command -v docker >/dev/null 2>&1; then
            pg_container=$(docker ps -a --filter "name=postgres" --format "{{.Names}}" | grep -E "v[i|r]gil-postgres" | head -n 1 || true)
            if [[ -n "$pg_container" ]]; then
                log_info "Found PostgreSQL Docker container '$pg_container'. Attempting to start it..."
                docker start "$pg_container" >/dev/null 2>&1 || true
                sleep 2
            fi
        fi

        # Re-check connection
        if ! (cd "$PROJECT_ROOT/backend" && uv run python -c "$check_db_cmd" >/dev/null 2>&1); then
            log_error "Could not connect to PostgreSQL database."
            log_error "Ensure PostgreSQL (with pgvector) is running, or start it via Docker:"
            log_error "  docker compose -f deploy/docker-compose.yml up -d postgres"
            exit 1
        fi
    fi
    log_success "PostgreSQL is connected and healthy."

    # Run Alembic migrations
    if [[ "$RUN_MIGRATIONS" == true ]]; then
        log_info "Running database migrations (alembic upgrade head)..."
        (cd "$PROJECT_ROOT/backend" && uv run alembic upgrade head >/dev/null)
        log_success "Database schema is up to date."
    fi
fi

# 7. Check for port conflicts
check_port_in_use() {
    local port="$1"
    local name="$2"
    if command -v ss >/dev/null 2>&1; then
        if ss -tulpn 2>/dev/null | grep -E -q ":${port}[[:space:]]"; then
            log_error "Port ${port} (${name}) is already in use by another process."
            return 1
        fi
    elif command -v lsof >/dev/null 2>&1; then
        if lsof -i ":${port}" >/dev/null 2>&1; then
            log_error "Port ${port} (${name}) is already in use by another process."
            return 1
        fi
    fi
    return 0
}

if [[ "$ENABLE_API" == true ]]; then
    if ! check_port_in_use "$API_PORT" "Agents API"; then
        log_error "Please free port $API_PORT or run with --api-port <new-port>"
        exit 1
    fi
fi

if [[ "$ENABLE_WEB" == true ]]; then
    if ! check_port_in_use "$WEB_PORT" "Website"; then
        log_error "Please free port $WEB_PORT or run with --web-port <new-port>"
        exit 1
    fi
fi

# ------------------------------------------------------------------------------
# Start Services
# ------------------------------------------------------------------------------
CHECK_HOST="$API_HOST"
if [[ "$CHECK_HOST" == "0.0.0.0" ]]; then
    CHECK_HOST="127.0.0.1"
fi

# 1. Start Agents API
if [[ "$ENABLE_API" == true ]]; then
    log_info "Starting Agents API on http://${API_HOST}:${API_PORT}..."
    if [[ "$RAW_LOGS" == true ]]; then
        (
            cd "$PROJECT_ROOT/backend"
            export PYTHONUNBUFFERED=1
            export EMBED_WORKER="$EMBED_WORKER"
            exec uv run uvicorn src.main:app --host "$API_HOST" --port "$API_PORT" --reload
        ) &
    else
        (
            cd "$PROJECT_ROOT/backend"
            export PYTHONUNBUFFERED=1
            export EMBED_WORKER="$EMBED_WORKER"
            exec uv run uvicorn src.main:app --host "$API_HOST" --port "$API_PORT" --reload
        ) > >(prefix_stream "api" "$C_CYAN") 2>&1 &
    fi
    API_PID=$!
    PIDS+=("$API_PID")

    # Wait for API readiness before launching frontend (up to 15 seconds)
    health_url="http://${CHECK_HOST}:${API_PORT}/api/v1/health/ready"
    log_info "Waiting for Agents API readiness at ${health_url}..."
    api_ready=false
    for _ in {1..30}; do
        if curl -fsS "$health_url" >/dev/null 2>&1; then
            api_ready=true
            break
        fi
        if ! kill -0 "$API_PID" 2>/dev/null; then
            log_error "Agents API process exited unexpectedly during startup."
            cleanup
        fi
        sleep 0.5
    done

    if [[ "$api_ready" == true ]]; then
        log_success "Agents API is ready and serving requests."
    else
        log_warn "Agents API did not report ready within timeout (continuing anyway)..."
    fi
fi

# 2. Start Background Job Worker (if enabled and not embedded)
WORKER_PID=""
if [[ "$ENABLE_WORKER" == true && "$EMBED_WORKER" == false ]]; then
    log_info "Starting background Agent Job Worker..."
    if [[ "$RAW_LOGS" == true ]]; then
        (
            cd "$PROJECT_ROOT/backend"
            export PYTHONUNBUFFERED=1
            exec env -u PYTHONPATH uv run python -m src.worker
        ) &
    else
        (
            cd "$PROJECT_ROOT/backend"
            export PYTHONUNBUFFERED=1
            exec env -u PYTHONPATH uv run python -m src.worker
        ) > >(prefix_stream "worker" "$C_MAGENTA") 2>&1 &
    fi
    WORKER_PID=$!
    PIDS+=("$WORKER_PID")
    log_success "Background Agent Worker started (PID: ${WORKER_PID})."
fi

# 3. Start Website (Vite Dev Server)
if [[ "$ENABLE_WEB" == true ]]; then
    log_info "Starting Website (Vite Dev Server) on http://localhost:${WEB_PORT}..."
    web_script="dev"
    if [[ "$MOCK_MODE" == true ]]; then
        web_script="dev:mock"
        log_info "Running website in simulation mode (VITE_VIGIL_MOCK=1)."
    fi

    proxy_target="http://${CHECK_HOST}:${API_PORT}"
    if [[ "$RAW_LOGS" == true ]]; then
        (
            cd "$PROJECT_ROOT/web"
            export VITE_API_PROXY="$proxy_target"
            exec npm run "$web_script" -- --port "$WEB_PORT"
        ) &
    else
        (
            cd "$PROJECT_ROOT/web"
            export VITE_API_PROXY="$proxy_target"
            exec npm run "$web_script" -- --port "$WEB_PORT"
        ) > >(prefix_stream "web" "$C_GREEN") 2>&1 &
    fi
    WEB_PID=$!
    PIDS+=("$WEB_PID")
fi

# ------------------------------------------------------------------------------
# Banner Output
# ------------------------------------------------------------------------------
worker_status="Disabled"
if [[ "$EMBED_WORKER" == true ]]; then
    worker_status="Embedded in API process"
elif [[ "$ENABLE_WORKER" == true ]]; then
    worker_status="Active (PID: ${WORKER_PID})"
fi

echo ""
echo -e "${C_BOLD}================================================================================${C_RESET}"
echo -e "  ${C_BOLD}${C_BLUE}VIGIL AI ASSISTANT - SERVICES READY${C_RESET}"
echo -e "${C_BOLD}--------------------------------------------------------------------------------${C_RESET}"
if [[ "$ENABLE_WEB" == true ]]; then
echo -e "  ${C_GREEN}🌐 Website (Web UI):${C_RESET}     ${C_BOLD}http://localhost:${WEB_PORT}${C_RESET}"
fi
if [[ "$ENABLE_API" == true ]]; then
echo -e "  ${C_CYAN}⚡ Agents API:${C_RESET}           ${C_BOLD}http://${CHECK_HOST}:${API_PORT}${C_RESET}"
echo -e "  ${C_DIM}📚 API Documentation:${C_RESET}    http://${CHECK_HOST}:${API_PORT}/docs"
fi
echo -e "  ${C_MAGENTA}⚙️  Worker Status:${C_RESET}        ${worker_status}"
echo -e "${C_BOLD}--------------------------------------------------------------------------------${C_RESET}"
echo -e "  ${C_YELLOW}ℹ️  Pairing Note:${C_RESET} Enter the temporary pairing code printed above at"
echo -e "                 ${C_BOLD}http://localhost:${WEB_PORT}/pair${C_RESET} to connect your device."
echo -e "  ${C_DIM}🛑 Press Ctrl+C at any time to gracefully shut down all services.${C_RESET}"
echo -e "${C_BOLD}================================================================================${C_RESET}"
echo ""

# ------------------------------------------------------------------------------
# Process Supervision Loop
# ------------------------------------------------------------------------------
while true; do
    for pid in "${PIDS[@]}"; do
        if ! kill -0 "$pid" 2>/dev/null; then
            log_warn "A service (PID $pid) terminated unexpectedly."
            cleanup
        fi
    done
    sleep 1 &
    wait $! 2>/dev/null || true
done
