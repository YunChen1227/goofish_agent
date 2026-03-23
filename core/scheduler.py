from __future__ import annotations

from datetime import datetime
from uuid import UUID

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from goofish_agent.core.task_manager import TaskManager


class TaskScheduler:
    def __init__(self, task_manager: TaskManager) -> None:
        self._manager = task_manager
        self._scheduler = AsyncIOScheduler()

    def start(self) -> None:
        self._scheduler.start()

    def stop(self) -> None:
        self._scheduler.shutdown()

    def schedule_task(self, task_id: UUID, run_at: datetime | None = None) -> None:
        job_id = str(task_id)
        if run_at:
            self._scheduler.add_job(
                self._manager.run_task,
                "date",
                run_date=run_at,
                args=[task_id],
                id=job_id,
            )
        else:
            self._scheduler.add_job(
                self._manager.run_task,
                args=[task_id],
                id=job_id,
            )

    def schedule_monitor(
        self, task_id: UUID, interval_minutes: int = 60
    ) -> None:
        """Schedule recurring monitoring for new listings."""
        self._scheduler.add_job(
            self._manager.run_task,
            "interval",
            minutes=interval_minutes,
            args=[task_id],
            id=f"monitor_{task_id}",
        )

    def cancel(self, task_id: UUID) -> None:
        for job_id in (str(task_id), f"monitor_{task_id}"):
            try:
                self._scheduler.remove_job(job_id)
            except Exception:
                pass
