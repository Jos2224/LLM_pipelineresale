"""Script 30b — vacia la cola de eventos que dejo el webhook.

ML manda solo un puntero ("/questions/123"). Aca se va a buscar el contenido
real y se guarda como mensaje. Cero polling: si nadie pregunta, no se gasta
ni una llamada a la API.

Topicos que interesan: questions, messages, orders_v2, items.
"""
from __future__ import annotations

import json
import re

import redis

from app import ml_api
from app.config import REDIS_URL
from app.db import ex, q1
from app.jobs import envuelto

R = redis.from_url(REDIS_URL, decode_responses=True)
COLA = "cazador:eventos"
MAX = 200

RE_MONTO = re.compile(r"(?:\$|\bpor\b|\bdejo en\b|\bofrezco\b)\s*([\d\.\s]{4,12})", re.I)


def _monto(texto: str) -> int | None:
    m = RE_MONTO.search(texto or "")
    if not m:
        return None
    d = re.sub(r"[^0-9]", "", m.group(1))
    if not d:
        return None
    n = int(d)
    return n if 1000 <= n <= 99_000_000 else None


def _pub_de_item(item_id: str) -> int | None:
    fila = q1("SELECT id FROM publicacion WHERE marketplace='ml' AND id_externo=%s", (item_id,))
    return fila["id"] if fila else None


def _pregunta(recurso: str) -> str:
    d = ml_api.recurso(recurso)
    pub = _pub_de_item(d.get("item_id", ""))
    if not pub:
        return "pregunta de un item que no esta en inventario"
    texto = d.get("text", "")
    ex(
        """INSERT INTO mensaje (publicacion, id_externo, direccion, tipo, texto, monto_oferta, estado)
           VALUES (%s,%s,'entra',%s,%s,%s,'nuevo')
           ON CONFLICT (id_externo, direccion) DO NOTHING""",
        (pub, str(d.get("id")), "oferta" if _monto(texto) else "pregunta", texto, _monto(texto)),
    )
    return "pregunta guardada"


def _orden(recurso: str) -> str:
    d = ml_api.recurso(recurso)
    if d.get("status") not in ("paid", "confirmed"):
        return f"orden en estado {d.get('status')}"
    for it in d.get("order_items", []):
        iid = (it.get("item") or {}).get("id")
        if iid:
            ex("UPDATE publicacion SET estado='vendida' WHERE marketplace='ml' AND id_externo=%s", (iid,))
    return "venta registrada"


def correr() -> str:
    hechos = saltados = 0
    for _ in range(MAX):
        crudo = R.lpop(COLA)
        if not crudo:
            break
        try:
            ev = json.loads(crudo)
        except Exception:
            continue
        topico = ev.get("topic", "")
        recurso = ev.get("resource", "")
        try:
            if topico == "questions":
                _pregunta(recurso)
            elif topico in ("orders_v2", "orders"):
                _orden(recurso)
            elif topico == "messages":
                # los mensajes post-venta se leen igual que una pregunta
                _pregunta(recurso)
            else:
                saltados += 1
                continue
            hechos += 1
        except Exception:
            # se reencola una vez al final para no perder el evento
            R.rpush(COLA + ":fallidos", crudo)
    return f"{hechos} eventos procesados, {saltados} ignorados"


if __name__ == "__main__":
    print(envuelto("webhook_ml", correr))
