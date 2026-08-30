"""Cliente de la API oficial de MercadoLibre.

Por que API y no scraping: ML bloquea scrapers y te puede cerrar la cuenta.
La API es gratis, te da precio, fotos, categoria y vendidos, y ademas te
manda notificaciones push cuando alguien pregunta o compra.

El token dura 6 horas y se renueva solo con el refresh token. El refresh
token dura 6 meses: cuando quedan 7 dias el bot te avisa por Telegram.
"""
from __future__ import annotations

import base64
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config import ML_CLIENT_ID, ML_CLIENT_SECRET, ML_REDIRECT_URI, ML_SITE
from app.db import ex, kv_get, kv_set, q1

BASE = "https://api.mercadolibre.com"
AUTH = "https://auth.mercadolibre.cl/authorization"

# `offline_access` es lo que hace que ML entregue el refresh_token. Sin el, el
# login funciona igual, dura 6 horas y despues se muere solo — que es
# exactamente lo que paso el 29-ago: conectado a las 22:25, muerto a las 04:20,
# y nadie se entero hasta la mañana.
#
# Va en DOS lados y hacen falta los dos:
#   1. aca, en el link de login (esto)
#   2. marcado en la app, en developers.mercadolibre.cl
# Marcarlo solo en el panel no alcanza si el link no lo pide, y pedirlo en el
# link no sirve si la app no lo tiene permitido. Por eso `canjear_codigo`
# revisa que el refresh_token haya llegado de verdad y avisa fuerte si no.
SCOPE = "offline_access%20read%20write"


class SinToken(Exception):
    pass


class RateLimit(Exception):
    pass


# ---------------------------------------------------------------- OAuth
def url_login() -> str:
    """Genera el link de login con `state` y PKCE, los dos de un solo uso.

    `state`: sin esto, cualquiera que logre que tu navegador visite el callback
    con SU codigo te deja el sistema conectado a la cuenta de ML de otro, y el
    bot empieza a publicar y negociar ahi.

    `PKCE`: la app tiene "Requiere PKCE" encendido en el panel de ML, asi que
    el canje del codigo pide un `code_verifier`. Sin el, ML responde
    400 "code_verifier is a required parameter" — y no lo dice en el redirect,
    lo dice recien al canjear, que es donde se descubrio (28-ago).

    Como funciona, en simple: se inventa un secreto (`verifier`), se manda su
    HUELLA (`challenge`) al pedir el login, y el secreto entero recien al
    canjear. Asi, aunque alguien intercepte el codigo en el camino, no puede
    canjearlo: le falta el secreto, que nunca viajo por el navegador.
    """
    estado = secrets.token_urlsafe(24)
    verifier = secrets.token_urlsafe(64)[:96]     # ML acepta 43-128 caracteres
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    kv_set("oauth_state", estado)
    kv_set("oauth_verifier", verifier)
    return (
        f"{AUTH}?response_type=code&client_id={ML_CLIENT_ID}"
        f"&redirect_uri={ML_REDIRECT_URI}&state={estado}"
        f"&scope={SCOPE}"
        f"&code_challenge={challenge}&code_challenge_method=S256"
    )


def verificar_state(state: str | None) -> bool:
    esperado = kv_get("oauth_state")
    if not esperado:
        return False
    ok = bool(state) and secrets.compare_digest(str(state), str(esperado))
    if ok:
        kv_set("oauth_state", "")   # un solo uso
    return ok


def _guardar(tok: dict) -> None:
    expira = datetime.now(timezone.utc) + timedelta(seconds=int(tok.get("expires_in", 21600)) - 300)
    ex(
        """INSERT INTO oauth_ml (id, ml_user_id, access_token, refresh_token, expira_en, actualizado)
           VALUES (1, %s, %s, %s, %s, now())
           ON CONFLICT (id) DO UPDATE SET
             ml_user_id = EXCLUDED.ml_user_id,
             access_token = EXCLUDED.access_token,
             refresh_token = COALESCE(EXCLUDED.refresh_token, oauth_ml.refresh_token),
             expira_en = EXCLUDED.expira_en,
             actualizado = now()""",
        (tok.get("user_id"), tok["access_token"], tok.get("refresh_token"), expira),
    )
    # Los permisos que ML concedio DE VERDAD. Es la unica forma de saber si
    # `offline_access` quedo o no: el link se puede pedir igual y ML lo ignora
    # en silencio si la app no lo tiene marcado.
    kv_set("oauth_scope", str(tok.get("scope", "")))


