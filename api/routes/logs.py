from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

router = APIRouter()

# 与 config/logging.py 中一致
_LOG_FILE = Path("logs/buyer_agent.log")


@router.get("/stream")
async def stream_logs(last_n: int = 200) -> StreamingResponse:
    """SSE endpoint: 先推送最近 last_n 行，之后实时 tail 新增行."""

    async def generator():
        # 先把已有的最近 N 行发给前端
        if _LOG_FILE.exists():
            lines = _LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            for line in lines[-last_n:]:
                yield _sse(line)

        # 然后持续 tail
        file_size = _LOG_FILE.stat().st_size if _LOG_FILE.exists() else 0
        while True:
            await asyncio.sleep(0.5)
            if not _LOG_FILE.exists():
                continue
            new_size = _LOG_FILE.stat().st_size
            if new_size > file_size:
                with _LOG_FILE.open(encoding="utf-8", errors="replace") as f:
                    f.seek(file_size)
                    new_text = f.read()
                file_size = new_size
                for line in new_text.splitlines():
                    if line.strip():
                        yield _sse(line)
            elif new_size < file_size:
                # 日志发生了 rotation，重置到文件头
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
    # 把换行符转义，保证 SSE 格式正确
    return f"data: {line}\n\n"
