"""Identificar un producto MIRANDO las fotos que mandaste por Telegram.

Esto es lo que pediste desde el principio: "al solo enviar fotos me publica".
Antes el bot dependia de que escribieras una linea; ahora la linea es opcional
y sirve de ayuda, no de requisito.

Quien hace que — y aca esta lo importante:

  la FOTO dice        marca, modelo, categoria, y el estado fisico que se ve
                      (golpes, rayas, teclado gastado, pantalla partida)
  la FOTO no dice     cuanta RAM tiene, que disco trae, si la bateria dura
  tu linea dice       eso, y la leen las REGLAS de app/extract.py

Por que la separacion es tan estricta: de las specs sale el estante de precios,
y del estante sale tu multiplo. Una RAM inventada mirando una foto se convierte
en un P50 equivocado y en una compra mala. **Las specs jamas salen de la foto**,
ni aunque el modelo jure que las ve. Si no las escribiste, quedan desconocidas y
el precio se calcula al nivel del modelo, avisandote que es estimacion.

La unica excepcion, y esta si es leible: cuando la foto muestra una etiqueta o
una pantalla con "Acerca de este equipo". Ahi el modelo puede TRANSCRIBIR lo que
dice, y eso se trata como si lo hubieras escrito tu — pasa igual por las reglas
de extract.py, que descartan lo que no tenga forma de spec.

Velocidad: en el servidor el 27B corre en CPU. Mirar 2 fotos y responder toma
minutos, no segundos. Por eso se achican las fotos antes de mandarlas y se
manda un maximo de `vision.max_fotos`.
"""
from __future__ import annotations

import base64
import io

from app import llm
from app.config import p
from app.extract import extraer

PROMPT = """Mira las fotos de un producto usado que alguien quiere vender en Chile.

{pista}

Devuelve JSON:
  "marca": la marca REAL del fabricante que ves. ThinkPad -> Lenovo,
           MacBook -> Apple, Latitude -> Dell, EliteBook -> HP,
           Galaxy -> Samsung. Si no logras leer ninguna marca, pon "".
  "modelo": el identificador comercial que se lea en el equipo o en la
            pantalla (ej "ThinkPad T480", "iPhone 13"). Si no se lee, pon "".
  "categoria": notebook, celular, monitor, tablet, computador, impresora,
               componente, accesorio u otro.
  "condicion": nuevo, usado o reacondicionado, segun como se ve.
  "estado_visible": lista corta de lo que se VE en las fotos sobre el estado
                    (ej ["teclado gastado", "tapa con rayas", "pantalla sana"]).
                    Solo lo que se ve. Si se ve bien, di que se ve bien.
  "texto_en_pantalla": si alguna foto muestra una etiqueta o una pantalla con
                       especificaciones, transcribela TAL CUAL, sin interpretar.
                       Si no hay ninguna, pon "".
  "confianza": 0 a 1, que tan seguro estas de la marca y el modelo.

Reglas absolutas:
- NO adivines cuanta RAM ni que disco tiene. Eso no se ve en una foto.
  Si no esta escrito en una etiqueta o pantalla, no lo pongas en ningun lado.
- Si dudas entre dos modelos, pon el mas generico y baja la confianza.
- Nunca inventes una marca para no dejar el campo vacio."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "marca": {"type": "string"},
        "modelo": {"type": "string"},
        "categoria": {"type": "string"},
        "condicion": {"type": "string"},
        "estado_visible": {"type": "array", "items": {"type": "string"}},
        "texto_en_pantalla": {"type": "string"},
        "confianza": {"type": "number"},
    },
    "required": ["marca", "modelo", "categoria", "condicion", "confianza"],
}


def _achicar(ruta: str) -> str | None:
    """Foto -> base64, reducida. Devuelve None si no se pudo leer.

    Una foto de celular son 4000 px y 4 MB. Mirarla entera en CPU es varias
    veces mas lento y no identifica mejor: para leer "ThinkPad T480" en una
    tapa sobra con 1024 px.
    """
    lado = int(p("vision.lado_max", 1024))
    try:
        from PIL import Image
        with Image.open(ruta) as im:
            im = im.convert("RGB")
            im.thumbnail((lado, lado))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=82, optimize=True)
            return base64.b64encode(buf.getvalue()).decode()
    except Exception:
        # Sin Pillow o con una foto rara: se manda tal cual. Mas lento, pero
        # funciona igual — es preferible a no identificar nada.
        try:
            with open(ruta, "rb") as f:
                return base64.b64encode(f.read()).decode()
        except Exception:
            return None


def identificar(rutas: list[str], pista: str = "") -> dict | None:
    """Mira las fotos y devuelve que producto es. None si el modelo fallo.

    `pista` es la linea que escribiste, si escribiste alguna. Se le pasa al
    modelo como contexto, pero las specs NO salen de aca ni de la foto: salen
    de las reglas, mas abajo.
    """
    if not rutas:
        return None
    fotos = [b for b in (_achicar(r) for r in rutas[:int(p("vision.max_fotos", 2))]) if b]
    if not fotos:
        return None

    texto_pista = (f'El vendedor escribio esto sobre el producto: "{pista.strip()}". '
                   "Usalo solo como ayuda; si la foto lo contradice, manda la foto."
                   if pista.strip() else
                   "El vendedor no escribio nada: identificalo solo con las fotos.")

    d = llm.json_de(PROMPT.format(pista=texto_pista), esquema=ESQUEMA,
                    modelo=llm.modelo_de("ver"), imagenes=fotos,
                    timeout=float(p("vision.timeout_seg", 900)))
    if not d or not isinstance(d, dict):
        return None

    # --- la guardia: las specs NUNCA salen de la foto -------------------
    # Se arman con las REGLAS, leyendo solo dos fuentes de texto: lo que tu
    # escribiste, y lo que el modelo transcribio de una etiqueta o pantalla.
    fuente_specs = " ".join([pista or "", str(d.get("texto_en_pantalla") or "")]).strip()
    d["specs"] = extraer(fuente_specs)["specs"] if fuente_specs else {}
    d["specs_de"] = ("tu texto" if pista.strip() and d["specs"] else
                     "etiqueta en la foto" if d["specs"] else "no se saben")
    d["fotos_miradas"] = len(fotos)
    return d
