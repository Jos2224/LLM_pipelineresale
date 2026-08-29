"""Script 22 — sube el borrador a MercadoLibre.

Doble freno para que nunca publique algo que no querias:
  1. policy.modo.publicar_auto = false -> el borrador espera tu boton
  2. las primeras N publicaciones piden boton SI O SI, aunque el auto este on

Sin fotos no publica. Nunca. Una publicacion sin foto no vende y ademas
ensucia tu reputacion.
"""
from __future__ import annotations

from app import ml_api, tg
from app.config import ML_SITE, p
from app.db import ex, q, q1
from app.jobs import envuelto

LOTE = 5


def _ya_publicadas() -> int:
    """Cuantas llegaron a ML de verdad.

    Antes contaba "todo lo que no es borrador", asi que los borradores
    esperando tu boton ('preparando') contaban como publicados y el freno de
    las primeras 20 se abria solo.
    """
    fila = q1("""SELECT count(*) AS n FROM publicacion
                 WHERE marketplace='ml' AND estado IN ('activa','vendida','pausada','cerrada')""")
    return fila["n"] if fila else 0


def _fotos_ml(fotos: list[str]) -> list[dict]:
    """Las de ML ya son URL; las que mandaste por Telegram son archivos locales."""
    salida: list[dict] = []
    for f in fotos[:10]:
        if f.startswith("http"):
            salida.append({"source": f})
            continue
        try:
            salida.append({"id": ml_api.subir_foto_archivo(f)})
        except Exception:
            continue
    return salida


def _cuerpo(pub: dict, fotos: list[dict], categoria: str, condicion: str) -> dict:
    return {
        "title": pub["titulo"][:60],
        "category_id": categoria,
        "price": int(pub["precio"]),
        "currency_id": "CLP",
        "available_quantity": 1,
        "buying_mode": "buy_it_now",
        "listing_type_id": "gold_special",
        "condition": {"usado": "used", "nuevo": "new"}.get(condicion, "used"),
        "site_id": ML_SITE,
        "pictures": fotos,
    }


def _publicar_uno(pub: dict) -> str:
    fotos = pub["fotos"] or []
    if not fotos:
        return "sin fotos"
    if not pub["precio"]:
        return "sin precio (falta indice)"
    categoria = pub["ml_categoria"]
    if not categoria:
        c = ml_api.categoria_de(pub["titulo"])
        categoria = c.get("category_id") if c else None
    if not categoria:
        return "sin categoria"

    subidas = _fotos_ml(fotos)
    if not subidas:
        return "no se pudo subir ninguna foto"

    creado = ml_api.publicar(_cuerpo(pub, subidas, categoria, pub["condicion"]))
    if pub.get("descripcion"):
        try:
            ml_api.poner_descripcion(creado["id"], pub["descripcion"])
        except Exception:
            pass

    ex(
        """UPDATE publicacion SET id_externo=%s, url=%s, estado='activa', fecha=now(),
                                  ultimo_ajuste=now() WHERE id=%s""",
        (creado["id"], creado.get("permalink"), pub["id"]),
    )
    ex("UPDATE inventario SET estado='publicado' WHERE id=%s", (pub["inventario"],))
    return creado.get("permalink", creado["id"])


def correr() -> str:
    auto = bool(p("modo.publicar_auto", False))
    minimo = int(p("modo.borradores_de_prueba", 20))
    if not auto or _ya_publicadas() < minimo:
        return "modo borrador: los listados esperan tu boton en Telegram"

    pend = q(
        """SELECT p.id, p.inventario, p.titulo, p.descripcion, p.precio,
                  inv.fotos, inv.condicion, pc.ml_categoria
           FROM publicacion p
           JOIN inventario inv ON inv.id = p.inventario
           LEFT JOIN producto_canon pc ON pc.id = inv.producto
           WHERE p.marketplace='ml' AND p.estado='borrador'
           ORDER BY p.fecha LIMIT %s""",
        (LOTE,),
    )
    ok = 0
    for pub in pend:
        try:
            r = _publicar_uno(dict(pub))
            if r.startswith("http"):
                ok += 1
                tg.PUBLICADOR.enviar(f"publicado: {pub['titulo']}\n{r}")
        except Exception as e:
            ex("UPDATE publicacion SET estado='rechazada' WHERE id=%s", (pub["id"],))
            tg.PUBLICADOR.enviar(f"⚠️ ML rechazo «{pub['titulo']}»\n{str(e)[:300]}")
    return f"{ok}/{len(pend)} publicados"


if __name__ == "__main__":
    print(envuelto("publish_ml", correr))
