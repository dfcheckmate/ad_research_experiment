"""Enqueue an experiment task on the local MCP controller.

Examples:
  python3 scripts/enqueue_experiment.py --small
  python3 scripts/enqueue_experiment.py --trials 3 --concurrency 2 --max-browsers 4
  python3 scripts/enqueue_experiment.py --sites-per-trial 12 --db-url sqlite:///out/ads.db
"""

from __future__ import annotations

import argparse
import json
from urllib.request import Request, urlopen


def build_task(args: argparse.Namespace) -> dict:
    screenshots = "1" if args.screenshots else "0"
    dom_snippets = "1" if args.dom_snippets else "0"
    telemetry = "1" if args.telemetry else "0"

    env = {
        "CAPTURE_SCREENSHOTS": screenshots,
        "CAPTURE_DOM_SNIPPETS": dom_snippets,
        "ENABLE_TELEMETRY": telemetry,
        "DB_URL": args.db_url,
    }
    if args.sites_per_trial is not None:
        env["MEASUREMENT_SITES_PER_TRIAL"] = str(args.sites_per_trial)

    return {
        "kind": "experiment",
        "repo": {"path": "/work/repo"},
        "requires_tags": ["chromium"],
        "env": env,
        "timeout_seconds": args.timeout_seconds,
        "artifacts": {
            "paths": [args.db_url.removeprefix("sqlite:///" )] if args.db_url.startswith("sqlite:///") else [],
            "include_stdout": True,
            "include_stderr": True,
        },
        "experiment": {
            "trials": args.trials,
            "concurrency": args.concurrency,
            "max_browsers": args.max_browsers,
        },
        "analysis": None,
        "pytest": None,
        "callback_url": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Enqueue MCP experiment task")
    parser.add_argument("--controller", default="http://localhost:8000/tasks")
    parser.add_argument("--trials", type=int, default=1)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--max-browsers", type=int, default=2)
    parser.add_argument("--timeout-seconds", type=int, default=3600)
    parser.add_argument("--db-url", default="sqlite:///out/ads.db")
    parser.add_argument("--sites-per-trial", type=int)
    parser.add_argument("--screenshots", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dom-snippets", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--telemetry", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--small",
        action="store_true",
        help="Shortcut for a small validation run (1 trial, 1 concurrency, 1 browser, 3 sites, no screenshots).",
    )
    args = parser.parse_args()

    if args.small:
        args.trials = 1
        args.concurrency = 1
        args.max_browsers = 1
        args.sites_per_trial = 3
        args.screenshots = False
        if args.db_url == "sqlite:///out/ads.db":
            args.db_url = "sqlite:///out/ads-small-trial.db"

    payload = json.dumps(build_task(args)).encode("utf-8")
    req = Request(args.controller, data=payload, headers={"content-type": "application/json"})
    with urlopen(req, timeout=15) as resp:
        print(resp.read().decode("utf-8"))


if __name__ == "__main__":
    main()
