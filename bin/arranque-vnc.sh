#!/bin/bash
# Arranca la pantalla virtual y la transmite como pagina web.
#
# Va como archivo aparte y no como heredoc dentro del Dockerfile: el `COPY
# <<'EOF'` solo funciona con BuildKit, y el Docker de el servidor usa el
# constructor clasico. Fallaba con "COPY failed: no source files were
# specified" (28-ago).
#
#   Xvfb        una pantalla que no existe fisicamente
#   openbox     el marco de las ventanas (sin esto Chrome sale sin bordes
#               y no lo puedes mover ni cerrar)
#   x11vnc      transmite esa pantalla por VNC en el 5900
#   websockify  convierte el VNC en una pagina web en el 6080
set -e

Xvfb :99 -screen 0 1440x900x24 -nolisten tcp &
sleep 2
openbox &
# -localhost y el 127.0.0.1 del websockify NO son decoracion: el contenedor
# corre en red de host, asi que sin eso el VNC quedaria escuchando en todas
# las interfaces — LAN y tailnet incluidos — y sin contraseña.
x11vnc -display :99 -forever -shared -nopw -quiet -localhost -rfbport 5900 &
websockify --web=/usr/share/novnc 127.0.0.1:6080 localhost:5900 &
sleep 2

echo "listo: abre http://127.0.0.1:6080/vnc.html en TU navegador"
exec "$@"
