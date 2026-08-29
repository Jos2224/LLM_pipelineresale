#!/usr/bin/env python3
"""Prueba de humo del indice por specs, contra la base real.

Mete 12 T480 falsos (6 flacos, 6 gordos), corre price_index y score de verdad,
y comprueba que:
  - se creen los DOS estantes ademas del general
  - un T480 flaco a 120.000 ya NO se marque como oportunidad
  - un T480 gordo a 150.000 SI se marque
  - un tramo sin muestras caiga al modelo ajustado, no al modelo pelado
Al final borra todo lo que creo.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/app")

from app.db import ex, q, q1                     # noqa: E402
from app.jobs import price_index, score          # noqa: E402

MARCA, MODELO = "SmokeLenovo", "SmokeThinkPad T480"
fallos = 0


def check(nombre: str, cond: bool, extra: str = "") -> None:
    global fallos
    if cond:
        print(f"✓ {nombre}")
    else:
        fallos += 1
        print(f"✖ {nombre}   {extra}")


def main() -> int:
    pid = q1("""INSERT INTO producto_canon (marca, modelo, categoria)
                VALUES (%s,%s,'notebook') RETURNING id""", (MARCA, MODELO))["id"]
    fid = q1("""INSERT INTO fuente (nombre, tipo) VALUES ('smoke_ml','ml')
                ON CONFLICT (nombre) DO UPDATE SET tipo='ml' RETURNING id""")["id"]

    FLACO = [279000, 289000, 295000, 299000, 310000, 315000]
    GORDO = [489000, 499000, 510000, 520000, 529000, 549000]
    for precio in FLACO:
        ex("""INSERT INTO precio_obs (producto, precio, estado, vendidos, origen,
                                      ram_gb, disco_gb, tramo)
              VALUES (%s,%s,'usado',0,'ml_activo',8,256,'r8-d256')""", (pid, precio))
    for precio in GORDO:
        ex("""INSERT INTO precio_obs (producto, precio, estado, vendidos, origen,
                                      ram_gb, disco_gb, tramo)
              VALUES (%s,%s,'usado',0,'ml_activo',32,1024,'r32-d1024')""", (pid, precio))

    items = []
    try:
        print(price_index.correr())
        idx = {r["tramo"]: r for r in q(
            "SELECT tramo, p50, n_muestras, coef_spec, spec_ref FROM indice_precio WHERE producto=%s",
            (pid,))}
        check(f"se crearon los estantes: {sorted(idx)}",
              set(idx) == {"*", "r8-d256", "r32-d1024"}, str(sorted(idx)))
        check(f"el estante flaco tiene su propio P50: {idx['r8-d256']['p50']:,.0f}",
              280000 < float(idx["r8-d256"]["p50"]) < 320000)
        check(f"el estante gordo tiene el suyo: {idx['r32-d1024']['p50']:,.0f}",
              480000 < float(idx["r32-d1024"]["p50"]) < 560000)
        check(f"el general queda en el medio: {idx['*']['p50']:,.0f}",
              380000 < float(idx["*"]["p50"]) < 430000)
        check(f"midio la pendiente de specs del producto: {idx['*']['coef_spec']}",
              idx["*"]["coef_spec"] is not None)

        # --- score con items reales ---
        def item(titulo, precio, specs, tramo):
            i = q1("""INSERT INTO item_raw (fuente, url, id_externo, titulo, precio,
                                            crudo, hash, normalizado)
                      VALUES (%s,%s,%s,%s,%s,%s,%s,true) RETURNING id""",
                   (fid, f"http://smoke/{titulo}", titulo, titulo, precio,
                    json.dumps({"producto": pid, "specs": specs, "tramo": tramo}),
                    f"smoke-{titulo}"))["id"]
            items.append(i)
            return i

        i_flaco = item("flaco120", 120000, {"ram_gb": 8, "disco_gb": 256}, "r8-d256")
        i_gordo = item("gordo150", 150000, {"ram_gb": 32, "disco_gb": 1024}, "r32-d1024")
        # Tramo que no tiene estante propio: debe caer al modelo AJUSTADO.
        i_medio = item("medio150", 150000, {"ram_gb": 16, "disco_gb": 512}, "r16-d512")

        print(score.correr())

        def op(iid):
            return q1("SELECT multiplo, v_liq FROM oportunidad WHERE item_raw=%s", (iid,))

        def origen(iid):
            return (q1("SELECT crudo FROM item_raw WHERE id=%s", (iid,))["crudo"] or {})

        check("el T480 flaco a 120.000 ya NO es oportunidad", op(i_flaco) is None,
              str(op(i_flaco)))
        o_gordo = op(i_gordo)
        check(f"el T480 gordo a 150.000 SI lo es ({float(o_gordo['multiplo']):.2f}x)"
              if o_gordo else "el T480 gordo a 150.000 SI lo es", o_gordo is not None)
        check(f"y usa el estante exacto: {origen(i_gordo).get('p50_origen')}",
              str(origen(i_gordo).get("p50_origen", "")).startswith("estante r32-d1024"))
        check(f"el flaco tambien miro su estante: {origen(i_flaco).get('p50_origen')}",
              str(origen(i_flaco).get("p50_origen", "")).startswith("estante r8-d256"))
        om = origen(i_medio)
        check(f"el 16GB/512 sin estante cae al modelo AJUSTADO: {om.get('p50_origen')}",
              "ajustado" in str(om.get("p50_origen", "")), str(om.get("p50_origen")))
        check(f"y el ajuste lo deja entre flaco y gordo: {om.get('p50_usado'):,}",
              300000 < float(om.get("p50_usado", 0)) < 520000)

        print(f"\n{'TODO OK' if not fallos else str(fallos) + ' FALLOS'}")
        return fallos
    finally:
        ex("DELETE FROM oportunidad WHERE producto=%s", (pid,))
        for i in items:
            ex("DELETE FROM item_raw WHERE id=%s", (i,))
        ex("DELETE FROM indice_precio WHERE producto=%s", (pid,))
        ex("DELETE FROM precio_obs WHERE producto=%s", (pid,))
        ex("DELETE FROM producto_canon WHERE id=%s", (pid,))
        ex("DELETE FROM fuente WHERE nombre='smoke_ml'")
        print("datos de prueba borrados")


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
