"""Tramos de specs, y cuanto vale cada spec en plata.

El problema que resuelve: un ThinkPad T480 de 8GB/256GB y uno de 32GB/1TB no
son el mismo producto, pero tienen la misma marca y el mismo modelo. Si los
metes en el mismo estante, la mediana queda en el medio y el multiplo sale mal
en las DOS direcciones — compras cosas que no rinden 2x y te pierdes las que si.

Dos niveles, en este orden:

  1. ESTANTE PROPIO. Si ese tramo exacto (mismo modelo, misma RAM, mismo
     disco) tiene al menos 3 observaciones, se usa su mediana. Es un dato, no
     una estimacion.

  2. AJUSTE. Si no las tiene, se usa la mediana del modelo entero corregida
     por cuanto pesa el equipamiento de ESE equipo contra el tipico del modelo.

Como se mide el nivel 2 — y por que asi:

    puntaje = log2(RAM) + peso_disco * log2(disco)

Un solo numero, y una sola pendiente ajustada contra el. La primera version
media RAM y disco por separado y estaba MAL: en la vida real van juntos (el
equipo caro trae mas de las dos cosas), asi que cada coeficiente se llevaba el
credito del otro y el ajuste se disparaba al triple cuando la verdad era 1,7x.
Con un puntaje unico eso no puede pasar, porque hay una sola pendiente que
repartir. Lo agarro `bin/smoke_specs.py`.

El precio de esta decision, dicho claro: el peso relativo entre RAM y disco se
supone (config/policy.yml), no se mide. Medirlo pedria muchos mas datos de los
que un producto usado junta en 90 dias.

Nada de esto pasa por el LLM. Son medianas y una recta.
"""
from __future__ import annotations

import math
import statistics

from app.config import p
from app.db import q1

# Escalones. Se elige el mas alto que no pase el valor real: 12 GB cae en 8,
# 24 GB cae en 16. Son gruesos a proposito — con escalones finos cada estante
# se queda sin muestras y el nivel 1 no se activa nunca.
ESCALA_RAM = [4, 8, 16, 32, 64, 128]
ESCALA_DISCO = [128, 256, 512, 1024, 2048, 4096]

TODO = "*"      # el estante del modelo entero


def _escalon(valor, escala: list[int]) -> int | None:
    try:
        v = int(valor)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    elegido = escala[0]
    for e in escala:
        if v >= e:
            elegido = e
    return elegido


def tramo(specs: dict | None) -> str:
    """Nombre del estante: 'r16-d512'. Sin specs utiles devuelve '*'.

    Se exige RAM Y disco: con solo uno de los dos el estante mezclaria mundos
    distintos igual que antes, y ademas seria un estante que casi nunca junta
    muestras.
    """
    s = specs or {}
    r = _escalon(s.get("ram_gb"), ESCALA_RAM)
    d = _escalon(s.get("disco_gb"), ESCALA_DISCO)
    if r is None or d is None:
        return TODO
    return f"r{r}-d{d}"


# -------------------------------------------------------------- puntaje
def puntaje(specs: dict | None) -> float | None:
    """Cuanto equipo trae, en un solo numero. None si no se puede saber.

    Cada duplicacion de RAM suma 1 punto; cada duplicacion de disco suma
    `peso_disco` (por defecto 0,5: el disco pesa la mitad que la RAM en el
    precio de un notebook usado).
    """
    s = specs or {}
    r = _escalon(s.get("ram_gb"), ESCALA_RAM)
    d = _escalon(s.get("disco_gb"), ESCALA_DISCO)
    if r is None and d is None:
        return None
    peso = float(p("specs.peso_disco", 0.5))
    total = 0.0
    if r is not None:
        total += math.log2(r)
    if d is not None:
        total += peso * math.log2(d)
    return total


def _limpio(x, minimo: float, maximo: float, defecto: float) -> float:
    """Recorta un coeficiente a un rango sensato.

    Sin esto, tres publicaciones raras pueden dar una pendiente de 3.0 y
    entonces un notebook con el doble de RAM valdria 8 veces mas. Un
    coeficiente fuera de rango no es una senal, es ruido.
    """
    try:
        v = float(x)
    except (TypeError, ValueError):
        return defecto
    if not math.isfinite(v):
        return defecto
    return max(minimo, min(maximo, v))


def coeficiente(medido=None) -> float:
    """Pendiente en base 2: cuanto sube el precio por punto de puntaje.

    0.30 significa que un equipo con 1 punto mas (p.ej. el doble de RAM) vale
    2^0.30 = +23%.
    """
    return _limpio(medido, 0.0, float(p("specs.coef_maximo", 0.6)),
                   float(p("specs.coef_spec", 0.30)))


