"""Script 33 — contesta a compradores en Facebook Marketplace.

El gemelo de reply_bot (ML) del lado de Facebook, con dos diferencias que
importan:

  1. FB no tiene API. Todo pasa por el navegador con el perfil guardado, asi
     que ANTES de tocar nada corre el candado de cuenta (app/fb_guard.py): si
     la sesion no es la cuenta desechable que aprobaste, aborta y te avisa.
  2. FB no distingue "pregunta" de "oferta" como ML. Se clasifica leyendo el
     texto con app/parseo.py — las mismas reglas que ya aciertan 14/14 con
     "350 lucas" y "1 palo".

Quien decide que:

  la plata          -> app/pricing.decidir_oferta, reglas puras, sin LLM
  responder dudas   -> Ollama, y SOLO con datos de la ficha. Si no esta en la
                       ficha responde NO_SE y el mensaje te llega a ti.

Con `facebook.responder_auto: false` (el default) no manda nada solo: deja el
texto redactado y te lo ofrece por Telegram con boton [Enviar]. Ese boton
empuja el id a Redis y la proxima pasada de este script lo manda de verdad.
"""
from __future__ import annotations

import hashlib
import random
import re
import time
from datetime import date

import redis

from app import fb_guard, llm, tg
from app.config import REDIS_URL, fb, p
from app.db import ex, q, q1
from app.jobs import envuelto
from app.parseo import leer_respuesta_vendedor
from app.pricing import decidir_oferta

R = redis.from_url(REDIS_URL, decode_responses=True)
COLA = "cazador:fb_enviar"          # ids de mensajes que aprobaste por boton
TOPE = "cazador:fb_resp:{}"         # contador diario de respuestas enviadas

PROMPT = """Responde la pregunta de un comprador en Facebook Marketplace Chile.

Ficha del producto (unica fuente de verdad):
{ficha}

Pregunta: {pregunta}

Reglas absolutas:
- Usa SOLO datos de la ficha. Si la ficha no lo dice, responde exactamente: NO_SE
- Maximo 2 frases, tuteo, sin emojis, sin saludos largos.
- Nunca prometas garantia, plazos de envio ni descuentos.

Responde solo el texto de la respuesta."""

PLANTILLA = {
    "aceptar": "Hola, acepto. Te lo dejo reservado, coordinamos la entrega.",
    "contraoferta": "Hola, gracias por la oferta. Te lo puedo dejar en ${monto}. Es mi ultimo precio.",
    "rechazar": "Hola, gracias, pero a ese precio no me da. El valor publicado ya esta ajustado.",
}


# ------------------------------------------------------------ selectores
def _buscar(scope, ruta: str):
    """Prueba los selectores de facebook.yml en orden y devuelve los que peguen.

    El YAML permite varios separados por coma justamente para esto: cuando FB
    cambia el diseño, el selector viejo deja de pegar y el nuevo salva el dia
    sin tocar codigo.
    """
    for sel in [s.strip() for s in (fb(ruta) or "").split(",") if s.strip()]:
        try:
            hallados = scope.query_selector_all(sel)
        except Exception:
            continue
        if hallados:
            return hallados
    return []


def _texto_de(elementos, tope: int) -> list[str]:
    salida = []
    for e in elementos[-tope:]:
        try:
            t = " ".join((e.inner_text() or "").split())
        except Exception:
            continue
        if t and len(t) > 1:
            salida.append(t)
    return salida


# ------------------------------------------------------- emparejar hilo
_RUIDO = {"de", "la", "el", "en", "con", "por", "para", "y", "a", "un", "una", "usado", "nuevo"}


def _tokens(t: str) -> set[str]:
    limpio = re.sub(r"[^a-z0-9áéíóúñ ]", " ", (t or "").lower())
    return {w for w in limpio.split() if len(w) > 1 and w not in _RUIDO}


def _parecido(a: str, b: str) -> float:
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(min(ta, tb, key=len))


def _publicacion_de(titulo_hilo: str, activas: list[dict]) -> dict | None:
    """Cual de tus publicaciones es. Si no esta claro, devuelve None y se
    escala: contestar la ficha equivocada es peor que no contestar."""
    minimo = float(p("facebook.parecido_minimo", 0.5))
    mejor, punta = None, 0.0
    for pub in activas:
        s = _parecido(titulo_hilo, pub["titulo"] or "")
        if s > punta:
            mejor, punta = pub, s
    return mejor if punta >= minimo else None


def _ficha(m: dict) -> str:
    return (
        f"Titulo: {m['titulo']}\n"
        f"Precio publicado: {int(m['precio'] or 0)} CLP\n"
        f"Condicion: {m['condicion']}\n"
        f"Specs: {m['specs'] or {}}\n"
        f"Stock: 1 unidad\n"
        f"Descripcion: {(m['descripcion'] or '')[:800]}"
    )


