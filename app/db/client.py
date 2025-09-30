from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg2
from psycopg2.pool import SimpleConnectionPool

from app.config import settings

_pool: SimpleConnectionPool | None = None


def init_pool() -> None:
    global _pool
    if _pool is not None:
        return
    if not settings.db_enabled:
        return
    dsn = settings.db_dsn.replace("postgresql+psycopg2://", "postgresql://")
    _pool = SimpleConnectionPool(1, 10, dsn=dsn)


@contextmanager
def get_conn() -> Iterator[psycopg2.extensions.connection]:
    if _pool is None:
        init_pool()
    if _pool is None:
        # DB not configured
        yield None  # type: ignore[misc]
        return
    conn: psycopg2.extensions.connection | None = None
    try:
        # Attempt to obtain a healthy connection (retry once on closed connections)
        for attempt in range(2):
            conn = _pool.getconn()
            try:
                if settings.db_schema:
                    with conn.cursor() as cur:
                        cur.execute(f"SET search_path TO {settings.db_schema}")
                break
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                try:
                    _pool.putconn(conn, close=True)
                except Exception:
                    pass
                conn = None
                if attempt == 1:
                    raise
        if conn is None:
            yield None  # type: ignore[misc]
            return
        yield conn
        conn.commit()
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        raise
    finally:
        if conn is not None:
            _pool.putconn(conn)
