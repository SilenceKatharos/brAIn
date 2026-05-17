#!/usr/bin/env bash
# Start the brAIn UI: FastAPI backend + Vite dev server
set -e

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "Starting brAIn backend on http://localhost:8000 ..."
cd "$ROOT"
.venv/bin/uvicorn ui.backend.main:app --reload --port 8000 &
BACKEND_PID=$!

echo "Starting brAIn frontend on http://localhost:5173 ..."
cd "$ROOT/ui/frontend"
npm run dev &
FRONTEND_PID=$!

echo ""
echo "  Backend : http://localhost:8000"
echo "  Frontend: http://localhost:5173"
echo ""
echo "Press Ctrl+C to stop."

trap "kill $BACKEND_PID $FRONTEND_PID 2>/dev/null" EXIT
wait
