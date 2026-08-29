"""Script 33 — la red de seguridad.

Todo lo que ninguna regla supo resolver termina aca, y aca no se pierde:
  - te lo manda a Telegram con la respuesta sugerida y botones
  - si en 6 horas no apretaste nada, te lo recuerda una vez
  - si en 24 horas sigue sin tocar, lo marca y lo saca del ciclo para no
    llenarte el chat

En ML una pregunta sin responder en 24h baja tu posicion en el buscador. Por
eso el recordatorio existe.
"""
from __future__ import annotations

from app import tg
from app.db import ex, q
from app.jobs import envuelto


def pedir(texto: str, botones: list[list[tuple[str, str]]] | None = None) -> None:
    """Helper que usan los otros scripts para escalar algo puntual."""
    tg.PUBLICADOR.enviar(texto, tg.teclado(botones) if botones else None)


def correr() -> str:
    viejos = q(
        """SELECT m.id, m.texto, p.titulo, m.ts FROM mensaje m
           JOIN publicacion p ON p.id = m.publicacion
           WHERE m.estado='escalado' AND m.direccion='entra'
             AND m.ts < now() - interval '6 hours'
             AND m.ts > now() - interval '24 hours'"""
    )
    for m in viejos:
        tg.PUBLICADOR.enviar(f"⏰ llevas 6h sin contestar\n«{m['titulo'][:60]}»\n{m['texto'][:200]}",
                  tg.teclado([[("✏️ Responder", f"msg_mio:{m['id']}"),
                               ("🗑 Dejar asi", f"msg_ignorar:{m['id']}")]]))

    muertos = ex(
        """UPDATE mensaje SET estado='ignorado'
           WHERE estado='escalado' AND ts < now() - interval '24 hours'"""
    )
    return f"{len(viejos)} recordatorios, {muertos} cerrados por tiempo"


if __name__ == "__main__":
    print(envuelto("escalate", correr))