# ------------------------------------------------------------- borradores
def _borrador(pub_id: int, hilo: str, entrada_id: int, tipo: str, texto: str) -> int:
    fila = q1(
        """INSERT INTO mensaje (publicacion, direccion, tipo, texto, canal, hilo,
                                responde_a, respondido_por, estado)
           VALUES (%s,'sale',%s,%s,'fb',%s,%s,'bot','nuevo') RETURNING id""",
        (pub_id, tipo, texto, hilo, entrada_id),
    )
    return int(fila["id"])


def _escalar(salida_id: int, entrada_id: int, cabecera: str, texto: str) -> None:
    tg.PUBLICADOR.enviar(
        f"{cabecera}\n\n<b>respuesta lista:</b>\n{texto}",
        tg.teclado([[("📤 Enviar en FB", f"fb_enviar:{salida_id}"),
                     ("✏️ Yo respondo", f"msg_mio:{entrada_id}")]]),
    )
    ex("UPDATE mensaje SET estado='escalado' WHERE id=%s", (entrada_id,))


# ---------------------------------------------------------------- envio
def _cupo_hoy() -> int:
    usado = int(R.get(TOPE.format(date.today().isoformat())) or 0)
    return max(0, int(p("facebook.respuestas_dia", 20)) - usado)


def _marcar_enviado(salida_id: int) -> None:
    clave = TOPE.format(date.today().isoformat())
    R.incr(clave)
    R.expire(clave, 3 * 24 * 3600)
    ex("UPDATE mensaje SET estado='respondido' WHERE id=%s", (salida_id,))
    ex("""UPDATE mensaje SET estado='respondido'
          WHERE id = (SELECT responde_a FROM mensaje WHERE id=%s)""", (salida_id,))


def _mandar(pag, hilo: str, texto: str) -> bool:
    """Escribe en el hilo. Devuelve False si el diseño de FB no dio pie."""
    try:
        pag.goto(f"https://www.facebook.com/marketplace/t/{hilo}/",
                 wait_until="domcontentloaded", timeout=45000)
        pag.wait_for_timeout(random.randint(2500, 5000))
        cajas = _buscar(pag, "hilo.caja_texto")
        if not cajas:
            return False
        caja = cajas[-1]
        caja.click()
        # Tecleo con ritmo de persona: FB mide la velocidad de escritura.
        caja.type(texto, delay=random.randint(35, 90))
        pag.wait_for_timeout(random.randint(800, 2000))
        botones = _buscar(pag, "hilo.boton_enviar")
        if botones:
            botones[-1].click()
        else:
            pag.keyboard.press("Enter")
        pag.wait_for_timeout(random.randint(1500, 3000))
        return True
    except Exception:
        return False


def _drenar_cola(pag) -> int:
    """Manda los borradores que aprobaste apretando el boton en Telegram."""
    enviados = 0
    lo, hi = p("facebook.pausa_resp_seg", [45, 150])
    while _cupo_hoy() > 0:
        crudo = R.lpop(COLA)
        if crudo is None:
            break
        m = q1("SELECT id, texto, hilo FROM mensaje WHERE id=%s AND direccion='sale'", (int(crudo),))
        if not m or not m["hilo"]:
            continue
        if _mandar(pag, m["hilo"], m["texto"]):
            _marcar_enviado(m["id"])
            enviados += 1
            time.sleep(random.uniform(lo, hi))
        else:
            tg.PUBLICADOR.enviar(
                "⚠️ no pude escribir en el hilo de FB. El diseño cambio o pidio "
                "verificacion. El texto sigue guardado, revisa a mano:\n\n"
                f"{m['texto'][:500]}")
    return enviados


