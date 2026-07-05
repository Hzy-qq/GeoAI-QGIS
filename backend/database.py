from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from geoai_agent.config import PROJECT_ROOT, env_int, env_str

from .models import Base


def _database_url(raw_url: str | None = None) -> str:
    url = raw_url or env_str("DATABASE_URL", "sqlite:///outputs/state5.db")
    prefix = "sqlite:///"
    if url.startswith(prefix):
        value = url[len(prefix):]
        path = Path(value)
        if not path.is_absolute():
            path = (PROJECT_ROOT / path).resolve()
            path.parent.mkdir(parents=True, exist_ok=True)
            return f"sqlite:///{path.as_posix()}"
    return url


class Database:
    def __init__(self, url: str | None = None) -> None:
        self.url = _database_url(url)
        options: dict = {"pool_pre_ping": True, "future": True}
        if self.url.startswith("sqlite"):
            options["connect_args"] = {"check_same_thread": False}
        else:
            options.update({
                "pool_size": env_int("DB_POOL_SIZE", 5),
                "max_overflow": env_int("DB_MAX_OVERFLOW", 10),
                "pool_recycle": env_int("DB_POOL_RECYCLE_SECONDS", 1800),
            })
        self.engine: Engine = create_engine(self.url, **options)
        self.session_factory = sessionmaker(
            bind=self.engine, autoflush=False, expire_on_commit=False, future=True,
        )

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    def check(self) -> None:
        with self.engine.connect() as connection:
            connection.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()
