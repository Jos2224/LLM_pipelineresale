"""Script 11 — remates. Lee lotes y, sobre todo, G en vivo.

Regla de oro: el G se lee del navegador en el momento y se guarda con la hora
exacta. Un G viejo se trata como desconocido, nunca como actual. Si el
selector no matchea, g = null y la alerta sale con "G=?" en rojo.

Todo lo que cambia entre portales vive en config/aduanas.yml. Este archivo no
tiene ni un selector adentro a proposito: cuando cambies de sitio, cambias el
YAML y listo.

Mientras `activo: false` no toca la red.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

from app.config import DATA, aduanas
from app.db import ex, q1
from app.jobs import envuelto

FUENTE = "aduanas"
VENCE_G_MIN = 10  # un G leido hace mas de esto ya no es "en vivo"


def _num(txt: str | None, patron: str) -> int | None:
    if not txt:
        return None
    limpio = re.sub(patron, "", txt)
    return int(limpio) if limpio.isdigit() else None


def _hash(link: str, titulo: str) -> str:
    return hashlib.sha1(f"aduanas:{link or titulo}".encode()).hexdigest()


def _guardar(fid: int, lote: dict) -> None:
    ahora = datetime.now(timezone.utc).isoformat()
    ex(
        """INSERT INTO item_raw (fuente, url, id_externo, titulo, precio, fotos, crudo, hash)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (hash) DO UPDATE SET
             precio = EXCLUDED.precio,
             crudo = item_raw.crudo || EXCLUDED.crudo,
             visto_en = now(),
             normalizado = false""",
        (
            fid, lote.get("link"), lote.get("link"), lote["titulo"][:400], lote.get("p0"),
            [lote["foto"]] if lote.get("foto") else [],
            json.dumps({
                "remate": True,
                "g": lote.get("g"),
                "g_leido_en": ahora if lote.get("g") is not None else None,
                "cierre": lote.get("cierre"),
            }),
            _hash(lote.get("link", ""), lote["titulo"]),
        ),
    )


def _raspar(cfg: dict) -> list[dict]:
    """Abre el portal con un navegador real y devuelve los lotes."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError("falta playwright: docker compose --profile scraper build")

    sel = cfg["selectores"]
    esp = cfg["espera"]
    perfil = DATA / "perfiles" / "aduanas"
    perfil.mkdir(parents=True, exist_ok=True)

    lotes: list[dict] = []
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(perfil), headless=True, viewport={"width": 1400, "height": 900},
            locale="es-CL", timezone_id="America/Santiago",
        )
        pag = ctx.new_page()
        pag.goto(cfg["portal"]["url_listado"], wait_until="domcontentloaded", timeout=45000)
        if esp.get("listo_cuando"):
            pag.wait_for_selector(esp["listo_cuando"], timeout=30000)
        pag.wait_for_timeout(int(esp.get("render_ms", 2500)))

        for nodo in pag.query_selector_all(sel["lote"]):
            def txt(clave: str) -> str | None:
                s = sel.get(clave)
                if not s:
                    return None
                el = nodo.query_selector(s)
                return el.inner_text().strip() if el else None

            titulo = txt("titulo")
            if not titulo:
                continue
            link_el = nodo.query_selector(sel["link"]) if sel.get("link") else None
            foto_el = nodo.query_selector(sel["foto"]) if sel.get("foto") else None
            lotes.append({
                "titulo": titulo,
                "p0": _num(txt("precio_base"), cfg["limpiar"]["precio"]),
                "g": _num(txt("competidores"), cfg["limpiar"]["competidores"]),
                "cierre": txt("cierre"),
                "link": link_el.get_attribute("href") if link_el else None,
                "foto": foto_el.get_attribute("src") if foto_el else None,
            })
        ctx.close()
    return lotes


def correr() -> str:
    cfg = aduanas()
    if not cfg.get("activo"):
        return "apagado (config/aduanas.yml: activo=false)"
    if not cfg.get("portal", {}).get("url_listado"):
        return "falta url_listado en config/aduanas.yml"

    fid = q1("SELECT id FROM fuente WHERE nombre = %s", (FUENTE,))["id"]
    lotes = _raspar(cfg)
    sin_g = 0
    for l in lotes:
        if l.get("g") is None:
            sin_g += 1
        _guardar(fid, l)
    return f"{len(lotes)} lotes, {sin_g} sin G (van marcados G=?)"


if __name__ == "__main__":
    print(envuelto("fetch_aduanas", correr))
