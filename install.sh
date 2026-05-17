#!/usr/bin/env bash
# brAIn installer — sets up everything a new user needs.
# Usage: ./install.sh [--no-ui] [--no-graph]
set -e

BRAIN_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON_BIN="${BRAIN_DIR}/.venv/bin/python"
PIP_BIN="${BRAIN_DIR}/.venv/bin/pip"
SKIP_UI=false
SKIP_GRAPH=false

for arg in "$@"; do
    case $arg in
        --no-ui)    SKIP_UI=true ;;
        --no-graph) SKIP_GRAPH=true ;;
    esac
done

echo "================================"
echo " brAIn installer"
echo "================================"
echo ""

# ── 1. Python version check ──────────────────────────────────────────────────
PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)
if [ "$PYTHON_MAJOR" -lt 3 ] || { [ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 10 ]; }; then
    echo "ERROR: Python 3.10+ required (found $PYTHON_VERSION)"
    exit 1
fi
echo "✓ Python $PYTHON_VERSION"

# ── 2. Virtual environment ────────────────────────────────────────────────────
if [ ! -f "$PYTHON_BIN" ]; then
    echo "Creating virtual environment..."
    python3 -m venv "${BRAIN_DIR}/.venv"
fi
echo "✓ Virtual environment"

# ── 3. Python dependencies ────────────────────────────────────────────────────
echo "Installing Python dependencies..."
"$PIP_BIN" install --quiet kuzu click fastapi "uvicorn[standard]"
echo "✓ Python dependencies"

# ── 4. Graph schema + restore ─────────────────────────────────────────────────
if [ "$SKIP_GRAPH" = false ]; then
    echo "Initializing graph schema..."
    "$PYTHON_BIN" "${BRAIN_DIR}/brain.py" init

    PAYLOADS=$(find "${BRAIN_DIR}/projects" -name "*.json" | sort)
    COUNT=$(echo "$PAYLOADS" | grep -c . 2>/dev/null || echo 0)
    if [ "$COUNT" -gt 0 ]; then
        echo "Ingesting $COUNT knowledge payload(s)..."
        echo "$PAYLOADS" | while read -r f; do
            echo "  → $(basename "$f")"
            "$PYTHON_BIN" "${BRAIN_DIR}/brain.py" ingest "$f" 2>/dev/null
        done
        echo "Graph restored:"
        "$PYTHON_BIN" "${BRAIN_DIR}/brain.py" stats
    fi
fi

# ── 5. Global `brain` CLI wrapper ─────────────────────────────────────────────
LOCAL_BIN="${HOME}/.local/bin"
mkdir -p "$LOCAL_BIN"
WRAPPER="${LOCAL_BIN}/brain"
cat > "$WRAPPER" << EOF
#!/usr/bin/env bash
exec "${PYTHON_BIN}" "${BRAIN_DIR}/brain.py" "\$@"
EOF
chmod +x "$WRAPPER"
echo "✓ Global 'brain' command installed at $WRAPPER"

if ! echo "$PATH" | grep -q "${LOCAL_BIN}"; then
    echo "  NOTE: Add to your shell profile: export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# ── 6. Frontend dependencies ──────────────────────────────────────────────────
if [ "$SKIP_UI" = false ] && command -v npm &>/dev/null; then
    echo "Installing frontend dependencies..."
    npm --prefix "${BRAIN_DIR}/ui/frontend" install --silent
    echo "✓ Frontend dependencies (npm)"
elif [ "$SKIP_UI" = false ]; then
    echo "  NOTE: npm not found — skip UI or install Node 18+ then run:"
    echo "    cd ui/frontend && npm install"
fi

# ── 7. MCP + hooks instructions ───────────────────────────────────────────────
echo ""
echo "================================"
echo " Manual steps (Claude Code)"
echo "================================"
echo ""
echo "1. MCP server — add to ~/.claude/settings.json:"
echo "   {"
echo "     \"mcpServers\": {"
echo "       \"brain\": {"
echo "         \"command\": \"${PYTHON_BIN}\","
echo "         \"args\": [\"${BRAIN_DIR}/mcp_server.py\"]"
echo "       }"
echo "     }"
echo "   }"
echo ""
echo "2. Hooks — add to ~/.claude/settings.json:"
echo "   {"
echo "     \"hooks\": {"
echo "       \"SessionStart\": [{\"hooks\": [{\"type\": \"command\", \"command\": \"${BRAIN_DIR}/brain_session_start.sh\", \"timeout\": 5}]}],"
echo "       \"PostToolUse\": [{\"matcher\": \"Write|Edit\", \"hooks\": [{\"type\": \"command\", \"command\": \"${BRAIN_DIR}/brain_hook.sh\", \"timeout\": 5}]}],"
echo "       \"Stop\": [{\"hooks\": [{\"type\": \"command\", \"command\": \"${PYTHON_BIN} ${BRAIN_DIR}/brain_stop_check.py\", \"timeout\": 15}]}]"
echo "     }"
echo "   }"
echo ""
echo "================================"
echo " Installation complete"
echo "================================"
echo ""
echo "Quick test:"
echo "  brain stats"
echo "  brain effects bus_factor_of_one"
