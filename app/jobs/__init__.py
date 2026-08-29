"""Cada script es una funcion `correr()` que se puede llamar sola o por cron.

    python -m app.jobs.fetch_ml     # a mano
    (el worker las llama solas por APScheduler)
"""
from __future__ import annotations

import time
import traceback

from app.db import log_job


def envuelto(nombre: str, fn) -> dict:
    """Corre un job, mide, y deja rastro en job_log pase lo que pase."""
    t0 = time.monotonic()
    try:
        detalle = fn() or ""
        ms = int((time.monotonic() - t0) * 1000)
        log_job(nombre, True, str(detalle), ms)
        return {"ok": True, "detalle": str(detalle), "ms": ms}
    except Exception as e:
        ms = int((time.monotonic() - t0) * 1000)
        log_job(nombre, False, f"{e}\n{traceback.format_exc()[-1200:]}", ms)
        return {"ok": False, "detalle": str(e), "ms": ms}
