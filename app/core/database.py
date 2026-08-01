"""Database configuration and session management"""
from contextlib import contextmanager
from typing import Callable, Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker, declarative_base
from app.core.config import settings

# Create engine
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_pre_ping=True,
    pool_recycle=3600,
)

# Create session factory
SessionLocal = sessionmaker(
    autocommit=False,
    autoflush=False,
    bind=engine,
)

# Create base class for models
Base = declarative_base()


def get_db() -> Iterator[Session]:
    """FastAPI dependency that owns one request-scoped session.

    Delegates the lifecycle (open → yield → always close) to :func:`task_session`
    so the request path and the background/Celery path cannot drift apart. The
    semantics are unchanged and deliberately identical to the previous inline
    ``try/finally``: **no implicit commit and no implicit rollback**. Endpoints
    that need either keep doing it explicitly.

    FastAPI throws endpoint exceptions back into this generator at the ``yield``,
    which propagates into the ``with`` block, so ``task_session``'s ``finally``
    still closes the session on both the success and failure paths.
    """
    with task_session() as db:
        yield db


@contextmanager
def task_session(
    session_factory: Callable[[], Session] | None = None,
) -> Iterator[Session]:
    """Own a background-task session lifecycle: open, yield, always close.

    Single seam for the Celery/background path, replacing the repeated
    ``db = SessionLocal(); try: ... finally: db.close()`` boilerplate. Semantics
    are deliberately identical to that boilerplate: **no implicit commit and no
    implicit rollback**. Bodies that need either keep doing it explicitly, so
    swapping the boilerplate for this helper cannot change task outcomes.

    ``session_factory`` is the injection point: callers/tests pass their own
    factory instead of patching a module global. It is spelled ``| None = None``
    rather than the in-process schedulers' ``session_factory: ... = SessionLocal``
    default *on purpose* — that default binds the object at import time, so a
    later ``app.core.database.SessionLocal`` patch never reaches it, while this
    helper resolves the module-level :data:`SessionLocal` at call time and
    therefore does honour such a patch.

    Blast radius of that patch surface: patching
    ``app.core.database.SessionLocal`` reaches every task going through
    ``task_session`` *and* any lazy-import script/service path that resolves the
    global at call time (e.g. ``promote_ml_release``), so prefer injecting
    ``session_factory`` and keep the patch for end-to-end task tests only.
    """
    factory = session_factory if session_factory is not None else SessionLocal
    db = factory()
    try:
        yield db
    finally:
        db.close()
