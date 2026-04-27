#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  ./start_agent.sh <agent-type> <game-code>

Agent types:
  react, react-agent
  multi, multi-agent
  heuristic, heuristic-agent
  benchmark, benchmark-agent

Examples:
  ./start_agent.sh react ABCDEF
  ./start_agent.sh multi-agent ABCDEF
  ./start_agent.sh heuristic-agent ABCDEF

Optional environment:
  CATAN_SERVER_URL=http://localhost:3001 ./start_agent.sh react ABCDEF
EOF
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

agent_type="$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"
game_code="$2"

case "$agent_type" in
  react|react-agent)
    mode="react"
    name="ReactBot"
    ;;
  multi|multi-agent)
    mode="multi"
    name="StrategyBot"
    ;;
  heuristic|heuristic-agent|benchmark|benchmark-agent)
    mode="benchmark"
    name="HeuristicBot"
    ;;
  *)
    echo "Unknown agent type: $1" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
esac

if [[ -x ".venv/bin/python" ]]; then
  python_bin=".venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  python_bin="python3"
elif command -v python >/dev/null 2>&1; then
  python_bin="python"
else
  echo "Could not find python3 or python on PATH." >&2
  exit 1
fi

echo "[start_agent] using python: $python_bin" >&2
echo "[start_agent] mode=$mode game_code=$game_code name=$name" >&2

exec "$python_bin" -m Agent.main \
  --mode "$mode" \
  --game-code "$game_code" \
  --name "$name"
