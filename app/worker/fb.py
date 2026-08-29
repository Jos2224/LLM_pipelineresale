"""El reloj de Facebook. Corre EN PARALELO con app/worker/main.py.

Por que hacen falta dos relojes y no uno:

  worker      imagen liviana (275 MB), sin navegador. Hace todo lo de
              MercadoLibre, los precios, las alertas y los avisos.
  worker-fb   imagen con Chrome adentro (4 GB). Hace lo de Facebook, que no
              tiene API y obliga a manejar un navegador de verdad.

Antes los jobs de FB estaban agendados en el worker liviano y **morian en el
acto**: `falta playwright` en cada ciclo, cada hora, sin que nadie lo mirara.
Parecia que buscaba en los dos lados y buscaba solo en ML.

Con los dos relojes andando, ML y Facebook se cazan a la vez y de verdad.

Ritmo — mas lento que el de ML a proposito, porque cada paso es un navegador
real y FB mira los patrones:

  fetch_fb       1 h    buscar gangas en Marketplace
  reply_fb      15 min  contestar a quien pregunta en TUS publicaciones
  negociar_fb   20 min  regatear la compra por Messenger
  publish_fb     4 h    subir lo que aprobaste

Nada de esto arranca si `modo.fb_activo` esta en false.
"""
from __future__ import annotations

import logging
import signal
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import p
from app.jobs import envuelto, fetch_fb, negociar_fb, publish_fb, reply_fb

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("cazador-fb")
TZ = ZoneInfo("America/Santiago")


def tarea(nombre: str, modulo):
    def _correr():
        r = envuelto(nombre, modulo.correr)
        log.info("%-14s %s %s", nombre, "ok " if r["ok"] else "FALLO", r["detalle"][:160])
    return _correr


def main() -> None:
    if not p("modo.fb_activo", False):
        log.info("facebook apagado (policy.yml: modo.fb_activo=false). "
                 "El contenedor queda quieto hasta que lo enciendas.")
        signal.pause()
        return

    s = BlockingScheduler(timezone="America/Santiago",
                          job_defaults={"max_instances": 1, "coalesce": True,
                                        "misfire_grace_time": 600})

    # Escalonados de a 3 min: dos sesiones de Chrome al mismo tiempo sobre el
    # mismo perfil se pisan el lock del navegador.
    arranque = [60]

    def cada(seg: int, nombre: str, modulo):
        s.add_job(tarea(nombre, modulo), IntervalTrigger(seconds=seg), id=nombre,
                  next_run_time=datetime.now(TZ) + timedelta(seconds=arranque[0]))
        arranque[0] += 180

    cada(15 * 60, "reply_fb", reply_fb)          # lo que mas urge: un comprador
    cada(20 * 60, "negociar_fb", negociar_fb)    # regatear la compra
    cada(3600, "fetch_fb", fetch_fb)             # buscar gangas
    cada(4 * 3600, "publish_fb", publish_fb)     # subir lo aprobado

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: s.shutdown(wait=False))

    log.info("worker-fb arriba con %d tareas", len(s.get_jobs()))
    s.start()


if __name__ == "__main__":
    main()
