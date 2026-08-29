"""Script 41 — el resumen del domingo 20:00.

Un solo mensaje con lo que importa: que se vendio, cuanto ganaste de verdad,
que lleva mucho parado y cuales fueron las mejores cazas de la semana.
"""
from __future__ import annotations

from app import tg
from app.db import q
from app.jobs import envuelto


def _plata(n) -> str:
    return f"${int(n or 0):,}".replace(",", ".")


def correr() -> str:
    if not tg.CAZADOR.chat_id():
        return "telegram sin emparejar"

    vendidos = q(
        """SELECT p.titulo, p.precio, inv.costo FROM publicacion p
           JOIN inventario inv ON inv.id = p.inventario
           WHERE p.estado='vendida' AND p.fecha > now() - interval '7 days'"""
    )
    ingreso = sum(float(v["precio"] or 0) for v in vendidos)
    costo = sum(float(v["costo"] or 0) for v in vendidos)
    sin_costo = sum(1 for v in vendidos if v["costo"] is None)

    # Antes esto casteaba un interval a date, que Postgres rechaza y tiraba
    # abajo el reporte entero del domingo.
    estancados = q(
        """SELECT inv.codigo, p.titulo, p.precio,
                  EXTRACT(day FROM now() - p.fecha)::int AS dias
           FROM publicacion p JOIN inventario inv ON inv.id = p.inventario
           WHERE p.estado='activa' AND p.fecha < now() - interval '45 days'
           ORDER BY p.fecha LIMIT 8"""
    )
    top = q(
        """SELECT i.titulo, i.precio, o.score FROM oportunidad o
           JOIN item_raw i ON i.id = o.item_raw
           WHERE o.creada > now() - interval '7 days'
           ORDER BY o.score DESC LIMIT 5"""
    )
    jobs = q(
        """SELECT job, count(*) FILTER (WHERE ok) AS ok, count(*) FILTER (WHERE NOT ok) AS mal
           FROM job_log WHERE ts > now() - interval '7 days' GROUP BY job ORDER BY mal DESC"""
    )

    l = ["<b>Semana</b>", ""]
    l.append(f"Vendido: {len(vendidos)} items · {_plata(ingreso)}")
    if costo:
        l.append(f"Margen real: {_plata(ingreso - costo)}" + (f"  ({sin_costo} sin costo cargado)" if sin_costo else ""))
    elif vendidos:
        l.append(f"Margen: falta el costo de {sin_costo} items")

    if top:
        l += ["", "<b>Mejores cazas</b>"]
        l += [f"· {t['titulo'][:55]} — {_plata(t['precio'])} (score {float(t['score']):.2f})" for t in top]

    if estancados:
        l += ["", "<b>Parados +45 dias</b>"]
        l += [f"· {e['codigo']} {e['titulo'][:45]} {_plata(e['precio'])}" for e in estancados]

    fallando = [j for j in jobs if j["mal"]]
    if fallando:
        l += ["", "<b>Scripts con errores</b>"]
        l += [f"· {j['job']}: {j['mal']} fallos / {j['ok']} ok" for j in fallando]

    tg.CAZADOR.enviar("\n".join(l))
    return f"reporte enviado ({len(vendidos)} ventas)"


if __name__ == "__main__":
    print(envuelto("report", correr))
