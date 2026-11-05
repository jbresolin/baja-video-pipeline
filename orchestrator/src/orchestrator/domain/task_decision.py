"""What evaluate_task answers: the task to hand out, or why not (task_rules.py)."""

from dataclasses import dataclass

from shared.models import (
    ArchiveClipTask,
    DeliverTask,
    DownloadRawTask,
    ErrorCode,
    MakeProxyTask,
    ProcessTask,
)


@dataclass(frozen=True)
class TaskDecision:
    task: DownloadRawTask | MakeProxyTask | ProcessTask | DeliverTask | ArchiveClipTask | None = (
        None
    )
    refusal: ErrorCode | None = None
    reason: str = ""


def refuse(code: ErrorCode, reason: str) -> TaskDecision:
    return TaskDecision(refusal=code, reason=reason)
