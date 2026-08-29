"""Script 17 — el bot le escribe al vendedor y regatea por ti.

Como negocias en ML Chile: preguntas publicas en la publicacion del vendedor.
No hay sistema de ofertas formal para usados, asi que es por ahi.

La conversacion, tal como la pediste:

  ronda 0   "Buenas, ¿sigue disponible?"        <- solo saluda, no ofrece nada
  (vendedor responde)
  ronda 1   ofrece el OBJETIVO      (V_liq / 2)
  ronda 2   sube a mitad de camino  (objetivo + (techo-objetivo)/2)
  ronda 3   ofrece el TECHO         (V_liq / 1.5)  y ni un peso mas
  -> si acepta: te avisa "acordado en $X, anda a buscarlo"
  -> si no:     cierra educado y no vuelve a escribir

Por que saludar primero: un "hola, ¿lo dejas en 40 lucas?" en frio lo ignoran o
lo toman a mal. Un saludo con una pregunta normal abre conversacion, y recien
ahi la oferta cae en un hilo que ya existe.

Reparto de trabajo, igual que en todo el sistema:
  el CODIGO decide cada numero      (pricing.siguiente_oferta)
  el LLM solo escribe la frase      y se verifica que no haya cambiado el numero
"""
from __future__ import annotations

import random
import re
from datetime import datetime, timezone

from app import llm, ml_api, tg
from app.config import p
from app.db import ex, q, q1
from app.jobs import envuelto
from app.parseo import leer_respuesta_vendedor
from app.pricing import siguiente_oferta

CONFIANZA_MINIMA = 0.6   # bajo esto, la respuesta del vendedor la lee el 8B

SALUDOS = [
    "Buenas, ¿sigue disponible?",
    "Hola, buenas. ¿Todavia lo tienes?",
    "Saludos, ¿sigue a la venta?",
    "Buenas tardes, ¿esta disponible aun?",
]

PROMPT_LEER = """Lee la respuesta de un vendedor de MercadoLibre y clasificala.

Respuesta del vendedor: "{texto}"

Devuelve JSON:
  "disponible": true si el producto sigue a la venta, false si dice que no o que se vendio
  "acepta_ofertas": true si esta abierto a negociar, false si dice precio fijo
  "precio": el numero en pesos que menciona el vendedor, o null si no menciona ninguno
  "cierra": true si el vendedor esta aceptando un trato

No inventes numeros. Si no dice un precio, "precio" es null."""

ESQUEMA_LEER = {
    "type": "object",
    "properties": {
        "disponible": {"type": "boolean"},
        "acepta_ofertas": {"type": "boolean"},
        "precio": {"type": ["integer", "null"]},
        "cierra": {"type": "boolean"},
    },
    "required": ["disponible", "acepta_ofertas", "precio", "cierra"],
}

