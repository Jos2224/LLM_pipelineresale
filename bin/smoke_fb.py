#!/usr/bin/env python3
"""Prueba de humo del circuito completo de respuestas, contra la base real.

Recorre el camino que antes estaba roto:

    llega pregunta -> se redacta borrador -> aprietas [Enviar] -> se manda

Sin ML ni navegador de por medio: lo que se comprueba es que el borrador se
guarde, que el boton empuje el id correcto y que el consumidor de la cola lo
encuentre y lo cierre. Al final borra todo lo que creo.

    docker compose run --rm -e PYTHONPATH=/app -v ./bin:/app/bin:ro \
      worker python /app/bin/smoke_fb.py
"""
from __future__ import annotations

import sys

sys.path.insert(0, "/app")

from app.db import ex, q1                       # noqa: E402
from app.jobs import reply_bot, reply_fb        # noqa: E402

CODIGO = "SMOKE-FB-BORRAR"
fallos = 0


def check(nombre: str, cond: bool, extra: str = "") -> None:
    global fallos
    if cond:
        print(f"✓ {nombre}")
    else:
        fallos += 1
        print(f"✖ {nombre} {extra}")


def main() -> int:
    inv = q1("""INSERT INTO inventario (codigo, titulo, condicion, piso_precio, estado)
                VALUES (%s,'ThinkPad T480 16GB 512GB SSD','usado',300000,'listo')
                RETURNING id""", (CODIGO,))["id"]
    pub_ml = q1("""INSERT INTO publicacion (inventario, marketplace, id_externo, titulo,
                                            descripcion, precio, estado)
                   VALUES (%s,'ml','MLC-SMOKE','ThinkPad T480 16GB 512GB SSD',
                           'Bateria buena, sin detalles.',415000,'activa')
                   RETURNING id""", (inv,))["id"]
    pub_fb = q1("""INSERT INTO publicacion (inventario, marketplace, titulo,
                                            descripcion, precio, estado)
                   VALUES (%s,'fb','ThinkPad T480 16GB 512GB SSD',
                           'Bateria buena, sin detalles.',415000,'activa')
                   RETURNING id""", (inv,))["id"]

    try:
        # --- ML: pregunta entra, borrador sale, boton, cola --------------
        entrada = q1("""INSERT INTO mensaje (publicacion, id_externo, direccion, tipo,
                                             texto, canal, estado)
                        VALUES (%s,'SMOKE-Q1','entra','pregunta',
                                '¿tiene bateria buena?','ml','nuevo') RETURNING id""",
                     (pub_ml,))["id"]
        salida = reply_bot._borrador(pub_ml, entrada, "pregunta", "Si, la bateria esta buena.")
        fila = q1("""SELECT direccion, estado, canal, responde_a FROM mensaje WHERE id=%s""",
                  (salida,))
        check("borrador ML guardado como salida pendiente",
              fila["direccion"] == "sale" and fila["estado"] == "nuevo"
              and fila["canal"] == "ml" and fila["responde_a"] == entrada, str(dict(fila)))

        # El boton de Telegram hace exactamente esto:
        reply_bot.R.rpush(reply_bot.COLA, str(salida))
        check("el consumidor encuentra el borrador aprobado",
              q1("""SELECT s.id, e.id_externo FROM mensaje s
                    LEFT JOIN mensaje e ON e.id = s.responde_a
                    WHERE s.id=%s AND s.direccion='sale'""", (salida,))["id_externo"] == "SMOKE-Q1")
        # Sin token de ML el envio falla; lo que importa es que NO reviente y
        # que el borrador quede vivo para reintentar.
        enviados = reply_bot._drenar_cola()
        check("sin login de ML no manda nada y no se cae", enviados == 0)
        check("el borrador sigue disponible tras el fallo",
              q1("SELECT estado FROM mensaje WHERE id=%s", (salida,))["estado"] == "nuevo")
        check("la cola quedo vacia (no se reencola en loop)",
              reply_bot.R.llen(reply_bot.COLA) == 0)

        # --- FB: emparejar, ofertar, y el tope diario -------------------
        activas = [{"id": pub_fb, "titulo": "ThinkPad T480 16GB 512GB SSD",
                    "precio": 415000, "piso_precio": 300000}]
        check("el hilo de FB se pega a la publicacion correcta",
              (reply_fb._publicacion_de("ThinkPad T480 16GB", activas) or {}).get("id") == pub_fb)
        check("un hilo de otro producto NO se adivina",
              reply_fb._publicacion_de("Refrigerador Mabe 300 litros", activas) is None)

        sfb = reply_fb._borrador(pub_fb, "999888777", entrada, "oferta",
                                 "Te lo puedo dejar en 300.000.")
        ffb = q1("SELECT canal, hilo, direccion, estado FROM mensaje WHERE id=%s", (sfb,))
        check("borrador FB guarda el hilo para poder contestar despues",
              ffb["canal"] == "fb" and ffb["hilo"] == "999888777"
              and ffb["direccion"] == "sale" and ffb["estado"] == "nuevo", str(dict(ffb)))
        check("hay cupo de respuestas para hoy", reply_fb._cupo_hoy() > 0)

        print(f"\n{'TODO OK' if not fallos else str(fallos) + ' FALLOS'}")
        return fallos
    finally:
        ex("DELETE FROM mensaje WHERE publicacion IN (%s,%s)", (pub_ml, pub_fb))
        ex("DELETE FROM publicacion WHERE inventario=%s", (inv,))
        ex("DELETE FROM inventario WHERE id=%s", (inv,))
        print("datos de prueba borrados")


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
