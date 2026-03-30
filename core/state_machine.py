from __future__ import annotations

from datetime import datetime, timezone

from goofish_agent.models.enums import TaskPhase, TaskStatus
from goofish_agent.models.task import Task

PHASE_ORDER: list[TaskPhase] = [
    TaskPhase.SEARCHING,
    TaskPhase.ASSESSING,
    TaskPhase.FAVORITING,
    TaskPhase.CHATTING,
    TaskPhase.NEGOTIATING,
    TaskPhase.NOTIFYING,
]

VALID_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.RUNNING: {
        TaskStatus.PAUSED,
        TaskStatus.COMPLETED,
        TaskStatus.FAILED,
        TaskStatus.ERROR,
        TaskStatus.CANCELLED,
    },
    TaskStatus.PAUSED: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.ERROR: {TaskStatus.RUNNING, TaskStatus.CANCELLED},
    TaskStatus.COMPLETED: set[TaskStatus](),
    TaskStatus.FAILED: set[TaskStatus](),
    TaskStatus.CANCELLED: set(),
}


class StateMachine:
    @staticmethod
    def can_transition(current: TaskStatus, target: TaskStatus) -> bool:
        return target in VALID_TRANSITIONS.get(current, set[TaskStatus]())

    @staticmethod
    def transition(task: Task, target_status: TaskStatus) -> None:
        """Transition task to *target_status*. Raises ``ValueError`` if invalid."""
        if not StateMachine.can_transition(task.status, target_status):
            raise ValueError(
                f"Invalid transition: {task.status.value} -> {target_status.value}"
            )
        task.status = target_status
        task.updated_at = datetime.now(timezone.utc)

    @staticmethod
    def advance_phase(task: Task) -> TaskPhase | None:
        """Move task to next phase. Returns new phase or ``None`` if last."""
        if task.current_phase is None:
            task.current_phase = PHASE_ORDER[0]
            return task.current_phase
        try:
            idx = PHASE_ORDER.index(task.current_phase)
            if idx + 1 < len(PHASE_ORDER):
                task.current_phase = PHASE_ORDER[idx + 1]
                return task.current_phase
        except ValueError:
            pass
        return None

    @staticmethod
    def get_resume_phase(task: Task) -> TaskPhase | None:
        """Get phase to resume from after error / pause."""
        return task.current_phase
