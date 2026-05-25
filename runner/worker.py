"""Runner process that leases tasks from controller and executes repo workloads."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

import requests
from ruamel.yaml import YAML


def _load_config(path: str) -> dict[str, Any]:
    try:
        if not path or not os.path.exists(path):
            return {}
        yaml = YAML(typ="safe")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


CONTROLLER_URL = os.environ.get("CONTROLLER_URL", "http://localhost:8000")
MCP_CONFIG_PATH = os.environ.get("MCP_CONFIG_PATH", "mcp_config.yml")
MCP_CONFIG = _load_config(MCP_CONFIG_PATH)

RUNNER_ID = (
    os.environ.get("RUNNER_ID")
    or (MCP_CONFIG.get("runner") or {}).get("id")
    or "runner-local"
)
RUNNER_TAGS = (MCP_CONFIG.get("runner") or {}).get("tags") or ["chromium", "python"]

REPO_ROOT = (
    os.environ.get("REPO_ROOT")
    or (MCP_CONFIG.get("repo") or {}).get("default_path")
    or "."
)
POLL_INTERVAL = float(
    (MCP_CONFIG.get("runner") or {}).get("poll_interval_seconds") or 2
)


def _post_json(url: str, payload: dict) -> requests.Response:
    return requests.post(url, json=payload, timeout=20)


def _log(msg: str) -> None:
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"[runner:{RUNNER_ID}] {ts} {msg}", flush=True)


def _maybe_copy_path(src: Path, dst: Path) -> None:
    if not src.exists():
        return
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        return
    if src.is_dir():
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)


def _tail(s: str, max_chars: int = 16_000) -> str:
    if s is None:
        return ""
    if len(s) <= max_chars:
        return s
    return s[-max_chars:]


def _build_cmd(task: dict[str, Any]) -> list[str]:
    kind = task.get("kind")
    if kind == "experiment":
        exp = task.get("experiment") or {}
        cmd = [
            "python",
            "src/experiment.py",
            "--trials",
            str(exp.get("trials", 1)),
            "--concurrency",
            str(exp.get("concurrency", 1)),
        ]
        max_browsers = exp.get("max_browsers")
        if max_browsers is not None:
            cmd += ["--max-browsers", str(max_browsers)]
        return cmd
    if kind == "analysis":
        an = task.get("analysis") or {}
        out = an.get("output_dir") or "results"
        return ["python", "src/analysis.py", "--output", out]
    if kind == "pytest":
        py = task.get("pytest") or {}
        args = py.get("args") or []
        return ["python", "-m", "pytest", *args]
    raise ValueError(f"Unknown task kind: {kind!r}")


def _execute(task: dict[str, Any]) -> dict[str, Any]:
    repo_path = (task.get("repo") or {}).get("path") or REPO_ROOT
    repo = Path(repo_path)
    if not repo.exists():
        raise FileNotFoundError(f"repo path not found: {repo_path}")

    env = os.environ.copy()
    env.update(task.get("env") or {})

    cmd = _build_cmd(task)
    started_at = time.time()

    _log(f"start task_id={task.get('task_id')} kind={task.get('kind')} cmd={cmd}")

    timeout_s = int(
        task.get("timeout_seconds")
        or (MCP_CONFIG.get("defaults") or {}).get("timeout_seconds")
        or 3600
    )
    proc = subprocess.run(
        cmd,
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )
    finished_at = time.time()

    _log(
        f"finish task_id={task.get('task_id')} rc={proc.returncode} elapsed_s={finished_at - started_at:.1f}"
    )

    # Artifacts: keep default small; captures/ can be huge.
    artifacts = task.get("artifacts") or {}
    rel_paths = artifacts.get("paths") or []
    if not rel_paths:
        rel_paths = ((MCP_CONFIG.get("defaults") or {}).get("artifacts") or {}).get(
            "paths"
        ) or []
    artifact_dir = repo / "mcp_artifacts" / str(task.get("task_id"))
    artifact_dir.mkdir(parents=True, exist_ok=True)

    meta = {
        "cmd": cmd,
        "cwd": str(repo),
        "returncode": proc.returncode,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    (artifact_dir / "meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8"
    )

    if artifacts.get("include_stdout", True):
        (artifact_dir / "stdout.txt").write_text(proc.stdout or "", encoding="utf-8")
    if artifacts.get("include_stderr", True):
        (artifact_dir / "stderr.txt").write_text(proc.stderr or "", encoding="utf-8")

    for rel in rel_paths:
        src = repo / rel
        dst = artifact_dir / "files" / rel
        _maybe_copy_path(src, dst)

    return {
        "returncode": proc.returncode,
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
        "started_at": started_at,
        "finished_at": finished_at,
        "artifact_dir": str(artifact_dir),
    }


def _lease_one() -> dict[str, Any] | None:
    r = _post_json(
        f"{CONTROLLER_URL}/lease", {"runner_id": RUNNER_ID, "tags": RUNNER_TAGS}
    )
    if r.status_code == 204:
        return None
    r.raise_for_status()
    task = r.json()
    if not isinstance(task, dict):
        return None
    return task


def main_loop() -> None:
    _log(f"online controller={CONTROLLER_URL} repo_root={REPO_ROOT} tags={RUNNER_TAGS}")
    while True:
        task = None
        try:
            task = _lease_one()
        except Exception:
            task = None

        if not task:
            time.sleep(POLL_INTERVAL)
            continue

        task_id = task.get("task_id")
        if not task_id:
            time.sleep(POLL_INTERVAL)
            continue

        _log(f"leased task_id={task_id} kind={task.get('kind')}")

        try:
            res = _execute(task)
            status = "completed" if res["returncode"] == 0 else "failed"
            payload = {
                "runner_id": RUNNER_ID,
                "status": status,
                "returncode": int(res["returncode"]),
                "started_at": float(res["started_at"]),
                "finished_at": float(res["finished_at"]),
                "artifact_dir": res["artifact_dir"],
                "stdout_tail": _tail(res["stdout"]),
                "stderr_tail": _tail(res["stderr"]),
                "meta": {},
            }
        except Exception as e:
            now = time.time()
            _log(f"error task_id={task_id} err={type(e).__name__}: {e}")
            payload = {
                "runner_id": RUNNER_ID,
                "status": "failed",
                "returncode": 1,
                "started_at": now,
                "finished_at": now,
                "artifact_dir": None,
                "stdout_tail": "",
                "stderr_tail": _tail(str(e)),
                "meta": {"error": type(e).__name__},
            }

        try:
            _post_json(f"{CONTROLLER_URL}/tasks/{task_id}/complete", payload)
            _log(f"reported task_id={task_id} status={payload['status']}")
        except Exception:
            # If controller is down, keep going; task will show as running.
            _log(f"warn failed to report task_id={task_id} to controller")
            pass


if __name__ == "__main__":
    main_loop()
