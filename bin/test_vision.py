#!/usr/bin/env python3
"""Casos del identificador por fotos.

Lo que se prueba aca NO es si el modelo reconoce un ThinkPad — eso depende del
modelo y se prueba mandando fotos de verdad. Lo que se prueba es la GUARDIA,
que es la parte que puede costarte plata:

  - que las specs jamas salgan de la foto
  - que si escribiste "16gb 512" esas si entren
  - que si la foto muestra una etiqueta, se lea de ahi
  - que achicar la foto no rompa nada

    ~/cazador/bin/cazador test
"""
from __future__ import annotations

import base64
import io
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, "/app")

from app import llm, vision   # noqa: E402

fallos = 0


def check(nombre: str, cond: bool, extra: str = "") -> None:
    global fallos
    if cond:
        print(f"✓ {nombre}")
    else:
        fallos += 1
        print(f"✖ {nombre}   {extra}")


def _foto_falsa(ancho=2400, alto=1800) -> str:
    """Una foto grande de mentira, para probar el achicado."""
    from PIL import Image
    ruta = Path(tempfile.mkdtemp()) / "foto.jpg"
    Image.new("RGB", (ancho, alto), (90, 90, 110)).save(ruta, "JPEG")
    return str(ruta)


# Respuestas de mentira del modelo, para no depender de que esté corriendo.
def _fingir(respuesta: dict):
    def falso(prompt, esquema=None, modelo=None, imagenes=None, timeout=None):
        falso.visto = {"imagenes": imagenes, "modelo": modelo, "prompt": prompt}
        return dict(respuesta)
    falso.visto = {}
    llm.json_de = falso
    return falso


VE_THINKPAD = {
    "marca": "Lenovo", "modelo": "ThinkPad T480", "categoria": "notebook",
    "condicion": "usado", "estado_visible": ["tapa con rayas leves"],
    "texto_en_pantalla": "", "confianza": 0.9,
}


def main() -> int:
    original = llm.json_de
    try:
        ruta = _foto_falsa()

        # --- 1. la foto sola: identifica, pero NO inventa specs ---------
        _fingir(VE_THINKPAD)
        d = vision.identificar([ruta], "")
        check(f"foto sola · reconoce: {d['marca']} {d['modelo']}",
              d["marca"] == "Lenovo" and d["modelo"] == "ThinkPad T480")
        check(f"foto sola · NO inventa specs: {d['specs']}", d["specs"] == {})
        check(f"foto sola · lo dice claro: '{d['specs_de']}'", d["specs_de"] == "no se saben")

        # --- 2. con tu linea: las specs salen de las REGLAS -------------
        _fingir(VE_THINKPAD)
        d = vision.identificar([ruta], "16gb 512 ssd, bateria buena")
        check(f"con tu linea · saca specs de tu texto: {d['specs']}",
              d["specs"].get("ram_gb") == 16 and d["specs"].get("disco_gb") == 512, str(d["specs"]))
        check(f"con tu linea · dice de donde salieron: '{d['specs_de']}'",
              d["specs_de"] == "tu texto")

        # --- 3. aunque el modelo JURE que ve la RAM, se ignora ----------
        mentiroso = dict(VE_THINKPAD)
        mentiroso["specs"] = {"ram_gb": 64, "disco_gb": 2048}   # inventado
        mentiroso["ram_gb"] = 64
        _fingir(mentiroso)
        d = vision.identificar([ruta], "")
        check(f"guardia · descarta specs inventadas por el ojo: {d['specs']}",
              d["specs"] == {}, str(d["specs"]))

        # --- 4. etiqueta legible en la foto: eso SI vale ----------------
        con_etiqueta = dict(VE_THINKPAD)
        con_etiqueta["texto_en_pantalla"] = "Intel Core i7-8650U 32GB RAM 1TB SSD"
        _fingir(con_etiqueta)
        d = vision.identificar([ruta], "")
        check(f"etiqueta · transcrita y pasada por reglas: {d['specs']}",
              d["specs"].get("ram_gb") == 32 and d["specs"].get("disco_gb") == 1024, str(d["specs"]))
        check(f"etiqueta · dice de donde salio: '{d['specs_de']}'",
              d["specs_de"] == "etiqueta en la foto")

        # --- 5. el achicado ---------------------------------------------
        b64 = vision._achicar(ruta)
        check("achicar · devuelve base64 valido", bool(b64) and len(b64) > 100)
        from PIL import Image
        im = Image.open(io.BytesIO(base64.b64decode(b64)))
        check(f"achicar · 2400x1800 -> {im.size[0]}x{im.size[1]}", max(im.size) <= 1024)
        check("achicar · una ruta que no existe no revienta",
              vision._achicar("/no/existe.jpg") is None)

        # --- 6. topes y modelo ------------------------------------------
        f = _fingir(VE_THINKPAD)
        vision.identificar([ruta, ruta, ruta, ruta], "")
        check(f"tope · manda maximo 2 fotos, mando {len(f.visto['imagenes'])}",
              len(f.visto["imagenes"]) == 2)
        check(f"modelo · usa el que ve: {f.visto['modelo']}",
              "27b" in str(f.visto["modelo"]))
        check("sin fotos · no llama al modelo", vision.identificar([], "algo") is None)

        print(f"\n{'TODO OK' if not fallos else str(fallos) + ' FALLOS'}")
        return fallos
    finally:
        llm.json_de = original


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
