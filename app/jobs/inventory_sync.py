"""Script 20 — tu inventario sale de tu propia cuenta de MercadoLibre.

No hay que exportar nada ni llenar un Excel. Se leen tus publicaciones
(activas, pausadas y cerradas) por la API y se arma el inventario solo:

  publicacion ML  ->  inventario (codigo, producto, costo?, piso, fotos)
                  ->  publicacion (marketplace='ml', id_externo, estado)

Lo unico que la API no sabe es cuanto te costo cada cosa. El costo queda en
null y el bot te lo pregunta por Telegram solo cuando hace falta (para el
reporte de margen), nunca antes.

De yapa: propone keywords para la watchlist a partir de lo que ya vendes.
"""
from __future__ import annotations

from collections import Counter

from app import ml_api
from app import specs as sp
from app.db import ex, q1
from app.extract import extraer
from app.jobs import envuelto
from app.pricing import piso_default

MAPA_ESTADO = {"active": "publicado", "paused": "pausado", "closed": "vendido", "under_review": "pausado"}


def _producto_de(it: dict) -> int | None:
    """Usa la marca/modelo que ML ya trae en los atributos: gratis y exacto."""
    attrs = {a.get("id"): (a.get("value_name") or "") for a in it.get("attributes", [])}
    marca = (attrs.get("BRAND") or "").strip()
    modelo = (attrs.get("MODEL") or attrs.get("LINE") or "").strip()
    if not marca or not modelo:
        return None
    fila = q1(
        """INSERT INTO producto_canon (marca, modelo, categoria, ml_categoria)
           VALUES (%s,%s,%s,%s)
           ON CONFLICT (marca, modelo) DO UPDATE SET ml_categoria = EXCLUDED.ml_categoria
           RETURNING id""",
        (marca.title()[:60], modelo[:120], "otro", it.get("category_id")),
    )
    return fila["id"] if fila else None


def correr() -> str:
    try:
        ml_api.token()
    except ml_api.SinToken:
        return "sin login de ML todavia"

    ids: list[str] = []
    for estado in ("active", "paused", "closed"):
        try:
            ids.extend(ml_api.mis_items(estado))
        except Exception:
            continue
    if not ids:
        return "0 publicaciones en tu cuenta"

    detalles = ml_api.items(ids)
    creados = actualizados = 0
    palabras: Counter = Counter()

    for it in detalles:
        pid = _producto_de(it)
        estado_inv = MAPA_ESTADO.get(it.get("status", ""), "pausado")
        fotos = [pic.get("secure_url") for pic in it.get("pictures", []) if pic.get("secure_url")]
        codigo = f"ML-{it['id']}"

        # Con las specs del propio item, sacadas de su titulo: un 32GB/1TB
        # tuyo no tiene el mismo piso que el 8GB/256 del vecino, aunque sean
        # el mismo modelo.
        p50, _ = sp.precio_mercado(pid, extraer(it.get("title") or "")["specs"])
        piso = piso_default(p50) if p50 else None

        fila = q1(
            """INSERT INTO inventario (codigo, producto, titulo, condicion, piso_precio, fotos, estado, origen)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'ml')
               ON CONFLICT (codigo) DO UPDATE SET
                 titulo = EXCLUDED.titulo,
                 fotos = EXCLUDED.fotos,
                 estado = EXCLUDED.estado,
                 producto = COALESCE(inventario.producto, EXCLUDED.producto),
                 piso_precio = CASE WHEN inventario.piso_manual THEN inventario.piso_precio
                                    ELSE COALESCE(EXCLUDED.piso_precio, inventario.piso_precio) END
               RETURNING id, (xmax = 0) AS nuevo""",
            (codigo, pid, it.get("title", "")[:400],
             {"new": "nuevo", "used": "usado"}.get(it.get("condition"), "usado"),
             piso, fotos, estado_inv),
        )
        if fila["nuevo"]:
            creados += 1
        else:
            actualizados += 1

        ex(
            """INSERT INTO publicacion (inventario, marketplace, id_externo, url, titulo, precio, estado, visitas)
               VALUES (%s,'ml',%s,%s,%s,%s,%s,%s)
               ON CONFLICT (marketplace, id_externo) DO UPDATE SET
                 precio = EXCLUDED.precio, estado = EXCLUDED.estado, titulo = EXCLUDED.titulo""",
            (fila["id"], it["id"], it.get("permalink"), it.get("title", "")[:400], it.get("price"),
             {"active": "activa", "paused": "pausada", "closed": "cerrada"}.get(it.get("status"), "pausada"),
             it.get("visits") or 0),
        )

        for w in (it.get("title") or "").lower().split():
            if len(w) > 3 and not w.isdigit():
                palabras[w] += 1

    # Semilla de watchlist: lo que mas repites vendiendo es lo que sabes vender.
    from app.db import kv_set
    kv_set("watchlist_sugerida", [w for w, n in palabras.most_common(40) if n >= 2])

    return f"{len(detalles)} publicaciones: {creados} nuevas, {actualizados} actualizadas"


if __name__ == "__main__":
    print(envuelto("inventory_sync", correr))
