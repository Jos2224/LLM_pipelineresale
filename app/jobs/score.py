"""Script 15 — el juez. Decide si algo es oportunidad o no.

Dos caminos:
  compra directa (ML / FB)  -> V_liq = 0.65 * P50 ; techo = V_liq / 2
  remate                    -> B = P0 * sqrt(G), bandas P25/P50/P80

De donde sale el P50 — y este es el punto que hace o deshace el multiplo:

  1. del estante EXACTO de ese equipo (mismo modelo, misma RAM, mismo disco)
     si ese estante tiene muestras suficientes. Es un dato medido.
  2. si no, de la mediana del modelo entero CORREGIDA por las specs, con los
     coeficientes medidos en ese mismo producto (ver app/specs.py).

Hasta el 28-ago solo existia el camino 2 sin correccion, o sea: un T480 de
8GB/256 se valoraba con la mediana de todos los T480, incluidos los de 32GB/1TB.

Cap inviolable: el techo nunca se recalcula hacia arriba. Si el precio lo pasa,
la oportunidad se cierra y no vuelve a avisar por mas que suba la puja.
"""
from __future__ import annotations

import json

from app import specs as sp
from app.config import p
from app.db import ex, q, q1
from app.jobs import envuelto
from app.pricing import banda_remate, evaluar

LOTE = 200


def _es_remate(crudo: dict, tipo: str) -> bool:
    return tipo == "aduanas" or bool((crudo or {}).get("remate"))


def _p50_para(pid: int, crudo: dict) -> tuple[float | None, str]:
    """El P50 de ESTE equipo. La logica vive en app/specs.py, unica fuente."""
    return sp.precio_mercado(pid, (crudo or {}).get("specs") or {})


def correr() -> str:
    candidatos = q(
        """SELECT i.id, i.titulo, i.precio, i.crudo, f.tipo
           FROM item_raw i
           JOIN fuente f ON f.id = i.fuente
           LEFT JOIN oportunidad o ON o.item_raw = i.id
           WHERE i.normalizado = true AND o.id IS NULL AND i.precio IS NOT NULL
           ORDER BY i.visto_en DESC LIMIT %s""",
        (LOTE,),
    )
    marcadas = increibles = baratos = 0
    tope_creible = float(p("compra.multiplo_maximo_creible", 12))
    valor_minimo = float(p("compra.valor_minimo", 50000))
    for it in candidatos:
        crudo = it["crudo"] or {}
        pid = crudo.get("producto")

        if _es_remate(crudo, it["tipo"]):
            g = crudo.get("g")
            b = banda_remate(float(it["precio"]), g)
            # En remate el "valor" es lo que se paga tipico (P50 de la banda),
            # y el techo de compra sale de ahi con la misma formula de margen.
            ev = evaluar(float(it["precio"]), b["p50"])
            ev["g_conocido"] = b["g_conocido"]
        else:
            if not pid:
                continue
            p50, origen = _p50_para(pid, crudo)
            if not p50:
                continue
            ev = evaluar(float(it["precio"]), p50)
            ev["g_conocido"] = True
            # De donde salio el numero queda anotado en el item, y la alerta lo
            # muestra: no es lo mismo un P50 medido que uno estimado.
            ex("UPDATE item_raw SET crudo = crudo || %s WHERE id = %s",
               (json.dumps({"p50_origen": origen, "p50_usado": round(p50)}), it["id"]))

        if not ev["oportunidad"]:
            continue

        # Piso de valor: bajo esto no vale el viaje, por buen multiplo que dé.
        if ev["v_liq"] < valor_minimo:
            baratos += 1
            continue

        # Freno de cordura: un multiplo imposible es un precio mal leido, no
        # una ganga. Medido el 28-ago: una tarjeta de FB dejo un precio de
        # $240 en un ThinkPad E14 y salio 1486x. Se descarta y queda anotado.
        if ev["multiplo"] > tope_creible:
            increibles += 1
            ex("""UPDATE item_raw SET crudo = crudo || %s WHERE id = %s""",
               (json.dumps({"descartado": f"multiplo increible {ev['multiplo']:.0f}x "
                                          f"con precio {it['precio']}"}), it["id"]))
            continue

        ex(
            """INSERT INTO oportunidad (item_raw, producto, v_liq, p_max, objetivo,
                                        multiplo, score, g_conocido)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (item_raw) DO NOTHING""",
            (it["id"], pid, ev["v_liq"], ev["p_max"], ev["objetivo"],
             ev["multiplo"], ev["multiplo"], ev["g_conocido"]),
        )
        marcadas += 1

    # Higiene: si el precio subio por sobre el techo ya calculado, la
    # oportunidad muere. El techo NO se toca. Una negociacion en curso no se
    # mata aca: esa la cierra negociar_compra.py con su propia regla.
    muertas = ex(
        """UPDATE oportunidad o SET estado = 'ignorar'
           FROM item_raw i
           WHERE o.item_raw = i.id AND o.estado IN ('nueva','avisada','watchlist')
             AND i.precio > o.p_max""",
    )
    extra = f", {increibles} por multiplo increible" if increibles else ""
    extra += f", {baratos} bajo el piso de valor" if baratos else ""
    return (f"{len(candidatos)} evaluados, {marcadas} oportunidades, "
            f"{muertas} pasadas de precio{extra}")


if __name__ == "__main__":
    print(envuelto("score", correr))
