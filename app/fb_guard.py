"""Candado de cuenta de Facebook. La cuenta PERSONAL nunca se automatiza.

Regla, en una linea: el sistema solo puede tocar la cuenta que aprobaste a
mano una vez con `bin/login-fb.sh`. Cualquier otra sesion -> aborta y avisa.

Como sabe que cuenta es: lee la cookie `c_user`, que es el numero de cuenta de
Facebook. NO mira el nombre en pantalla a proposito — FB cambia el diseño cada
pocas semanas y un candado que depende del diseño es un candado que se abre
solo el dia que lo cambian. La cookie no cambia.

Falla cerrada: si no puede saber con certeza que cuenta esta abierta, no hace
nada. Preferimos perder un ciclo de publicaciones a publicar desde tu perfil.
"""
from __future__ import annotations

import json
from pathlib import Path

import time

import redis

from app import tg
from app.config import DATA, REDIS_URL, p

_R = redis.from_url(REDIS_URL, decode_responses=True)
LOCK = "cazador:fb_navegador"

PERFIL = DATA / "perfiles" / "facebook"
FICHA = PERFIL / "cuenta.json"


# --------------------------------------------------------------- ficha
def registrada() -> dict | None:
    """La cuenta que aprobaste, o None si nunca aprobaste ninguna."""
    try:
        return json.loads(FICHA.read_text(encoding="utf-8"))
    except Exception:
        return None


def registrar(uid: str, nombre: str, aprobada: bool) -> None:
    PERFIL.mkdir(parents=True, exist_ok=True)
    FICHA.write_text(
        json.dumps({"id": str(uid), "nombre": nombre, "aprobada": bool(aprobada)},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def id_en_sesion(ctx) -> str | None:
    """Numero de cuenta de la sesion abierta en el navegador, o None."""
    try:
        for c in ctx.cookies("https://www.facebook.com"):
            if c.get("name") == "c_user" and c.get("value"):
                return str(c["value"])
    except Exception:
        return None
    return None


# --------------------------------------------------------------- candado
def verificar(ctx) -> tuple[bool, str]:
    """(permitido, motivo). Motivo es texto listo para job_log y Telegram."""
    prohibidas = {str(x) for x in (p("facebook.cuentas_prohibidas", []) or [])}

    reg = registrada()
    if not reg or not reg.get("aprobada") or not reg.get("id"):
        return False, ("no hay cuenta de FB aprobada. Corre bin/login-fb.sh y "
                       "confirma que es la cuenta desechable, no la tuya")

    if str(reg["id"]) in prohibidas:
        return False, (f"la cuenta aprobada ({reg['id']}) esta en la lista de "
                       "cuentas PERSONALES de policy.yml. No se toca")

    vivo = id_en_sesion(ctx)
    if not vivo:
        return False, "no hay sesion de FB abierta en el perfil (cookie c_user vacia)"

    if vivo in prohibidas:
        return False, (f"el navegador esta con una cuenta PERSONAL ({vivo}). "
                       "Abortado sin tocar nada")

    if vivo != str(reg["id"]):
        return False, (f"la sesion cambio de cuenta: aprobada {reg['id']}, "
                       f"abierta {vivo}. Abortado")

    return True, vivo


def verificar_o_avisar(ctx, job: str) -> tuple[bool, str]:
    """Igual que verificar(), pero un rechazo te llega al Telegram."""
    ok, motivo = verificar(ctx)
    if not ok:
        tg.PUBLICADOR.enviar(f"🔒 <b>{job}</b> abortado por el candado de cuenta\n\n{motivo}")
    return ok, motivo


def perfil_listo() -> Path | None:
    """Ruta del perfil de navegador, o None si nunca se hizo el login."""
    return PERFIL if PERFIL.exists() else None



# ------------------------------------------------------- turno del navegador
# Un solo Chrome por vez sobre el perfil de Facebook.
#
# Un perfil de navegador tiene un lock de archivos: si dos procesos lo abren a
# la vez, el segundo se queda esperando y muere con
# `launch_persistent_context: Timeout 180000ms exceeded`. Peor todavia, el
# contexto a medio abrir devuelve cookies vacias y el candado de cuenta cree
# que perdiste la sesion — un falso negativo que apaga el ciclo entero.
#
# Paso el 28-ago con reply_fb y fetch_fb corriendo juntos. Los jobs estan
# escalonados de a 3 min, pero una pasada lenta alcanza para que se pisen.
def tomar_turno(job: str, espera_seg: int = 240) -> bool:
    limite = time.monotonic() + espera_seg
    while True:
        # NX + EX: se toma solo si esta libre, y caduca solo a los 20 min por
        # si un proceso muere sin devolverlo.
        if _R.set(LOCK, job, nx=True, ex=1200):
            return True
        if time.monotonic() >= limite:
            return False
        time.sleep(5)


def soltar_turno(job: str) -> None:
    # Solo lo suelta quien lo tomo: si ya caduco y lo tiene otro, no se le
    # quita de las manos.
    try:
        if _R.get(LOCK) == job:
            _R.delete(LOCK)
    except Exception:
        pass
