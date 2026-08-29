"""Prueba de humo del Bloque 1 con datos falsos. No toca internet.

  docker compose run --rm worker python bin/smoke.py

Mete 7 publicaciones inventadas de un mismo modelo (6 caras, 1 regalada),
corre normalize -> price_index -> score y muestra si detecto la ganga.
"""
import hashlib
import json

from app.db import ex, q
from app.jobs import normalize, price_index, score

FALSOS = [
    ("Notebook Lenovo ThinkPad T480 i5 8va 16GB SSD 512GB", 420000),
    ("LENOVO THINKPAD T480 CORE I5 16GB RAM SSD 512 !!", 445000),
    ("Thinkpad T480 i5-8250U 16gb 512gb ssd impecable", 399000),
    ("Notebook Lenovo Thinkpad T480 16GB 512GB SSD garantia", 460000),
    ("Lenovo ThinkPad T480 i5 16GB SSD 512", 410000),
    ("Thinkpad T480 16GB/512GB usado excelente estado", 435000),
    ("Thinkpad T480 i5 16gb 512 ssd remato hoy urgente", 135000),  # barato, NO pasa el techo
    ("Thinkpad T480 i5 16gb 512gb ssd sin cargador", 70000),       # la ganga de verdad
]


def sembrar():
    ex("TRUNCATE oportunidad, precio_obs, indice_precio, item_raw, producto_canon RESTART IDENTITY CASCADE")
    fid = q("SELECT id FROM fuente WHERE nombre='mercadolibre'")[0]["id"]
    for i, (t, p) in enumerate(FALSOS):
        ex(
            """INSERT INTO item_raw (fuente, url, id_externo, titulo, precio, crudo, hash)
               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (hash) DO NOTHING""",
            (fid, f"https://ejemplo/{i}", f"SMOKE{i}", t, p,
             json.dumps({"condition": "used", "sold_quantity": 1}),
             hashlib.sha1(f"smoke{i}".encode()).hexdigest()),
        )
    print(f"sembrados {len(FALSOS)} items falsos")


def main():
    sembrar()
    print("normalize  :", normalize.correr())
    print("price_index:", price_index.correr())
    print("score      :", score.correr())

    print("\n--- indice ---")
    for r in q("""SELECT pc.marca, pc.modelo, ip.p25, ip.p50, ip.p80, ip.n_muestras
                  FROM indice_precio ip JOIN producto_canon pc ON pc.id=ip.producto"""):
        print(f"{r['marca']} {r['modelo']}: p25={r['p25']} p50={r['p50']} p80={r['p80']} n={r['n_muestras']}")

    print("\n--- oportunidades ---")
    filas = q("""SELECT i.titulo, i.precio, o.v_liq, o.p_max, o.score
                 FROM oportunidad o JOIN item_raw i ON i.id=o.item_raw
                 ORDER BY o.score DESC""")
    for r in filas:
        print(f"{r['titulo'][:52]:52} precio={int(r['precio']):>8} "
              f"p_max={int(r['p_max']):>8} score={float(r['score']):.2f}")
    if not filas:
        print("(ninguna — revisa que ollama este arriba)")

    print("\n--- lo que NO paso el filtro ---")
    for r in q("""SELECT i.titulo, i.precio FROM item_raw i
                  LEFT JOIN oportunidad o ON o.item_raw=i.id
                  WHERE o.id IS NULL AND i.precio IS NOT NULL ORDER BY i.precio"""):
        print(f"{r['titulo'][:52]:52} precio={int(r['precio']):>8}")

    prueba_negociacion()


def prueba_negociacion():
    """La escalera de ofertas y el candado que revisa lo que escribe el LLM."""
    from app.jobs.negociar_compra import _mensaje_oferta
    from app.pricing import objetivo, siguiente_oferta, techo, v_liquido

    p50 = 415000
    v = v_liquido(p50)
    t, o = techo(v), objetivo(v)
    print(f"\n--- negociacion (P50={p50:,}) ---".replace(",", "."))
    print(f"V_liq={int(v):,}  techo={int(t):,} (1,5x)  objetivo={int(o):,} (2x)".replace(",", "."))
    for r in (1, 2, 3, 4):
        of = siguiente_oferta(r, o, t)
        alerta = "  <-- PASA EL TECHO" if of > t else ""
        print(f"  ronda {r}: ofrece {int(of):,}{alerta}".replace(",", "."))

    print("\n--- mensajes que manda el bot ---")
    for ronda in (1, 2, 3):
        of = siguiente_oferta(ronda, o, t)
        esperado = f"${int(of):,}".replace(",", ".")
        msg = _mensaje_oferta(ronda, of, precio_fijo=False)
        print(f"  r{ronda} [{'OK' if esperado in msg else 'SIN EL NUMERO'}]: {msg}")
    print(f"  precio fijo: {_mensaje_oferta(1, siguiente_oferta(1, o, t), precio_fijo=True)}")

    print("\n--- guardia de numeros (specs no son plata) ---")
    from app.jobs.negociar_compra import _numeros
    caso = "Te ofrezco $134.875 por el ThinkPad T480 16GB 512GB"
    print(f"  «{caso}»\n  -> {sorted(n for n in _numeros(caso) if n >= 1000)}  (debe ser [134875])")


if __name__ == "__main__":
    main()
