"""Configuracion: variables de entorno + los YAML de config/."""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
CONFIG = RAIZ / "config"
DATA = RAIZ / "data"

PG_DSN = os.getenv("PG_DSN", "postgresql://cazador:cazador@127.0.0.1:5434/cazador")
REDIS_URL = os.getenv("REDIS_URL", "redis://127.0.0.1:6380/0")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434")
# 8B siempre. El 4B alucina specs y confunde roles, y en este sistema una
# alucinacion es plata. El 8B se desborda a CPU en la P1000 (5,2 tok/s contra
# 20,7 del 4B), y por eso mismo el grueso del trabajo lo hacen las reglas de
# app/extract.py y app/parseo.py: al modelo solo le llegan los casos raros.
OLLAMA_MODELO = os.getenv("OLLAMA_MODELO", "huihui_ai/qwen3-abliterated:8b")

ML_CLIENT_ID = os.getenv("ML_CLIENT_ID", "")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")
ML_REDIRECT_URI = os.getenv("ML_REDIRECT_URI", "")
ML_SITE = os.getenv("ML_SITE", "MLC")

# Dos bots: uno caza y negocia, el otro publica.
TG_TOKEN_CAZADOR = os.getenv("TG_TOKEN_CAZADOR", "")
TG_TOKEN_PUBLICADOR = os.getenv("TG_TOKEN_PUBLICADOR", "")
TG_PASS = os.getenv("TG_PASS", "")             # palabra para emparejar el chat

BASE_PUBLICA = os.getenv("BASE_PUBLICA", "")   # https://host/cazador


def _yaml(nombre: str) -> dict:
    ruta = CONFIG / nombre
    if not ruta.exists():
        return {}
    return yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=8)
def policy() -> dict:
    return _yaml("policy.yml")


def watchlist() -> dict:
    # Sin cache: el bot la reescribe en caliente cuando apruebas keywords.
    return _yaml("watchlist.yml")


def aduanas() -> dict:
    return _yaml("aduanas.yml")


def facebook() -> dict:
    # Sin cache: cuando FB cambia el diseño quieres arreglar el YAML y que el
    # proximo ciclo ya lo tome, sin reiniciar contenedores.
    return _yaml("facebook.yml")


def fb(ruta: str, defecto=None):
    """facebook.yml anidado por punto: fb('hilo.caja_texto')"""
    nodo = facebook()
    for parte in ruta.split("."):
        if not isinstance(nodo, dict) or parte not in nodo:
            return defecto
        nodo = nodo[parte]
    return nodo


def p(ruta: str, defecto=None):
    """policy anidada por punto: p('compra.iva') -> 1.19"""
    nodo = policy()
    for parte in ruta.split("."):
        if not isinstance(nodo, dict) or parte not in nodo:
            return defecto
        nodo = nodo[parte]
    return nodo
