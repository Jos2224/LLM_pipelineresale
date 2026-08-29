"""Script 34 — regatea la COMPRA por Messenger, en Facebook Marketplace.

El gemelo de negociar_compra.py (que usa preguntas de MercadoLibre) para el
lado de Facebook. Misma escalera, mismos topes, mismo techo inviolable — lo
unico que cambia es por donde viaja el mensaje.

  ronda 0   "Hola, ¿sigue disponible?"        <- solo saluda, no ofrece nada
  (el vendedor responde)
  ronda 1   ofrece el OBJETIVO      (V_liq / 2.5)
  ronda 2   sube a mitad de camino
  ronda 3   ofrece el TECHO         (V_liq / 2.0)  y ni un peso mas

Quien decide que:
  el numero    -> app/pricing.siguiente_oferta, reglas puras. Nunca el LLM.
  el texto     -> plantillas con variantes. Nunca el LLM.
  que dijo el  -> app/parseo.py con reglas; el modelo solo si la confianza
  vendedor        queda bajo 0.6.

Por que las plantillas y no el modelo: se probo y de "ThinkPad T480 16GB" saca
48016 y lo mete como si fuera plata, y una de cada tres veces invierte los
roles ("Vendo el ThinkPad..." cuando estas comprando). Un regateo son quince
palabras formulaicas: no hay nada que ganar y si hay plata que perder.

FRENOS, que aca importan mas que en ML porque Messenger es mas sensible:
  - tope de negociaciones nuevas por dia (facebook.negociar_por_dia)
  - pausa larga entre mensaje y mensaje (facebook.negociar_pausa_min)
  - maximo 3 rondas y despues se cierra educado
  - sin respuesta en 48 h -> se cierra sola
  - tecleo con retardo de persona, nunca pegado de golpe
  - y el candado de cuenta antes de tocar nada: si la sesion no es la cuenta
    desechable aprobada, aborta (app/fb_guard.py)
"""
from __future__ import annotations

import random
import re
import time

from app import fb_guard, llm, tg
from app.config import fb, p
from app.db import ex, q, q1
from app.jobs import envuelto
from app.parseo import leer_respuesta_vendedor
from app.pricing import siguiente_oferta

CONFIANZA_MINIMA = 0.6

SALUDOS = [
    "Hola, ¿sigue disponible?",
    "Buenas, ¿todavia lo tienes?",
    "Hola, ¿sigue a la venta?",
    "Buenas tardes, ¿esta disponible aun?",
]

FRASES = {
    1: [
        "¿Te sirven {monto} al contado? Lo retiro yo.",
        "Te ofrezco {monto} en efectivo y lo retiro cuando digas.",
        "¿Lo dejarias en {monto}? Pago al contado y retiro yo.",
    ],
    2: [
        "Puedo llegar a {monto}, al contado y retiro yo. ¿Cerramos?",
        "Te subo a {monto}. Es lo que puedo pagar por ese.",
        "¿Y en {monto}? Pago hoy y lo voy a buscar.",
    ],
    3: [
        "Mi tope es {monto}, al contado y lo retiro hoy. Si te sirve lo cierro.",
        "Hasta {monto} puedo llegar, es mi ultimo precio. Quedo atento.",
    ],
}
FRASES_PRECIO_FIJO = [
    "Entiendo. Por si acaso te dejo mi oferta: {monto} al contado, retiro yo.",
    "Dale, lo dejo planteado igual: {monto} en efectivo y lo retiro hoy.",
]

PROMPT_LEER = """Lee la respuesta de un vendedor de Facebook Marketplace y clasificala.

Respuesta del vendedor: "{texto}"

Devuelve JSON:
  "disponible": true si el producto sigue a la venta, false si dice que no o que se vendio
  "acepta_ofertas": true si esta abierto a negociar, false si dice precio fijo
  "cierra": true si el vendedor esta aceptando un trato

No inventes numeros y no devuelvas precios: de eso se encarga otro."""

