from contextlib import contextmanager
from typing import Generator

from sqlmodel import Session, SQLModel, create_engine

from goofish_agent.config.settings import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, echo=False)


def init_db() -> None:
    import goofish_agent.models  # noqa: F401 — register all table models before create_all
    SQLModel.metadata.create_all(engine)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
