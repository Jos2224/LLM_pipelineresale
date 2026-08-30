"""Abrir una negociacion de compra. La unica puerta, para los dos canales.

Esto vivia adentro del bot de Telegram, como parte del manejador del boton
[Negociar]. Sacarlo de ahi no es cosmetico: mientras estuvo enterrado en el
bot, la unica forma de empezar una negociacion era que una persona apretara un
boton, y por eso el sistema entero terminaba en "te aviso y tu cierras".

Ahora la usan dos:
  - `app/bot/cazador.py`  cuando aprietas [Negociar]
  - `app/jobs/alert.py`   sola, si `modo.negociar_auto_inicio` esta encendido

Los dos canales terminan en la misma tabla `negociacion`; lo que cambia es la
manija para volver a encontrar el aviso: ML lo abre por id de publicacion,
Facebook por URL. De ahi en adelante el regateo lo hacen
`jobs/negociar_compra.py` (ML) y `jobs/negociar_fb.py` (Messenger), cada uno
con su tope diario y su escalera de precios, que es la misma:
saludo -> objetivo -> mitad de camino -> techo, y nunca mas que el techo.
"""
from __future__ import annotations

from app.db import ex, q1


class NoSePudo(Exception):
    """Motivo legible de por que no se abrio. Se le muestra al usuario tal cual."""


def confiable(producto_id: int | None, minimo: int) -> tuple[bool, str]:
    """¿El indice de precios de este producto aguanta una oferta real?

    Freno para el modo automatico. Un multiplo sale de un P50, y un P50 sacado
    de dos muestras no es un precio de mercado: es una casualidad. Con una
    persona apretando el boton eso se filtraba sola — miraba el aviso y decidia.
    Sin persona, este es el filtro.

    El 29-ago hubo dos casos reales de indice envenenado (notebooks metidos en
    el estante de una tarjeta de video, cargadores en el de un notebook). Los
    dos se arreglaron, pero la leccion es que el indice se puede corromper sin
    hacer ruido, y automatizar sobre el pide un piso de muestras.
    """
    if not producto_id:
        return False, "sin producto identificado"
    fila = q1(
        """SELECT max(n_muestras) AS n FROM indice_precio WHERE producto = %s""",
        (producto_id,),
    )
    n = int((fila or {}).get("n") or 0)
    if n < minimo:
        return False, f"el indice tiene {n} muestras, hacen falta {minimo}"
    return True, f"{n} muestras"


def abrir(op_id) -> str:
    """Crea la fila de negociacion. Devuelve texto para mostrarle al usuario.

    Lanza `NoSePudo` con el motivo si falta algo. No manda ningun mensaje al
    vendedor: de eso se encargan los jobs, con sus pausas y sus topes.
    """
    o = q1(
        """SELECT o.id, o.p_max, o.objetivo, i.precio, i.id_externo, i.url, f.tipo
           FROM oportunidad o JOIN item_raw i ON i.id = o.item_raw
           JOIN fuente f ON f.id = i.fuente WHERE o.id = %s""",
        (op_id,),
    )
    if not o:
        raise NoSePudo("no la encuentro")
    if o["tipo"] not in ("ml", "fb"):
        raise NoSePudo(f"no se negociar en {o['tipo']} todavia")
    # Sin techo ni objetivo la escalera de ofertas no tiene de donde agarrarse
    # y reventaria a mitad de la negociacion, con el vendedor esperando.
    if not o["p_max"] or not o["objetivo"]:
        raise NoSePudo("sin techo calculado todavia, espera el proximo indice de precios")
    # Cada canal necesita su propia manija para volver a encontrar el aviso.
    if o["tipo"] == "ml" and not o["id_externo"]:
        raise NoSePudo("esa publicacion de ML no tiene id, no puedo escribirle")
    if o["tipo"] == "fb" and not o["url"]:
        raise NoSePudo("esa publicacion de Facebook no tiene link, no puedo abrirla")

    ex(
        """INSERT INTO negociacion (oportunidad, item_externo, precio_pedido,
                                    precio_objetivo, precio_techo, canal, url_item)
           VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (oportunidad) DO NOTHING""",
        (o["id"], o["id_externo"], o["precio"], o["objetivo"], o["p_max"],
         o["tipo"], o["url"] if o["tipo"] == "fb" else None),
    )
    ex("UPDATE oportunidad SET estado = 'negociando' WHERE id = %s", (op_id,))
    if o["tipo"] == "fb":
        return "el bot le escribe por Messenger en unos minutos"
    return "el bot saluda al vendedor en unos minutos"
