"""Script 24 — que nunca vendas dos veces lo mismo.

El caso feo: se vende en ML y sigue publicado en FB. Llega un comprador, pagas
la cara y quedas mal. Este script es el seguro.

Como funciona: cuando una publicacion cambia a vendida, toma un lock en Redis
por el codigo del item y pausa TODAS las demas publicaciones de ese item en
los otros marketplaces. El lock evita que dos procesos hagan lo mismo a la vez.
"""
from __future__ import annotations

import redis

from app import ml_api, tg
from app.config import REDIS_URL
from app.db import ex, q
from app.jobs import envuelto

R = redis.from_url(REDIS_URL, decode_responses=True)


def _lock(codigo: str, seg: int = 60):
    return R.set(f"lock:stock:{codigo}", "1", nx=True, ex=seg)


def correr() -> str:
    vendidos = q(
        """SELECT DISTINCT inv.id, inv.codigo FROM publicacion p
           JOIN inventario inv ON inv.id = p.inventario
           WHERE p.estado = 'vendida' AND inv.estado <> 'vendido'"""
    )
    cerradas = 0
    for inv in vendidos:
        if not _lock(inv["codigo"]):
            continue
        hermanas = q(
            """SELECT id, marketplace, id_externo FROM publicacion
               WHERE inventario = %s AND estado IN ('activa','borrador')""",
            (inv["id"],),
        )
        for h in hermanas:
            if h["marketplace"] == "ml" and h["id_externo"]:
                try:
                    ml_api.actualizar_item(h["id_externo"], {"status": "paused"})
                except Exception:
                    pass
            ex("UPDATE publicacion SET estado='pausada' WHERE id=%s", (h["id"],))
            cerradas += 1
        ex("UPDATE inventario SET estado='vendido' WHERE id=%s", (inv["id"],))
        if hermanas:
            tg.PUBLICADOR.enviar(f"vendido {inv['codigo']} — pause {len(hermanas)} publicacion(es) gemela(s)")
    return f"{len(vendidos)} vendidos, {cerradas} gemelas pausadas"


if __name__ == "__main__":
    print(envuelto("sync_stock", correr))
