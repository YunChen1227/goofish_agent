from __future__ import annotations

import sys
from pathlib import Path
from uuid import UUID

from loguru import logger

TASK_LOG_DIR = Path("logs/tasks")
_LOG_FMT = (
    "{time:YYYY-MM-DD HH:mm:ss} | {level:<8} | "
    "{name}:{function}:{line} - {message}"
)


def setup_logging(level: str = "INFO") -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level=level,
        format=_LOG_FMT,
    )
    logger.add(
        "logs/buyer_agent.log",
        level=level,
        rotation="10 MB",
        format=_LOG_FMT,
    )


# ---------------------------------------------------------------------------
# Per-task sinks
# ---------------------------------------------------------------------------
#
# Every running task gets its own file sink at ``logs/tasks/{task_id}.log``.
# The sink is attached when the task starts running (``TaskManager.run_task``)
# and detached when it exits. Records are routed to it via a filter matching
# ``extra["task_id"]``, which is bound with ``logger.contextualize(...)``.
#
# The per-task file is what the UI's SSE stream tails, so the log console only
# shows messages produced by *that* task, and deleting the task also deletes
# its log file (see :func:`remove_task_log_file`).


def task_log_path(task_id: UUID | str) -> Path:
    TASK_LOG_DIR.mkdir(parents=True, exist_ok=True)
    return TASK_LOG_DIR / f"{task_id}.log"


def enable_task_log(task_id: UUID | str, level: str = "INFO") -> int:
    """Attach a per-task file sink. Returns a loguru handler id (pass to disable)."""
    tid = str(task_id)
    path = task_log_path(tid)

    def _filter(record: dict) -> bool:
        return record["extra"].get("task_id") == tid

    return logger.add(
        str(path),
        level=level,
        format=_LOG_FMT,
        filter=_filter,
        enqueue=True,  # safe under threads / asyncio
    )


def disable_task_log(handler_id: int) -> None:
    try:
        logger.remove(handler_id)
    except ValueError:
        # already removed or never added
        pass


def remove_task_log_file(task_id: UUID | str) -> None:
    path = task_log_path(task_id)
    try:
        if path.exists():
            path.unlink()
    except OSError:
        # Best-effort: a stale handle (e.g. on Windows) shouldn't break deletion
        pass
