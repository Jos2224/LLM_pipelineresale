"""Script 13 — de titulo sucio a producto identificado.

"NOTEBOOK LENOVO THINKPAD T480 i5 8VA 16GB SSD 256 !!OFERTA!!"
        -> {marca: Lenovo, modelo: ThinkPad T480, ram_gb: 16, disco_gb: 256}

Dos etapas, en este orden:

  1. `app/extract.py`  — reglas puras. Resuelve la gran mayoria en 0,3 ms,
                         siempre igual, sin inventar nada.
  2. Ollama 8B         — SOLO lo que las reglas no reconocieron (confianza
                         bajo 0.6): marca nueva, formato raro, titulo mudo.

El 8B en esta maquina da 5,2 tok/s. Mandarle los 40 items de cada ciclo serian
20 minutos; mandarle los 3 raros son 40 segundos. Por eso el orden importa.

Cuando el 8B resuelve algo que las reglas no supieron, queda anotado en
`crudo.via = 'llm'`. Esa lista es el material para agregar el caso a
extract.py y que la proxima vez ya no necesite al modelo.

El match contra el catalogo siempre lo hace Postgres con trigramas, que es
deterministico y repetible.
"""
from __future__ import annotations

import json

from app import llm

from app.db import ex, q, q1
from app.extract import extraer
from app.jobs import envuelto
from app.specs import tramo

LOTE = 40
CONFIANZA_MINIMA = 0.6
MAX_LLM_POR_CICLO = 8   # freno: el 8B es lento, no lo dejamos tapar el ciclo

ESQUEMA = {
    "type": "object",
    "properties": {
        "marca": {"type": "string"},
        "modelo": {"type": "string"},
        "categoria": {"type": "string"},
        "condicion": {"type": "string", "enum": ["nuevo", "usado", "reacondicionado", "desconocido"]},
    },
    "required": ["marca", "modelo", "categoria", "condicion"],
}

PROMPT = """Eres un catalogador de electronica usada en Chile.
Del titulo de abajo extrae marca, modelo, categoria y condicion.

Reglas:
- marca: la marca REAL del fabricante. ThinkPad -> Lenovo. MacBook -> Apple.
  Latitude -> Dell. EliteBook -> HP. Galaxy -> Samsung.
- modelo: el identificador comercial exacto, sin repetir la marca.
- categoria: notebook, celular, monitor, tablet, computador, impresora,
  componente, accesorio u otro.
- condicion: nuevo, usado, reacondicionado o desconocido.
- Si no estas seguro, pon "desconocido". No inventes.

Titulo: {titulo}
Responde solo JSON."""

# El modelo chico confunde la linea con la marca. Se corrige aca tambien por si
# el LLM (que es el unico que llega a esta funcion sin pasar por extract.py)
# vuelve a hacerlo.
from app.extract import LINEAS  # noqa: E402


def _normaliza(marca: str, modelo: str) -> tuple[str, str]:
    marca = (marca or "").strip().title()[:60]
    modelo = " ".join((modelo or "").split())[:120]
    baja = marca.lower()
    if baja in LINEAS:
        if not modelo.lower().startswith(baja):
            modelo = f"{marca} {modelo}".strip()
        marca = LINEAS[baja][0]
    if modelo.lower().startswith(marca.lower() + " "):
        modelo = modelo[len(marca) + 1:].strip()
    return marca, modelo


def _canon(marca: str, modelo: str, categoria: str, specs: dict) -> int | None:
    marca, modelo = _normaliza(marca, modelo)
    if not marca or not modelo or "desconoc" in modelo.lower() or "desconoc" in marca.lower():
        return None

    # Match difuso: "ThinkPad T480s" y "Thinkpad  T480 S" son el mismo estante.
    fila = q1(
        """SELECT id FROM producto_canon
           WHERE lower(marca) = lower(%s) AND similarity(lower(modelo), lower(%s)) > 0.72
           ORDER BY similarity(lower(modelo), lower(%s)) DESC LIMIT 1""",
        (marca, modelo, modelo),
    )
    if fila:
        return fila["id"]

    # Segunda pasada ignorando la marca: agarra "HP" vs "Hewlett Packard".
    fila = q1(
        """SELECT id FROM producto_canon
           WHERE similarity(lower(marca || ' ' || modelo), lower(%s)) > 0.85
           ORDER BY similarity(lower(marca || ' ' || modelo), lower(%s)) DESC LIMIT 1""",
        (f"{marca} {modelo}", f"{marca} {modelo}"),
    )
    if fila:
        return fila["id"]

    fila = q1(
        """INSERT INTO producto_canon (marca, modelo, categoria, specs)
           VALUES (%s,%s,%s,%s)
           ON CONFLICT (marca, modelo) DO UPDATE SET categoria = EXCLUDED.categoria
           RETURNING id""",
        (marca, modelo, (categoria or "otro")[:40], json.dumps(specs or {})),
    )
    return fila["id"] if fila else None


