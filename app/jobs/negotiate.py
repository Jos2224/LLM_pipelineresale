"""Script 32 — negocia con reglas duras. El LLM no participa.

  oferta >= piso            -> acepta
  entre 90% y 100% del piso -> contraoferta al piso exacto
  bajo 90%                  -> rechaza corto y educado

Por que sin LLM: un modelo de 4B se deja convencer. "porfa, es para mi mama"
y regala tu margen. Una comparacion numerica no.

Con modo.negociar_auto = false (el default) no manda nada: deja la respuesta
escrita y te la ofrece por Telegram con boton [Enviar].
"""
from __future__ import annotations

from app import tg
from app.config import p
from app.db import ex, q, q1
from app.jobs import envuelto
from app.pricing import decidir_oferta

PLANTILLA = {
    "aceptar": "Hola, acepto. Te dejo el producto reservado, coordinamos entrega.",
    "contraoferta": "Hola, gracias por la oferta. Te lo puedo dejar en ${monto}. Es mi ultimo precio.",
    "rechazar": "Hola, gracias, pero a ese precio no me da. El valor publicado ya esta ajustado.",
}


def correr() -> str:
    auto = bool(p("modo.negociar_auto", False))
    ofertas = q(
        """SELECT m.id, m.texto, m.monto_oferta, m.publicacion, p.titulo, inv.piso_precio
           FROM mensaje m
           JOIN publicacion p ON p.id = m.publicacion
           JOIN inventario inv ON inv.id = p.inventario
           WHERE m.tipo='oferta' AND m.estado='nuevo' AND m.monto_oferta IS NOT NULL
             AND p.marketplace='ml'"""
        # Solo ML: las ofertas de Facebook las resuelve reply_fb en el mismo
        # ciclo en que las lee, con estas mismas reglas. Sin este filtro los
        # dos scripts contestarian la misma oferta.
    )
    resueltas = 0
    for o in ofertas:
        piso = float(o["piso_precio"] or 0)
        accion, contra = decidir_oferta(float(o["monto_oferta"]), piso)
        if accion == "escalar":
            ex("UPDATE mensaje SET estado='escalado' WHERE id=%s", (o["id"],))
            continue

        texto = PLANTILLA[accion].replace("${monto}", f"{int(contra or 0):,}".replace(",", "."))
        if auto:
            ex("""INSERT INTO mensaje (publicacion, direccion, tipo, texto, canal,
                                       responde_a, respondido_por, estado)
                  VALUES (%s,'sale','oferta',%s,'ml',%s,'bot','respondido')""",
               (o["publicacion"], texto, o["id"]))
            ex("UPDATE mensaje SET estado='respondido' WHERE id=%s", (o["id"],))
        else:
            # El boton tiene que apuntar al BORRADOR, no a la oferta: es el
            # borrador el que tiene el texto que hay que mandar.
            salida = q1(
                """INSERT INTO mensaje (publicacion, direccion, tipo, texto, canal,
                                        responde_a, respondido_por, estado)
                   VALUES (%s,'sale','oferta',%s,'ml',%s,'bot','nuevo') RETURNING id""",
                (o["publicacion"], texto, o["id"]))["id"]
            tg.PUBLICADOR.enviar(
                f"💬 oferta {int(float(o['monto_oferta'])):,} sobre piso {int(piso):,}\n"
                f"«{o['titulo'][:70]}»\n\nRegla dice: <b>{accion}</b>\n\n{texto}".replace(",", "."),
                tg.teclado([[("📤 Enviar", f"msg_enviar:{salida}"),
                             ("✏️ Yo respondo", f"msg_mio:{o['id']}")]]),
            )
            ex("UPDATE mensaje SET estado='escalado' WHERE id=%s", (o["id"],))
        resueltas += 1
    return f"{len(ofertas)} ofertas, {resueltas} resueltas por regla"


if __name__ == "__main__":
    print(envuelto("negotiate", correr))
