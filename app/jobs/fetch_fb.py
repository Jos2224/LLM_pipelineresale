"""Script 12 — Facebook Marketplace. EL ULTIMO DE LA FILA, a proposito.

FB no tiene API para Marketplace. Automatizar ahi rompe sus terminos y el
castigo es cerrarte la cuenta. Por eso:
  - cuenta aparte, nunca la personal
  - navegador con perfil persistente: se entra UNA vez, a mano, y queda
  - ritmo humano: 1 busqueda cada 3-5 min, nunca en paralelo
  - apagado por defecto (policy.yml: modo.fb_activo)

Alternativa menos riesgosa que dejo escrita aca: los grupos de compraventa de
FB tambien salen en Google. Buscar por Google con `site:facebook.com` y leer
solo el resultado publico no toca la sesion de FB. Es menos completo pero no
arriesga la cuenta. Se activa con `modo.fb_via_buscador: true`.
"""
from __future__ import annotations

import hashlib
import json
import random
import re
import time

from app import fb_guard
from app.config import fb, p, watchlist
from app.db import ex, q1
from app.jobs import envuelto

FUENTE = "facebook"


def _hash(link: str) -> str:
    return hashlib.sha1(f"fb:{link}".encode()).hexdigest()


# Una linea que es SOLO plata: "$150.000", "150.000", "$1.200.000".
SOLO_PLATA = re.compile(r"^\s*\$?\s*[\d.,]+\s*$")
# Facebook cierra la tarjeta con "Comuna, RM". Eso es donde esta el equipo, y
# es lo que necesitas para mandar a alguien a buscarlo.
COMUNA = re.compile(r"^(.{2,40}),\s*(RM|R\.M\.|Región.*|Metropolitana)$", re.I)


def _lineas(txt: str) -> list[str]:
    return [l.strip() for l in (txt or "").split("\n") if l.strip()]


def _precio(txt: str) -> int | None:
    """La PRIMERA linea es el precio vigente.

    Cuando el vendedor baja el precio, FB pone abajo el anterior tachado: la
    tarjeta queda [150.000, 170.000, titulo, comuna]. El vigente es el primero,
    asi que esto esta bien — lo que estaba mal era el titulo, que se llevaba
    el precio viejo pegado adelante y de ahi "170.000" entraba a extract.py
    como si fuera una spec.
    """
    ls = _lineas(txt)
    if not ls:
        return None
    d = re.sub(r"[^0-9]", "", ls[0])
    return int(d) if d else None


def _titulo_y_comuna(txt: str) -> tuple[str, str | None]:
    """Titulo limpio y comuna. Descarta TODAS las lineas que son solo plata."""
    utiles = [l for l in _lineas(txt) if not SOLO_PLATA.match(l)]
    if not utiles:
        return "", None
    comuna = None
    m = COMUNA.match(utiles[-1])
    if m and len(utiles) > 1:
        comuna = m.group(1).strip()
        utiles = utiles[:-1]
    return " ".join(utiles)[:400], comuna


def correr() -> str:
    if not p("modo.fb_activo", False):
        return "apagado (policy.yml: modo.fb_activo=false)"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "falta playwright: docker compose --profile scraper build"

    perfil = fb_guard.perfil_listo()
    if not perfil:
        return "falta el login de FB: correr bin/login-fb.sh una vez"

    fid = q1("SELECT id FROM fuente WHERE nombre = %s", (FUENTE,))["id"]
    kws = [k["q"] for k in (watchlist().get("keywords") or []) if k.get("activa", True)]
    random.shuffle(kws)
    kws = kws[:6]
    lo, hi = p("ritmo.fb_pausa_min", [3, 5])

    nuevos = 0
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(perfil), headless=True, viewport={"width": 1366, "height": 850},
            locale="es-CL", timezone_id="America/Santiago",
        )
        permitido, motivo = fb_guard.verificar_o_avisar(ctx, "fetch_fb")
        if not permitido:
            ctx.close()
            return f"candado de cuenta: {motivo}"

        pag = ctx.new_page()
        for i, kw in enumerate(kws):
            pag.goto(fb("buscar.url").format(kw.replace(" ", "%20")),
                     wait_until="domcontentloaded", timeout=45000)
            pag.wait_for_timeout(random.randint(2500, 5000))
            pag.mouse.wheel(0, random.randint(800, 2000))   # scroll humano
            pag.wait_for_timeout(random.randint(1500, 3000))

            for a in pag.query_selector_all(fb("buscar.resultado")):
                href = (a.get_attribute("href") or "").split("?")[0]
                txt = a.inner_text()
                if not href or not txt:
                    continue
                titulo, comuna = _titulo_y_comuna(txt)
                precio = _precio(txt)
                if not titulo:
                    continue
                # xmax = 0 distingue insert de update; el rowcount no.
                fila = q1(
                    """INSERT INTO item_raw (fuente, url, id_externo, titulo, precio, crudo, hash)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (hash) DO UPDATE SET visto_en = now()
                       RETURNING (xmax = 0) AS nuevo""",
                    (fid, f"https://www.facebook.com{href}", href,
                     titulo, precio,
                     json.dumps({"fuente": "fb", "comuna": comuna}), _hash(href)),
                )
                nuevos += 1 if fila and fila["nuevo"] else 0

            if i < len(kws) - 1:
                time.sleep(random.uniform(lo * 60, hi * 60))
        ctx.close()
    return f"{len(kws)} busquedas FB, {nuevos} nuevos"


if __name__ == "__main__":
    print(envuelto("fetch_fb", correr))