def _condicion_cruda(crudo: dict) -> str | None:
    """ML ya trae la condicion en el item. Es mas confiable que el titulo."""
    c = (crudo or {}).get("condition")
    return {"new": "nuevo", "used": "usado", "refurbished": "reacondicionado"}.get(c)


def correr() -> str:
    pendientes = q(
        """SELECT i.id, i.titulo, i.precio, i.crudo, f.tipo
           FROM item_raw i JOIN fuente f ON f.id = i.fuente
           WHERE i.normalizado = false ORDER BY i.visto_en DESC LIMIT %s""",
        (LOTE,),
    )
    if not pendientes:
        return "nada pendiente"

    por_regla = por_llm = sin_match = 0
    presupuesto_llm = MAX_LLM_POR_CICLO
    llm_arriba = None   # se consulta una sola vez y solo si hace falta

    for it in pendientes:
        d = extraer(it["titulo"])
        via = "regla"

        # --- el borde: lo que las reglas no supieron leer ---
        if d["confianza"] < CONFIANZA_MINIMA and presupuesto_llm > 0:
            if llm_arriba is None:
                llm_arriba = llm.vivo()
            if llm_arriba:
                presupuesto_llm -= 1
                r = llm.json_de(PROMPT.format(titulo=it["titulo"]),
                                esquema=ESQUEMA, modelo=llm.modelo_de("catalogar"))
                if r and r.get("marca") and r.get("modelo"):
                    # Las specs siguen saliendo de las reglas: el LLM inventa
                    # numeros y aca un numero malo ensucia el indice de precios.
                    d = {**d, "marca": r["marca"], "modelo": r["modelo"],
                         "categoria": r.get("categoria") or d["categoria"],
                         "condicion": r.get("condicion") or d["condicion"]}
                    via = "llm"

        ex("UPDATE item_raw SET normalizado = true WHERE id = %s", (it["id"],))

        pid = _canon(d["marca"] or "", d["modelo"] or "", d["categoria"], d["specs"])
        if not pid:
            sin_match += 1
            continue

        if via == "llm":
            por_llm += 1
        else:
            por_regla += 1

        # Solo los items de ML alimentan el indice de precios: son el mercado.
        # Un remate o un aviso de FB es una compra, no una referencia de venta.
        if it["tipo"] == "ml" and it["precio"]:
            cond = _condicion_cruda(it["crudo"]) or d["condicion"]
            # Las specs viajan con el precio: sin ellas el indice mezcla un
            # 8GB/256 con un 32GB/1TB y la mediana no significa nada.
            ex(
                """INSERT INTO precio_obs (producto, precio, estado, vendidos, origen,
                                           ram_gb, disco_gb, tramo)
                   VALUES (%s,%s,%s,%s,'ml_activo',%s,%s,%s)""",
                (pid, it["precio"], cond, (it["crudo"] or {}).get("sold_quantity") or 0,
                 d["specs"].get("ram_gb"), d["specs"].get("disco_gb"), tramo(d["specs"])),
            )
        # Las specs quedan en el item para que score.py sepa a que estante
        # mirar sin tener que volver a leer el titulo.
        ex("UPDATE item_raw SET crudo = crudo || %s WHERE id = %s",
           (json.dumps({"producto": pid, "via": via, "confianza": d["confianza"],
                        "specs": d["specs"], "tramo": tramo(d["specs"])}), it["id"]))

    return (f"{por_regla} por reglas, {por_llm} por 8B, {sin_match} sin match "
            f"({len(pendientes)} vistos)")


if __name__ == "__main__":
    print(envuelto("normalize", correr))
