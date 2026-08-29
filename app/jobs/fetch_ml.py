"""Script 10 — lee MercadoLibre por la API oficial. Riesgo cero: solo mira.

Cada 30 min recorre la watchlist. Guarda todo lo nuevo en item_raw.
No decide nada: eso es de normalize.py y score.py.
"""
from __future__ import annotations

import hashlib
import json
import time

from app import ml_api
from app.config import p, watchlist
from app.db import ex, q1
from app.jobs import envuelto

FUENTE = "mercadolibre"


def _hash(id_externo: str) -> str:
    return hashlib.sha1(f"ml:{id_externo}".encode()).hexdigest()


def _fuente_id() -> int:
    return q1("SELECT id FROM fuente WHERE nombre = %s", (FUENTE,))["id"]


def _guardar(fid: int, r: dict) -> bool:
    """Devuelve True si el item era nuevo.

    Ojo: con ON CONFLICT DO UPDATE el rowcount es 1 tanto si inserto como si
    actualizo, asi que contar filas mentia y TODO salia como "nuevo". La
    verdad esta en xmax = 0, que hay que leer con q1, no con ex.
    """
    fila = q1(
        """INSERT INTO item_raw (fuente, url, id_externo, titulo, precio, moneda, fotos, crudo, hash)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (hash) DO UPDATE
             SET precio = EXCLUDED.precio, visto_en = now(),
                 normalizado = CASE WHEN item_raw.precio IS DISTINCT FROM EXCLUDED.precio
                                    THEN false ELSE item_raw.normalizado END
           RETURNING (xmax = 0) AS nuevo""",
        (
            fid,
            r.get("permalink"),
            r.get("id"),
            (r.get("title") or "")[:400],
            r.get("price"),
            r.get("currency_id", "CLP"),
            [r["thumbnail"].replace("-I.jpg", "-O.jpg")] if r.get("thumbnail") else [],
            json.dumps({
                "condition": r.get("condition"),
                "sold_quantity": r.get("sold_quantity"),
                "category_id": r.get("category_id"),
                "seller_id": (r.get("seller") or {}).get("id"),
                "shipping_free": (r.get("shipping") or {}).get("free_shipping"),
                "attributes": r.get("attributes", [])[:12],
            }),
            _hash(r.get("id", "")),
        ),
    )
    return bool(fila and fila["nuevo"])


def correr() -> str:
    fid = _fuente_id()
    wl = watchlist()
    entradas = [k for k in (wl.get("keywords") or []) if k.get("activa", True)]
    entradas += [dict(k, remate=True) for k in (wl.get("remates") or []) if k.get("activa", True)]
    entradas = entradas[: int(p("ritmo.ml_busquedas_por_ciclo", 25))]

    pausa = float(p("ritmo.ml_pausa_seg", 1.2))
    nuevos = vistos = 0
    for e in entradas:
        try:
            d = ml_api.buscar(e["q"], limite=50, condicion=e.get("condicion", "all"))
        except ml_api.SinToken:
            return "sin login de ML todavia — nada que hacer"
        except Exception as err:
            ex("INSERT INTO job_log (job, ok, detalle) VALUES ('fetch_ml.q', false, %s)",
               (f"{e['q']}: {err}",))
            continue
        for r in d.get("results", []):
            tope = e.get("max_precio")
            if tope and (r.get("price") or 0) > tope:
                continue
            vistos += 1
            if _guardar(fid, r):
                nuevos += 1
        time.sleep(pausa)
    return f"{len(entradas)} busquedas, {vistos} items, {nuevos} nuevos"


if __name__ == "__main__":
    print(envuelto("fetch_ml", correr))
