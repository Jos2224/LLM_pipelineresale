"""Script 23 — publica en Facebook Marketplace. El de mas riesgo del stack.

Mismo contenido que ML, ritmo humano, tope de 5 publicaciones al dia. Usa el
perfil de navegador donde entraste UNA vez a mano.

Alternativa mas segura que dejo como opcion en policy.yml: no publicar en FB
automatico y en su lugar generar el texto listo para copiar y pegar, que el
bot te manda por Telegram. Pierdes 2 minutos por item y no arriesgas la
cuenta. Con `modo.fb_activo: false` (default) hace exactamente eso.
"""
from __future__ import annotations

import random
import time

from app import fb_guard, tg
from app.config import fb, p
from app.db import ex, q
from app.jobs import envuelto


def _copia_manual(pend: list[dict]) -> str:
    for pub in pend[:3]:
        tg.PUBLICADOR.enviar(
            f"📋 <b>listo para pegar en FB</b>\n\n<b>{pub['titulo']}</b>\n"
            f"Precio: {int(pub['precio'] or 0):,}\n\n{(pub['descripcion'] or '')[:900]}".replace(",", "."),
            tg.teclado([[("✅ Ya lo pegue", f"fb_hecho:{pub['id']}")]]),
        )
    return f"{min(3, len(pend))} textos enviados para copiar a mano"


def correr() -> str:
    pend = q(
        """SELECT p.id, p.titulo, p.descripcion, p.precio, inv.fotos, inv.id AS inv_id
           FROM publicacion p JOIN inventario inv ON inv.id = p.inventario
           WHERE p.marketplace='fb' AND p.estado='borrador'
           ORDER BY p.fecha LIMIT %s""",
        (int(p("ritmo.fb_publicaciones_dia", 5)),),
    )
    if not pend:
        return "nada pendiente para FB"
    if not p("modo.fb_activo", False):
        return _copia_manual([dict(x) for x in pend])

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "falta playwright: docker compose --profile scraper build"

    perfil = fb_guard.perfil_listo()
    if not perfil:
        return "falta el login de FB: correr bin/login-fb.sh una vez"

    ok = 0
    lo, hi = p("ritmo.fb_pausa_min", [3, 5])
    # Un solo Chrome por vez sobre el perfil (ver fb_guard).
    if not fb_guard.tomar_turno("publish_fb"):
        return "otro job de FB tiene el navegador; se hace en la proxima"
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(perfil), headless=False, viewport={"width": 1366, "height": 900},
            locale="es-CL", timezone_id="America/Santiago",
        )
        try:
            # Antes de escribir una sola letra: ¿es la cuenta desechable?
            permitido, motivo = fb_guard.verificar_o_avisar(ctx, "publish_fb")
            if not permitido:
                return f"candado de cuenta: {motivo}"

            pag = ctx.new_page()
            for i, pub in enumerate(pend):
                try:
                    pag.goto(fb("publicar.url"), wait_until="domcontentloaded", timeout=60000)
                    pag.wait_for_timeout(random.randint(3000, 6000))
                    # FB cambia el DOM seguido: si algo no aparece, se aborta este
                    # item y se avisa, en vez de dejar una publicacion a medias.
                    # Los selectores estan en config/facebook.yml, no aca.
                    pag.set_input_files(fb("publicar.input_fotos"),
                                        [f for f in (pub["fotos"] or [])[:10]])
                    pag.wait_for_timeout(random.randint(2000, 4000))
                    pag.get_by_label(fb("publicar.label_titulo")).fill(pub["titulo"][:99])
                    pag.get_by_label(fb("publicar.label_precio")).fill(str(int(pub["precio"] or 0)))
                    pag.get_by_label(fb("publicar.label_descripcion")).fill(
                        (pub["descripcion"] or "")[:4000])
                    pag.wait_for_timeout(random.randint(2000, 4000))
                    pag.get_by_role("button", name=fb("publicar.boton_siguiente")).click()
                    pag.wait_for_timeout(random.randint(2500, 5000))
                    pag.get_by_role("button", name=fb("publicar.boton_publicar")).click()
                    pag.wait_for_timeout(5000)
                    ex("UPDATE publicacion SET estado='activa', fecha=now() WHERE id=%s", (pub["id"],))
                    ok += 1
                except Exception as e:
                    tg.PUBLICADOR.enviar(f"⚠️ FB fallo con «{pub['titulo'][:60]}»: {str(e)[:200]}\n"
                              f"El DOM de FB cambio o pidio verificacion. Revisa a mano.\n"
                              f"Los selectores se arreglan en config/facebook.yml")
                if i < len(pend) - 1:
                    time.sleep(random.uniform(lo * 60, hi * 60))
        finally:
            ctx.close()
            fb_guard.soltar_turno("publish_fb")
    return f"{ok}/{len(pend)} publicados en FB"


if __name__ == "__main__":
    print(envuelto("publish_fb", correr))
