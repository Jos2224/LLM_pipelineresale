"""Acceso a Postgres. Pool unico, helpers cortos, SQL a la vista."""
from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Iterable

from psycopg import connect
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.config import PG_DSN

_pool: ConnectionPool | None = None


def pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(PG_DSN, min_size=1, max_size=6, kwargs={"row_factory": dict_row})
    return _pool


@contextmanager
def cur():
    with pool().connection() as con:
        with con.cursor() as c:
            yield c


def q(sql: str, args: Iterable[Any] = ()) -> list[dict]:
    with cur() as c:
        c.execute(sql, tuple(args))
        if c.description is None:
            return []
        return c.fetchall()


def q1(sql: str, args: Iterable[Any] = ()) -> dict | None:
    filas = q(sql, args)
    return filas[0] if filas else None


def ex(sql: str, args: Iterable[Any] = ()) -> int:
    with cur() as c:
        c.execute(sql, tuple(args))
        return c.rowcount


# ------------------------------------------------------------------ kv
def kv_get(clave: str, defecto=None):
    fila = q1("SELECT valor FROM kv WHERE clave = %s", (clave,))
    return fila["valor"] if fila else defecto


def kv_set(clave: str, valor) -> None:
    ex(
        "INSERT INTO kv (clave, valor, ts) VALUES (%s, %s, now()) "
        "ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor, ts = now()",
        (clave, json.dumps(valor)),
    )


# ------------------------------------------------------------------ log
def log_job(job: str, ok: bool, detalle: str = "", ms: int = 0) -> None:
    ex(
        "INSERT INTO job_log (job, ok, detalle, ms) VALUES (%s, %s, %s, %s)",
        (job, ok, detalle[:2000], ms),
    )