ESQUEMA_LEER = {
    "type": "object",
    "properties": {
        "disponible": {"type": "boolean"},
        "acepta_ofertas": {"type": "boolean"},
        "cierra": {"type": "boolean"},
    },
    "required": ["disponible", "acepta_ofertas", "cierra"],
}


def _plata(n) -> str:
    return f"${int(n or 0):,}".replace(",", ".")


def _buscar(scope, ruta: str):
    for sel in [s.strip() for s in (fb(ruta) or "").split(",") if s.strip()]:
        try:
            hallados = scope.query_selector_all(sel)
        except Exception:
            continue
        if hallados:
            return hallados
    return []


def _burbujas(pag, tope: int) -> list[str]:
    salida = []
    for e in _buscar(pag, "hilo.burbuja")[-tope:]:
        try:
            t = " ".join((e.inner_text() or "").split())
        except Exception:
            continue
        if t and len(t) > 1:
            salida.append(t)
    return salida


def _numeros(texto: str) -> set[int]:
    """Numeros que son PLATA, no specs. Ignora los pegados a letras (T480, 16GB)."""
    limpio = re.sub(r"(?<=[A-Za-z])\d+|\d+(?=[A-Za-z])", " ", texto or "")
    salida = set()
    for m in re.findall(r"\$?\s?\d[\d.\s]{2,}", limpio):
        d = re.sub(r"[^0-9]", "", m)
        if d:
            salida.add(int(d))
    return salida


# ------------------------------------------------------------- navegador
def _escribir(pag, texto: str) -> bool:
    """Escribe en el chat abierto de Marketplace. False si el DOM no dio pie."""
    try:
        cajas = _buscar(pag, "hilo.caja_texto")
        if not cajas:
            return False
        caja = cajas[-1]
        caja.click()
        caja.type(texto, delay=random.randint(35, 95))   # ritmo de persona
        pag.wait_for_timeout(random.randint(700, 1800))
        botones = _buscar(pag, "hilo.boton_enviar")
        if botones:
            botones[-1].click()
        else:
            pag.keyboard.press("Enter")
        pag.wait_for_timeout(random.randint(1500, 3000))
        return True
    except Exception:
        return False


def _abrir_chat(pag, n: dict) -> bool:
    """Deja abierto el chat con el vendedor. Por hilo si ya existe, si no
    entrando por la publicacion y apretando 'Enviar mensaje'."""
    try:
        if n.get("hilo"):
            pag.goto(f"https://www.facebook.com/marketplace/t/{n['hilo']}/",
                     wait_until="domcontentloaded", timeout=45000)
            pag.wait_for_timeout(random.randint(2500, 5000))
            return bool(_buscar(pag, "hilo.caja_texto"))

        pag.goto(n["url_item"], wait_until="domcontentloaded", timeout=45000)
        pag.wait_for_timeout(random.randint(3000, 6000))
        botones = _buscar(pag, "item.boton_mensaje")
        if not botones:
            return False
        botones[0].click()
        pag.wait_for_timeout(random.randint(2500, 5000))
        m = re.search(r"/marketplace/t/(\d+)", pag.url or "")
        if m:
            ex("UPDATE negociacion SET hilo=%s WHERE id=%s", (m.group(1), n["id"]))
        return bool(_buscar(pag, "hilo.caja_texto"))
    except Exception:
        return False


# ---------------------------------------------------------------- pasos
def _guardar_msg(nid: int, direccion: str, texto: str) -> None:
    ex("""INSERT INTO negociacion_msg (negociacion, direccion, texto)
          VALUES (%s,%s,%s)""", (nid, direccion, texto))


def _saludar(pag, n: dict) -> str:
    if not _abrir_chat(pag, n):
        ex("UPDATE negociacion SET estado='cancelada' WHERE id=%s", (n["id"],))
        return f"no pude abrir el chat de «{(n.get('titulo') or '')[:40]}»"
    saludo = random.choice(SALUDOS)
    if not _escribir(pag, saludo):
        ex("UPDATE negociacion SET estado='cancelada' WHERE id=%s", (n["id"],))
        return "no pude escribir el saludo"
    ex("""UPDATE negociacion SET estado='saludo', ronda=0, ultimo_mov=now()
          WHERE id=%s""", (n["id"],))
    _guardar_msg(n["id"], "sale", saludo)
    return "saludo enviado"


