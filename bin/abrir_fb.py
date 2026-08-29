#!/usr/bin/env python3
"""Abre Facebook en una ventana que tu ves por el navegador, y guarda la sesion.

Corre DENTRO del contenedor `navegador` (Dockerfile.vnc). Tu no lo llamas
directo: lo llama `bin/login-fb.sh`.

Lo que hace:
  1. abre un Chrome de verdad en la pantalla virtual del contenedor
  2. espera a que entres a mano (hasta 20 min, por si hay codigo de verificacion)
  3. cuando aparece la cookie `c_user` — el numero de tu cuenta — la anota
  4. deja la cuenta SIN aprobar. La aprueba el `SI` que escribes en la terminal

Ese ultimo punto es el candado: aunque el login funcione, el sistema no toca la
cuenta hasta que tu confirmes en la terminal que es la desechable. Ver
app/fb_guard.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

PERFIL = "/app/data/perfiles/facebook"
FICHA = Path(PERFIL) / "cuenta.json"
ESPERA_MAX_SEG = 1200   # 20 min: FB suele pedir codigo por mail o SMS


def main() -> int:
    Path(PERFIL).mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            PERFIL, headless=False,
            viewport={"width": 1360, "height": 820},
            locale="es-CL", timezone_id="America/Santiago",
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )
        pag = ctx.pages[0] if ctx.pages else ctx.new_page()
        pag.goto("https://www.facebook.com/login")

        print("\n" + "=" * 62)
        print("  Abre esto en TU navegador:   http://127.0.0.1:6080/vnc.html")
        print("  Boton 'Connect'. Ahi ves el Chrome del servidor.")
        print("  Entra a Facebook a mano CON LA CUENTA DESECHABLE.")
        print("=" * 62 + "\n")

        uid = None
        for i in range(ESPERA_MAX_SEG):
            for c in ctx.cookies("https://www.facebook.com"):
                if c.get("name") == "c_user" and c.get("value"):
                    uid = str(c["value"])
                    break
            if uid:
                break
            if i and i % 60 == 0:
                print(f"  esperando... ({i // 60} min)")
            pag.wait_for_timeout(1000)

        nombre = "(no se pudo leer el nombre)"
        if uid:
            try:
                pag.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=30000)
                pag.wait_for_timeout(2500)
                nombre = (pag.title() or "").split("|")[0].strip() or nombre
            except Exception:
                pass
            # Queda anotada SIN aprobar, a proposito.
            FICHA.write_text(json.dumps(
                {"id": uid, "nombre": nombre, "aprobada": False}, ensure_ascii=False, indent=2),
                encoding="utf-8")
        ctx.close()

    if not uid:
        print("SIN_SESION — no llegaste a entrar. No se guardo nada.")
        return 1
    print(f"\nCUENTA_ID={uid}\nCUENTA_NOMBRE={nombre}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
