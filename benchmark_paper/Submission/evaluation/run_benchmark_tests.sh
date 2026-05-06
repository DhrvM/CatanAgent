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

cd "$ROOT_DIR/Environment/server"
npm run test:benchmark
