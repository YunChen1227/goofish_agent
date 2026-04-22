from contextlib import contextmanager
from typing import Generator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from goofish_agent.config.settings import get_settings

settings = get_settings()

engine = create_engine(settings.database_url, echo=False)


def _sqlite_add_column_if_missing(
    conn, table: str, existing: set[str], column: str, ddl_type: str
) -> None:
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))


def _migrate_sqlite_schema() -> None:
    """为已有 SQLite 库追加新列（create_all 不会修改已存在的表）。"""
    if not str(settings.database_url).startswith("sqlite"):
        return
    try:
        insp = inspect(engine)
        table_names = set(insp.get_table_names())
    except Exception:
        return

    with engine.begin() as conn:
        if "task" in table_names:
            existing = {c["name"] for c in insp.get_columns("task")}
            _sqlite_add_column_if_missing(
                conn, "task", existing, "damage_pattern_description", "TEXT"
            )
            _sqlite_add_column_if_missing(
                conn, "task", existing, "damage_example_images", "JSON"
            )
            _sqlite_add_column_if_missing(
                conn, "task", existing, "buyer_todo_list", "JSON"
            )
        if "sellerconversation" in table_names:
            existing = {c["name"] for c in insp.get_columns("sellerconversation")}
            _sqlite_add_column_if_missing(
                conn, "sellerconversation", existing, "todo_state", "JSON"
            )


def init_db() -> None:
    import goofish_agent.models  # noqa: F401 — register all table models before create_all
    SQLModel.metadata.create_all(engine)
    _migrate_sqlite_schema()


@contextmanager
def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
