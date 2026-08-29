"""Script 35 — tus ventas cerradas de ML, que son el mejor precio que existe.

Por que hizo falta escribir esto: el 28-ago se descubrio que **MercadoLibre
cerro su API de busqueda publica**. `/sites/MLC/search`, `/items/{id}` y hasta
`/sites/MLC/categories` devuelven 403 `PolicyAgent`, con token y sin token. O
sea que el precio de mercado ya NO se puede leer de ML como se hacia antes.

Lo que ML si deja ver de tu cuenta:

    /orders/search   tus ventas          <- esto
    /questions/search tus preguntas
    /users/me        tu usuario

Y resulta que tus ventas son la MEJOR fuente de precio que puede existir, mejor
que la que se perdio:

    una publicacion activa dice lo que alguien PIDE
    una venta cerrada dice lo que alguien PAGO

Todo el indice se construia con precios pedidos, con la nota honesta de que era
"la mejor aproximacion posible". Esto no es una aproximacion: es el dato.

Por eso estas observaciones entran con peso alto (`vendidos`), que en
price_index.py ya multiplica su influencia hasta 3 veces.

Limitacion, dicha claro: solo ves TUS ventas. Al principio son pocas o ninguna,
asi que el sistema sigue apoyandose en Facebook. A medida que vendas, este
indice se vuelve el bueno y el de FB pasa a respaldo.
"""
from __future__ import annotations

import json

from app import ml_api
from app.db import ex, q1
from app.extract import extraer
from app.jobs import envuelto
from app.specs import tramo

DIAS = 180        # medio año de ventas propias
LIMITE = 50


def _canon(titulo: str) -> tuple[int | None, dict]:
    """Identifica el producto vendido con las mismas reglas de siempre."""
    d = extraer(titulo or "")
    marca, modelo = (d.get("marca") or "").strip(), (d.get("modelo") or "").strip()
    if not marca or not modelo:
        return None, d.get("specs") or {}
    fila = q1(
        """SELECT id FROM producto_canon
           WHERE lower(marca)=lower(%s) AND similarity(lower(modelo), lower(%s)) > 0.72
           ORDER BY similarity(lower(modelo), lower(%s)) DESC LIMIT 1""",
        (marca, modelo, modelo))
    if fila:
        return fila["id"], d.get("specs") or {}
    fila = q1(
        """INSERT INTO producto_canon (marca, modelo, categoria, specs)
           VALUES (%s,%s,%s,%s) ON CONFLICT (marca, modelo) DO UPDATE
             SET categoria = EXCLUDED.categoria RETURNING id""",
        (marca[:60], modelo[:120], (d.get("categoria") or "otro")[:40],
         json.dumps(d.get("specs") or {})))
    return (fila["id"] if fila else None), d.get("specs") or {}


def correr() -> str:
    # Solo SinToken significa "no hay login". Atrapar Exception a secas hacia
    # que un error de programacion (una funcion mal escrita) se reportara como
    # "sin login de ML todavia" y mandara a buscar el problema al lado
    # equivocado. Paso el 28-ago con este mismo archivo.
    try:
        uid = ml_api.usuario_id()
    except ml_api.SinToken:
        return "sin login de ML todavia"
    if not uid:
        return "sin login de ML todavia"

    try:
        d = ml_api._get("/orders/search", {
            "seller": uid, "order.status": "paid", "sort": "date_desc", "limit": LIMITE})
    except Exception as e:
        return f"no pude leer las ventas: {str(e)[:120]}"

    nuevas = sin_match = 0
    for o in d.get("results", []):
        for it in o.get("order_items", []):
            item = it.get("item") or {}
            titulo = item.get("title") or ""
            # unit_price es lo que se PAGO de verdad, no lo que se pedia.
            precio = it.get("unit_price") or item.get("price")
            iid = item.get("id")
            if not titulo or not precio or not iid:
                continue

            pid, specs = _canon(titulo)
            if not pid:
                sin_match += 1
                continue

            # id_externo de la venta como llave: una orden se lee muchas veces
            # y no puede sumar una observacion nueva cada vez.
            marca_unica = f"venta:{o.get('id')}:{iid}"
            fila = q1(
                """INSERT INTO precio_obs (producto, precio, estado, vendidos, origen,
                                           ram_gb, disco_gb, tramo, mercado)
                   VALUES (%s,%s,'usado',3,%s,%s,%s,%s,'ml')
                   ON CONFLICT DO NOTHING RETURNING id""",
                (pid, precio, marca_unica, specs.get("ram_gb"), specs.get("disco_gb"),
                 tramo(specs)))
            if fila:
                nuevas += 1
            # Y la publicacion queda marcada como vendida, para que sync_stock
            # pause la gemela de Facebook y no la vendas dos veces.
            ex("""UPDATE publicacion SET estado='vendida'
                  WHERE marketplace='ml' AND id_externo=%s AND estado <> 'vendida'""", (iid,))

    return f"{nuevas} ventas nuevas al indice, {sin_match} sin identificar"


if __name__ == "__main__":
    print(envuelto("ventas_ml", correr))
