"""Script 30-alterno — el reemplazo del webhook cuando NO hay endpoint publico.

El webhook (script 30) es mejor: ML te avisa al instante y no gastas llamadas.
Pero abrir el path publico en el Funnel pide un comando con root una sola vez.
Mientras eso no exista, este script pregunta cada 5 minutos:

  /questions/search  status=UNANSWERED  -> preguntas sin responder
  /orders/search     status=paid        -> ventas nuevas

Costo: ~576 llamadas al dia. El limite de ML es de miles por hora, asi que
sobra. La unica diferencia real es que te enteras hasta 5 minutos despues.

Se apaga solo cuando BASE_PUBLICA existe: ahi manda el webhook.
"""
from __future__ import annotations

from app import ml_api
from app.config import BASE_PUBLICA
from app.db import ex, q1
from app.jobs import envuelto
from app.jobs.webhook_ml import _monto


def _guardar_pregunta(qs: dict) -> bool:
    pub = q1("SELECT id FROM publicacion WHERE marketplace='ml' AND id_externo=%s",
             (qs.get("item_id"),))
    if not pub:
        return False
    texto = qs.get("text", "")
    monto = _monto(texto)
    n = ex(
        """INSERT INTO mensaje (publicacion, id_externo, direccion, tipo, texto, monto_oferta, estado)
           VALUES (%s,%s,'entra',%s,%s,%s,'nuevo')
           ON CONFLICT (id_externo, direccion) DO NOTHING""",
        (pub["id"], str(qs.get("id")), "oferta" if monto else "pregunta", texto, monto),
    )
    return n > 0


def correr() -> str:
    if BASE_PUBLICA:
        return "apagado: hay endpoint publico, manda el webhook"
    try:
        uid = ml_api.usuario_id()
        if not uid:
            return "sin login de ML todavia"
    except Exception:
        return "sin login de ML todavia"

    nuevas = ventas = 0
    try:
        d = ml_api._get("/questions/search",
                        {"seller_id": uid, "status": "UNANSWERED", "api_version": 4, "limit": 50})
        for qs in d.get("questions", []):
            nuevas += 1 if _guardar_pregunta(qs) else 0
    except Exception as e:
        return f"preguntas fallaron: {e}"

    try:
        d = ml_api._get("/orders/search",
                        {"seller": uid, "order.status": "paid", "sort": "date_desc", "limit": 30})
        for o in d.get("results", []):
            for it in o.get("order_items", []):
                iid = (it.get("item") or {}).get("id")
                if iid:
                    ventas += ex(
                        """UPDATE publicacion SET estado='vendida'
                           WHERE marketplace='ml' AND id_externo=%s AND estado <> 'vendida'""",
                        (iid,),
                    )
    except Exception:
        pass

    return f"{nuevas} preguntas nuevas, {ventas} ventas detectadas"


if __name__ == "__main__":
    print(envuelto("poll_ml", correr))
