"""Script 40 — memoria del mercado.

Guarda una foto diaria del P50 por categoria y de cuantos dias tarda en
venderse. Con eso, en dos meses, sabes que categoria se esta enfriando ANTES
de comprar mas de lo mismo. Sin este historico el indice solo dice "hoy".
"""
from __future__ import annotations

from app.db import ex, q
from app.jobs import envuelto


def correr() -> str:
    filas = q(
        """SELECT COALESCE(pc.categoria,'otro') AS categoria,
                  percentile_cont(0.5) WITHIN GROUP (ORDER BY ip.p50) AS p50,
                  count(*) AS n
           FROM indice_precio ip JOIN producto_canon pc ON pc.id = ip.producto
           WHERE ip.tramo = '*'
           GROUP BY 1"""
        # tramo '*' = una fila por producto. Sin este filtro cada modelo
        # contaria una vez por cada estante de specs que tenga, y los modelos
        # con mas variantes pesarian mas en la tendencia solo por eso.
    )
    dias = {
        r["categoria"]: r["dias"]
        for r in q(
            """SELECT COALESCE(pc.categoria,'otro') AS categoria,
                      avg(EXTRACT(epoch FROM (p.fecha - inv.creado)) / 86400) AS dias
               FROM publicacion p
               JOIN inventario inv ON inv.id = p.inventario
               LEFT JOIN producto_canon pc ON pc.id = inv.producto
               WHERE p.estado='vendida' AND p.fecha > now() - interval '180 days'
               GROUP BY 1"""
        )
    }
    for f in filas:
        ex(
            """INSERT INTO tendencia (categoria, fecha, p50, dias_venta, n)
               VALUES (%s, current_date, %s, %s, %s)
               ON CONFLICT (categoria, fecha) DO UPDATE SET
                 p50=EXCLUDED.p50, dias_venta=EXCLUDED.dias_venta, n=EXCLUDED.n""",
            (f["categoria"], f["p50"], dias.get(f["categoria"]), f["n"]),
        )
    return f"{len(filas)} categorias registradas"


if __name__ == "__main__":
    print(envuelto("trends", correr))
