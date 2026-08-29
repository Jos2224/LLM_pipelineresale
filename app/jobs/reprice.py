"""Script 25 — el que no deja stock muerto.

Cada 20 dias sin venta baja 5%. Nunca cruza el piso. Cuando llega al piso deja
de bajar y te avisa una vez: ahi la decision es tuya (regalar, desarmar para
repuestos, o guardar).
"""
from __future__ import annotations

from app import ml_api, tg
from app.config import p
from app.db import ex, q
from app.jobs import envuelto
from app.pricing import rebaja


def correr() -> str:
    dias = int(p("venta.baja_cada_dias", 20))
    pend = q(
        """SELECT p.id, p.id_externo, p.titulo, p.precio, inv.piso_precio, inv.codigo
           FROM publicacion p JOIN inventario inv ON inv.id = p.inventario
           WHERE p.marketplace='ml' AND p.estado='activa'
             AND COALESCE(p.ultimo_ajuste, p.fecha) < now() - make_interval(days => %s)""",
        (dias,),
    )
    bajadas = tocaron_piso = 0
    for pub in pend:
        piso = float(pub["piso_precio"] or 0)
        actual = float(pub["precio"] or 0)
        if not actual or not piso:
            continue
        nuevo = rebaja(actual, piso)
        if nuevo >= actual:
            # ya esta en el piso
            ex("UPDATE publicacion SET ultimo_ajuste = now() WHERE id=%s", (pub["id"],))
            tocaron_piso += 1
            tg.PUBLICADOR.enviar(f"🧊 {pub['codigo']} lleva {dias}+ dias en el piso ({int(piso):,} CLP)\n"
                      f"«{pub['titulo'][:80]}»\nDecision tuya: regalar, repuestos o guardar."
                      .replace(",", "."))
            continue
        try:
            if pub["id_externo"]:
                ml_api.actualizar_item(pub["id_externo"], {"price": int(nuevo)})
        except Exception:
            continue
        ex("UPDATE publicacion SET precio=%s, ultimo_ajuste=now() WHERE id=%s", (nuevo, pub["id"]))
        bajadas += 1
    return f"{bajadas} rebajas, {tocaron_piso} en el piso"


if __name__ == "__main__":
    print(envuelto("reprice", correr))
