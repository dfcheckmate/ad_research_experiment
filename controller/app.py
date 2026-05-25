"""Minimal MCP-style controller for this repository.

This repo's real workloads are:

- `python src/experiment.py ...` (Playwright-based capture into DB + captures/)
- `python src/analysis.py --output results/` (writes results/)
- `python -m pytest ...` (infrastructure tests)

So the controller models tasks around those entrypoints and lets runners lease
work based on tags (e.g. chromium-capable).

This controller is intentionally simple: in-memory queue, no auth, no DB.
"""

from __future__ import annotations

import os
import time
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Response
from pydantic import BaseModel, Field
from ruamel.yaml import YAML


def _load_config(path: str) -> dict[str, Any]:
    try:
        if not path:
            return {}
        if not os.path.exists(path):
            return {}
        yaml = YAML(typ="safe")
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.load(f) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


app = FastAPI()

MCP_CONFIG_PATH = os.getenv("MCP_CONFIG_PATH", "mcp_config.yml")
MCP_CONFIG = _load_config(MCP_CONFIG_PATH)


TaskKind = Literal["experiment", "analysis", "pytest"]


class RepoSpec(BaseModel):
    # For now we support a local path on the runner. (Git checkout can be
    # layered in later if you need remote execution.)
    path: str = Field(..., description="Absolute or container path to repo root")


class ExperimentArgs(BaseModel):
    trials: int = 1
    concurrency: int = 1
    max_browsers: int | None = None


class AnalysisArgs(BaseModel):
    output_dir: str = "results"


class PytestArgs(BaseModel):
    args: list[str] = Field(default_factory=list, description="Raw pytest args")


class ArtifactSpec(BaseModel):
    # Paths are relative to repo root on the runner.
    paths: list[str] = Field(default_factory=list)
    include_stdout: bool = True
    include_stderr: bool = True


class TaskCreate(BaseModel):
    kind: TaskKind
    repo: RepoSpec
    requires_tags: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = 3600
    artifacts: ArtifactSpec = Field(default_factory=ArtifactSpec)
    experiment: ExperimentArgs | None = None
    analysis: AnalysisArgs | None = None
    pytest: PytestArgs | None = None
    callback_url: str | None = None


class LeaseRequest(BaseModel):
    runner_id: str
    tags: list[str] = Field(default_factory=list)


class TaskResult(BaseModel):
    runner_id: str
    status: Literal["completed", "failed"]
    returncode: int
    started_at: float
    finished_at: float
    artifact_dir: str | None = None
    stdout_tail: str | None = None
    stderr_tail: str | None = None
    meta: dict[str, Any] = Field(default_factory=dict)


_LOCK = None
try:
    import asyncio

    _LOCK = asyncio.Lock()
except Exception:
    _LOCK = None


TASKS: dict[str, dict[str, Any]] = {}
PENDING: list[str] = []


def _now() -> float:
    return time.time()


def _tags_satisfy(runner_tags: set[str], required: list[str]) -> bool:
    if not required:
        return True
    return set(required).issubset(runner_tags)


@app.get("/healthz")
def healthz():
    return {"ok": True, "pending": len(PENDING), "tasks": len(TASKS)}


@app.get("/tasks")
def list_tasks(status: str | None = None, limit: int = 50):
    """List recent tasks.

    Query params:
    - status: optional filter (queued|running|completed|failed)
    - limit: max tasks to return
    """
    items = list(TASKS.values())
    items.sort(key=lambda r: float(r.get("created_at") or 0.0), reverse=True)
    if status:
        items = [r for r in items if r.get("status") == status]
    return items[: max(1, min(int(limit), 500))]


@app.post("/tasks")
async def create_task(task: TaskCreate):
    task_id = str(uuid4())

    # Apply config defaults (if the task didn't specify them).
    defaults = MCP_CONFIG.get("defaults") or {}
    if task.timeout_seconds == 3600 and isinstance(
        defaults.get("timeout_seconds"), int
    ):
        task.timeout_seconds = int(defaults["timeout_seconds"])

    d_art = defaults.get("artifacts") or {}
    if not task.artifacts.paths and d_art.get("paths"):
        task.artifacts.paths = list(d_art.get("paths") or [])

    payload = task.model_dump()
    payload["task_id"] = task_id
    rec = {
        "task_id": task_id,
        "status": "queued",
        "created_at": _now(),
        "updated_at": _now(),
        "task": payload,
        "runner_id": None,
        "result": None,
    }
    if _LOCK:
        async with _LOCK:
            TASKS[task_id] = rec
            PENDING.append(task_id)
    else:
        TASKS[task_id] = rec
        PENDING.append(task_id)
    return {"task_id": task_id, "status": "queued"}


# Backwards-compatible alias for the original template.
@app.post("/enqueue")
async def enqueue(task: TaskCreate):
    return await create_task(task)


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    rec = TASKS.get(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task not found")
    return rec


@app.get("/status/{task_id}")
def status(task_id: str):
    return get_task(task_id)


@app.post("/lease")
async def lease(req: LeaseRequest):
    runner_tags = set(req.tags)

    if _LOCK:
        async with _LOCK:
            return _lease_locked(req.runner_id, runner_tags)
    return _lease_locked(req.runner_id, runner_tags)


def _lease_locked(runner_id: str, runner_tags: set[str]):
    # Find the first queued task whose required tags are satisfied.
    for i, task_id in enumerate(list(PENDING)):
        rec = TASKS.get(task_id)
        if not rec or rec.get("status") != "queued":
            continue
        task = rec.get("task") or {}
        required = task.get("requires_tags") or []
        if not _tags_satisfy(runner_tags, required):
            continue

        # Lease it.
        PENDING.pop(i)
        rec["status"] = "running"
        rec["runner_id"] = runner_id
        rec["updated_at"] = _now()
        return rec["task"]

    return Response(status_code=204)


@app.post("/tasks/{task_id}/complete")
async def complete(task_id: str, result: TaskResult):
    if _LOCK:
        async with _LOCK:
            return _complete_locked(task_id, result)
    return _complete_locked(task_id, result)


def _complete_locked(task_id: str, result: TaskResult):
    rec = TASKS.get(task_id)
    if not rec:
        raise HTTPException(status_code=404, detail="task not found")

    rec["status"] = result.status
    rec["updated_at"] = _now()
    rec["runner_id"] = result.runner_id
    rec["result"] = result.model_dump()
    return {"task_id": task_id, "status": rec["status"]}
