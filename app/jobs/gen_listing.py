"""Script 21 — el LLM escribe titulo, descripcion y bullets.

Reparto de trabajo:
  Ollama  -> texto (titulo <= 60 char, descripcion, bullets)
  API ML  -> categoria y atributos obligatorios (domain_discovery)
  codigo  -> precio (pricing.py). El modelo NUNCA ve ni escribe un precio.

Deja todo en publicacion con estado 'borrador'. Nadie publica aca.
"""
from __future__ import annotations

from app import llm, ml_api
from app import specs as sp
from app.db import ex, q, q1
from app.jobs import envuelto
from app.pricing import piso_default, precio_lista

LOTE = 10

PROMPT = """Escribe una publicacion de MercadoLibre Chile para este producto usado.

Producto: {titulo}
Condicion: {condicion}
Datos conocidos: {specs}

Devuelve JSON con:
  "titulo": maximo 60 caracteres, sin signos de exclamacion, sin MAYUSCULAS
            completas, sin la palabra "oferta". Marca + modelo + 2 specs clave.
  "bullets": lista de 4 frases cortas con lo concreto (specs, estado, que incluye)
  "descripcion": 3 parrafos cortos. Honesto sobre el uso. Sin inventar
                 garantias, sin precios, sin datos de contacto.

Nunca inventes specs que no esten en los datos conocidos."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "titulo": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "descripcion": {"type": "string"},
    },
    "required": ["titulo", "bullets", "descripcion"],
}


def correr() -> str:
    pend = q(
        """SELECT inv.id, inv.codigo, inv.titulo, inv.condicion, inv.producto, inv.piso_precio,
                  pc.marca, pc.modelo, pc.specs
           FROM inventario inv
           LEFT JOIN producto_canon pc ON pc.id = inv.producto
           -- Sin join a indice_precio: desde que hay una fila por tramo, ese
           -- join DUPLICABA cada item del inventario tantas veces como
           -- estantes tuviera su modelo. El P50 se pide item por item mas
           -- abajo, con las specs de ese equipo.
           LEFT JOIN publicacion p ON p.inventario = inv.id AND p.marketplace = 'ml'
           WHERE inv.estado = 'listo' AND p.id IS NULL
           LIMIT %s""",
        (LOTE,),
    )
    if not pend:
        return "nada listo para redactar"
    if not llm.vivo():
        return "ollama caido"

    hechos = 0
    for inv in pend:
        base = f"{inv['marca'] or ''} {inv['modelo'] or ''}".strip() or inv["titulo"]
        d = llm.json_de(
            PROMPT.format(titulo=base, condicion=inv["condicion"], specs=inv["specs"] or {}),
            esquema=ESQUEMA, modelo=llm.modelo_de("redactar"),
        )
        if not d:
            continue

        titulo = (d.get("titulo") or base)[:60]
        cuerpo = (d.get("descripcion") or "")
        bullets = "\n".join(f"• {b}" for b in (d.get("bullets") or [])[:6])
        desc = f"{bullets}\n\n{cuerpo}".strip()

        # El P50 del estante de ESTE equipo, no el del modelo en general.
        p50, _ = sp.precio_mercado(inv["producto"], inv["specs"] or {})
        piso = float(inv["piso_precio"]) if inv["piso_precio"] else (piso_default(p50) if p50 else None)
        precio = precio_lista(p50, piso) if p50 else None

        cat = None
        try:
            c = ml_api.categoria_de(titulo)
            cat = c.get("category_id") if c else None
        except Exception:
            pass

        ex(
            """INSERT INTO publicacion (inventario, marketplace, titulo, descripcion, precio, estado)
               VALUES (%s,'ml',%s,%s,%s,'borrador')""",
            (inv["id"], titulo, desc, precio),
        )
        ex("UPDATE inventario SET estado = 'borrador', piso_precio = COALESCE(piso_precio, %s) WHERE id = %s",
           (piso, inv["id"]))
        if cat:
            ex("UPDATE producto_canon SET ml_categoria = %s WHERE id = %s AND ml_categoria IS NULL",
               (cat, inv["producto"]))
        hechos += 1
    return f"{hechos} borradores redactados"


if __name__ == "__main__":
    print(envuelto("gen_listing", correr))
