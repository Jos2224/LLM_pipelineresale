"""Script 31 — contesta preguntas publicas de ML.

Solo responde lo que esta en la base de datos. Si la respuesta no sale de un
dato guardado, escala a Telegram. Nunca promete garantia, envio gratis ni
plazos que no existen.

El LLM se usa unicamente para redactar bonito un dato que ya tenemos. Se le
pasa una ficha cerrada y se le prohibe agregar nada.
"""
from __future__ import annotations

import redis

from app import llm, ml_api, tg
from app.config import REDIS_URL, p
from app.db import ex, q, q1
from app.jobs import envuelto

R = redis.from_url(REDIS_URL, decode_responses=True)
COLA = "cazador:enviar_msg"   # ids de respuestas que aprobaste con el boton

PROMPT = """Responde la pregunta de un comprador en MercadoLibre Chile.

Ficha del producto (unica fuente de verdad):
{ficha}

Pregunta: {pregunta}

Reglas absolutas:
- Usa SOLO datos de la ficha. Si la ficha no lo dice, responde exactamente: NO_SE
- Maximo 2 frases, tuteo, sin emojis, sin saludos largos.
- Nunca prometas garantia, plazos de envio ni descuentos.

Responde solo el texto de la respuesta."""


def _ficha(m: dict) -> str:
    return (
        f"Titulo: {m['titulo']}\n"
        f"Precio publicado: {int(m['precio'] or 0)} CLP\n"
        f"Condicion: {m['condicion']}\n"
        f"Specs: {m['specs'] or {}}\n"
        f"Stock: 1 unidad\n"
        f"Descripcion: {(m['descripcion'] or '')[:800]}"
    )


def _borrador(pub_id: int, entrada_id: int, tipo: str, texto: str) -> int:
    """Guarda la respuesta redactada como mensaje de salida, esperando tu boton."""
    fila = q1(
        """INSERT INTO mensaje (publicacion, direccion, tipo, texto, canal,
                                responde_a, respondido_por, estado)
           VALUES (%s,'sale',%s,%s,'ml',%s,'bot','nuevo') RETURNING id""",
        (pub_id, tipo, texto, entrada_id),
    )
    return int(fila["id"])


def _drenar_cola() -> int:
    """Manda lo que aprobaste apretando [Enviar] en Telegram.

    Antes esto no existia: el boton empujaba el id a Redis y nadie lo sacaba
    nunca, asi que la respuesta se quedaba ahi para siempre y el comprador no
    recibia nada. Con negociar_auto en false — el default — era el 100% de los
    casos.
    """
    enviados = 0
    while True:
        crudo = R.lpop(COLA)
        if crudo is None:
            return enviados
        salida = q1(
            """SELECT s.id, s.texto, e.id AS entrada, e.id_externo
               FROM mensaje s LEFT JOIN mensaje e ON e.id = s.responde_a
               WHERE s.id = %s AND s.direccion = 'sale'""", (int(crudo),))
        if not salida or not salida["id_externo"]:
            continue
        try:
            ml_api.responder_pregunta(salida["id_externo"], salida["texto"])
        except Exception as e:
            tg.PUBLICADOR.enviar(f"⚠️ ML rechazo la respuesta: {str(e)[:200]}\n\n{salida['texto'][:400]}")
            continue
        ex("UPDATE mensaje SET estado='respondido' WHERE id IN (%s, %s)",
           (salida["id"], salida["entrada"]))
        enviados += 1


def correr() -> str:
    de_cola = _drenar_cola()
    pend = q(
        """SELECT m.id, m.id_externo, m.texto, p.id AS pub, p.titulo, p.precio,
                  p.descripcion, inv.condicion, pc.specs
           FROM mensaje m
           JOIN publicacion p ON p.id = m.publicacion
           JOIN inventario inv ON inv.id = p.inventario
           LEFT JOIN producto_canon pc ON pc.id = inv.producto
           WHERE m.direccion='entra' AND m.tipo='pregunta' AND m.estado='nuevo'
           LIMIT 20"""
    )
    if not pend:
        return f"sin preguntas ({de_cola} enviadas de la cola)"
    if not llm.vivo():
        return f"ollama caido ({de_cola} enviadas de la cola)"

    auto = bool(p("modo.negociar_auto", False))
    resp = esc = 0
    for m in pend:
        r = llm.texto(PROMPT.format(ficha=_ficha(dict(m)), pregunta=m["texto"]),
                      modelo=llm.modelo_de("responder")).strip()
        if not r or "NO_SE" in r.upper() or len(r) > 400:
            tg.PUBLICADOR.enviar(f"❓ pregunta sin respuesta automatica\n«{m['titulo'][:70]}»\n\n{m['texto']}",
                      tg.teclado([[("✏️ Responder yo", f"msg_mio:{m['id']}")]]))
            ex("UPDATE mensaje SET estado='escalado' WHERE id=%s", (m["id"],))
            esc += 1
            continue

        if auto and m["id_externo"]:
            try:
                ml_api.responder_pregunta(m["id_externo"], r)
                ex("UPDATE mensaje SET estado='respondido', respondido_por='bot' WHERE id=%s", (m["id"],))
                resp += 1
                continue
            except Exception:
                pass
        # El boton manda ESTE borrador, no el mensaje del comprador.
        salida = _borrador(m["pub"], m["id"], "pregunta", r)
        tg.PUBLICADOR.enviar(f"❓ «{m['titulo'][:60]}»\n{m['texto']}\n\n<b>respuesta sugerida:</b>\n{r}",
                  tg.teclado([[("📤 Enviar", f"msg_enviar:{salida}"),
                               ("✏️ Yo respondo", f"msg_mio:{m['id']}")]]))
        ex("UPDATE mensaje SET estado='escalado' WHERE id=%s", (m["id"],))
        esc += 1
    return f"{resp} respondidas por bot, {esc} escaladas, {de_cola} enviadas de la cola"


if __name__ == "__main__":
    print(envuelto("reply_bot", correr))
