"""Lectura DETERMINISTA de lo que escribe el vendedor. Sin LLM.

Un vendedor de ML responde con cinco o seis frases distintas siempre:
"sigue disponible", "precio fijo", "te lo dejo en 130 lucas", "ya se vendio",
"dale, listo". Eso se lee con reglas y sale igual todas las veces.

El 8B entra SOLO cuando estas reglas devuelven confianza baja, que es cuando
el vendedor escribio algo largo o ambiguo. Ahi si vale gastarse los 10
segundos del modelo.

Chilenismos que importan y que un modelo generico se come:
  "130 lucas"  = 130.000      "130 mil" = 130.000
  "1 palo"     = 1.000.000    "en 130"  = 130.000 (si el pedido es de ese orden)
"""
from __future__ import annotations

import re

NO_DISPONIBLE = r"(ya\s*(se\s*)?(lo\s*)?vend|vendido|no\s*(lo\s*)?(esta|est[aá]|hay|tengo|queda)|" \
                r"no\s*disponible|se\s*me\s*fue|reservado|comprometido)"
DISPONIBLE = r"(sigue\s*disponible|si\s*,?\s*(esta|est[aá]|lo\s*tengo)|a[uú]n\s*(lo\s*tengo|est[aá])|" \
             r"disponible|todav[ií]a\s*(lo\s*tengo|est[aá]))"
PRECIO_FIJO = r"(precio\s*(es\s*)?fijo|no\s*(es\s*)?negociable|sin\s*rebaja|" \
              r"no\s*(hago|hay)\s*(rebaja|descuento)|es\s*el\s*precio|no\s*bajo)"
CIERRA = r"(trato\s*hecho|dale\s*(no\s*m[aá]s)?|listo|de\s*acuerdo|acepto|" \
         r"me\s*sirve|ya\s*po|cerramos|te\s*lo\s*dejo\s*en)"


def _plata_en(texto: str, referencia: float | None = None) -> int | None:
    """Saca la cifra de plata del mensaje, sin confundirla con specs.

    "te lo dejo en 130 lucas"       -> 130000
    "lo dejo en 350.000"            -> 350000
    "el T480 16GB sale 350 mil"     -> 350000  (ignora T480 y 16GB)
    """
    t = texto.lower()
    # Los numeros pegados a letras son modelo o specs, no plata.
    t = re.sub(r"(?<=[a-z])\d+|\d+(?=\s*(gb|tb|mb|ghz|mhz|w|mah|pulg|\"))", " ", t)

    m = re.search(r"(\d{1,4})\s*(?:lucas|luk|mil)\b", t)
    if m:
        return int(m.group(1)) * 1000
    m = re.search(r"(\d{1,3})\s*(?:palos?|millones?|mill[oó]n)\b", t)
    if m:
        return int(m.group(1)) * 1_000_000

    candidatos = []
    for m in re.finditer(r"\$?\s*(\d{1,3}(?:[.\s]\d{3})+|\d{4,9})", t):
        n = int(re.sub(r"[^0-9]", "", m.group(1)))
        if 1000 <= n <= 99_000_000:
            candidatos.append(n)
    if candidatos:
        return candidatos[0]

    # "te lo dejo en 130" — numero pelado que solo tiene sentido como miles.
    if referencia:
        m = re.search(r"\b(?:en|por|a)\s*\$?\s*(\d{2,3})\b", t)
        if m:
            n = int(m.group(1)) * 1000
            if 0.2 * referencia <= n <= 2 * referencia:
                return n
    return None


def leer_respuesta_vendedor(texto: str, pedido: float | None = None) -> dict:
    """Devuelve disponible / acepta_ofertas / precio / cierra / confianza."""
    t = " ".join((texto or "").lower().split())
    if not t:
        return {"disponible": True, "acepta_ofertas": True, "precio": None,
                "cierra": False, "confianza": 0.0}

    no_hay = bool(re.search(NO_DISPONIBLE, t))
    si_hay = bool(re.search(DISPONIBLE, t))
    fijo = bool(re.search(PRECIO_FIJO, t))
    cierra = bool(re.search(CIERRA, t))
    precio = _plata_en(t, pedido)

    señales = sum([no_hay, si_hay, fijo, cierra, precio is not None])
    # Un "si" pelado de tres letras tambien es respuesta, pero vale poco.
    corto_afirmativo = len(t) <= 12 and re.search(r"\b(si|sip|claro|obvio)\b", t)

    if no_hay:
        confianza = 0.9
    elif señales >= 2:
        confianza = 0.9
    elif señales == 1:
        confianza = 0.75
    elif corto_afirmativo:
        confianza = 0.7
    else:
        confianza = 0.3      # texto largo y raro -> que lo lea el 8B

    return {
        "disponible": not no_hay,
        "acepta_ofertas": not fijo,
        "precio": precio,
        "cierra": cierra,
        "confianza": round(confianza, 2),
    }
