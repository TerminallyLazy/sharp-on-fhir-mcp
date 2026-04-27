#!/usr/bin/env bash
#
# scripts/start.sh — one-shot bring-up for sharp-fhir-mcp (with embedded mem0).
#
# Resolves docker on PATH (handles the OrbStack-removed-Docker-Desktop-still-
# here case), seeds .env if missing, prunes Docker build cache on demand,
# and runs `docker compose up --build`.
#
# Usage:
#   ./scripts/start.sh                 # build + start, detached
#   ./scripts/start.sh --no-memory     # disable mem0 (export MEM0_DISABLED=1)
#   ./scripts/start.sh --build         # force rebuild even if cached
#   ./scripts/start.sh --prune         # docker builder prune -af before build
#   ./scripts/start.sh --logs          # follow logs after start
#   ./scripts/start.sh --foreground    # don't detach
#   ./scripts/start.sh --down          # tear down + remove volumes (loses memory)
#

# Note: not `set -u`. macOS ships bash 3.2, which errors on empty-array
# expansion under nounset even with the canonical `${arr[@]+"${arr[@]}"}`
# guard. We rely on `-e` and `-o pipefail` to catch real failures.
set -eo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# --
# Args
# --
BUILD_FLAGS=("--build")
DETACH=1
PRUNE=0
DOWN=0
FOLLOW_LOGS=0
NO_MEMORY=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-memory)  NO_MEMORY=1; shift ;;
        --build)      BUILD_FLAGS=("--build" "--force-recreate"); shift ;;
        --no-build)   BUILD_FLAGS=(); shift ;;
        --prune)      PRUNE=1; shift ;;
        --foreground) DETACH=0; shift ;;
        --logs)       FOLLOW_LOGS=1; shift ;;
        --down)       DOWN=1; shift ;;
        -h|--help)    sed -n '2,17p' "$0"; exit 0 ;;
        *)            echo "unknown flag: $1" >&2; exit 2 ;;
    esac
done

# --
# Locate docker
# --
if ! command -v docker >/dev/null 2>&1; then
    DD_BIN="/Applications/Docker.app/Contents/Resources/bin"
    if [[ -x "$DD_BIN/docker" ]]; then
        export PATH="$DD_BIN:$PATH"
        echo "ℹ️  docker not on PATH — using Docker Desktop at $DD_BIN"
    else
        echo "❌ docker not found. Install Docker Desktop or fix PATH." >&2
        exit 1
    fi
fi

if ! docker info >/dev/null 2>&1; then
    echo "❌ Docker daemon not running. Start Docker Desktop, then re-run." >&2
    exit 1
fi

# --
# Down handling
# --
if [[ $DOWN -eq 1 ]]; then
    echo "🧹 Tearing down stack + volumes..."
    docker compose down -v
    exit 0
fi

# --
# Bootstrap config
# --
if [[ ! -f .env ]]; then
    cp .env.example .env
    echo "📄 Created .env from .env.example"
    echo "   ⚠️  Set OPENAI_API_KEY (or OPENAI_API_BASE for an OpenAI-compat provider)."
    echo "   Edit .env now, then re-run this script."
    exit 0
fi

# Surface common config gotchas without being annoying.
if [[ $NO_MEMORY -eq 0 ]] \
    && grep -qE '^OPENAI_API_KEY=$' .env \
    && ! grep -qE '^OPENAI_API_BASE=.+' .env \
    && ! grep -qE '^MEM0_DISABLED=1' .env; then
    echo "⚠️  Both OPENAI_API_KEY and OPENAI_API_BASE are empty in .env."
    echo "   The memory_* tools (mem0) need an LLM for memory extraction."
    echo "   Either set OPENAI_API_KEY, point OPENAI_API_BASE at a"
    echo "   compat provider (Ollama/OpenRouter/...), or pass --no-memory."
    echo "   Continue anyway? [y/N]"
    read -r ans
    case "$ans" in [Yy]*) ;; *) exit 0 ;; esac
fi

# --
# Disk hygiene
# --
if [[ $PRUNE -eq 1 ]]; then
    echo "🗑️  Pruning Docker build cache + dangling images (aggressive)..."
    # No --filter age — the build cache is mostly fresh layers from this
    # project's own iterative builds. Wipe everything not currently in use.
    docker builder prune -af || true
    docker image prune -af || true
fi

# Detect cache pressure. `docker system df` size column may be GB / MB /
# kB depending on volume — normalise to GB before threshold check.
read_cache_gb() {
    docker system df 2>/dev/null | awk '
        /^Build Cache/ {
            for (i=1; i<=NF; i++) if ($i ~ /[0-9]+(\.[0-9]+)?(GB|MB|kB|B)/) { s=$i; break }
            n = s+0
            if (s ~ /MB$/) n = n / 1024
            else if (s ~ /kB$/) n = n / (1024 * 1024)
            else if (s ~ /B$/ && s !~ /[GMk]B$/) n = n / (1024 * 1024 * 1024)
            print n
            exit
        }
    '
}
CACHE_GB="$(read_cache_gb || echo 0)"
if awk -v g="$CACHE_GB" 'BEGIN { exit (g > 20 ? 0 : 1) }'; then
    printf "⚠️  Docker build cache ≈ %.1f GB. Re-run with --prune if the build\n" "$CACHE_GB"
    echo "   fails with 'no space left on device'."
fi

# --
# Bring up
# --
if [[ $NO_MEMORY -eq 1 ]]; then
    export MEM0_DISABLED=1
    echo "🧠 mem0 disabled — memory_* tools will not be registered."
fi

echo "🚀 docker compose up ${BUILD_FLAGS[*]}"

if [[ $DETACH -eq 1 ]]; then
    docker compose up "${BUILD_FLAGS[@]}" -d
    echo
    echo "✅ Stack started (detached)."
    docker compose ps
    echo
    echo "Endpoints:"
    echo "   • sharp-fhir-mcp MCP: http://localhost:${SHARP_HOST_PORT:-8000}/mcp"
    echo
    echo "Useful follow-ups:"
    echo "   ./scripts/start.sh --logs    # tail logs"
    echo "   ./scripts/start.sh --down    # tear down + remove memory volume"

    if [[ $FOLLOW_LOGS -eq 1 ]]; then
        echo
        docker compose logs -f
    fi
else
    docker compose up "${BUILD_FLAGS[@]}"
fi
