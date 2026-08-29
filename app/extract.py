"""Extractor DETERMINISTA. Sin LLM.

De "NOTEBOOK LENOVO THINKPAD T480 i5 8VA 16GB SSD 256 !!OFERTA!!" saca
{marca: Lenovo, modelo: ThinkPad T480, ram_gb: 16, ssd_gb: 256, cpu: i5}
con reglas, no con un modelo.

Por que asi y no con el LLM:
  - mismo titulo -> mismo resultado, siempre. Un modelo cambia de opinion.
  - 0,3 ms contra 10-30 s. En un ciclo de 40 items eso es la diferencia entre
    un segundo y veinte minutos.
  - no inventa. El 8B en esta maquina da 5,2 tok/s y aun asi alucina specs.

El LLM entra SOLO cuando esto devuelve confianza baja, que es cuando aparece
una marca o un formato que la tabla no conoce todavia. Cada vez que pase, se
agrega aca y deja de necesitar al modelo para siempre.
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- marcas
# linea comercial -> (marca real, categoria por defecto)
LINEAS: dict[str, tuple[str, str]] = {
    "thinkpad": ("Lenovo", "notebook"), "ideapad": ("Lenovo", "notebook"),
    "thinkbook": ("Lenovo", "notebook"), "yoga": ("Lenovo", "notebook"),
    "legion": ("Lenovo", "notebook"), "thinkcentre": ("Lenovo", "computador"),
    "latitude": ("Dell", "notebook"), "inspiron": ("Dell", "notebook"),
    "vostro": ("Dell", "notebook"), "precision": ("Dell", "notebook"),
    "xps": ("Dell", "notebook"), "optiplex": ("Dell", "computador"),
    "elitebook": ("Hp", "notebook"), "probook": ("Hp", "notebook"),
    "pavilion": ("Hp", "notebook"), "zbook": ("Hp", "notebook"),
    "omen": ("Hp", "notebook"), "victus": ("Hp", "notebook"),
    "macbook": ("Apple", "notebook"), "imac": ("Apple", "computador"),
    "iphone": ("Apple", "celular"), "ipad": ("Apple", "tablet"),
    "airpods": ("Apple", "accesorio"), "watch": ("Apple", "accesorio"),
    "galaxy": ("Samsung", "celular"),
    "redmi": ("Xiaomi", "celular"), "poco": ("Xiaomi", "celular"),
    "vivobook": ("Asus", "notebook"), "zenbook": ("Asus", "notebook"),
    "tuf": ("Asus", "notebook"), "rog": ("Asus", "notebook"),
    "aspire": ("Acer", "notebook"), "nitro": ("Acer", "notebook"),
    "predator": ("Acer", "notebook"), "swift": ("Acer", "notebook"),
}

# marcas que aparecen solas, sin linea
MARCAS = {
    "lenovo": "Lenovo", "dell": "Dell", "hp": "Hp", "hewlett": "Hp",
    "apple": "Apple", "samsung": "Samsung", "xiaomi": "Xiaomi", "asus": "Asus",
    "acer": "Acer", "msi": "Msi", "toshiba": "Toshiba", "sony": "Sony",
    "lg": "Lg", "aoc": "Aoc", "benq": "Benq", "viewsonic": "Viewsonic",
    "kingston": "Kingston", "crucial": "Crucial", "corsair": "Corsair",
    "adata": "Adata", "sandisk": "Sandisk", "seagate": "Seagate",
    "western": "Western Digital", "wd": "Western Digital", "intel": "Intel",
    "amd": "Amd", "nvidia": "Nvidia", "logitech": "Logitech", "motorola": "Motorola",
    "huawei": "Huawei", "epson": "Epson", "canon": "Canon", "brother": "Brother",
}

# Como se ve el codigo de modelo de cada linea. El orden importa: el primero
# que matchea gana.
MODELOS: list[tuple[str, str]] = [
    # Lenovo ThinkPad: T480, T14 Gen 2, X1 Carbon Gen 9, E14, P52, L390
    ("thinkpad", r"\b(x1\s*(?:carbon|yoga|extreme)?(?:\s*gen\s*\d)?|[txpelw]\d{2,3}[siyep]?(?:\s*gen\s*\d)?)\b"),
    ("ideapad",  r"\b(\d{1,2}\s*(?:pro|slim)?|s\d{3}|flex\s*\d)\b"),
    ("latitude", r"\b(\d{4})\b"),
    ("inspiron", r"\b(\d{4})\b"),
    ("vostro",   r"\b(\d{4})\b"),
    ("elitebook", r"\b(\d{3,4}\s*(?:g\d{1,2})?)\b"),
    ("probook",  r"\b(\d{3,4}\s*(?:g\d{1,2})?)\b"),
    ("zbook",    r"\b(\w+\s*(?:g\d{1,2})?)\b"),
    ("macbook",  r"\b(air|pro)\b"),
    ("iphone",   r"\b(\d{1,2}\s*(?:pro\s*max|pro|plus|mini|se)?|se\s*\d?)\b"),
    ("ipad",     r"\b(air|mini|pro)?\b"),
    ("galaxy",   r"\b([sanjmz]\d{1,2}\s*(?:ultra|plus|fe|\+)?|note\s*\d{1,2}|tab\s*\w\d?)\b"),
    ("redmi",    r"\b(note\s*\d{1,2}\s*(?:pro|s)?|\d{1,2}[ac]?)\b"),
    ("vivobook", r"\b([a-z]?\d{3,4}\w*)\b"),
    ("zenbook",  r"\b([a-z]?\d{2,4}\w*)\b"),
    ("aspire",   r"\b([a-z]?\d{1,4}\w*)\b"),
    ("nitro",    r"\b(\d)\b"),
]

CATEGORIA_PALABRA = [
    (r"\b(notebook|laptop|portatil|ultrabook)\b", "notebook"),
    (r"\b(celular|smartphone|telefono)\b", "celular"),
    (r"\b(monitor|pantalla)\b", "monitor"),
    (r"\b(tablet|ipad)\b", "tablet"),
    (r"\b(impresora|multifuncional)\b", "impresora"),
    (r"\b(ssd|disco|hdd|nvme|m\.?2)\b", "componente"),
    (r"\b(ram|memoria|sodimm|dimm|ddr[345])\b", "componente"),
    (r"\b(placa\s*madre|motherboard|tarjeta\s*de\s*video|gpu|procesador)\b", "componente"),
    (r"\b(cargador|funda|teclado|mouse|cable|adaptador|dock)\b", "accesorio"),
    (r"\b(all\s*in\s*one|computador|pc\s*escritorio|torre)\b", "computador"),
]

CONDICION = [
    (r"\b(reacondicionado|refurbished|refurb|remanufacturado)\b", "reacondicionado"),
    (r"\b(nuevo|sellado|sin\s*abrir|en\s*caja)\b", "nuevo"),
    (r"\b(usado|segunda\s*mano|ocasion|semi\s*nuevo)\b", "usado"),
]

RE_CPU = re.compile(
    r"\b(i[3579])\s*[- ]?\s*(\d{4,5}\s*[a-z]{0,2})?\b"
    r"|\b(ryzen)\s*([3579])\s*(\d{4}[a-z]{0,2})?\b"
    r"|\b(celeron|pentium|athlon|xeon)\b"
    r"|\b(m[123])\s*(pro|max|ultra)?\b", re.I)
RE_PULGADAS = re.compile(r"\b(\d{2}(?:[.,]\d)?)\s*(?:\"|''|”|pulg\w*|inch)", re.I)
RE_ALMACEN = re.compile(r"\b(\d{1,4})\s*(gb|tb)\b", re.I)
RE_GEN = re.compile(r"\b(\d{1,2})\s*(?:va|ª|a)?\s*gen(?:eracion)?\b", re.I)

PAL_RAM = r"ram|memoria|sodimm|dimm|ddr[345]"
PAL_DISCO = r"ssd|hdd|nvme|disco\s*duro|disco|almacenamiento"


def _limpiar(t: str) -> str:
    t = t.lower()
    t = re.sub(r"[¡!¿?*]+", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _gb(n: str, unidad: str | None) -> int:
    return int(n) * 1024 if (unidad or "gb").lower() == "tb" else int(n)


def _specs(t: str) -> dict:
    """RAM y disco se separan por la palabra que tienen al lado.

    Cuando no hay palabra ("16gb 512gb"), manda la regla practica: de dos
    cifras, la chica es RAM y la grande es disco. Con una sola cifra y sin
    palabra, 64 GB o mas es disco (un celular de 256) y 32 o menos es RAM.
    """
    specs: dict = {}
    hay_ram = bool(re.search(rf"\b({PAL_RAM})\b", t))
    hay_disco = bool(re.search(rf"\b({PAL_DISCO})\b", t))

    # --- explicitos: la palabra pegada al numero manda
    m = (re.search(rf"\b(\d{{1,3}})\s*gb\s*(?:de\s*)?(?:{PAL_RAM})\b", t)
         or re.search(rf"\b(?:{PAL_RAM})\s*(?:de\s*)?(\d{{1,3}})\s*gb\b", t))
    if m:
        specs["ram_gb"] = int(m.group(1))

    # Primero "ssd 256", despues "256 ssd": en "16GB SSD 256" el disco es el
    # numero de la derecha, no el de la izquierda.
    m = (re.search(rf"\b(?:{PAL_DISCO})\s*(?:de\s*)?(\d{{1,4}})\s*(gb|tb)?\b", t)
         or re.search(rf"\b(\d{{1,4}})\s*(gb|tb)?\s*(?:de\s*)?(?:{PAL_DISCO})\b", t))
    if m:
        specs["disco_gb"] = _gb(m.group(1), m.group(2))

    # --- lo que quedo suelto
    cifras = sorted({_gb(m.group(1), m.group(2)) for m in RE_ALMACEN.finditer(t)})
    libres = [c for c in cifras if c not in (specs.get("ram_gb"), specs.get("disco_gb"))]

    if "ram_gb" not in specs and "disco_gb" not in specs and len(libres) >= 2:
        specs["ram_gb"], specs["disco_gb"] = libres[0], libres[-1]
    elif libres:
        if "ram_gb" not in specs:
            cand = [c for c in libres if c <= 64]
            if cand and (hay_ram or "disco_gb" in specs or len(cifras) > 1):
                specs["ram_gb"] = cand[0]
        if "disco_gb" not in specs:
            cand = [c for c in libres if c >= 64 and c != specs.get("ram_gb")]
            if cand and (hay_disco or "ram_gb" in specs or len(cifras) > 1
                         or (len(cifras) == 1 and cand[0] >= 64)):
                specs["disco_gb"] = cand[-1]

    m = RE_CPU.search(t)
    if m:
        if m.group(1):
            specs["cpu"] = f"{m.group(1)}{'-' + m.group(2).replace(' ', '') if m.group(2) else ''}".upper()
        elif m.group(3):
            specs["cpu"] = f"Ryzen {m.group(4)}{' ' + m.group(5) if m.group(5) else ''}".strip()
        elif m.group(6):
            specs["cpu"] = m.group(6).capitalize()
        elif m.group(7):
            specs["cpu"] = (m.group(7) + (" " + m.group(8) if m.group(8) else "")).upper().strip()

    g = RE_GEN.search(t)
    if g:
        specs["generacion"] = int(g.group(1))
    p = RE_PULGADAS.search(t)
    if p:
        specs["pulgadas"] = float(p.group(1).replace(",", "."))
    return specs



# ---------------------------------------------------------------------------
# PIEZAS DE COMPUTACION. No solo notebooks.
#
# Aca la marca que importa NO es la del fabricante de la caja sino la del CHIP:
# una RTX 3060 vale lo mismo sea Gigabyte, ASUS o MSI, con diferencias chicas.
# Si se usara la marca de la caja, cada RTX 3060 caeria en un estante distinto
# y ninguno juntaria las 3 muestras que el indice necesita. Por eso
# "RTX 3060 Ti de Gigabyte" -> marca Nvidia, modelo "RTX 3060 Ti".
#
# Cada entrada: (patron, marca, plantilla de modelo, categoria, confianza)
PIEZAS = [
    # --- tarjetas de video ---
    (r"\b(rtx|gtx)\s*(\d{3,4})\s*(ti|super)?\b", "Nvidia",
     lambda m: f"{m.group(1).upper()} {m.group(2)}"
               + (f" {m.group(3).title()}" if m.group(3) else ""), "componente", 0.85),
    (r"\brx\s*(\d{3,4})\s*(xt|gre)?\b", "AMD",
     lambda m: f"RX {m.group(1)}" + (f" {m.group(2).upper()}" if m.group(2) else ""),
     "componente", 0.85),
    (r"\barc\s*(a\d{3,4})\b", "Intel",
     lambda m: f"Arc {m.group(1).upper()}", "componente", 0.8),

    # --- procesadores ---
    (r"\b(?:core\s*)?i([3579])[\s-]*(\d{4,5}[a-z]{0,2})\b", "Intel",
     lambda m: f"Core i{m.group(1)}-{m.group(2).upper()}", "componente", 0.85),
    (r"\bryzen\s*([3579])\s*(\d{4}[a-z]{0,2})\b", "AMD",
     lambda m: f"Ryzen {m.group(1)} {m.group(2).upper()}", "componente", 0.85),
    (r"\b(xeon)\s*([a-z]?\d{4}[a-z]?\d?)\b", "Intel",
     lambda m: f"Xeon {m.group(2).upper()}", "componente", 0.8),

    # --- placas madre: manda el chipset, que es lo que fija el precio ---
    # El chipset (B450, X570) es lo que fija el precio, no el sufijo del
    # fabricante: B450M y B450-A son el mismo chipset y valen casi igual.
    (r"\b(?:placa|madre|motherboard|mobo)\b.*?\b([abhxz])(\d{3})[a-z]{0,3}\b", None,
     lambda m: f"Chipset {m.group(1).upper()}{m.group(2)}", "componente", 0.75),
    (r"\b([abhxz])(\d{3})[a-z]{0,3}\b(?=.*\b(?:placa|madre|motherboard|mobo|am4|am5|lga)\b)",
     None, lambda m: f"Chipset {m.group(1).upper()}{m.group(2)}", "componente", 0.75),

    # --- fuentes de poder: los watts SON el modelo ---
    (r"\b(?:fuente|psu)\b.*?\b(\d{3,4})\s*w\b", None,
     lambda m: f"Fuente {m.group(1)}W", "componente", 0.75),
    (r"\b(\d{3,4})\s*w\b.*?\b(?:80\s*plus|fuente|psu)\b", None,
     lambda m: f"Fuente {m.group(1)}W", "componente", 0.75),
]

# Categorias que faltaban. La palabra que aparece primero en el titulo gana.
CATEGORIA_EXTRA = [
    (r"\b(tarjeta\s*(de\s*)?video|placa\s*de\s*video|gpu|grafica|gr[aá]fica)\b", "componente"),
    (r"\b(procesador|cpu|micro)\b", "componente"),
    (r"\b(placa\s*madre|motherboard|mobo)\b", "componente"),
    (r"\b(fuente\s*(de\s*)?poder|psu)\b", "componente"),
    (r"\b(gabinete|case|torre)\b", "componente"),
    (r"\b(refrigeraci[oó]n|cooler|disipador|ventilador)\b", "componente"),
    (r"\b(teclado|mouse|mousepad|audifonos?|aud[ií]fonos?|headset|parlantes?)\b", "accesorio"),
    (r"\b(router|switch|access\s*point|repetidor|modem)\b", "red"),
    (r"\b(webcam|c[aá]mara\s*web|micr[oó]fono)\b", "accesorio"),
    (r"\b(silla\s*gamer|escritorio)\b", "otro"),
    (r"\b(pc\s*gamer|computador\s*armado|torre\s*gamer|desktop)\b", "computador"),
    (r"\b(all\s*in\s*one|aio)\b", "computador"),
    (r"\b(ups|respaldo\s*de\s*energ[ií]a)\b", "otro"),
    (r"\b(proyector|data\s*show)\b", "otro"),
    (r"\b(scanner|esc[aá]ner)\b", "impresora"),
]

# Marcas que no son "linea comercial" pero identifican el producto igual.
MARCAS_SUELTAS = [
    "kingston", "corsair", "gigabyte", "asus", "msi", "evga", "zotac", "sapphire",
    "xfx", "powercolor", "asrock", "biostar", "seagate", "western digital", "wd",
    "adata", "crucial", "hyperx", "logitech", "redragon", "razer", "steelseries",
    "tp-link", "tplink", "d-link", "mercusys", "netgear", "ubiquiti", "mikrotik",
    "epson", "canon", "brother", "samsung", "lg", "aoc", "benq", "viewsonic",
    "cooler master", "thermaltake", "nzxt", "deepcool", "noctua", "antec",
]


# Categorias donde el modelo es un NOMBRE, no un numero: "Archer C6",
# "Kumara", "MX Master". La marca sola no basta — un teclado Redragon de
# $15.000 y uno de $80.000 son los dos "Redragon".
CAT_POR_NOMBRE = {"accesorio", "red", "impresora"}


def _por_marca_y_nombre(t: str, categoria: str):
    """Marca conocida + la palabra siguiente como modelo. (marca, modelo, conf)"""
    if categoria not in CAT_POR_NOMBRE:
        return None
    for mk in MARCAS_SUELTAS:
        i = t.find(mk)
        if i < 0:
            continue
        resto = t[i + len(mk):].strip()
        # Hasta dos palabras: "archer c6", "mx master". Se descartan las de
        # relleno para no quedarse con "para" o "con".
        palabras = [w for w in re.findall(r"[a-z0-9]+", resto)[:3]
                    if w not in ("para", "con", "de", "y", "gamer", "original", "nuevo")]
        if not palabras:
            return mk.title(), None, 0.4
        modelo = " ".join(palabras[:2]) if len(palabras[0]) <= 3 else palabras[0]
        return mk.title(), _titulo(modelo), 0.7
    return None


def _pieza(t: str):
    """Reconoce piezas de computacion. Devuelve (marca, modelo, cat, conf) o None."""
    for patron, marca_fija, plantilla, cat, conf in PIEZAS:
        m = re.search(patron, t)
        if not m:
            continue
        marca = marca_fija
        if marca is None:
            marca = next((mk for mk in MARCAS_SUELTAS if mk in t), None)
            marca = marca.title() if marca else "Generico"
        return marca, plantilla(m), cat, conf
    return None


def _categoria(t: str, por_linea: str | None, pos_linea: int) -> str:
    """Gana la palabra que aparece PRIMERO en el titulo.

    En castellano el aviso empieza por lo que se vende:
      "Memoria RAM Kingston 8GB ... notebook"  -> memoria(0) gana  -> componente
      "Notebook Dell Latitude ... ssd"         -> notebook(0) gana -> notebook
      "Cargador original Lenovo ThinkPad 65w"  -> cargador(0) gana -> accesorio
      "Thinkpad T480 16gb 512gb ssd"           -> thinkpad(0) gana -> notebook
    Sin esta regla, un ThinkPad con SSD quedaba clasificado como componente.
    """
    mejor_pos, mejor_cat = 10_000, None
    for patron, cat in list(CATEGORIA_PALABRA) + CATEGORIA_EXTRA:
        m = re.search(patron, t)
        if m and m.start() < mejor_pos:
            mejor_pos, mejor_cat = m.start(), cat
    if por_linea is not None and pos_linea < mejor_pos:
        return por_linea
    return mejor_cat or por_linea or "otro"


def _condicion(t: str) -> str:
    for patron, cond in CONDICION:
        if re.search(patron, t):
            return cond
    return "desconocido"


def _titulo(palabra: str) -> str:
    """thinkpad -> ThinkPad, x1 carbon -> X1 Carbon, t480 -> T480"""
    especiales = {"thinkpad": "ThinkPad", "thinkbook": "ThinkBook",
                  "ideapad": "IdeaPad", "elitebook": "EliteBook",
                  "probook": "ProBook", "zbook": "ZBook", "macbook": "MacBook",
                  "vivobook": "VivoBook", "zenbook": "ZenBook", "iphone": "iPhone",
                  "ipad": "iPad", "imac": "iMac", "xps": "XPS", "rog": "ROG",
                  "tuf": "TUF"}
    if palabra.lower() in especiales:
        return especiales[palabra.lower()]
    if re.fullmatch(r"[a-z]\d+[a-z]?", palabra, re.I) or re.fullmatch(r"g\d+", palabra, re.I):
        return palabra.upper()
    return " ".join(w.upper() if len(w) <= 2 and any(c.isdigit() for c in w) else w.capitalize()
                    for w in palabra.split())


def extraer(titulo: str) -> dict:
    """Devuelve marca, modelo, categoria, condicion, specs y confianza 0..1.

    confianza >= 0.6 -> se usa tal cual, sin tocar el LLM
    confianza <  0.6 -> el llamador decide si vale la pena preguntarle al 8B
    """
    t = _limpiar(titulo)
    marca = modelo = None
    cat_linea = None
    pos_linea = 10_000
    confianza = 0.0

    # 1) linea comercial conocida (thinkpad, latitude, galaxy...)
    for linea, (mar, cat) in LINEAS.items():
        m_linea = re.search(rf"\b{linea}\b", t)
        if m_linea:
            marca, cat_linea, pos_linea = mar, cat, m_linea.start()
            resto = t.split(linea, 1)[1]
            codigo = None
            for nombre, patron in MODELOS:
                if nombre == linea:
                    m = re.search(patron, resto)
                    if m and m.group(1):
                        codigo = " ".join(m.group(1).split())
                    break
            if codigo:
                modelo = f"{_titulo(linea)} {_titulo(codigo)}"
                confianza = 0.9
            else:
                modelo = _titulo(linea)
                confianza = 0.5
            break

    # 2) marca suelta + algo que parezca codigo de modelo
    if not marca:
        for alias, mar in MARCAS.items():
            if re.search(rf"\b{alias}\b", t):
                marca = mar
                resto = t.split(alias, 1)[1]
                m = re.search(r"\b([a-z]{0,3}\d{2,5}[a-z]{0,2})\b", resto)
                if m and not re.fullmatch(r"\d{1,2}", m.group(1)):
                    modelo = _titulo(m.group(1))
                    confianza = 0.65
                else:
                    confianza = 0.3
                break

    specs = _specs(t)
    categoria = _categoria(t, cat_linea, pos_linea)

    # Piezas de computacion: GPU, CPU, placa madre, fuente. Se prueba SIEMPRE,
    # no solo cuando la confianza es baja, y gana si es mas segura que lo que
    # habia. Con el corte en 0.6 un 'AMD 6600' generico de 0.65 le ganaba al
    # 'RX 6600 XT' correcto de 0.85, que es peor: el XT vale bastante mas y
    # habrian quedado en el mismo estante.
    pz = _pieza(t)
    # Un PC armado completo NO es su tarjeta de video: "PC Gamer con RTX 3050"
    # quedaria en el mismo estante que una RTX 3050 suelta, que vale una
    # fraccion. El equipo entero se identifica por lo que es.
    if categoria == "computador":
        if pz:
            # Se guarda para armar el modelo del equipo, pero NO se usa como
            # identidad: un PC entero no vale lo que su tarjeta sola.
            gpu = pz[1]
            pz = None
        else:
            gpu = None
        cpu = None
        mc = re.search(r"\b(?:core\s*)?i([3579])\b", t) or re.search(r"\bryzen\s*([3579])\b", t)
        if mc:
            cpu = ("Ryzen " if "ryzen" in t else "i") + mc.group(1)
        if cpu or gpu:
            marca = marca or "Armado"
            modelo = "PC " + " + ".join(x for x in (cpu, gpu) if x)
            confianza = max(confianza, 0.7)
    if pz and pz[3] > confianza:
        marca, modelo, cat_pz, confianza = pz
        if categoria in ("otro", "componente", "accesorio"):
            categoria = cat_pz

    # Un componente generico (ram, ssd) no tiene modelo: la capacidad ES el
    # modelo. Sin esto cada SSD quedaria en un estante distinto y ninguno
    # juntaria las 3 muestras que el indice de precios necesita.
    # En un monitor lo que manda el precio es el tamaño, igual que la
    # capacidad en una RAM. Sin esto cada monitor quedaba en su propio estante.
    if categoria == "monitor":
        pulg = specs.get("pulgadas")
        if pulg is None:
            # En un monitor un numero suelto entre 15 y 49 son pulgadas: nadie
            # escribe "Monitor Samsung 27 pulgadas" siempre. Sin esto quedaba
            # sin modelo y cada monitor en su propio estante.
            mp = re.search(r"\b([1-4]\d)\b(?!\s*(gb|tb|hz|w|mhz))", t)
            pulg = int(mp.group(1)) if mp and 15 <= int(mp.group(1)) <= 49 else None
    if categoria == "monitor" and pulg:
        pulg = int(pulg)
        # Queda guardada en specs, no solo en el nombre: de ahi salen los
        # estantes de precio, y un 24" y un 32" no valen lo mismo.
        specs["pulgadas"] = pulg
        extra = " 4K" if re.search(r"\b4k|uhd\b", t) else (" QHD" if "qhd" in t or "1440" in t else "")
        modelo = f'Monitor {pulg}"{extra}'
        confianza = max(confianza, 0.7)
        if not marca:
            marca = "Generico"

    # Perifericos, routers e impresoras: el modelo es un nombre, no un numero.
    if confianza < 0.6:
        pn = _por_marca_y_nombre(t, categoria)
        if pn and pn[1] and pn[2] > confianza:
            marca, modelo, confianza = pn

    if categoria == "componente" and marca:
        es_ram = re.search(rf"\b({PAL_RAM})\b", t)
        if es_ram and "ram_gb" in specs:
            ddr = re.search(r"\b(ddr[345])\b", t)
            modelo = f"RAM {specs['ram_gb']} GB{' ' + ddr.group(1).upper() if ddr else ''}"
            confianza = 0.7
        elif "disco_gb" in specs:
            tipo = "SSD" if re.search(r"\b(ssd|nvme)\b", t) else "Disco"
            modelo = f"{tipo} {specs['disco_gb']} GB"
            confianza = 0.7

    if marca and modelo and specs:
        confianza = min(1.0, confianza + 0.05)

    return {
        "marca": marca,
        "modelo": modelo,
        "categoria": categoria,
        "condicion": _condicion(t),
        "specs": specs,
        "confianza": round(confianza, 2),
    }
