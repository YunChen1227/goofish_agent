from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from goofish_agent.config.logging import task_log_path

router = APIRouter()

# 全局聚合日志（所有任务混合写入，兜底用）
_GLOBAL_LOG_FILE = Path("logs/buyer_agent.log")


@router.get("/stream")
async def stream_logs(
    last_n: int = 200,
    task_id: Optional[str] = None,
) -> StreamingResponse:
    """SSE endpoint: tail a log file and push new lines in real time.

    - 传入 ``task_id``：只推送该任务的日志 (``logs/tasks/{task_id}.log``)。
      任务被删除 → 日志文件被删除 → 流自动结束。
    - 不传：兜底 tail 全局日志文件（历史行为）。
    """

    log_file = task_log_path(task_id) if task_id else _GLOBAL_LOG_FILE

    async def generator():
        # 1) 先把已有最近 N 行补发
        if log_file.exists():
            lines = log_file.read_text(
                encoding="utf-8", errors="replace"
            ).splitlines()
            for line in lines[-last_n:]:
                yield _sse(line)

        # 2) 持续 tail
        file_size = log_file.stat().st_size if log_file.exists() else 0
        while True:
            await asyncio.sleep(0.5)
            if not log_file.exists():
                # 文件被删除（例如任务被删除），结束流，让前端自动重连或停止
                if task_id is not None:
                    yield _sse("[log stream] task log file removed")
                    return
                continue
            new_size = log_file.stat().st_size
            if new_size > file_size:
                with log_file.open(encoding="utf-8", errors="replace") as f:
                    f.seek(file_size)
                    new_text = f.read()
                file_size = new_size
                for line in new_text.splitlines():
                    if line.strip():
                        yield _sse(line)
            elif new_size < file_size:
                # 发生 rotation / 截断，重置到文件头
                file_size = 0

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(line: str) -> str:
    return f"data: {line}\n\n"
