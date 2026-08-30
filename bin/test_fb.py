#!/usr/bin/env python3
"""Casos del lado Facebook. Tres cosas, y las tres son plata o tu cuenta:

  1. el candado de cuenta        -> que jamas toque la cuenta personal
  2. emparejar hilo con producto -> contestar la ficha equivocada es peor
                                    que no contestar
  3. leer ofertas de compradores -> "te doy 250 lucas" tiene que ser 250000

Corre sin navegador, sin base y sin Ollama: son reglas puras.

    ~/cazador/bin/cazador test
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")

from app import fb_guard                     # noqa: E402
from app.jobs.reply_fb import _parecido, _publicacion_de  # noqa: E402
from app.parseo import leer_respuesta_vendedor            # noqa: E402

PERSONAL = "100000000000001"
DESECHABLE = "100000000000002"
OTRA = "100000000000003"


class CtxFalso:
    """Imita lo unico que el candado le pide a Playwright: las cookies."""

    def __init__(self, uid: str | None):
        self.uid = uid

    def cookies(self, _url: str):
        return [{"name": "c_user", "value": self.uid}] if self.uid else []


# ------------------------------------------------------------ 1. candado
# (nombre, ficha guardada, cuenta en el navegador, prohibidas, debe_permitir)
CASOS_CANDADO = [
    ("cuenta aprobada y es la que esta abierta",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, DESECHABLE, [], True),

    ("nunca se aprobo ninguna cuenta",
     None, DESECHABLE, [], False),

    ("se hizo login pero no se confirmo con SI",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": False}, DESECHABLE, [], False),

    ("el navegador quedo con la cuenta PERSONAL",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, PERSONAL, [PERSONAL], False),

    ("la personal esta abierta y ni siquiera esta en la lista negra",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, PERSONAL, [], False),

    ("alguien aprobo la personal por error: la lista negra manda",
     {"id": PERSONAL, "nombre": "Yo", "aprobada": True}, PERSONAL, [PERSONAL], False),

    ("sesion caida, no hay cookie",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, None, [], False),

    ("se cambio de cuenta a una tercera",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, OTRA, [], False),
]


def probar_candado() -> int:
    fallos = 0
    tmp = Path(tempfile.mkdtemp())
    fb_guard.PERFIL = tmp
    fb_guard.FICHA = tmp / "cuenta.json"

    for nombre, ficha, en_sesion, prohibidas, esperado in CASOS_CANDADO:
        if ficha is None:
            fb_guard.FICHA.unlink(missing_ok=True)
        else:
            fb_guard.FICHA.write_text(json.dumps(ficha), encoding="utf-8")
        fb_guard.p = lambda ruta, defecto=None, _pr=prohibidas: (
            _pr if ruta == "facebook.cuentas_prohibidas" else defecto)

        ok, motivo = fb_guard.verificar(CtxFalso(en_sesion))
        if ok != esperado:
            fallos += 1
            print(f"✖ candado · {nombre}\n    permitio={ok} esperado={esperado} · {motivo}")
        else:
            print(f"✓ candado · {nombre}")
    return fallos


# ------------------------------------------------ 1b. candado de LECTURA
# El candado suave, el que usa fetch_fb para buscar precios.
#
# Una sola diferencia con el de arriba, y es la fila 1: sin sesion DEJA PASAR,
# porque buscar no toca ninguna cuenta. Todo lo demas tiene que seguir cerrado
# igual — sobre todo la personal, que es lo que el candado protege.
CASOS_LECTURA = [
    ("sin sesion: busca anonimo, es lo mas seguro que hay",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, None, [], True),

    ("sin sesion y sin ninguna cuenta aprobada: igual busca",
     None, None, [], True),

    ("con la cuenta desechable abierta: busca",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, DESECHABLE, [], True),

    ("con la PERSONAL abierta: NO mira nada, aunque solo sea leer",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, PERSONAL, [PERSONAL], False),

    ("la personal abierta sin estar en la lista negra: tampoco",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, PERSONAL, [], False),

    ("una sesion que nunca aprobaste: no se navega con ella",
     None, OTRA, [], False),

    ("se cambio a una tercera cuenta: abortado",
     {"id": DESECHABLE, "nombre": "Ventas FM", "aprobada": True}, OTRA, [], False),
]


def probar_candado_lectura() -> int:
    fallos = 0
    tmp = Path(tempfile.mkdtemp())
    fb_guard.PERFIL = tmp
    fb_guard.FICHA = tmp / "cuenta.json"

    for nombre, ficha, en_sesion, prohibidas, esperado in CASOS_LECTURA:
        if ficha is None:
            fb_guard.FICHA.unlink(missing_ok=True)
        else:
            fb_guard.FICHA.write_text(json.dumps(ficha), encoding="utf-8")
        fb_guard.p = lambda ruta, defecto=None, _pr=prohibidas: (
            _pr if ruta == "facebook.cuentas_prohibidas" else defecto)

        ok, motivo = fb_guard.verificar_lectura(CtxFalso(en_sesion))
        if ok != esperado:
            fallos += 1
            print(f"✖ lectura · {nombre}\n    permitio={ok} esperado={esperado} · {motivo}")
        else:
            print(f"✓ lectura · {nombre}")
    return fallos


# ------------------------------------------------- 1c. no repetir el aviso
# 13 mensajes por hora, todos iguales, hacen que silencies el bot — y ahi
# pierdes las alertas de oportunidad. El aviso sale una vez; si el motivo
# CAMBIA, eso si es noticia y sale al toque.
def probar_antiruido() -> int:
    fallos = 0
    vistos: set[str] = set()

    # Redis falso: `set(nx=True)` devuelve True solo la primera vez.
    class RedisFalso:
        def set(self, llave, _v, nx=False, ex=None):
            if llave in vistos:
                return None
            vistos.add(llave)
            return True

    original = fb_guard._R
    fb_guard._R = RedisFalso()
    try:
        casos = [
            ("fetch_fb", "no hay sesion", True, "el primero siempre pasa"),
            ("fetch_fb", "no hay sesion", False, "el mismo motivo se calla"),
            ("fetch_fb", "no hay sesion", False, "y se sigue callando"),
            ("reply_fb", "no hay sesion", True, "otro job avisa por su cuenta"),
            ("fetch_fb", "la sesion cambio de cuenta", True, "motivo NUEVO: pasa"),
        ]
        for job, motivo, esperado, por_que in casos:
            salio = fb_guard._aviso_nuevo(job, motivo)
            if salio != esperado:
                fallos += 1
                print(f"✖ ruido · {por_que}: aviso={salio} esperado={esperado}")
            else:
                print(f"✓ ruido · {por_que}")

        # Si Redis se cae, mejor avisar de mas que tragarse un problema.
        class RedisMuerto:
            def set(self, *a, **k):
                raise RuntimeError("redis caido")

        fb_guard._R = RedisMuerto()
        if not fb_guard._aviso_nuevo("fetch_fb", "no hay sesion"):
            fallos += 1
            print("✖ ruido · con Redis caido tiene que avisar igual")
        else:
            print("✓ ruido · con Redis caido avisa igual")
    finally:
        fb_guard._R = original
    return fallos


# --------------------------------------------------------- 2. emparejar
ACTIVAS = [
    {"id": 1, "titulo": "Notebook Lenovo ThinkPad T480 i5 16GB 512GB SSD"},
    {"id": 2, "titulo": "Notebook Dell Latitude 7490 i7 16GB 256GB SSD"},
    {"id": 3, "titulo": "Monitor LG 24 pulgadas IPS Full HD"},
]

CASOS_HILO = [
    ("ThinkPad T480 16GB 512 SSD", 1),
    ("Notebook Lenovo ThinkPad T480", 1),
    ("Dell Latitude 7490 i7", 2),
    ("Monitor LG 24 IPS", 3),
    # Ninguna se parece lo suficiente: NO debe adivinar, debe preguntarte.
    ("Bicicleta aro 29 usada", None),
    ("", None),
]


def probar_emparejar() -> int:
    fallos = 0
    for titulo_hilo, esperado in CASOS_HILO:
        pub = _publicacion_de(titulo_hilo, ACTIVAS)
        salio = pub["id"] if pub else None
        if salio != esperado:
            fallos += 1
            print(f"✖ hilo · «{titulo_hilo}» -> {salio}, esperado {esperado} "
                  f"(parecidos: " +
                  ", ".join(f"{a['id']}={_parecido(titulo_hilo, a['titulo']):.2f}" for a in ACTIVAS) + ")")
        else:
            print(f"✓ hilo · «{titulo_hilo or '(vacio)'}» -> {salio}")
    return fallos


# ------------------------------------------------------------ 3. ofertas
# Mensajes tipicos de comprador en Marketplace. El numero es lo unico que
# importa: de ahi sale aceptar / contraofertar / rechazar.
CASOS_OFERTA = [
    ("hola, te doy 250 lucas y lo paso a buscar hoy", 250000),
    ("¿lo dejas en 180.000?", 180000),
    ("te ofrezco 1 palo por el notebook", 1000000),
    ("me interesa, ¿en cuanto lo dejas?", None),
    ("¿todavia esta disponible?", None),
    # Trampa: 16GB y T480 son specs, no plata.
    ("el T480 de 16GB me sirve, ¿lo dejas en 300 mil?", 300000),
]


def probar_ofertas() -> int:
    fallos = 0
    for texto, esperado in CASOS_OFERTA:
        d = leer_respuesta_vendedor(texto, 350000)
        if d["precio"] != esperado:
            fallos += 1
            print(f"✖ oferta · «{texto[:55]}» -> {d['precio']}, esperado {esperado}")
        else:
            tipo = "oferta" if d["precio"] else "pregunta"
            print(f"✓ oferta · «{texto[:55]}» -> {tipo} {d['precio'] or ''}")
    return fallos



# ------------------------------------------------- 4. tarjetas de Marketplace
# La tarjeta trae el precio, a veces el precio ANTERIOR tachado, el titulo y
# la comuna, todo en lineas sueltas. Leerla mal cuesta plata dos veces: el
# precio equivocado arruina el multiplo, y el titulo sucio arruina la
# identificacion (un "170.000" pegado adelante entra como si fuera una spec).
CASOS_TARJETA = [
    ("$15.000\nCargador original Lenovo 90W\nLa Pintana, RM",
     15000, "Cargador original Lenovo 90W", "La Pintana", "lo normal"),
    ("$150.000\n$170.000\nNotebook Lenovo nuevo\nRecoleta, RM",
     150000, "Notebook Lenovo nuevo", "Recoleta", "bajo el precio: vale el primero"),
    ("$15.000\nCargador Neteboock Dell\nConcón, VS",
     15000, "Cargador Neteboock Dell", "Concón", "region que no es la RM"),
    ("$99.000\nNotebook HP\nConcepción, BI",
     99000, "Notebook HP", "Concepción", "Biobio"),
    # El que rompio la primera version: partia "90W" en "90" + "w La Pintana".
    ("$70.000\nThinkPad T420 16GB, 512GB SSD",
     70000, "ThinkPad T420 16GB, 512GB SSD", None, "specs con coma NO son comuna"),
    ("$22.000\nCargador Dell 65W tipo C original\nQuinta Normal, RM",
     22000, "Cargador Dell 65W tipo C original", "Quinta Normal", "watts no se cortan"),
    # El que tiro numeric field overflow y dejo 85 de 111 items sin precio:
    # FB antepone insignias y la version vieja las convertia a numero.
    ("Recién publicado\n$45.000\nMonitor Master G Full HD 24\"\nSantiago, RM",
     45000, 'Monitor Master G Full HD 24"', "Santiago", "insignia antes del precio"),
    ("Se envía a todo Chile\n$120.000\nNotebook HP\nMaipú, RM",
     120000, "Notebook HP", "Maipú", "otra insignia"),
    ("Recién publicado\nMonitor sin precio\nLa Pintana, RM",
     None, "Monitor sin precio", "La Pintana", "sin precio no inventa"),
]


# ------------------------------------------- 4b. tarjetas de otro pais
# El slug `santiago` (sin CL) mandaba a Santa Clara, California. Esto es la
# red por si FB vuelve a cambiar de ciudad: un item gringo no se puede ir a
# buscar y su precio corrompe el indice.
CASOS_MONEDA = [
    ("Recién publicado\nUS$200\nLenovo ThinkPad E570\nSanta Clara, CA", True,
     "dolar con simbolo"),
    ("$60 USD\nThinkPad T400\nSan Francisco, CA", True, "dolar escrito"),
    ("R$ 1.200\nNotebook Dell\nSão Paulo", True, "real brasileño"),
    ("€250\nThinkPad X1\nMadrid", True, "euro"),
    ("S/. 900\nLaptop HP\nLima", True, "sol peruano"),
    ("$100.000\nLenovo ThinkPad X280 (Bateria mala)\nMaipú, RM", False,
     "chilena normal"),
    ("Recién publicado\nGratis\nCargador Lenovo 65W\nÑuñoa, RM", False,
     "gratis igual es chilena"),
    # Trampa: "USB" empieza igual que "USD" y no es plata.
    ("$70.000\ndocking lenovo hybrid USB-C with USB-A\nRecoleta, RM", False,
     "USB no es USD"),
    # Trampa: un titulo que MENCIONA dolares pero cuesta pesos.
    ("$450.000\nMacBook comprado en USA sin uso\nLas Condes, RM", False,
     "USA no es una moneda"),
]


def probar_moneda() -> int:
    from app.jobs.fetch_fb import es_extranjera
    fallos = 0
    for txt, esperado, por_que in CASOS_MONEDA:
        salio = es_extranjera(txt)
        if salio != esperado:
            fallos += 1
            print(f"✖ moneda · {por_que}: extranjera={salio} esperado={esperado}")
        else:
            print(f"✓ moneda · {por_que}: {'se tira' if salio else 'se guarda'}")
    return fallos


def probar_tarjetas() -> int:
    from app.jobs.fetch_fb import _precio, _titulo_y_comuna
    fallos = 0
    for txt, precio, titulo, comuna, por_que in CASOS_TARJETA:
        p, (t, c) = _precio(txt), _titulo_y_comuna(txt)
        mal = []
        if p != precio:
            mal.append(f"precio={p} esperado {precio}")
        if t != titulo:
            mal.append(f"titulo={t!r} esperado {titulo!r}")
        if c != comuna:
            mal.append(f"comuna={c!r} esperado {comuna!r}")
        if mal:
            fallos += 1
            print(f"✖ tarjeta · {por_que}")
            for m in mal:
                print(f"    {m}")
        else:
            print(f"✓ tarjeta · {por_que}: {t[:38]} · {comuna or 'sin comuna'}")
    return fallos


def main() -> int:
    total = 0
    for titulo, fn in (("candado de cuenta", probar_candado),
                       ("candado de lectura (buscar precios)", probar_candado_lectura),
                       ("no repetir el mismo aviso", probar_antiruido),
                       ("emparejar hilo con publicacion", probar_emparejar),
                       ("leer ofertas de compradores", probar_ofertas),
                       ("tarjetas de Marketplace", probar_tarjetas),
                       ("descartar tarjetas de otro pais", probar_moneda)):
        print(f"\n--- {titulo}")
        total += fn()
    n = (len(CASOS_CANDADO) + len(CASOS_LECTURA) + 6 + len(CASOS_HILO)
         + len(CASOS_OFERTA) + len(CASOS_TARJETA) + len(CASOS_MONEDA))
    print(f"\n{n - total}/{n} casos OK · {total} fallos")
    return total


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
