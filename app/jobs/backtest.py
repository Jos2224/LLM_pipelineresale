"""Script 42 — el modelo se corrige solo.

Compara lo que tu formula B = P0 * sqrt(G) predijo contra lo que el remate
cerro de verdad. Con >= 20 cierres ajusta los factores p25/p50/p80 y te
propone el cambio por Telegram. NO los cambia solo: un ajuste automatico de la
formula que decide compras es exactamente donde un bug se come tu plata.
"""
from __future__ import annotations

import statistics

from app import tg
from app.config import CONFIG, p
from app.db import q
from app.jobs import envuelto

MIN_CIERRES = 20


def correr() -> str:
    cierres = q(
        """SELECT p0, g, precio_real FROM remate_cierre
           WHERE precio_real IS NOT NULL AND p0 > 0 AND g > 0
             AND fecha > now() - interval '365 days'"""
    )
    if len(cierres) < MIN_CIERRES:
        return f"{len(cierres)}/{MIN_CIERRES} cierres — falta muestra"

    # ratio real = precio_real / (P0 * sqrt(G))
    ratios = sorted(float(c["precio_real"]) / (float(c["p0"]) * float(c["g"]) ** 0.5) for c in cierres)
    n = len(ratios)

    def pct(f: float) -> float:
        i = f * (n - 1)
        lo, hi = int(i), min(n - 1, int(i) + 1)
        return ratios[lo] + (ratios[hi] - ratios[lo]) * (i - lo)

    nuevo = {"p25": round(pct(0.25), 3), "p50": round(statistics.median(ratios), 3), "p80": round(pct(0.80), 3)}
    viejo = {k: float(p(f"remate.{k}", 0)) for k in nuevo}
    desvio = max(abs(nuevo[k] - viejo[k]) / max(viejo[k], 0.001) for k in nuevo)

    if desvio < 0.08:
        return f"modelo calibrado (desvio {desvio:.1%} sobre {n} cierres)"

    tg.CAZADOR.enviar(
        f"📐 el modelo de remate se desvio {desvio:.0%} en {n} cierres\n\n"
        f"actual:    p25={viejo['p25']}  p50={viejo['p50']}  p80={viejo['p80']}\n"
        f"medido:    p25={nuevo['p25']}  p50={nuevo['p50']}  p80={nuevo['p80']}\n\n"
        f"Si aceptas, escribo los nuevos en {CONFIG.name}/policy.yml.",
        tg.teclado([[("✅ Aplicar", f"cal_ok:{nuevo['p25']}_{nuevo['p50']}_{nuevo['p80']}"),
                     ("✖ Dejar igual", "cal_no:0")]]),
    )
    return f"propuesta de calibracion enviada (desvio {desvio:.0%})"


if __name__ == "__main__":
    print(envuelto("backtest", correr))
