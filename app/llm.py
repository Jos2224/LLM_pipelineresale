"""Ollama local. Solo dos trabajos: extraer JSON y redactar texto.

Nunca decide precios. Si el modelo devuelve basura, el llamador se queda con
None y el flujo sigue con reglas.
"""
from __future__ import annotations

import json
import re

import httpx

from app.config import OLLAMA_MODELO, OLLAMA_URL

_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def _pedir(prompt: str, formato: str | None = None, modelo: str | None = None,
           imagenes: list[str] | None = None, timeout: float | None = None) -> str:
    cuerpo = {
        "model": modelo or OLLAMA_MODELO,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 4096},
        # qwen3 piensa por defecto; apagado para que responda corto y rapido
        "think": False,
    }
    if formato:
        cuerpo["format"] = formato
    if imagenes:
        # Ollama recibe las fotos en base64. Solo sirve con un modelo que vea
        # (qwen3.8:27b trae capa projector); a uno de solo texto las ignora.
        cuerpo["images"] = imagenes
        # Mirar una foto en CPU es lento: minutos, no segundos.
        cuerpo["options"]["num_ctx"] = 8192
    lim = httpx.Timeout(timeout, connect=10.0) if timeout else _TIMEOUT
    with httpx.Client(timeout=lim) as c:
        r = c.post(f"{OLLAMA_URL}/api/generate", json=cuerpo)
        r.raise_for_status()
        return r.json().get("response", "")


def json_de(prompt: str, esquema: dict | None = None, modelo: str | None = None,
            imagenes: list[str] | None = None, timeout: float | None = None) -> dict | None:
    """Pide JSON y lo devuelve parseado, o None si el modelo fallo."""
    try:
        crudo = _pedir(prompt, formato="json" if esquema is None else esquema,
                       modelo=modelo, imagenes=imagenes, timeout=timeout)
    except Exception:
        return None
    crudo = re.sub(r"<think>.*?</think>", "", crudo, flags=re.S).strip()
    try:
        return json.loads(crudo)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", crudo, flags=re.S)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def texto(prompt: str, modelo: str | None = None) -> str:
    try:
        salida = _pedir(prompt, modelo=modelo)
    except Exception:
        return ""
    return re.sub(r"<think>.*?</think>", "", salida, flags=re.S).strip()


def modelo_de(tarea: str) -> str:
    """Que modelo usa cada tarea. Se configura en policy.yml -> llm.

    Por que no uno solo para todo: en esta maquina el 27B corre en CPU a
    ~0,9 palabras por segundo (medido el 28-ago; la P1000 tiene 4 GB y el
    modelo pesa 17). Es excelente donde hay que pensar y el volumen es bajo —
    redactar una publicacion, entender un mensaje raro — y es inviable donde
    hay que procesar 40 titulos cada dos minutos.

    Reparto por defecto:
      catalogar      -> 8B   (volumen alto, decision facil, 8 por ciclo)
      leer_vendedor  -> 27B  (una vez cada tanto, y equivocarse cuesta plata)
      redactar       -> 27B  (una vez por publicacion, calidad se nota)
      responder      -> 27B  (le habla a un comprador de verdad)
      ver            -> 27B  (OBLIGATORIO que vea: el 8B no tiene ojos)
    """
    from app.config import p
    return str(p(f"llm.{tarea}", "") or OLLAMA_MODELO)


def vivo() -> bool:
    try:
        with httpx.Client(timeout=5.0) as c:
            return c.get(f"{OLLAMA_URL}/api/tags").status_code == 200
    except Exception:
        return False
