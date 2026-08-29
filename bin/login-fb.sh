#!/usr/bin/env bash
# Login de Facebook UNA sola vez. Abre un Chrome real en tu pantalla por
# X11 forwarding (el servidor ya lo tiene habilitado), entras a mano, y el perfil
# queda guardado en data/perfiles/facebook. Despues nunca mas.
#
# Desde el notebook:
#   ssh -X remato
#   ~/cazador/bin/login-fb.sh
#
# CANDADO: al final te muestra CON QUE CUENTA quedaste y te pregunta si es la
# desechable. Si contestas cualquier cosa que no sea SI, el perfil se borra y
# no queda ninguna sesion guardada. Nada se automatiza sin ese SI.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/perfiles/facebook

docker compose --profile scraper run --rm \
  -e DISPLAY="${DISPLAY:?necesitas ssh -X}" \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$HOME/.Xauthority:/root/.Xauthority:ro" \
  scraper python - <<'PY'
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

FICHA = Path("/app/data/perfiles/facebook/cuenta.json")

with sync_playwright() as pw:
    ctx = pw.chromium.launch_persistent_context(
        "/app/data/perfiles/facebook", headless=False,
        viewport={"width": 1366, "height": 850},
        locale="es-CL", timezone_id="America/Santiago")
    pag = ctx.new_page()
    pag.goto("https://www.facebook.com/login")
    print("Entra a mano. Cuando la sesion este abierta, ESPERA sin cerrar nada.")

    # Se espera a que aparezca la cookie c_user: ese es el numero de cuenta.
    # Hasta 10 minutos, por si hay codigo de verificacion de por medio.
    uid = None
    for _ in range(600):
        for c in ctx.cookies("https://www.facebook.com"):
            if c.get("name") == "c_user" and c.get("value"):
                uid = str(c["value"])
                break
        if uid:
            break
        pag.wait_for_timeout(1000)

    nombre = "(no se pudo leer el nombre)"
    if uid:
        try:
            pag.goto("https://www.facebook.com/me", wait_until="domcontentloaded", timeout=30000)
            pag.wait_for_timeout(2500)
            nombre = (pag.title() or "").split("|")[0].strip() or nombre
        except Exception:
            pass
        # Queda anotada SIN aprobar. La aprueba el SI de afuera, nunca esto.
        FICHA.write_text(json.dumps(
            {"id": uid, "nombre": nombre, "aprobada": False}, ensure_ascii=False, indent=2),
            encoding="utf-8")

    ctx.close()

if not uid:
    print("SIN_SESION")
else:
    print(f"CUENTA_ID={uid}")
    print(f"CUENTA_NOMBRE={nombre}")
PY

ficha=data/perfiles/facebook/cuenta.json
if [ ! -f "$ficha" ]; then
  echo
  echo "No quedo ninguna sesion abierta. No se guardo nada."
  exit 1
fi

uid=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["id"])' "$ficha")
nombre=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["nombre"])' "$ficha")

cat <<FIN

────────────────────────────────────────────────
Quedaste dentro con:

   nombre : $nombre
   cuenta : $uid

Esta CUENTA es la que el bot va a usar para publicar y para contestar
mensajes en Marketplace, sola, sin preguntarte cada vez.

Si esta es tu cuenta PERSONAL, responde cualquier cosa menos SI:
el perfil se borra y no queda sesion guardada.
────────────────────────────────────────────────
FIN

read -r -p "¿Es la cuenta DESECHABLE, aparte de la tuya? escribe SI: " r

if [ "$r" = "SI" ]; then
  python3 - "$ficha" <<'PY'
import json, sys
p = sys.argv[1]
d = json.load(open(p))
d["aprobada"] = True
json.dump(d, open(p, "w"), ensure_ascii=False, indent=2)
PY
  echo
  echo "Aprobada. Perfil guardado en data/perfiles/facebook"
  echo
  echo "Ultimo paso recomendado: pon tu cuenta PERSONAL en la lista negra."
  echo "  config/policy.yml -> facebook.cuentas_prohibidas: [\"tu_numero\"]"
  echo "Asi, aunque un dia el navegador quede con tu sesion, el bot aborta."
else
  rm -rf data/perfiles/facebook
  echo
  echo "Borrado. No quedo ninguna sesion de Facebook guardada en el servidor."
  echo "Vuelve a correr esto entrando con la cuenta desechable."
  exit 1
fi