# Las ofertas se escriben con plantillas, NO con el LLM.
#
# Se probo con el modelo de 4B y no sirve para esto: de "ThinkPad T480 16GB"
# saca el numero 48016 y lo mete como si fuera plata, y una de cada tres veces
# invierte los roles ("Vendo el ThinkPad..." cuando estas comprando). Un
# mensaje de negociacion son 15 palabras formulaicas; no hay nada que ganar y
# si hay plata que perder. El LLM se queda solo leyendo la respuesta del
# vendedor, que es donde de verdad ayuda.
FRASES = {
    1: [
        "Hola, ¿te sirven {monto} al contado? Lo retiro yo.",
        "Buenas, te ofrezco {monto} en efectivo y lo retiro cuando digas.",
        "Hola, ¿lo dejarias en {monto}? Pago al contado y retiro yo.",
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


def _plata(n) -> str:
    return f"${int(n or 0):,}".replace(",", ".")


def _numeros(texto: str) -> set[int]:
    """Numeros que son PLATA, no specs.

    Ignora los pegados a letras (T480, 16GB, 512GB) porque esos son modelo y
    caracteristicas, no precios.
    """
    limpio = re.sub(r"(?<=[A-Za-z])\d+|\d+(?=[A-Za-z])", " ", texto or "")
    salida = set()
    for m in re.findall(r"\$?\s?\d[\d.\s]{2,}", limpio):
        d = re.sub(r"[^0-9]", "", m)
        if d:
            salida.add(int(d))
    return salida


def _mensaje_oferta(ronda: int, oferta: float, precio_fijo: bool) -> str:
    banco = FRASES_PRECIO_FIJO if precio_fijo else FRASES.get(min(ronda, 3), FRASES[3])
    return random.choice(banco).format(monto=_plata(oferta))


# ------------------------------------------------------------ pasos
def _abrir(n: dict) -> str:
    """Ronda 0: saludar. No se ofrece ningun numero todavia."""
    saludo = random.choice(SALUDOS)
    r = ml_api.preguntar(n["item_externo"], saludo)
    ex("""UPDATE negociacion SET estado='saludo', ronda=0, pregunta_abierta=%s,
           ultimo_mov=now() WHERE id=%s""", (str(r.get("id")), n["id"]))
    ex("""INSERT INTO negociacion_msg (negociacion, direccion, texto, id_externo)
          VALUES (%s,'sale',%s,%s)""", (n["id"], saludo, str(r.get("id"))))
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
        f"🤝 <b>ACORDADO</b>\n\n«{n['titulo'][:90]}»\n\n"
        f"Cerrado en   <b>{_plata(precio)}</b>\n"
        f"Pedia        {_plata(n['precio_pedido'])}\n"
        f"Se revende a {_plata(n['v_liq'])}  →  <b>{mult:.1f}x</b>\n\n"
        f'<a href="{n["url"]}">abrir publicacion</a>\n\n'
        f"Te toca a ti: pagar y retirar.",
        tg.teclado([[("✅ Lo compre", f"neg_compre:{n['id']}"),
                     ("✖ Me arrepenti", f"neg_cancelar:{n['id']}")]]),
    )
    return "acordado"


def _responder(n: dict, lectura: dict) -> str:
    """Ya contesto el vendedor. El codigo decide el numero, el LLM lo escribe."""
    techo = float(n["precio_techo"])
    obj = float(n["precio_objetivo"])
    ronda = int(n["ronda"])

    if not lectura.get("disponible", True):
        return _cerrar(n, "rechazado", f"«{n['titulo'][:70]}» ya se vendio. Cerrada.")

    # El vendedor puso un numero sobre la mesa.
    dicho = lectura.get("precio")
    if dicho and 1000 <= dicho <= 99_000_000:
        if dicho <= techo:
            return _acordar(n, float(dicho))
        if ronda >= int(p("compra_negociacion.max_rondas", 3)):
            return _cerrar(
                n, "rechazado",
                f"❌ «{n['titulo'][:70]}»\nNo bajo del {_plata(dicho)} y tu techo era "
                f"{_plata(techo)}. Cerrada sin comprar.")

    if ronda >= int(p("compra_negociacion.max_rondas", 3)):
        return _cerrar(n, "rechazado",
                       f"❌ «{n['titulo'][:70]}» — se acabaron las rondas. Cerrada.")

    oferta = siguiente_oferta(ronda + 1, obj, techo)
    texto = _mensaje_oferta(ronda + 1, oferta, lectura.get("acepta_ofertas") is False)

    r = ml_api.preguntar(n["item_externo"], texto)
    ex("""UPDATE negociacion SET estado='ofertando', ronda=%s, pregunta_abierta=%s,
           ultimo_mov=now() WHERE id=%s""", (ronda + 1, str(r.get("id")), n["id"]))
    ex("""INSERT INTO negociacion_msg (negociacion, direccion, texto, id_externo)
          VALUES (%s,'sale',%s,%s)""", (n["id"], texto, str(r.get("id"))))
    return f"oferta ronda {ronda + 1}: {_plata(oferta)}"


def _leer_respuesta(n: dict) -> dict | None:
    """Mira si el vendedor ya contesto, y entiende que dijo.

    Primero reglas (`app/parseo.py`): "sigue disponible", "precio fijo",
    "te lo dejo en 130 lucas" salen igual siempre y en microsegundos.
    Si el vendedor escribio algo largo o ambiguo, ahi recien entra el 8B.
    """
    try:
        d = ml_api.pregunta(n["pregunta_abierta"])
    except Exception:
        return None
    resp = (d.get("answer") or {}).get("text")
    if not resp:
        return None
    ex("""INSERT INTO negociacion_msg (negociacion, direccion, texto, id_externo)
          VALUES (%s,'entra',%s,%s)""", (n["id"], resp, n["pregunta_abierta"]))

    lectura = leer_respuesta_vendedor(resp, float(n["precio_pedido"] or 0))
    if lectura["confianza"] >= CONFIANZA_MINIMA:
        lectura["via"] = "regla"
        return lectura

    if llm.vivo():
        r = llm.json_de(PROMPT_LEER.format(texto=resp[:600]),
                        esquema=ESQUEMA_LEER, modelo=llm.modelo_de("leer_vendedor"))
        if r:
            # El precio SIEMPRE lo saca el parser, y si el parser no encontro
            # ninguno entonces no hay ninguno. Antes esta linea caia al numero
            # del modelo cuando las reglas no veian nada, y eso es justo donde
            # mas peligro hay.
            #
            # Medido el 28-ago con qwen3.8:27b, que es el modelo grande:
            #   entrada "te lo dejo en 130 y lo vienes a ver"
            #   el modelo devuelve  {"precio": 130}     <- ciento treinta pesos
            #   app/parseo.py       130000              <- correcto
            # Con el fallback puesto, una respuesta que las reglas no leyeran
            # habria metido una oferta mil veces mas chica en la negociacion.
            r["precio"] = lectura["precio"]
            r["via"] = "llm"
            return r

    # Ni reglas ni modelo: se trata como "respondio algo, sigue disponible" y
    # la escalera de ofertas sigue igual. Nunca se inventa un precio.
    lectura["via"] = "duda"
    return lectura


# ------------------------------------------------------------ job
def correr() -> str:
    try:
        ml_api.token()
    except ml_api.SinToken:
        return "sin login de ML todavia"

    pausa_min = int(p("compra_negociacion.pausa_entre_min", 8))
    hechos: list[str] = []

    # 1) abrir las que aprobaste, respetando el tope diario
    hoy = q1("SELECT n FROM negociaciones_hoy")["n"]
    cupo = max(0, int(p("compra_negociacion.max_por_dia", 12)) - hoy)
    if cupo:
        nuevas = q(
            """SELECT ng.id, ng.oportunidad, ng.item_externo
               FROM negociacion ng WHERE ng.estado='por_saludar' LIMIT %s""",
            (cupo,),
        )
        for n in nuevas:
            try:
                hechos.append(_abrir(dict(n)))
            except Exception as e:
                ex("UPDATE negociacion SET estado='cancelada' WHERE id=%s", (n["id"],))
                tg.CAZADOR.enviar(f"⚠️ ML no dejo preguntar: {str(e)[:200]}")

    # 2) las que esperan respuesta
    esperando = q(
        """SELECT ng.*, i.titulo, i.url, o.v_liq
           FROM negociacion ng
           JOIN oportunidad o ON o.id = ng.oportunidad
           JOIN item_raw i ON i.id = o.item_raw
           WHERE ng.estado IN ('saludo','ofertando') AND ng.pregunta_abierta IS NOT NULL
             AND ng.ultimo_mov < now() - make_interval(mins => %s)""",
        (pausa_min,),
    )
    for n in esperando:
        n = dict(n)
        lectura = _leer_respuesta(n)
        if lectura is None:
            continue
        try:
            hechos.append(_responder(n, lectura))
        except Exception as e:
            hechos.append(f"error: {str(e)[:80]}")

    # 3) las que nunca contestaron
    horas = int(p("compra_negociacion.espera_respuesta_h", 48))
    mudas = ex(
        """UPDATE negociacion SET estado='sin_respuesta'
           WHERE estado IN ('saludo','ofertando')
             AND ultimo_mov < now() - make_interval(hours => %s)""",
        (horas,),
    )
    if mudas:
        hechos.append(f"{mudas} sin respuesta")

    return "; ".join(hechos) if hechos else "nada que mover"


if __name__ == "__main__":
    print(envuelto("negociar_compra", correr))
