#!/usr/bin/env python3
"""Casos de `app/negociacion.py` — la puerta a cerrar compras.

Por que existe este archivo: hasta el 29-ago la unica puerta a la tabla
`negociacion` era el boton de Telegram, y rechazaba todo lo que no fuera
MercadoLibre. Como ML cerro su busqueda publica, el 100% de las oportunidades
vienen de Facebook, asi que la tabla estaba vacia y `jobs/negociar_fb.py`
corria cada 20 minutos leyendo una tabla que nadie podia llenar.

Nadie lo noto porque el job decia "0 negociaciones" y eso se parece a "no
habia nada bueno". **Cuando un job lleva dias diciendo 0, hay que preguntarse
si eso que cuenta tiene productor.**

Usa la base de verdad y borra todo lo que crea.

    ~/cazador/bin/cazador test
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from app import negociacion                       # noqa: E402
from app.db import ex, q1                         # noqa: E402

PREFIJO = "TEST_NEGOC_"


def _limpiar() -> None:
    ex("""DELETE FROM negociacion WHERE oportunidad IN (
            SELECT o.id FROM oportunidad o JOIN item_raw i ON i.id=o.item_raw
            WHERE i.hash LIKE %s)""", (PREFIJO + "%",))
    ex("""DELETE FROM oportunidad WHERE item_raw IN (
            SELECT id FROM item_raw WHERE hash LIKE %s)""", (PREFIJO + "%",))
    ex("DELETE FROM indice_precio WHERE producto IN "
       "(SELECT id FROM producto_canon WHERE modelo LIKE %s)", (PREFIJO + "%",))
    ex("DELETE FROM item_raw WHERE hash LIKE %s", (PREFIJO + "%",))
    ex("DELETE FROM producto_canon WHERE modelo LIKE %s", (PREFIJO + "%",))


def _producto(sufijo: str, muestras: int | None) -> int:
    pid = q1("""INSERT INTO producto_canon (marca, modelo, categoria)
                VALUES ('TestMarca', %s, 'notebook') RETURNING id""",
             (PREFIJO + sufijo,))["id"]
    if muestras is not None:
        ex("""INSERT INTO indice_precio (producto, p50, n_muestras, tramo, mercado)
              VALUES (%s, 400000, %s, '*', 'fb')""", (pid, muestras))
    return pid


def _oportunidad(fuente: str, pid: int, url: str | None, id_ext: str | None,
                 techo=134875, objetivo=107900) -> int:
    fid = q1("SELECT id FROM fuente WHERE tipo = %s", (fuente,))["id"]
    iid = q1("""INSERT INTO item_raw (fuente, url, id_externo, titulo, precio, hash)
                VALUES (%s,%s,%s,'Notebook de prueba',70000,%s) RETURNING id""",
             (fid, url, id_ext, PREFIJO + (url or id_ext or "x")))["id"]
    return q1("""INSERT INTO oportunidad (item_raw, producto, v_liq, p_max, objetivo,
                                          multiplo, estado)
                 VALUES (%s,%s,269750,%s,%s,3.85,'nueva') RETURNING id""",
              (iid, pid, techo, objetivo))["id"]


def probar_abrir() -> int:
    fallos = 0
    pid = _producto("prod", 9)

    # 1. EL CASO QUE ESTABA ROTO: una oportunidad de Facebook.
    op = _oportunidad("fb", pid, "https://www.facebook.com/marketplace/item/123", None)
    try:
        negociacion.abrir(op)
        n = q1("SELECT canal, url_item, estado, precio_techo FROM negociacion "
               "WHERE oportunidad=%s", (op,))
        mal = []
        if not n:
            mal.append("no se creo la fila")
        else:
            if n["canal"] != "fb":
                mal.append(f"canal={n['canal']} esperado fb")
            if not n["url_item"]:
                mal.append("sin url_item: negociar_fb no puede abrir el chat")
            if n["estado"] != "por_saludar":
                mal.append(f"estado={n['estado']} esperado por_saludar")
        est = q1("SELECT estado FROM oportunidad WHERE id=%s", (op,))["estado"]
        if est != "negociando":
            mal.append(f"la oportunidad quedo en {est}")
        if mal:
            fallos += 1
            print("✖ abrir · Facebook: " + " · ".join(mal))
        else:
            print("✓ abrir · Facebook crea la fila con canal=fb y url_item")
    except negociacion.NoSePudo as e:
        fallos += 1
        print(f"✖ abrir · Facebook fue rechazada: {e}")

    # 2. MercadoLibre sigue funcionando igual que antes.
    op = _oportunidad("ml", pid, None, "MLC123456789")
    try:
        negociacion.abrir(op)
        n = q1("SELECT canal, item_externo FROM negociacion WHERE oportunidad=%s", (op,))
        if not n or n["canal"] != "ml" or n["item_externo"] != "MLC123456789":
            fallos += 1
            print(f"✖ abrir · ML quedo mal: {dict(n) if n else None}")
        else:
            print("✓ abrir · MercadoLibre sigue igual, canal=ml con id de publicacion")
    except negociacion.NoSePudo as e:
        fallos += 1
        print(f"✖ abrir · ML fue rechazada: {e}")

    # 3. Facebook SIN link: no se puede abrir el chat, hay que rechazarla.
    op = _oportunidad("fb", pid, None, "solo-id")
    try:
        negociacion.abrir(op)
        fallos += 1
        print("✖ abrir · FB sin link se acepto; el saludo no tendria a donde ir")
    except negociacion.NoSePudo:
        print("✓ abrir · FB sin link se rechaza con motivo")

    # 4. Sin techo la escalera de precios no tiene de donde agarrarse.
    op = _oportunidad("fb", pid, "https://fb/x/sin-techo", None, techo=None, objetivo=None)
    try:
        negociacion.abrir(op)
        fallos += 1
        print("✖ abrir · sin techo se acepto; reventaria a mitad de la negociacion")
    except negociacion.NoSePudo:
        print("✓ abrir · sin techo calculado se rechaza")

    return fallos


def probar_freno_calidad() -> int:
    """El filtro que reemplaza el ojo humano cuando nadie aprieta el boton."""
    fallos = 0
    casos = [
        ("indice con 9 muestras", 9, 5, True),
        ("justo en el minimo", 5, 5, True),
        ("una muestra menos que el minimo", 4, 5, False),
        ("dos muestras: es casualidad, no mercado", 2, 5, False),
        ("producto sin indice", None, 5, False),
    ]
    for nombre, muestras, minimo, esperado in casos:
        pid = _producto(f"cal{muestras}", muestras)
        ok, por_que = negociacion.confiable(pid, minimo)
        if ok != esperado:
            fallos += 1
            print(f"✖ freno · {nombre}: permitio={ok} esperado={esperado} ({por_que})")
        else:
            print(f"✓ freno · {nombre}: {'negocia sola' if ok else 'pide tu boton'}")

    ok, _ = negociacion.confiable(None, 5)
    if ok:
        fallos += 1
        print("✖ freno · sin producto identificado no puede negociar sola")
    else:
        print("✓ freno · sin producto identificado pide tu boton")
    return fallos


def main() -> int:
    _limpiar()
    total = 0
    try:
        for titulo, fn in (("abrir la negociacion", probar_abrir),
                           ("freno de calidad del indice", probar_freno_calidad)):
            print(f"\n--- {titulo}")
            total += fn()
    finally:
        _limpiar()
        print("\n(datos de prueba borrados)")
    n = 4 + 6
    print(f"{n - total}/{n} casos OK · {total} fallos")
    return total


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
