"""Las formulas. Aca se decide toda la plata.

Ninguna funcion de este archivo llama al LLM. A proposito: un modelo de 4B
alucina numeros y aca un numero malo es plata perdida. El LLM redacta; este
archivo decide.
"""
from __future__ import annotations

import math
import statistics

from app.config import p


# ------------------------------------------------------------ indice ML
def percentiles(precios: list[float]) -> tuple[float, float, float, int] | None:
    """P25 / P50 / P80 de una lista de precios observados."""
    limpios = sorted(x for x in precios if x and x > 0)
    if len(limpios) < 3:
        return None
    # Recorte del 5% en cada punta: en ML siempre hay un repuesto a $1 y un
    # vendedor loco a $9.999.999 que ensucian la mediana.
    corte = max(1, int(len(limpios) * 0.05))
    if len(limpios) > 8:
        limpios = limpios[corte:-corte] or limpios
    n = len(limpios)

    def pct(f: float) -> float:
        i = f * (n - 1)
        lo, hi = math.floor(i), math.ceil(i)
        return limpios[lo] + (limpios[hi] - limpios[lo]) * (i - lo)

    return round(pct(0.25)), round(statistics.median(limpios)), round(pct(0.80)), n


# ------------------------------------------------------------ compra
def v_liquido(p50: float) -> float:
    """Lo que TU sacas si tienes que vender rapido, no el precio de vitrina."""
    return p50 * float(p("liquidacion.factor_liq", 0.65))


def techo(v_liq: float) -> float:
    """Lo maximo que pagas. Sobre esto no compras nunca.

    V_liq / 1.5 -> al revender ganas al menos 1,5x lo que pusiste.

    Es un tope duro: nada lo sube, ni en medio de un remate caliente. Ese es el
    unico control que impide comprar caro por adrenalina.
    """
    return max(0.0, v_liq / float(p("compra.multiplo_techo", 1.5)))


def objetivo(v_liq: float) -> float:
    """Donde el bot trata de cerrar. V_liq / 2 -> el articulo vale el doble."""
    return max(0.0, v_liq / float(p("compra.multiplo_objetivo", 2.0)))


def multiplo(precio: float, v_liq: float) -> float:
    """Por cuanto se revende, en veces. 2.0 = lo vendes al doble."""
    if not precio or precio <= 0:
        return 0.0
    return v_liq / precio


def evaluar(precio: float, p50: float) -> dict:
    v = v_liquido(p50)
    t = techo(v)
    o = objetivo(v)
    m = multiplo(precio, v)
    costos = float(p("compra.costos_fijos", 0))
    return {
        "v_liq": round(v),
        "p_max": round(t),          # techo (la columna se llama p_max en la DB)
        "objetivo": round(o),
        "multiplo": round(m, 3),
        "margen_bruto": round(v - precio) if precio else 0,
        "margen_neto": round(v - precio - costos) if precio else 0,
        "oportunidad": bool(precio) and precio <= t,
    }


def siguiente_oferta(ronda: int, obj: float, tope: float) -> float:
    """Escalera de la negociacion de compra.

      ronda 1 -> el objetivo (V_liq / 2)
      ronda 2 -> a mitad de camino entre objetivo y techo
      ronda 3 -> el techo, y ni un peso mas

    Nunca empieza por el techo: si abres con tu maximo, no te queda nada que
    ceder y el vendedor igual va a pedir mas.
    """
    paso = float(p("compra_negociacion.paso_hacia_techo", 0.5))
    if ronda <= 1:
        valor = obj
    elif ronda == 2:
        valor = obj + (tope - obj) * paso
    else:
        valor = tope
    return redondear_abajo(min(valor, tope))


def redondear_abajo(monto: float) -> float:
    """Nadie ofrece $134.875. Se ofrece $130.000.

    Siempre hacia ABAJO, nunca hacia arriba: redondear para arriba podria
    cruzar el techo, y ademas una cifra redonda suena a oferta pensada y no a
    numero salido de una planilla.
    """
    if monto < 50_000:
        paso = 1_000
    elif monto < 500_000:
        paso = 5_000
    else:
        paso = 10_000
    return float(int(monto // paso) * paso)


# ------------------------------------------------------------ remates
def banda_remate(p0: float, g: int | None) -> dict:
    """Modelo propio: B = P0 * sqrt(G).

    G = cuantos compiten. Mas gente, mas sube. Si no se sabe G se usa el
    supuesto SOLO para ordenar la lista, y la alerta sale marcada G=?.
    """
    conocido = g is not None and g > 0
    g_uso = g if conocido else int(p("remate.g_supuesto", 12))
    b = p0 * math.sqrt(max(1, g_uso))
    return {
        "b": round(b),
        "p25": round(b * float(p("remate.p25", 0.907))),
        "p50": round(b * float(p("remate.p50", 1.073))),
        "p80": round(b * float(p("remate.p80", 1.646))),
        "g_conocido": conocido,
        "g_usado": g_uso,
    }


# ------------------------------------------------------------ venta
def precio_lista(p50: float, piso: float | None = None) -> float:
    base = p50 * float(p("venta.factor_precio", 0.65))
    return round(max(base, piso or 0))


def piso_default(p50: float) -> float:
    return round(p50 * float(p("venta.factor_precio", 0.65)) * float(p("venta.factor_piso", 0.85)))


def rebaja(precio_actual: float, piso: float) -> float:
    """Cada N dias sin venta baja un %, pero nunca cruza el piso."""
    nuevo = precio_actual * (1 - float(p("venta.baja_pct", 0.05)))
    return round(max(nuevo, piso))


# ------------------------------------------------------------ negociacion
def decidir_oferta(monto: float, piso: float) -> tuple[str, float | None]:
    """Reglas duras. Devuelve (accion, contraoferta).

    accion: aceptar | contraoferta | rechazar
    """
    if piso <= 0:
        return "escalar", None
    r = monto / piso
    if r >= float(p("negociacion.acepta_desde", 1.0)):
        return "aceptar", None
    if r >= float(p("negociacion.contraoferta_desde", 0.90)):
        return "contraoferta", round(piso)
    return "rechazar", None
