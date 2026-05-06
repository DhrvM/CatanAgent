#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$SCRIPT_DIR"

while [ "$ROOT_DIR" != "/" ]; do
  if [ -d "$ROOT_DIR/Agent" ] && [ -d "$ROOT_DIR/Environment" ]; then
    break
  fi
  ROOT_DIR="$(dirname "$ROOT_DIR")"
done

if [ ! -d "$ROOT_DIR/Agent" ] || [ ! -d "$ROOT_DIR/Environment" ]; then
  printf 'Could not locate repository root from %s\n' "$SCRIPT_DIR" >&2
  exit 1
fi

SERVER="http://localhost:3001"
GAMES="10"
PLAYERS="3"
PARALLEL="1"
TIMEOUT_S="1800"
OUTPUT="$ROOT_DIR/Agent/training/results/heuristic_baseline_10_games.json"

usage() {
  printf '%s\n' "Usage: $0 [--server URL] [--games N] [--players N] [--parallel N] [--timeout-s SECONDS] [--output PATH]"
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --server)
      SERVER="$2"
      shift 2
      ;;
    --games)
      GAMES="$2"
      shift 2
      ;;
    --players)
      PLAYERS="$2"
      shift 2
      ;;
    --parallel)
      PARALLEL="$2"
      shift 2
      ;;
    --timeout-s)
      TIMEOUT_S="$2"
      shift 2
      ;;
    --output)
      OUTPUT="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'Unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
  PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
else
  PYTHON_BIN="${PYTHON:-python3}"
fi

cd "$ROOT_DIR"
"$PYTHON_BIN" -m Agent.training.run_self_play \
  --games "$GAMES" \
  --players "$PLAYERS" \
  --parallel "$PARALLEL" \
  --timeout-s "$TIMEOUT_S" \
  --server "$SERVER" \
  --agent-type benchmark \
  --output "$OUTPUT"