def factor(specs: dict | None, ref, medido=None) -> float:
    """Cuanto corregir el P50 del modelo para ESTE equipo en concreto.

    factor = 2 ^ (coef * (puntaje_del_equipo - puntaje_de_referencia))

    `ref` es el puntaje del equipo tipico de ese modelo, calculado en medir().
    Si falta el puntaje o la referencia, devuelve 1: no se inventa nada.
    """
    pj = puntaje(specs)
    if pj is None or ref is None:
        return 1.0
    try:
        r = float(ref)
    except (TypeError, ValueError):
        return 1.0
    f = 2.0 ** (coeficiente(medido) * (pj - r))
    # Tope duro: por mucho equipamiento que tenga, un equipo no vale el doble
    # del modelo promedio. Si el factor se dispara es que los datos estan malos.
    return max(float(p("specs.factor_minimo", 0.45)),
               min(float(p("specs.factor_maximo", 2.2)), f))


def precio_mercado(pid, specs: dict | None, mercado: str | None = None) -> tuple[float | None, str]:
    """El P50 que le corresponde a ESTE equipo, y de donde salio.

    UNICO lugar donde se consulta indice_precio para valorar algo concreto. Va
    aca y no en cada job porque desde que la tabla tiene una fila por tramo,
    un `WHERE producto = %s` a secas devuelve un tramo cualquiera, y un
    `LEFT JOIN indice_precio ON producto` multiplica las filas del job.

    El texto de vuelta dice por que CAMINO salio el numero, no cuanto se movio:
    un ajuste que da x1.00 sigue siendo una estimacion, y saberlo puede cambiar
    tu decision de comprar.
    """
    if not pid:
        return None, "sin producto"

    # Orden de preferencia entre mercados, y el POR QUE:
    # tu COMPRAS en Facebook y VENDES en MercadoLibre, asi que "en cuanto lo
    # revendo" se responde con el precio de ML. El de FB es el respaldo para
    # cuando ML todavia no tiene datos de ese producto — y por ser mas bajo es
    # conservador: te hace perder oportunidades, nunca inventarlas.
    mercados = [mercado] if mercado else ["ml", "fb"]
    minimo = int(p("specs.min_muestras_tramo", 3))
    t = tramo(specs)

    for m in mercados:
        etiqueta = "" if m == "ml" else " · precio de FB"

        # 1) estante exacto de ese equipo, en ese mercado
        if t != TODO:
            fila = q1("""SELECT p50, n_muestras FROM indice_precio
                         WHERE producto = %s AND tramo = %s AND mercado = %s""",
                      (pid, t, m))
            if fila and fila["p50"] and (fila["n_muestras"] or 0) >= minimo:
                return float(fila["p50"]), f"estante {t} ({fila['n_muestras']} datos){etiqueta}"

        # 2) modelo entero, corregido por specs
        base = q1("""SELECT p50, n_muestras, coef_spec, spec_ref FROM indice_precio
                     WHERE producto = %s AND tramo = %s AND mercado = %s""", (pid, TODO, m))
        if not base or not base["p50"]:
            continue
        if puntaje(specs) is None:
            return float(base["p50"]), f"modelo, specs desconocidas ({base['n_muestras']} datos){etiqueta}"
        fa = factor(specs, base["spec_ref"], base["coef_spec"])
        return float(base["p50"]) * fa, f"modelo ajustado x{fa:.2f} ({base['n_muestras']} datos){etiqueta}"

    return None, "sin indice en ML ni en FB"


def medir(observaciones: list[dict]) -> dict:
    """Mide, del propio producto, cuanto sube el precio por punto de puntaje.

    Metodo: se agrupan las observaciones por puntaje; de cada grupo se toma la
    MEDIANA (no el promedio: una publicacion loca no debe mover la recta) y se
    ajusta una recta de log(precio) contra puntaje. La pendiente es el
    coeficiente.

    La referencia es el puntaje PROMEDIO de las observaciones, no el mas
    frecuente. Asi el factor vale 1 justo en el equipo tipico del modelo, que
    es el que la mediana del modelo esta describiendo. (Usar el mas frecuente
    fue el primer intento y con dos grupos empatados elegia uno al azar: el
    equipo del medio terminaba valorado por debajo del mas barato.)

    Devuelve {} si no hay con que medir; el llamador cae a policy.yml.
    """
    if len(observaciones) < int(p("specs.min_para_medir", 6)):
        return {}

    puntos: list[tuple[float, float]] = []
    for o in observaciones:
        pj = puntaje({"ram_gb": o.get("ram_gb"), "disco_gb": o.get("disco_gb")})
        precio = o.get("precio")
        if pj is not None and precio and float(precio) > 0:
            puntos.append((pj, float(precio)))
    if len(puntos) < 2:
        return {}

    salida: dict = {"spec_ref": sum(x for x, _ in puntos) / len(puntos)}

    grupos: dict[float, list[float]] = {}
    for pj, precio in puntos:
        grupos.setdefault(round(pj, 3), []).append(precio)
    # Dos puntajes distintos, y cada uno con al menos 2 datos: con un solo dato
    # por puntaje la "pendiente" es la diferencia entre dos anuncios sueltos.
    utiles = {k: statistics.median(v) for k, v in grupos.items() if len(v) >= 2}
    if len(utiles) < 2:
        return salida

    xs = list(utiles)
    ys = [math.log(v) for v in utiles.values()]
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den <= 0:
        return salida
    pendiente = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den
    # La recta esta en base e; se pasa a base 2 para que 2^coef sea la subida.
    salida["coef_spec"] = pendiente / math.log(2)
    return salida