def _cerrar(n: dict, estado: str, aviso: str) -> str:
    ex("UPDATE negociacion SET estado=%s, ultimo_mov=now() WHERE id=%s", (estado, n["id"]))
    ex("UPDATE oportunidad SET estado=%s WHERE id=%s",
       ("acordada" if estado == "acordado" else "ignorar", n["oportunidad"]))
    if aviso:
        tg.CAZADOR.enviar(aviso)
    return estado


def _acordar(n: dict, precio: float) -> str:
    ex("""UPDATE negociacion SET estado='acordado', precio_acordado=%s, ultimo_mov=now()
          WHERE id=%s""", (precio, n["id"]))
    ex("UPDATE oportunidad SET estado='acordada' WHERE id=%s", (n["oportunidad"],))
    mult = float(n["v_liq"]) / precio if precio else 0
    tg.CAZADOR.enviar(
        f"🤝 <b>ACORDADO en Facebook</b>\n\n«{(n['titulo'] or '')[:90]}»\n\n"
        f"Cerrado en   <b>{_plata(precio)}</b>\n"
        f"Pedia        {_plata(n['precio_pedido'])}\n"
        f"Se revende a {_plata(n['v_liq'])}  →  <b>{mult:.1f}x</b>\n\n"
        f'<a href="https://www.facebook.com/marketplace/t/{n.get("hilo") or ""}/">abrir el chat</a>\n\n'
        f"Te toca a ti: coordinar el retiro y pagar.")
    return "acordado"


def _leer(n: dict, nuevas: list[str]) -> dict:
    """Que dijo el vendedor. Reglas primero, el modelo solo si dudan."""
    texto = nuevas[-1]
    lectura = leer_respuesta_vendedor(texto, float(n["precio_pedido"] or 0))
    if lectura["confianza"] < CONFIANZA_MINIMA and llm.vivo():
        r = llm.json_de(PROMPT_LEER.format(texto=texto[:600]), esquema=ESQUEMA_LEER,
                        modelo=llm.modelo_de("leer_vendedor"))
        if r:
            # El precio SIEMPRE del parser. Medido: el 27B lee "te lo dejo en
            # 130" y devuelve 130, no 130000.
            lectura = {**r, "precio": lectura["precio"], "via": "llm"}
    return lectura


def _responder(pag, n: dict, lectura: dict) -> str:
    techo = float(n["precio_techo"])
    obj = float(n["precio_objetivo"])
    ronda = int(n["ronda"])
    max_rondas = int(p("compra_negociacion.max_rondas", 3))

    if not lectura.get("disponible", True):
        return _cerrar(n, "rechazado", f"«{(n['titulo'] or '')[:70]}» ya se vendio. Cerrada.")

    dicho = lectura.get("precio")
    if dicho and 1000 <= dicho <= 99_000_000:
        if dicho <= techo:
            return _acordar(n, float(dicho))
        if ronda >= max_rondas:
            return _cerrar(n, "rechazado",
                           f"❌ FB · «{(n['titulo'] or '')[:70]}»\nNo bajo del {_plata(dicho)} "
                           f"y tu techo era {_plata(techo)}. Cerrada sin comprar.")

    if ronda >= max_rondas:
        return _cerrar(n, "rechazado",
                       f"❌ FB · «{(n['titulo'] or '')[:70]}» — se acabaron las rondas.")

    oferta = siguiente_oferta(ronda + 1, obj, techo)
    banco = FRASES_PRECIO_FIJO if lectura.get("acepta_ofertas") is False \
        else FRASES.get(min(ronda + 1, 3), FRASES[3])
    texto = random.choice(banco).format(monto=_plata(oferta))

    # Guardia: la cifra que sale escrita tiene que ser EXACTAMENTE la que
    # calculo el codigo. Si no, no se manda.
    if int(oferta) not in _numeros(texto):
        return f"guardia: la cifra no cuadra en «{texto[:40]}», no se mando"
    if oferta > techo:
        return "guardia: la oferta pasaba el techo, no se mando"

    if not _escribir(pag, texto):
        return "no pude escribir la oferta (¿cambio el diseño de FB?)"

    ex("""UPDATE negociacion SET estado='ofertando', ronda=%s, ultimo_mov=now()
          WHERE id=%s""", (ronda + 1, n["id"]))
    _guardar_msg(n["id"], "sale", texto)
    return f"ronda {ronda + 1}: {_plata(oferta)}"


