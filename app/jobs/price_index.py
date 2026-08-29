"""Script 14 — el ancla. Cuanto vale de verdad cada producto.

Toma todos los precios vistos en ML en los ultimos 90 dias y saca P25/P50/P80.
Todo lo demas (V_liq, techo, objetivo, piso, precio de lista) cuelga de este P50.

Desde el 28-ago calcula DOS niveles por producto:

  tramo '*'      la mediana del modelo entero, mas los coeficientes medidos de
                 cuanto valen la RAM y el disco en ESE modelo
  tramo 'rN-dM'  la mediana del estante exacto (misma RAM, mismo disco), si
                 junta al menos `specs.min_muestras_tramo` observaciones

score.py usa el estante exacto cuando existe, y si no existe usa el del modelo
corregido por el factor. Antes solo existia el primero, y por eso un T480 de
8GB heredaba el precio de uno de 32GB.

Nota honesta que sigue vigente: ML ya no deja consultar publicaciones cerradas
a terceros, asi que "lo que se vendio de verdad" no se puede leer directo. Se
aproxima de dos formas, y las dos estan implementadas:
  1. precio de publicaciones activas, recortando las puntas locas
  2. peso extra a las que tienen ventas registradas (sold_quantity > 0),
     que es la prueba mas cercana de que a ese precio si se vende
"""
from __future__ import annotations

from app import specs as sp
from app.config import p
from app.db import ex, q
from app.jobs import envuelto
from app.pricing import percentiles

DIAS = 90
MIN_MUESTRAS = 3


def _pesados(filas: list[dict]) -> list[float]:
    """Una publicacion con ventas cuenta doble, hasta 3 veces."""
    precios: list[float] = []
    for f in filas:
        peso = 1 + min(2, int(f["vendidos"] or 0))
        precios.extend([float(f["precio"])] * peso)
    return precios


def _guardar(pid: int, tramo: str, filas: list[dict], medidos: dict | None) -> bool:
    res = percentiles(_pesados(filas))
    if not res:
        return False
    p25, p50, p80, _ = res
    m = medidos or {}
    ex(
        """INSERT INTO indice_precio (producto, tramo, p25, p50, p80, n_muestras,
                                      coef_spec, spec_ref, calculado)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s, now())
           ON CONFLICT (producto, tramo) DO UPDATE SET
             p25=EXCLUDED.p25, p50=EXCLUDED.p50, p80=EXCLUDED.p80,
             n_muestras=EXCLUDED.n_muestras, coef_spec=EXCLUDED.coef_spec,
             spec_ref=EXCLUDED.spec_ref, calculado=now()""",
        (pid, tramo, p25, p50, p80, len(filas), m.get("coef_spec"), m.get("spec_ref")),
    )
    return True


def correr() -> str:
    min_tramo = int(p("specs.min_muestras_tramo", 3))
    productos = q("SELECT id FROM producto_canon")
    hechos = flacos = estantes = 0

    for pr in productos:
        # make_interval en vez de meter el parametro dentro de las comillas de
        # interval '...': ahi el valor viaja como texto y basta que deje de ser
        # un entero para romper la consulta.
        filas = [dict(x) for x in q(
            """SELECT precio, vendidos, ram_gb, disco_gb, tramo FROM precio_obs
               WHERE producto = %s AND fecha > now() - make_interval(days => %s)
                 AND estado IN ('usado','reacondicionado','desconocido')""",
            (pr["id"], DIAS))]
        if len(filas) < MIN_MUESTRAS:
            # Sin usados suficientes: se cae a todo estado, marcando que es flojo.
            filas = [dict(x) for x in q(
                """SELECT precio, vendidos, ram_gb, disco_gb, tramo FROM precio_obs
                   WHERE producto = %s AND fecha > now() - make_interval(days => %s)""",
                (pr["id"], DIAS))]
        if len(filas) < MIN_MUESTRAS:
            flacos += 1
            continue

        # --- nivel 2: el modelo entero, con sus coeficientes medidos ---
        if not _guardar(pr["id"], sp.TODO, filas, sp.medir(filas)):
            flacos += 1
            continue
        hechos += 1

        # --- nivel 1: un estante por tramo de specs, si junta muestras ---
        por_tramo: dict[str, list[dict]] = {}
        for f in filas:
            t = f["tramo"] or sp.TODO
            if t != sp.TODO:
                por_tramo.setdefault(t, []).append(f)
        for t, sub in por_tramo.items():
            if len(sub) >= min_tramo and _guardar(pr["id"], t, sub, None):
                estantes += 1

        # Higiene: un estante que dejo de tener muestras suficientes se borra,
        # si no score.py seguiria usando una mediana vieja de hace meses.
        vivos = [t for t, sub in por_tramo.items() if len(sub) >= min_tramo]
        ex("""DELETE FROM indice_precio
              WHERE producto = %s AND tramo <> %s AND NOT (tramo = ANY(%s))""",
           (pr["id"], sp.TODO, vivos))

    return (f"{hechos} modelos al dia, {estantes} estantes por specs, "
            f"{flacos} sin datos suficientes")


if __name__ == "__main__":
    print(envuelto("price_index", correr))
