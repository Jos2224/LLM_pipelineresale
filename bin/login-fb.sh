#!/usr/bin/env bash
# Login de Facebook UNA sola vez, viendo el navegador desde TU PC.
#
# NO usa `ssh -X`: en el servidor eso no funciona (falta xauth, y el drop-in
# 10-server-hardening.conf pone X11Forwarding no, que gana por ser el primero).
# Arreglarlo pediria root. Esto no lo pide.
#
# En vez de eso el navegador corre adentro del contenedor sobre una pantalla
# virtual, y se sirve como pagina web en 127.0.0.1:6080 del servidor. Tu la ves
# por un tunel SSH, desde el navegador que ya tienes, en cualquier sistema.
#
# ---------------------------------------------------------------------------
# COMO SE USA — dos terminales
#
#   Terminal 1 (en el servidor):   ~/cazador/bin/login-fb.sh
#   Terminal 2 (en TU PC):         ssh -L 6080:127.0.0.1:6080 remato
#   Navegador (en TU PC):          http://127.0.0.1:6080/vnc.html  -> Connect
#
# ---------------------------------------------------------------------------
# CANDADO: al final te muestra CON QUE CUENTA quedaste y te pregunta si es la
# desechable. Si contestas cualquier cosa que no sea SI, el perfil se borra y
# no queda ninguna sesion guardada. Nada se automatiza sin ese SI.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p data/perfiles/facebook

cat <<'FIN'

──────────────────────────────────────────────────────────────
 Abriendo el navegador en el servidor.

 En TU PC, en otra terminal:
     ssh -L 6080:127.0.0.1:6080 remato

 Y en tu navegador:
     http://127.0.0.1:6080/vnc.html      (boton "Connect")

 Ahi vas a ver el Chrome del servidor. Entra a Facebook a mano
 CON LA CUENTA DESECHABLE, nunca con la tuya.
──────────────────────────────────────────────────────────────

FIN

docker compose --profile vnc run --rm --service-ports navegador || true

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
  # La aprobacion se escribe DESDE EL CONTENEDOR, no desde aca. El navegador
  # corre como root y deja cuenta.json con dueño root; tu usuario no lo puede
  # tocar y esto reventaba con "Permission denied" justo en el ultimo paso,
  # despues de que ya habias entrado a Facebook (paso el 28-ago).
  docker compose --profile vnc run --rm --entrypoint python navegador -c "
import json
from pathlib import Path
f = Path('/app/data/perfiles/facebook/cuenta.json')
d = json.loads(f.read_text())
d['aprobada'] = True
f.write_text(json.dumps(d, ensure_ascii=False, indent=2))
print('aprobada:', d)
" 2>&1 | grep -vE "Container|^listo:|Listen on|Web server|Web root|SSL|proxying"
  echo
  echo "Aprobada. Perfil guardado en data/perfiles/facebook"
  echo
  echo "Ultimo paso recomendado: pon tu cuenta PERSONAL en la lista negra."
  echo "  config/policy.yml -> facebook.cuentas_prohibidas: [\"tu_numero\"]"
  echo "Asi, aunque un dia el navegador quede con tu sesion, el bot aborta."
  echo
  echo "Y para encender Facebook de verdad:"
  echo "  config/policy.yml -> modo.fb_activo: true"
  echo "  ~/cazador/bin/cazador arriba"
else
  # Tambien desde el contenedor, y por lo mismo: el perfil es de root.
  docker compose --profile vnc run --rm --entrypoint python navegador -c "
import shutil; shutil.rmtree('/app/data/perfiles/facebook', ignore_errors=True)
print('perfil borrado')" >/dev/null 2>&1
  rm -rf data/perfiles/facebook 2>/dev/null || true
  echo
  echo "Borrado. No quedo ninguna sesion de Facebook guardada en el servidor."
  echo "Vuelve a correr esto entrando con la cuenta desechable."
  exit 1
fi
