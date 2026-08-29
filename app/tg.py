"""Telegram por HTTP puro. Sin librerias grandes: menos que romper.

Hay DOS bots y cada uno hace una cosa sola:

  CAZADOR    @tu_bot_cazador          te avisa de ofertas y negocia por ti
  PUBLICADOR @tu_bot_publicador  le mandas fotos y publica en ML y Facebook

Cada uno guarda su propio chat_id, asi que se emparejan por separado y las
alertas de uno nunca se mezclan con las del otro.
"""
from __future__ import annotations

from typing import Any

import httpx

from app.config import TG_TOKEN_CAZADOR, TG_TOKEN_PUBLICADOR
from app.db import kv_get, kv_set

API = "https://api.telegram.org/bot{}/{}"


class Bot:
    def __init__(self, token: str, clave_chat: str, nombre: str):
        self.token = token
        self.clave_chat = clave_chat
        self.nombre = nombre

    # ---------------------------------------------------------- basico
    def chat_id(self) -> str | None:
        return kv_get(self.clave_chat)

    def emparejar(self, chat: str) -> None:
        kv_set(self.clave_chat, chat)

    def _post(self, metodo: str, payload: dict) -> dict | None:
        if not self.token:
            return None
        try:
            with httpx.Client(timeout=30.0) as c:
                return c.post(API.format(self.token, metodo), json=payload).json()
        except Exception:
            return None

    # ---------------------------------------------------------- enviar
    def enviar(self, texto: str, botones: dict | None = None, chat: str | None = None) -> dict | None:
        destino = chat or self.chat_id()
        if not destino:
            return None
        pay: dict[str, Any] = {
            "chat_id": destino, "text": texto[:4000],
            "parse_mode": "HTML", "disable_web_page_preview": True,
        }
        if botones:
            pay["reply_markup"] = botones
        return self._post("sendMessage", pay)

    def foto(self, url_foto: str, texto: str, botones: dict | None = None,
             chat: str | None = None) -> dict | None:
        destino = chat or self.chat_id()
        if not destino:
            return None
        pay: dict[str, Any] = {"chat_id": destino, "photo": url_foto,
                               "caption": texto[:1000], "parse_mode": "HTML"}
        if botones:
            pay["reply_markup"] = botones
        r = self._post("sendPhoto", pay)
        if not r or not r.get("ok"):
            # Foto rota (pasa seguido con CDN de remates): manda solo texto.
            return self.enviar(texto, botones, chat)
        return r

    def album(self, urls: list[str], texto: str, chat: str | None = None) -> dict | None:
        destino = chat or self.chat_id()
        if not destino or not urls:
            return None
        medios = [{"type": "photo", "media": u} for u in urls[:10]]
        medios[0]["caption"] = texto[:1000]
        medios[0]["parse_mode"] = "HTML"
        return self._post("sendMediaGroup", {"chat_id": destino, "media": medios})

    # ---------------------------------------------------------- botones
    def responder_boton(self, callback_id: str, aviso: str = "") -> None:
        self._post("answerCallbackQuery", {"callback_query_id": callback_id, "text": aviso[:200]})

    def editar_botones(self, chat: str, message_id: int, botones: dict | None) -> None:
        self._post("editMessageReplyMarkup", {
            "chat_id": chat, "message_id": message_id,
            "reply_markup": botones or {"inline_keyboard": []},
        })

    # ---------------------------------------------------------- recibir
    def updates(self, offset: int, timeout: int = 50) -> list[dict]:
        if not self.token:
            return []
        try:
            with httpx.Client(timeout=timeout + 15) as c:
                d = c.get(API.format(self.token, "getUpdates"),
                          params={"offset": offset, "timeout": timeout}).json()
                return d.get("result", []) if d.get("ok") else []
        except Exception:
            return []

    def archivo_url(self, file_id: str) -> str | None:
        r = self._post("getFile", {"file_id": file_id})
        if not r or not r.get("ok"):
            return None
        return f"https://api.telegram.org/file/bot{self.token}/{r['result']['file_path']}"


def teclado(filas: list[list[tuple[str, str]]]) -> dict:
    """[[("Comprar","comprar:12"), ("Ignorar","ignorar:12")]] -> markup"""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in fila] for fila in filas]}


CAZADOR = Bot(TG_TOKEN_CAZADOR, "tg_chat_cazador", "cazador")
PUBLICADOR = Bot(TG_TOKEN_PUBLICADOR, "tg_chat_publicador", "publicador")
