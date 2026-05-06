#!/usr/bin/env python3
"""Export benchmark API snapshots from a running Catan server."""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, Iterable, Tuple


DEFAULT_SERVER = "http://localhost:3001"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Catan benchmark API JSON.")
    parser.add_argument("--server", default=DEFAULT_SERVER, help="Benchmark server URL.")
    parser.add_argument("--out", default=None, help="Output directory. Defaults to this script's results/<timestamp> directory.")
    parser.add_argument("--benchmark-id", default=None, help="Optional benchmarkId filter.")
    parser.add_argument("--live-log-limit", type=int, default=500, help="Number of live log entries to export.")
    args = parser.parse_args()

    server = args.server.rstrip("/")
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    script_dir = Path(__file__).resolve().parent
    output_dir = Path(args.out) if args.out else script_dir / "results" / timestamp
    output_dir.mkdir(parents=True, exist_ok=True)

    query = {}
    if args.benchmark_id:
        query["benchmarkId"] = args.benchmark_id

    endpoints = {
        "definitions": ("/api/benchmark/definitions", {}),
        "overview": ("/api/benchmark/overview", query),
        "leaderboard_agents": ("/api/benchmark/leaderboard", {**query, "entityType": "agent"}),
        "leaderboard_players": ("/api/benchmark/leaderboard", {**query, "entityType": "player"}),
        "slices": ("/api/benchmark/slices", query),
        "live_log": ("/api/benchmark/live-log", {**query, "limit": str(args.live_log_limit)}),
    }

    manifest: Dict[str, Any] = {
        "server": server,
        "exportedAtUnix": time.time(),
        "outputDir": str(output_dir),
        "filters": {"benchmarkId": args.benchmark_id},
        "files": {},
    }

    try:
        for name, (path, params) in endpoints.items():
            url = build_url(server, path, params.items())
            payload = fetch_json(url)
            out_file = output_dir / f"{name}.json"
            write_json(out_file, payload)
            manifest["files"][name] = {"path": str(out_file), "url": url}
            print(f"[export] wrote {out_file}")

        manifest_file = output_dir / "manifest.json"
        write_json(manifest_file, manifest)
        print(f"[export] wrote {manifest_file}")
        return 0
    except urllib.error.URLError as exc:
        print(f"[export] could not reach benchmark server at {server}: {exc}", file=sys.stderr)
        print("[export] start the server with: cd Environment/server && npm start", file=sys.stderr)
        return 1
    except json.JSONDecodeError as exc:
        print(f"[export] server returned invalid JSON: {exc}", file=sys.stderr)
        return 1


def build_url(server: str, path: str, params: Iterable[Tuple[str, Any]]) -> str:
    clean_params = {
        key: str(value)
        for key, value in params
        if value is not None and str(value) != ""
    }
    query_string = urllib.parse.urlencode(clean_params)
    if not query_string:
        return f"{server}{path}"
    return f"{server}{path}?{query_string}"


def fetch_json(url: str) -> Any:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
