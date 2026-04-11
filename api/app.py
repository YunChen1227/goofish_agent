from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI

from goofish_agent.config.logging import setup_logging
from goofish_agent.storage.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    init_db()
    setup_logging()
    yield


app = FastAPI(title="Marketplace Buyer Agent", version="1.0.0", lifespan=lifespan)

from goofish_agent.api.routes import results, tasks  # noqa: E402

app.include_router(tasks.router, prefix="/api/tasks", tags=["tasks"])
app.include_router(results.router, prefix="/api/results", tags=["results"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