def canjear_codigo(code: str) -> dict:
    """Paso final del login: el codigo de la URL se cambia por tokens."""
    with httpx.Client(timeout=30.0) as c:
        cuerpo = {
            "grant_type": "authorization_code",
            "client_id": ML_CLIENT_ID,
            "client_secret": ML_CLIENT_SECRET,
            "code": code,
            "redirect_uri": ML_REDIRECT_URI,
        }
        # El secreto de PKCE que se guardo al generar el link.
        verifier = kv_get("oauth_verifier")
        if verifier:
            cuerpo["code_verifier"] = str(verifier)
        r = c.post(f"{BASE}/oauth/token", data=cuerpo,
                   headers={"Accept": "application/json"})
        if r.status_code >= 400:
            # ML explica el motivo en el cuerpo y raise_for_status lo tira a la
            # basura. Sin esto, un 400 no decia nada y habia que reproducirlo a
            # mano para enterarse de que faltaba el code_verifier.
            raise RuntimeError(f"ML {r.status_code}: {r.text[:200]}")
        tok = r.json()
    kv_set("oauth_verifier", "")   # un solo uso, igual que el state
    _guardar(tok)
    return tok


def aviso_sin_refresh(tok: dict) -> str | None:
    """Texto de alarma si el login va a morir en 6 h, o None si quedo bien.

    Se separa del canje a proposito: el canje ya guardo el token y la conexion
    SIRVE — por 6 horas. Esto no es un error que aborte, es un aviso que tiene
    que llegar AHORA y no cuando el token se muera de madrugada.
    """
    if tok.get("refresh_token"):
        return None
    return (
        "⚠️ <b>Conectado, pero se va a morir en 6 horas.</b>\n\n"
        "ML no entrego <code>refresh_token</code>, asi que no puedo renovar solo.\n"
        f"Permisos que dio: <code>{tok.get('scope') or '(ninguno)'}</code>\n\n"
        "Arreglo, una sola vez:\n"
        "1. entra a <b>developers.mercadolibre.cl</b> → tu app <b>Cazador</b> → Editar\n"
        "2. en <b>Scopes</b> marca <b>offline_access</b> (ademas de read y write)\n"
        "3. guarda, y aca manda <b>/conectar</b> otra vez\n\n"
        "Cuando quede bien, este aviso no aparece."
    )


def _refrescar(refresh_token: str) -> str:
    with httpx.Client(timeout=30.0) as c:
        r = c.post(f"{BASE}/oauth/token", data={
            "grant_type": "refresh_token",
            "client_id": ML_CLIENT_ID,
            "client_secret": ML_CLIENT_SECRET,
            "refresh_token": refresh_token,
        }, headers={"Accept": "application/json"})
        # Igual que en el canje: `raise_for_status` tira el cuerpo a la basura y
        # deja un "400" pelado que no dice nada. ML explica el motivo ahi.
        if r.status_code >= 400:
            raise SinToken(f"no pude renovar el token (ML {r.status_code}: "
                           f"{r.text[:160]}). Manda /conectar de nuevo")
        tok = r.json()
    _guardar(tok)
    return tok["access_token"]


def token() -> str:
    fila = q1("SELECT access_token, refresh_token, expira_en FROM oauth_ml WHERE id = 1")
    if not fila or not fila["access_token"]:
        raise SinToken("no hay login de MercadoLibre todavia")
    if fila["expira_en"] and fila["expira_en"] > datetime.now(timezone.utc):
        return fila["access_token"]
    if not fila["refresh_token"]:
        raise SinToken(
            "el token vencio y no hay refresh_token: a la app de ML le falta "
            "el permiso offline_access. Marcalo en developers.mercadolibre.cl "
            "y manda /conectar")
    return _refrescar(fila["refresh_token"])


def usuario_id() -> int | None:
    fila = q1("SELECT ml_user_id FROM oauth_ml WHERE id = 1")
    return fila["ml_user_id"] if fila else None


def dias_para_vencer() -> int | None:
    fila = q1("SELECT actualizado FROM oauth_ml WHERE id = 1")
    if not fila or not fila["actualizado"]:
        return None
    # El refresh token vive 6 meses desde el ultimo canje.
    vence = fila["actualizado"] + timedelta(days=180)
    return (vence - datetime.now(timezone.utc)).days


# ---------------------------------------------------------------- HTTP
@retry(retry=retry_if_exception_type(RateLimit),
       wait=wait_exponential(multiplier=2, min=2, max=60),
       stop=stop_after_attempt(5), reraise=True)
def _get(ruta: str, params: dict | None = None, auth: bool = True) -> dict:
    cab = {"Accept": "application/json"}
    if auth:
        cab["Authorization"] = f"Bearer {token()}"
    with httpx.Client(timeout=30.0) as c:
        r = c.get(f"{BASE}{ruta}", params=params or {}, headers=cab)
    if r.status_code == 429:
        raise RateLimit(ruta)
    r.raise_for_status()
    return r.json()


