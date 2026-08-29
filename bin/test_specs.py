#!/usr/bin/env python3
"""Casos del indice por specs. Es el numero del que cuelga todo el negocio.

Tres cosas:
  1. los tramos    -> que 12 GB y 16 GB caigan donde corresponde
  2. el factor     -> que corregir por specs suba y baje lo razonable, y que
                      nunca se dispare aunque los datos vengan locos
  3. la medicion   -> que los coeficientes salgan de los datos cuando hay, y
                      que NO salgan cuando no alcanzan

Corre sin base, sin navegador y sin Ollama: son medianas y una recta.

    ~/cazador/bin/cazador test
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from app import specs as sp   # noqa: E402

fallos = 0


def check(nombre: str, cond: bool, extra: str = "") -> None:
    global fallos
    if cond:
        print(f"✓ {nombre}")
    else:
        fallos += 1
        print(f"✖ {nombre}   {extra}")


# ------------------------------------------------------------- 1. tramos
def probar_tramos() -> None:
    casos = [
        ({"ram_gb": 8, "disco_gb": 256}, "r8-d256", "el tipico"),
        ({"ram_gb": 16, "disco_gb": 512}, "r16-d512", "el bueno"),
        ({"ram_gb": 12, "disco_gb": 500}, "r8-d256", "12 GB baja a 8, 500 baja a 256"),
        ({"ram_gb": 32, "disco_gb": 1024}, "r32-d1024", "el gordo"),
        ({"ram_gb": 64, "disco_gb": 2048}, "r64-d2048", "workstation"),
        # Sin una de las dos NO hay estante: mezclaria mundos distintos igual
        # que antes, y ademas nunca juntaria muestras.
        ({"ram_gb": 16}, "*", "solo RAM no basta"),
        ({"disco_gb": 512}, "*", "solo disco no basta"),
        ({}, "*", "sin specs"),
        (None, "*", "specs nulas"),
        ({"ram_gb": 0, "disco_gb": 0}, "*", "ceros no cuentan"),
        ({"ram_gb": "no", "disco_gb": "se"}, "*", "basura no revienta"),
    ]
    for specs, esperado, por_que in casos:
        salio = sp.tramo(specs)
        check(f"tramo · {por_que}: {salio}", salio == esperado, f"esperado {esperado}")


# ------------------------------------------------------------- 2. factor
def probar_factor() -> None:
    # El equipo tipico del modelo es 8 GB / 256 GB -> puntaje 3 + 0,5*8 = 7.
    tipico = {"ram_gb": 8, "disco_gb": 256}
    ref = sp.puntaje(tipico)
    check(f"puntaje · 8GB/256 = {ref}", abs(ref - 7.0) < 0.001)
    check(f"puntaje · 32GB/1TB = {sp.puntaje({'ram_gb': 32, 'disco_gb': 1024})}",
          abs(sp.puntaje({"ram_gb": 32, "disco_gb": 1024}) - 10.0) < 0.001)

    f_igual = sp.factor(tipico, ref)
    check(f"factor · el equipo tipico no se corrige: x{f_igual:.2f}",
          abs(f_igual - 1.0) < 0.001)

    f_doble = sp.factor({"ram_gb": 16, "disco_gb": 256}, ref)
    check(f"factor · el doble de RAM sube: x{f_doble:.2f}", 1.15 < f_doble < 1.30)

    f_mitad = sp.factor({"ram_gb": 4, "disco_gb": 256}, ref)
    check(f"factor · la mitad de RAM baja: x{f_mitad:.2f}", 0.75 < f_mitad < 0.88)

    f_gordo = sp.factor({"ram_gb": 32, "disco_gb": 1024}, ref)
    check(f"factor · 32GB/1TB contra 8GB/256: x{f_gordo:.2f}", 1.5 < f_gordo < 2.0)

    # Sin ninguna spec no se inventa nada.
    check(f"factor · sin specs no adivina: x{sp.factor({}, ref):.2f}",
          abs(sp.factor({}, ref) - 1.0) < 0.001)
    check("factor · sin referencia no adivina",
          abs(sp.factor(tipico, None) - 1.0) < 0.001)

    # Topes duros: aunque los datos digan cualquier cosa.
    f_bestia = sp.factor({"ram_gb": 512, "disco_gb": 8192}, ref, 0.6)
    check(f"factor · tope arriba aunque los datos deliren: x{f_bestia:.2f}",
          f_bestia <= 2.2001)
    f_pulga = sp.factor({"ram_gb": 1, "disco_gb": 16}, ref, 0.6)
    check(f"factor · tope abajo: x{f_pulga:.2f}", f_pulga >= 0.4499)

    # Coeficiente absurdo -> se recorta, no se usa tal cual.
    check(f"factor · coeficiente de 9.9 recortado: x{sp.factor({'ram_gb': 16, 'disco_gb': 256}, ref, 9.9):.2f}",
          sp.factor({"ram_gb": 16, "disco_gb": 256}, ref, 9.9) <= 2.2001)


# ------------------------------------------------------------ 3. medicion
def probar_medicion() -> None:
    # Datos donde la RAM SI mueve el precio: 8GB ~290k, 16GB ~360k, 32GB ~500k.
    datos = [
        {"precio": 285000, "ram_gb": 8, "disco_gb": 256},
        {"precio": 295000, "ram_gb": 8, "disco_gb": 256},
        {"precio": 355000, "ram_gb": 16, "disco_gb": 512},
        {"precio": 365000, "ram_gb": 16, "disco_gb": 512},
        {"precio": 495000, "ram_gb": 32, "disco_gb": 1024},
        {"precio": 505000, "ram_gb": 32, "disco_gb": 1024},
    ]
    m = sp.medir(datos)
    check(f"medir · saca la pendiente de los datos: {m.get('coef_spec', 0):.3f}",
          m.get("coef_spec") is not None and 0.15 < m["coef_spec"] < 0.5, str(m))
    # La referencia es el PROMEDIO de puntajes, no el mas frecuente: con dos
    # grupos empatados el "mas frecuente" elegia uno al azar y el equipo del
    # medio terminaba valorado por debajo del mas barato.
    check(f"medir · la referencia es el puntaje promedio: {m.get('spec_ref'):.2f}",
          abs(m["spec_ref"] - 8.5) < 0.01, str(m))

    # Con 4 datos no alcanza: mejor los de policy.yml que una recta inventada.
    check("medir · con pocos datos no inventa nada", sp.medir(datos[:4]) == {})

    # Todos con las MISMAS specs: hay referencia, pero no hay pendiente.
    planos = [{"precio": 300000 + i * 1000, "ram_gb": 8, "disco_gb": 256} for i in range(8)]
    m2 = sp.medir(planos)
    check("medir · sin variacion de specs no devuelve pendiente",
          "coef_spec" not in m2 and "spec_ref" in m2, str(m2))

    # Un solo dato por puntaje no es una pendiente, son dos anuncios sueltos.
    sueltos = [{"precio": 200000, "ram_gb": 8, "disco_gb": 256},
               {"precio": 900000, "ram_gb": 16, "disco_gb": 256},
               {"precio": 210000, "ram_gb": 4, "disco_gb": 128},
               {"precio": 205000, "ram_gb": 4, "disco_gb": 128},
               {"precio": 215000, "ram_gb": 4, "disco_gb": 128},
               {"precio": 220000, "ram_gb": 4, "disco_gb": 128}]
    check("medir · ignora puntajes con un solo dato",
          sp.medir(sueltos).get("coef_spec") is None, str(sp.medir(sueltos)))


# ---------------------------------------------------- 4. el caso completo
def probar_caso_real() -> None:
    """El caso que motivo todo esto, con precios reales de ML Chile."""
    from app.pricing import evaluar, percentiles

    FLACO = [279000, 289000, 295000, 299000, 310000, 315000]   # i5 8GB 256GB
    GORDO = [489000, 499000, 510000, 520000, 529000, 549000]   # i7 32GB 1TB

    _, p50_flaco, _, _ = percentiles(FLACO)
    _, p50_todo, _, _ = percentiles(FLACO + GORDO)

    # Antes: un T480 de 8GB se valoraba con la mediana de TODOS los T480.
    antes = evaluar(120000, p50_todo)
    # Ahora: estante propio del 8GB/256.
    ahora = evaluar(120000, p50_flaco)

    check(f"caso real · antes alertaba {antes['multiplo']:.2f}x (falso 2x)",
          antes["oportunidad"] is True)
    check(f"caso real · ahora dice {ahora['multiplo']:.2f}x y NO alerta",
          ahora["oportunidad"] is False)

    # Y la ganga de verdad, que antes se perdia.
    _, p50_gordo, _, _ = percentiles(GORDO)
    antes2 = evaluar(150000, p50_todo)
    ahora2 = evaluar(150000, p50_gordo)
    check(f"caso real · antes se perdia la ganga ({antes2['multiplo']:.2f}x)",
          antes2["oportunidad"] is False)
    check(f"caso real · ahora la agarra ({ahora2['multiplo']:.2f}x)",
          ahora2["oportunidad"] is True)


def main() -> int:
    for titulo, fn in (("tramos de specs", probar_tramos),
                       ("factor de correccion", probar_factor),
                       ("medir coeficientes en los datos", probar_medicion),
                       ("el caso T480 completo", probar_caso_real)):
        print(f"\n--- {titulo}")
        fn()
    print(f"\n{'TODO OK' if not fallos else str(fallos) + ' FALLOS'}")
    return fallos


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
