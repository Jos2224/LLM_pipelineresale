"""El reloj. Corre cada script cuando toca y no deja que se pisen.

Nada corre en paralelo consigo mismo (max_instances=1) y todo queda anotado en
job_log, asi el reporte del domingo puede decirte que se rompio.

Ritmo:
  webhook_ml      30 s   vaciar la cola de eventos de ML
  normalize        2 min  identificar lo nuevo
  score            3 min  juzgar
  alert            2 min  avisarte
  negociar_compra  3 min  saludar al vendedor, regatear, cerrar trato
  fetch_ml        30 min  mirar mercado
  fetch_aduanas   15 min  remates (1 min cerca del cierre)
  price_index      6 h    recalcular el ancla
  inventory_sync  12 h    tu inventario desde tu cuenta ML
  ventas_ml        2 h    tus ventas cerradas -> indice de precios
  gen_listing      1 h    redactar borradores
  publish_ml      15 min  subir lo aprobado
  sync_stock       5 min  evitar vender dos veces
  reply_bot       10 min  contestar preguntas
  negotiate        5 min  ofertas
  escalate         1 h    recordatorios
  reprice         diario 09:00
  trends          diario 23:30
  backtest        lunes 08:00
  report          domingo 20:00

Todo lo de Facebook vive en app/worker/fb.py y corre en paralelo, en la imagen
que trae Chrome. Aca no cabe: esta imagen no tiene navegador.
"""
from __future__ import annotations

import logging
import signal
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import BASE_PUBLICA, aduanas, p
from app.jobs import envuelto
from app.jobs import (alert, backtest, escalate, fetch_aduanas, fetch_ml,
                      gen_listing, inventory_sync, negociar_compra, negotiate,
                      normalize, poll_ml, price_index, publish_ml, reply_bot, ventas_ml,
                      report, reprice, score, sync_stock, trends, webhook_ml)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("cazador")
TZ = ZoneInfo("America/Santiago")


def tarea(nombre: str, modulo):
    def _correr():
        r = envuelto(nombre, modulo.correr)
        log.info("%-16s %s %s", nombre, "ok " if r["ok"] else "FALLO", r["detalle"][:160])
    return _correr


def main() -> None:
    s = BlockingScheduler(timezone="America/Santiago",
                          job_defaults={"max_instances": 1, "coalesce": True, "misfire_grace_time": 300})

    # Al arrancar, cada tarea corre una vez enseguida en vez de esperar su
    # intervalo completo (fetch_ml tardaria 30 min en dar señales de vida).
    # Se escalonan de a 20 s para no golpear ML ni Ollama todas juntas.
    arranque = [0]

    def cada(seg: int, nombre: str, modulo):
        arranque[0] += 20
        s.add_job(tarea(nombre, modulo), IntervalTrigger(seconds=seg), id=nombre,
                  next_run_time=datetime.now(TZ) + timedelta(seconds=arranque[0]))

    def diario(cron: str, nombre: str, modulo):
        s.add_job(tarea(nombre, modulo), CronTrigger.from_crontab(cron), id=nombre)

    # MercadoLibre entero, encendido o apagado de una vez (modo.ml_activo).
    #
    # Apagado el 29-ago. Con la app sin `offline_access` el token se muere a las
    # 6 horas y no se puede renovar, asi que los nueve jobs de ML corrian solo
    # para escribir "sin login de ML todavia" en el registro, cada pocos
    # minutos, tapando lo que si estaba pasando. Un job que no puede funcionar
    # no debe estar agendado: agendado y fallando se parece demasiado a
    # agendado y andando.
    #
    # Para volver a encenderlo: marcar offline_access en la app de ML,
    # `modo.ml_activo: true` aca abajo, /conectar en el bot y `cazador arriba`.
    ml = bool(p("modo.ml_activo", True))

    # --- bloque 1: detectar (riesgo cero) -----------------------------
    if ml:
        cada(30 * 60, "fetch_ml", fetch_ml)
        cada(3 * 60, "negociar_compra", negociar_compra)   # saluda, regatea, cierra
    cada(2 * 60, "normalize", normalize)
    cada(3 * 60, "score", score)
    cada(2 * 60, "alert", alert)
    cada(6 * 3600, "price_index", price_index)

    cfg_ad = aduanas()
    if cfg_ad.get("activo"):
        cada(int(cfg_ad.get("ritmo", {}).get("cada_min", 15)) * 60, "fetch_aduanas", fetch_aduanas)

    # --- bloque 2: publicar -------------------------------------------
    cada(3600, "gen_listing", gen_listing)   # el texto sirve para ML y para FB
    if ml:
        cada(12 * 3600, "inventory_sync", inventory_sync)
        # Tus ventas cerradas: el mejor precio que hay. ML cerro la busqueda
        # publica (403 PolicyAgent), asi que esta es la unica fuente de ML que
        # queda para el indice — y es mejor que la que se perdio, porque dice
        # lo que la gente PAGO y no lo que pedia. Es lo que mas se echa de
        # menos con ML apagado.
        cada(2 * 3600, "ventas_ml", ventas_ml)
        cada(15 * 60, "publish_ml", publish_ml)
        cada(5 * 60, "sync_stock", sync_stock)
        diario("0 9 * * *", "reprice", reprice)

    # --- bloque 3: negociar -------------------------------------------
    if ml:
        # Con endpoint publico: webhook (instantaneo). Sin el: cada 5 min.
        if BASE_PUBLICA:
            cada(30, "webhook_ml", webhook_ml)
        else:
            cada(5 * 60, "poll_ml", poll_ml)
        cada(10 * 60, "reply_bot", reply_bot)
    # Estos dos no son de ningun canal: leen la tabla `mensaje`, que llenan
    # tanto reply_bot (ML) como reply_fb (Messenger).
    cada(5 * 60, "negotiate", negotiate)
    cada(3600, "escalate", escalate)

    # --- bloque 4: mercado --------------------------------------------
    diario("30 23 * * *", "trends", trends)
    diario("0 8 * * 1", "backtest", backtest)
    diario("0 20 * * 0", "report", report)

    # --- facebook: NO va aca ------------------------------------------
    # Los jobs de FB manejan un navegador de verdad y esta imagen no lo trae.
    # Estuvieron agendados aca y morian con "falta playwright" en cada ciclo,
    # en silencio: parecia que cazaba en los dos lados y cazaba solo en ML.
    # Ahora viven en app/worker/fb.py, que corre en la imagen con Chrome y en
    # PARALELO con este reloj. Ver el servicio worker-fb.

    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: s.shutdown(wait=False))

    log.info("worker arriba con %d tareas · MercadoLibre %s",
             len(s.get_jobs()), "ON" if ml else "OFF (modo.ml_activo)")
    s.start()


if __name__ == "__main__":
    main()