def _post(ruta: str, cuerpo: dict) -> dict:
    with httpx.Client(timeout=60.0) as c:
        r = c.post(f"{BASE}{ruta}", json=cuerpo,
                   headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    if r.status_code == 429:
        raise RateLimit(ruta)
    r.raise_for_status()
    return r.json()


def _put(ruta: str, cuerpo: dict) -> dict:
    with httpx.Client(timeout=60.0) as c:
        r = c.put(f"{BASE}{ruta}", json=cuerpo,
                  headers={"Authorization": f"Bearer {token()}", "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json()


# ---------------------------------------------------------------- lectura
def buscar(q_texto: str, limite: int = 50, offset: int = 0, condicion: str = "all") -> dict:
    params = {"q": q_texto, "limit": min(limite, 50), "offset": offset}
    if condicion in ("new", "used"):
        params["ITEM_CONDITION"] = "2230284" if condicion == "new" else "2230581"
    return _get(f"/sites/{ML_SITE}/search", params)


def item(item_id: str) -> dict:
    return _get(f"/items/{item_id}")


def items(ids: list[str]) -> list[dict]:
    salida = []
    for i in range(0, len(ids), 20):
        lote = _get("/items", {"ids": ",".join(ids[i:i + 20])})
        salida.extend(x.get("body", {}) for x in lote if x.get("code") == 200)
        time.sleep(0.3)
    return salida


def mis_items(estado: str = "active") -> list[str]:
    """Todas mis publicaciones. Usa scan porque offset muere en 1000."""
    uid = usuario_id()
    if not uid:
        raise SinToken("falta el user_id de ML")
    ids: list[str] = []
    scroll = None
    while True:
        params = {"search_type": "scan", "limit": 100, "status": estado}
        if scroll:
            params["scroll_id"] = scroll
        d = _get(f"/users/{uid}/items/search", params)
        nuevos = d.get("results", [])
        ids.extend(nuevos)
        scroll = d.get("scroll_id")
        if not nuevos or not scroll:
            break
        time.sleep(0.3)
    return ids


def categoria_de(titulo: str) -> dict | None:
    d = _get(f"/sites/{ML_SITE}/domain_discovery/search", {"q": titulo}, auth=False)
    return d[0] if isinstance(d, list) and d else None


def atributos_categoria(cat_id: str) -> list[dict]:
    return _get(f"/categories/{cat_id}/attributes", auth=False)


def preguntas(item_id: str) -> list[dict]:
    d = _get("/questions/search", {"item": item_id, "api_version": 4})
    return d.get("questions", [])


def recurso(path: str) -> dict:
    """El webhook manda un path relativo; esto lo resuelve."""
    return _get(path)


# ---------------------------------------------------------------- escritura
def publicar(cuerpo: dict) -> dict:
    return _post("/items", cuerpo)


def actualizar_item(item_id: str, cuerpo: dict) -> dict:
    return _put(f"/items/{item_id}", cuerpo)


def poner_descripcion(item_id: str, texto_desc: str) -> dict:
    return _post(f"/items/{item_id}/description", {"plain_text": texto_desc})


def responder_pregunta(question_id: str, texto_resp: str) -> dict:
    return _post("/answers", {"question_id": question_id, "text": texto_resp})


def preguntar(item_id: str, texto_preg: str) -> dict:
    """Le escribe al vendedor en SU publicacion. Asi se negocia en ML Chile.

    ML rechaza preguntas con telefono, mail o links, y no deja preguntar en
    tus propios items. El texto va limpio desde negociar_compra.py.
    """
    return _post("/questions", {"item_id": item_id, "text": texto_preg[:1990]})


def pregunta(question_id: str) -> dict:
    """Trae una pregunta y, si ya contestaron, su respuesta."""
    return _get(f"/questions/{question_id}", {"api_version": 4})


def subir_foto_url(url_foto: str) -> dict:
    return _post("/pictures/items/upload", {"source": url_foto})


def subir_foto_archivo(ruta: str) -> str:
    """Sube una foto local (las que mandas por Telegram) y devuelve su id."""
    with open(ruta, "rb") as f:
        with httpx.Client(timeout=120.0) as c:
            r = c.post(
                f"{BASE}/pictures/items/upload",
                headers={"Authorization": f"Bearer {token()}"},
                files={"file": (ruta.split("/")[-1], f, "image/jpeg")},
            )
    r.raise_for_status()
    return r.json()["id"]