# ---------------------------------------------------------------- correr
def correr() -> str:
    if not p("modo.fb_activo", False):
        return "apagado (policy.yml: modo.fb_activo=false)"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "falta playwright: docker compose --profile vnc build navegador"
    perfil = fb_guard.perfil_listo()
    if not perfil:
        return "falta el login de FB: correr bin/login-fb.sh una vez"

    pausa_min = int(p("facebook.negociar_pausa_min", 15))
    hechos: list[str] = []

    with sync_playwright() as pw:
        ctx = pw.chromium.launch_persistent_context(
            str(perfil), headless=True, viewport={"width": 1366, "height": 900},
            locale="es-CL", timezone_id="America/Santiago")
        try:
            ok, motivo = fb_guard.verificar_o_avisar(ctx, "negociar_fb")
            if not ok:
                return f"candado de cuenta: {motivo}"
            pag = ctx.new_page()

            # 1) abrir las que aprobaste, respetando el tope diario de FB
            fila = q1("SELECT n FROM negociaciones_hoy_canal WHERE canal='fb'")
            cupo = max(0, int(p("facebook.negociar_por_dia", 5)) - int((fila or {}).get("n") or 0))
            if cupo:
                nuevas = q(
                    """SELECT ng.id, ng.oportunidad, ng.hilo, ng.url_item, i.titulo
                       FROM negociacion ng
                       JOIN oportunidad o ON o.id = ng.oportunidad
                       JOIN item_raw i ON i.id = o.item_raw
                       WHERE ng.canal='fb' AND ng.estado='por_saludar' LIMIT %s""",
                    (cupo,))
                for n in nuevas:
                    hechos.append(_saludar(pag, dict(n)))
                    time.sleep(random.uniform(60, 180))

            # 2) las que ya tienen conversacion y toca mirar si contestaron
            esperando = q(
                """SELECT ng.*, i.titulo, o.v_liq
                   FROM negociacion ng
                   JOIN oportunidad o ON o.id = ng.oportunidad
                   JOIN item_raw i ON i.id = o.item_raw
                   WHERE ng.canal='fb' AND ng.estado IN ('saludo','ofertando')
                     AND ng.hilo IS NOT NULL
                     AND ng.ultimo_mov < now() - make_interval(mins => %s)""",
                (pausa_min,))
            for n in esperando:
                n = dict(n)
                if not _abrir_chat(pag, n):
                    continue
                vistas = {r["texto"] for r in q(
                    "SELECT texto FROM negociacion_msg WHERE negociacion=%s", (n["id"],))}
                nuevas_b = [b for b in _burbujas(pag, int(p("facebook.burbujas_leer", 12)))
                            if b not in vistas]
                if not nuevas_b:
                    continue
                _guardar_msg(n["id"], "entra", nuevas_b[-1])
                hechos.append(_responder(pag, n, _leer(n, nuevas_b)))
                time.sleep(random.uniform(60, 180))
        finally:
            ctx.close()

    # 3) las que nunca contestaron
    horas = int(p("compra_negociacion.espera_respuesta_h", 48))
    mudas = ex(
        """UPDATE negociacion SET estado='sin_respuesta'
           WHERE canal='fb' AND estado IN ('saludo','ofertando')
             AND ultimo_mov < now() - make_interval(hours => %s)""", (horas,))
    if mudas:
        hechos.append(f"{mudas} sin respuesta")

    return "; ".join(hechos) if hechos else "nada que mover"


if __name__ == "__main__":
    print(envuelto("negociar_fb", correr))
