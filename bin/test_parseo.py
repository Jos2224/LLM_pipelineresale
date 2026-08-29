"""Prueba del lector de respuestas del vendedor. Sin red, sin LLM.

  docker compose run --rm -e PYTHONPATH=/app -v ./bin:/app/bin:ro worker python /app/bin/test_parseo.py

Cada caso es algo que un vendedor de ML Chile contesta de verdad.
Lo que salga con confianza < 0.6 es lo que se le manda al 8B.
"""
from app.parseo import leer_respuesta_vendedor

PEDIDO = 415000

# (respuesta, disponible, acepta_ofertas, precio, minimo de confianza)
CASOS = [
    ("Hola, si sigue disponible", True, True, None, 0.6),
    ("si", True, True, None, 0.6),
    ("Ya se vendio, gracias", False, True, None, 0.6),
    ("no lo tengo", False, True, None, 0.6),
    ("Precio fijo, no negociable", True, False, None, 0.6),
    ("Disponible pero el precio es fijo", True, False, None, 0.6),
    ("Te lo dejo en 350 lucas", True, True, 350000, 0.6),
    ("lo dejo en 350.000 y es lo ultimo", True, True, 350000, 0.6),
    ("son 380 mil, no bajo mas", True, False, 380000, 0.6),
    ("Si esta, el T480 tiene 16GB y 512GB ssd, valor 350000", True, True, 350000, 0.6),
    ("dale, trato hecho", True, True, None, 0.6),
    ("1 palo y es tuyo", True, True, 1000000, 0.6),
    ("te lo dejo en 300", True, True, 300000, 0.6),
    # Este es de los que deben caer al 8B: largo, sin ninguna señal clara.
    ("Buenas, mira, lo estuve conversando con mi señora y la verdad es que "
     "estamos viendo varias cosas todavia, te aviso cualquier cosa",
     True, True, None, 0.0),
]


def main():
    ok = fallos = al_llm = 0
    for texto, disp, acepta, precio, conf_min in CASOS:
        d = leer_respuesta_vendedor(texto, PEDIDO)
        mal = []
        if d["disponible"] != disp:
            mal.append(f"disponible={d['disponible']} esperado {disp}")
        if d["acepta_ofertas"] != acepta:
            mal.append(f"acepta_ofertas={d['acepta_ofertas']} esperado {acepta}")
        if d["precio"] != precio:
            mal.append(f"precio={d['precio']} esperado {precio}")
        if conf_min and d["confianza"] < conf_min:
            mal.append(f"confianza={d['confianza']} deberia ser >= {conf_min}")
        if d["confianza"] < 0.6:
            al_llm += 1
        if mal:
            fallos += 1
            print(f"\n✖ «{texto[:60]}»")
            for m in mal:
                print(f"    {m}")
            print(f"    salio: {d}")
        else:
            ok += 1
    print(f"\n{ok}/{len(CASOS)} casos OK · {fallos} fallos · {al_llm} irian al 8B")
    return fallos


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
