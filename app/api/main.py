"""Script 30 — la unica puerta que da a internet.

Dos endpoints y nada mas:
  GET  /oauth/callback  el unico click que tienes que dar para conectar ML
  POST /ml/webhook      ML avisa cuando hay pregunta, mensaje o venta

El webhook contesta 200 en menos de 500 ms (ML corta ahi y reintenta). Por eso
no procesa nada: empuja a Redis y el worker se hace cargo.

Se publica por Tailscale Funnel bajo el path /cazador. Como Funnel puede o no
quitar ese prefijo segun version, todas las rutas estan montadas dos veces.
"""
from __future__ import annotations

import json

import redis
from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app import ml_api
from app.config import ML_CLIENT_ID, REDIS_URL
from app.db import q1

R = redis.from_url(REDIS_URL, decode_responses=True)
COLA = "cazador:eventos"

router = APIRouter()


def _pagina(titulo: str, cuerpo: str) -> HTMLResponse:
    return HTMLResponse(
        f"<html><head><meta charset=utf-8><title>cazador</title></head>"
        f"<body style='font-family:system-ui;max-width:38rem;margin:4rem auto;line-height:1.6'>"
        f"<h2>{titulo}</h2><p>{cuerpo}</p></body></html>"
    )


@router.get("/salud")
def salud():
    try:
        fila = q1("SELECT ml_user_id, expira_en FROM oauth_ml WHERE id = 1")
        return {"ok": True, "ml_conectado": bool(fila and fila["ml_user_id"]), "cola": R.llen(COLA)}
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=500)


@router.get("/oauth/login")
def oauth_login():
    """Abre esto en el navegador y ML te devuelve aca ya conectado."""
    if not ML_CLIENT_ID:
        return _pagina("Falta configurar", "No hay ML_CLIENT_ID en .env todavia.")
    return HTMLResponse(f'<meta http-equiv="refresh" content="0;url={ml_api.url_login()}">')


@router.get("/oauth/callback")
def oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error or not code:
        return _pagina("No se pudo conectar", f"MercadoLibre respondio: {error or 'sin codigo'}")
    if not ml_api.verificar_state(state):
        return _pagina("Link vencido o adulterado",
                       "Ese link ya se uso o no salio de aca. Pide uno nuevo con /conectar.")
    try:
        tok = ml_api.canjear_codigo(code)
    except Exception as e:
        return _pagina("Fallo el canje", str(e)[:400])
    return _pagina("Listo ✅", f"Cuenta {tok.get('user_id')} conectada. Ya puedes cerrar esta pestaña.")


@router.post("/ml/webhook")
async def ml_webhook(req: Request):
    """Contestar rapido es obligatorio: ML corta a los 500 ms."""
    try:
        cuerpo = await req.json()
    except Exception:
        return JSONResponse({"ok": True})
    try:
        R.rpush(COLA, json.dumps(cuerpo))
        R.ltrim(COLA, -5000, -1)
    except Exception:
        pass
    return JSONResponse({"ok": True})


app = FastAPI(title="cazador", docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(router)
app.include_router(router, prefix="/cazador")
