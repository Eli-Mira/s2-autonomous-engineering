"""Durable task state machine for autonomous engineering.

States: PENDING -> CLAIMED -> RUNNING -> VERIFYING -> REVIEWING -> COMMITTED -> DONE -> NEXT TASK
Also: BLOCKED, FAILED, CANCELLED, RECOVERABLE
"""

import json
import os
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class TaskState(Enum):
    PENDING = "pending"
    CLAIMED = "claimed"
    RUNNING = "running"
    VERIFYING = "verifying"
    REVIEWING = "reviewing"
    COMMITTED = "committed"
    DONE = "done"
    NEXT_TASK = "next_task"
    BLOCKED = "blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECOVERABLE = "recoverable"


@dataclass
class TaskRecord:
    task_id: str
    idempotency_key: str
    title: str
    description: str
    state: TaskState
    specialist: str
    workspace: str
    model: str
    skill: str
    source_hashes: dict = field(default_factory=dict)
    attempts: int = 0
    repairs: int = 0
    max_repairs: int = 3
    test_evidence: dict = field(default_factory=dict)
    commit_id: Optional[str] = None
    recovery_checkpoint: Optional[str] = None
    next_task_id: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    dependencies: list[str] = field(default_factory=list)
    finished_at: Optional[str] = None

    def to_dict(self):
        d = asdict(self)
        d["state"] = self.state.value
        return d

    @classmethod
    def from_dict(cls, d):
        d = dict(d)
        d["state"] = TaskState(d["state"])
        return cls(**d)


class TaskStore:
    """File-backed durable task store with atomic writes and locking."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._tasks: dict[str, TaskRecord] = {}
        self._load()

    def _store_path(self) -> Path:
        return self.root / "tasks.json"

    def _load(self):
        p = self._store_path()
        if p.exists():
            data = json.loads(p.read_text())
            for t in data.get("tasks", []):
                self._tasks[t["task_id"]] = TaskRecord.from_dict(t)

    def _save(self):
        p = self._store_path()
        tmp = p.with_suffix(".tmp")
        data = {"tasks": [t.to_dict() for t in self._tasks.values()]}
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=2))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
        # Platform: O_DIRECTORY is Linux-only; skip directory-fsync on Windows.
        try:
            dir_fd = os.open(str(p.parent), os.O_RDONLY | os.O_DIRECTORY)
        except (AttributeError, OSError):
            return
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)

    def save(self, task: TaskRecord):
        with self._lock:
            self._tasks[task.task_id] = task
            self._save()

    def get(self, task_id: str) -> Optional[TaskRecord]:
        with self._lock:
            return self._tasks.get(task_id)

    def all(self) -> list[TaskRecord]:
        with self._lock:
            return list(self._tasks.values())

    def all_task_ids(self) -> list[str]:
        with self._lock:
            return list(self._tasks.keys())

    def pending_tasks(self) -> list[str]:
        with self._lock:
            return [tid for tid, t in self._tasks.items() if t.state == TaskState.PENDING]

    def transition(self, task_id: str, new_state: TaskState, **kwargs) -> TaskRecord:
        with self._lock:
            t = self._tasks.get(task_id)
            if t is None:
                raise KeyError(task_id)
            old = t.state
            valid = self._valid_transition(old, new_state)
            if not valid:
                raise ValueError(f"Invalid transition {old.value} -> {new_state.value} for {task_id}")
            t.state = new_state
            t.updated_at = time.time()
            for k, v in kwargs.items():
                setattr(t, k, v)
            self._save()
            return t

    @staticmethod
    def _valid_transition(old: TaskState, new: TaskState) -> bool:
        allowed = {
            TaskState.PENDING: {TaskState.CLAIMED, TaskState.BLOCKED, TaskState.CANCELLED},
            TaskState.CLAIMED: {TaskState.RUNNING, TaskState.RECOVERABLE, TaskState.FAILED, TaskState.CANCELLED},
            TaskState.RUNNING: {TaskState.VERIFYING, TaskState.RECOVERABLE, TaskState.FAILED, TaskState.CANCELLED},
            TaskState.VERIFYING: {TaskState.REVIEWING, TaskState.RECOVERABLE, TaskState.FAILED},
            TaskState.REVIEWING: {TaskState.COMMITTED, TaskState.RECOVERABLE, TaskState.FAILED},
            TaskState.COMMITTED: {TaskState.DONE, TaskState.NEXT_TASK, TaskState.RECOVERABLE},
            TaskState.DONE: set(),
            TaskState.NEXT_TASK: set(),
            TaskState.BLOCKED: {TaskState.RECOVERABLE, TaskState.CANCELLED},
            TaskState.FAILED: {TaskState.RECOVERABLE, TaskState.CANCELLED},
            TaskState.CANCELLED: set(),
            TaskState.RECOVERABLE: {TaskState.PENDING, TaskState.CLAIMED, TaskState.CANCELLED},
        }
        return new in allowed.get(old, set())

    def claim_next(self, specialist: str, model: str, skill: str) -> Optional[TaskRecord]:
        with self._lock:
            for t in self._tasks.values():
                if t.state == TaskState.PENDING:
                    t.state = TaskState.CLAIMED
                    t.specialist = specialist
                    t.model = model
                    t.skill = skill
                    t.attempts += 1
                    t.updated_at = time.time()
                    self._save()
                    return t
            return None


def make_task(task_id: str, title: str, description: str, specialist="lain",
              model="deterministic-simulated", skill="coding", **kw) -> TaskRecord:
    return TaskRecord(
        task_id=task_id,
        idempotency_key=f"{task_id}-{uuid.uuid4().hex[:8]}",
        title=title,
        description=description,
        state=TaskState.PENDING,
        specialist=specialist,
        workspace="",
        model=model,
        skill=skill,
        **kw,
    )