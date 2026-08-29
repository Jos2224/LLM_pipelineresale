"""Script 16 — te avisa por Telegram con los numeros ya masticados.

La regla es tuya: te llega SOLO lo que ya se revende por 1,5x o mas al precio
publicado, sin negociar siquiera. Si el bot despues negocia y lo baja al
objetivo, el multiplo sube a 2x o mas.

Botones: Negociar (el bot saluda al vendedor y regatea solo) / Ignorar /
Seguir (lo vigila y avisa si baja).
"""
from __future__ import annotations

import redis

from app import tg
from app.config import REDIS_URL, p
from app.db import ex, q
from app.jobs import envuelto

MAX_POR_CICLO = 8
R = redis.from_url(REDIS_URL, decode_responses=True)


def _plata(n) -> str:
    return f"${int(n or 0):,}".replace(",", ".")


def _texto(o: dict) -> str:
    # Postgres devuelve numeric como Decimal, y Decimal - float explota con
    # TypeError. Todo se pasa a float una sola vez, aca arriba.
    precio = float(o["precio"])
    v_liq = float(o["v_liq"] or 0)
    techo = float(o["p_max"] or 0)
    objetivo = float(o["objetivo"] or 0)
    mult = float(o["multiplo"] or 0)
    costos = float(p("compra.costos_fijos", 0))
    # Las etiquetas salen de policy.yml. Estaban escritas a mano como "1,5x" y
    # "2x", asi que al cambiar los multiplos la alerta mentia.
    m_techo = float(p("compra.multiplo_techo", 2.0))
    m_obj = float(p("compra.multiplo_objetivo", 2.5))
    crudo = o.get("crudo") or {}

    lineas = [
        f"<b>{o['titulo'][:110]}</b>",
        "",
        f"Piden         <b>{_plata(precio)}</b>",
        f"Se revende a  {_plata(v_liq)}   →  <b>{mult:.1f}x</b>",
        "",
        f"Techo ({m_techo:g}x)    {_plata(techo)}   ← no pagar mas que esto",
        f"Objetivo ({m_obj:g}x) {_plata(objetivo)}   ← donde intenta cerrar el bot",
        "",
        f"Ganas hoy     {_plata(v_liq - precio)} bruto · "
        f"{_plata(v_liq - precio - costos)} despues de costos",
    ]
    if objetivo and precio > objetivo:
        lineas.append(f"Si baja {_plata(precio - objetivo)} llegas al {m_obj:g}x")
    # De donde salio el precio de mercado. Un P50 del estante exacto es un dato
    # medido; uno "ajustado" es una estimacion y tu decision puede cambiar.
    origen = crudo.get("p50_origen")
    if origen:
        marca = "📊" if origen.startswith("estante") else "≈"
        lineas += ["", f"{marca} mercado: {_plata(crudo.get('p50_usado'))} · {origen}"]
    if not o["g_conocido"]:
        lineas += ["", "⚠️ <b>G=?</b> — no se cuanta gente compite. Numero estimado, no confirmado."]
    if o.get("url"):
        lineas += ["", f'<a href="{o["url"]}">abrir publicacion</a>']
    return "\n".join(lineas)


def correr() -> str:
    if not tg.CAZADOR.chat_id():
        return "telegram sin emparejar todavia (manda /start al bot)"
    if R.get("cazador:alertas_pausadas"):
        return "alertas en pausa (/pausa)"

    pend = q(
        """SELECT o.id, o.v_liq, o.p_max, o.objetivo, o.multiplo, o.g_conocido,
                  i.titulo, i.precio, i.url, i.fotos, i.crudo
           FROM oportunidad o JOIN item_raw i ON i.id = o.item_raw
           WHERE o.estado = 'nueva'
           ORDER BY o.multiplo DESC NULLS LAST LIMIT %s""",
        (MAX_POR_CICLO,),
    )
    for o in pend:
        botones = tg.teclado([
            [("🤝 Negociar", f"op_negociar:{o['id']}")],
            [("✖ Ignorar", f"op_ignorar:{o['id']}"), ("👁 Seguir", f"op_watch:{o['id']}")],
        ])
        fotos = o["fotos"] or []
        if fotos:
            tg.CAZADOR.foto(fotos[0], _texto(dict(o)), botones)
        else:
            tg.CAZADOR.enviar(_texto(dict(o)), botones)
        ex("UPDATE oportunidad SET estado = 'avisada' WHERE id = %s", (o["id"],))
    return f"{len(pend)} alertas enviadas"


if __name__ == "__main__":
    print(envuelto("alert", correr))