# ---------------------------------------------------------------- correr
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

    activas = [dict(x) for x in q(
        """SELECT p.id, p.titulo, p.descripcion, p.precio, inv.condicion,
                  inv.piso_precio, pc.specs
           FROM publicacion p
           JOIN inventario inv ON inv.id = p.inventario
           LEFT JOIN producto_canon pc ON pc.id = inv.producto
           WHERE p.marketplace='fb' AND p.estado IN ('activa','pausada')""")]

    leidos = nuevos = auto = esc = 0
    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(perfil), headless=True, viewport={"width": 1366, "height": 900},
            locale="es-CL", timezone_id="America/Santiago")
        try:
            ok, motivo = fb_guard.verificar_o_avisar(ctx, "reply_fb")
            if not ok:
                return f"candado de cuenta: {motivo}"

            pag = ctx.new_page()
            enviados_cola = _drenar_cola(pag)

            if not activas:
                return f"{enviados_cola} de la cola; sin publicaciones activas en FB"

            pag.goto(fb("inbox.url"), wait_until="domcontentloaded", timeout=45000)
            pag.wait_for_timeout(random.randint(3000, 6000))

            hilos, vistos = [], set()
            for a in _buscar(pag, "inbox.hilo_link"):
                href = (a.get_attribute("href") or "").split("?")[0]
                m = re.search(r"/marketplace/t/(\d+)", href)
                if m and m.group(1) not in vistos:
                    vistos.add(m.group(1))
                    hilos.append(m.group(1))
            if not hilos:
                return (f"{enviados_cola} de la cola; no encontre hilos. "
                        "Revisa inbox.hilo_link en config/facebook.yml")

            responder_auto = bool(p("facebook.responder_auto", False))
            lo, hi = p("facebook.pausa_resp_seg", [45, 150])

            for hilo in hilos[:int(p("facebook.hilos_por_ciclo", 8))]:
                pag.goto(f"https://www.facebook.com/marketplace/t/{hilo}/",
                         wait_until="domcontentloaded", timeout=45000)
                pag.wait_for_timeout(random.randint(2500, 5000))
                leidos += 1

                titulos = _texto_de(_buscar(pag, "hilo.producto_titulo"), 1)
                burbujas = _texto_de(_buscar(pag, "hilo.burbuja"),
                                     int(p("facebook.burbujas_leer", 12)))
                if not burbujas:
                    continue

                # Lo que ya esta en la base (mandado o leido) no es novedad.
                # Asi no hace falta adivinar en el DOM quien escribio cada
                # burbuja: si el texto salio de nosotros, ya esta guardado.
                previos = {x["texto"] for x in q(
                    "SELECT texto FROM mensaje WHERE hilo=%s", (hilo,)) if x["texto"]}
                entrantes = [b for b in burbujas if b not in previos]
                if not entrantes:
                    continue
                ultimo = entrantes[-1]

                pub = _publicacion_de(titulos[0] if titulos else "", activas)
                if not pub:
                    tg.PUBLICADOR.enviar(
                        "❓ mensaje en FB y no supe de que publicacion es\n"
                        f"producto en el hilo: «{(titulos[0] if titulos else '?')[:70]}»\n\n{ultimo[:400]}\n\n"
                        f"https://www.facebook.com/marketplace/t/{hilo}/")
                    continue

                lectura = leer_respuesta_vendedor(ultimo, float(pub["precio"] or 0))
                monto = lectura["precio"]
                fila = q1(
                    """INSERT INTO mensaje (publicacion, id_externo, direccion, tipo,
                                            texto, monto_oferta, canal, hilo, estado)
                       VALUES (%s,%s,'entra',%s,%s,%s,'fb',%s,'nuevo')
                       ON CONFLICT (id_externo, direccion) DO NOTHING
                       RETURNING id""",
                    (pub["id"], f"fb:{hilo}:{hashlib.sha1(ultimo.encode()).hexdigest()[:12]}",
                     "oferta" if monto else "pregunta", ultimo, monto, hilo),
                )
                if not fila:
                    continue
                entrada_id = int(fila["id"])
                nuevos += 1

                # ---- oferta: la decide el codigo, nunca el modelo ----
                if monto:
                    piso = float(pub["piso_precio"] or 0)
                    accion, contra = decidir_oferta(float(monto), piso)
                    if accion == "escalar":
                        tg.PUBLICADOR.enviar(
                            f"💬 oferta de {int(monto):,} en FB sobre «{pub['titulo'][:60]}» "
                            "y esa publicacion no tiene piso puesto. Dime tu.".replace(",", "."))
                        ex("UPDATE mensaje SET estado='escalado' WHERE id=%s", (entrada_id,))
                        esc += 1
                        continue
                    texto = PLANTILLA[accion].replace(
                        "${monto}", f"{int(contra or 0):,}".replace(",", "."))
                    cabecera = (f"💬 FB · oferta {int(monto):,} sobre piso {int(piso):,}\n"
                                f"«{pub['titulo'][:60]}»\nRegla dice: <b>{accion}</b>").replace(",", ".")
                    tipo = "oferta"
                # ---- pregunta: el modelo redacta, solo con la ficha ----
                else:
                    if not llm.vivo():
                        ex("UPDATE mensaje SET estado='escalado' WHERE id=%s", (entrada_id,))
                        esc += 1
                        continue
                    texto = llm.texto(PROMPT.format(ficha=_ficha(pub), pregunta=ultimo),
                                      modelo=llm.modelo_de("responder")).strip()
                    if not texto or "NO_SE" in texto.upper() or len(texto) > 400:
                        tg.PUBLICADOR.enviar(
                            f"❓ FB · pregunta que no supe contestar\n«{pub['titulo'][:60]}»\n\n{ultimo[:400]}",
                            tg.teclado([[("✏️ Responder yo", f"msg_mio:{entrada_id}")]]))
                        ex("UPDATE mensaje SET estado='escalado' WHERE id=%s", (entrada_id,))
                        esc += 1
                        continue
                    cabecera = f"❓ FB · «{pub['titulo'][:60]}»\n{ultimo[:300]}"
                    tipo = "pregunta"

                salida_id = _borrador(pub["id"], hilo, entrada_id, tipo, texto)

                if responder_auto and _cupo_hoy() > 0 and _mandar(pag, hilo, texto):
                    _marcar_enviado(salida_id)
                    auto += 1
                    time.sleep(random.uniform(lo, hi))
                else:
                    _escalar(salida_id, entrada_id, cabecera, texto)
                    esc += 1
        finally:
            ctx.close()

    return (f"{leidos} hilos leidos, {nuevos} mensajes nuevos, "
            f"{auto} contestados solos, {esc} escalados, {enviados_cola} de la cola")


if __name__ == "__main__":
    print(envuelto("reply_fb", correr))
