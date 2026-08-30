"""Prueba del extractor determinista. No usa red ni LLM.

  docker compose run --rm -e PYTHONPATH=/app -v ./bin:/app/bin:ro worker python /app/bin/test_extract.py

Cada caso es un titulo real de MercadoLibre Chile con lo que TIENE que salir.
Si un caso falla, el arreglo va en app/extract.py, no en el LLM.
"""
from app.extract import extraer

# (titulo, marca, modelo, categoria, specs que deben estar)
CASOS = [
    # --- computacion que NO es notebook (agregado 28-ago) ---
    # El identificador estaba armado casi entero alrededor de los ThinkPads:
    # de 12 titulos de piezas, 5 salian con confianza 0. Buscar cosas que
    # despues no se reconocen solo llena la base de items sin identificar.
    ("RTX 3060 Ti 8GB Gigabyte Gaming OC", "Nvidia", "RTX 3060 Ti", "componente", {}),
    ("Tarjeta de video AMD RX 6600 XT 8GB", "AMD", "RX 6600 XT", "componente", {}),
    ("Procesador Intel Core i7 12700K", "Intel", "Core i7-12700K", "componente", {}),
    ("Ryzen 5 5600X AM4", "AMD", "Ryzen 5 5600X", "componente", {}),
    ("Placa madre ASUS B450M TUF Gaming", "Asus", "Chipset B450", "componente", {}),
    ("Fuente de poder Corsair 650W 80 Plus", "Corsair", "Fuente 650W", "componente", {}),
    ("Teclado mecanico Redragon Kumara", "Redragon", "Kumara", "accesorio", {}),
    ("Router TP-Link Archer C6 AC1200", "Tp-Link", "Archer", "red", {}),
    ("Monitor Samsung 27 4K", "Samsung", 'Monitor 27" 4K', "monitor", {"pulgadas": 27}),
    # Un PC entero NO es su tarjeta de video: quedaria en el mismo estante que
    # una RTX 3050 suelta, que vale una fraccion.
    ("PC Gamer i5 16GB RTX 3050 SSD 1TB", "Armado", "PC i5 + RTX 3050", "computador", {"ram_gb": 16, "disco_gb": 1024}),
    # Lo mismo vale para NOTEBOOKS, y ahi se colaban. Titulos reales del
    # 29-ago que quedaron en el estante "Nvidia RTX 3050" con P50 550.000 y
    # dejaron mintiendo el multiplo de la tarjeta suelta.
    # Lo que se arregla aca es la MARCA y la CATEGORIA: antes salian
    # "Nvidia / RTX 3050 / componente". Que el modelo quede en "TUF" en vez de
    # "TUF Dash F15" es otra cosa y NO hace daño: "tuf" esta en la lista de
    # nombres de linea de specs.py, asi que el sistema se niega a ponerle
    # precio y te lo pregunta. Un estante que no existe es mejor que uno falso.
    ("Notebook Gamer ASUS TUF Dash F15 (i7 / RTX 3050 / 16GB RAM / SSD 512GB)",
     "Asus", "TUF", "notebook", {"ram_gb": 16, "disco_gb": 512}),
    ("Notebook Gamer HP OMEN 15 / Ryzen 7 4800H / 16GB RAM / RTX2060",
     "Hp", "Omen", "notebook", {"ram_gb": 16}),
    # Sin nombre propio SI se arma uno, pero de notebook, no de tarjeta.
    # Los "16gb" quedan FUERA de las specs a proposito: al lado dice
    # "RTX 3050 6gb", y adivinar cual numero es la RAM y cual la VRAM es
    # justo lo que no se hace — de las specs sale el estante de precios.
    ("Notebook gamer i7 con RTX 3050 16gb",
     "Generico", "Notebook i7 + RTX 3050", "notebook", {}),
    # Y la tarjeta SUELTA tiene que seguir siendo la tarjeta.
    ("rtx 3050 6gb", "Nvidia", "RTX 3050", "componente", {}),
    ("NOTEBOOK LENOVO THINKPAD T480 i5 8VA 16GB SSD 256 !!OFERTA!!",
     "Lenovo", "ThinkPad T480", "notebook", {"ram_gb": 16, "disco_gb": 256, "cpu": "I5"}),
    ("Thinkpad T480 i5-8250U 16gb 512gb ssd impecable",
     "Lenovo", "ThinkPad T480", "notebook", {"ram_gb": 16, "disco_gb": 512}),
    ("Lenovo ThinkPad X1 Carbon Gen 9 i7 16GB 1TB",
     "Lenovo", "ThinkPad X1 Carbon Gen 9", "notebook", {"ram_gb": 16, "disco_gb": 1024}),
    ("Notebook Dell Latitude 7490 i5 8gb 256gb ssd usado",
     "Dell", "Latitude 7490", "notebook", {"ram_gb": 8, "disco_gb": 256}),
    ("HP EliteBook 840 G5 i5 8GB 256GB SSD reacondicionado",
     "Hp", "EliteBook 840 G5", "notebook", {"ram_gb": 8, "disco_gb": 256}),
    # El AÑO va en el modelo. Un MacBook Pro 2015 y uno 2019 no valen parecido,
    # y sin el año caian en el mismo estante: 30 muestras que mezclaban equipos
    # de 200.000 con otros de 900.000. Cambiado el 28-ago.
    ("MacBook Pro 13 2015 i5 8gb 256 ssd",
     "Apple", "MacBook Pro 2015", "notebook", {"ram_gb": 8, "disco_gb": 256}),
    ("iPhone 13 Pro Max 256gb liberado impecable",
     "Apple", "iPhone 13 Pro Max", "celular", {"disco_gb": 256}),
    ("Samsung Galaxy S21 Ultra 12gb 256gb",
     "Samsung", "Galaxy S21 Ultra", "celular", {"ram_gb": 12, "disco_gb": 256}),
    ("Monitor LG 24 pulgadas IPS full hd usado",
     "Lg", None, "monitor", {"pulgadas": 24.0}),
    ("Disco SSD Kingston 480GB sata nuevo sellado",
     "Kingston", "SSD 480 GB", "componente", {"disco_gb": 480}),
    ("Memoria RAM Kingston 8GB DDR4 sodimm notebook",
     "Kingston", "RAM 8 GB DDR4", "componente", {"ram_gb": 8}),
    ("Notebook Asus VivoBook X515 Ryzen 5 8GB 512GB SSD",
     "Asus", "VivoBook X515", "notebook", {"ram_gb": 8, "disco_gb": 512}),
    ("Cargador original Lenovo ThinkPad 65w usb-c",
     "Lenovo", None, "accesorio", {}),
    ("Notebook HP ProBook 440 G7 i5 16gb 512 ssd",
     "Hp", "ProBook 440 G7", "notebook", {"ram_gb": 16, "disco_gb": 512}),
    ("Xiaomi Redmi Note 12 Pro 8gb 256gb",
     "Xiaomi", "Redmi Note 12 Pro", "celular", {"ram_gb": 8, "disco_gb": 256}),
]


def main():
    ok = fallos = bajo = 0
    problemas = []
    for titulo, marca, modelo, cat, specs in CASOS:
        d = extraer(titulo)
        mal = []
        if d["marca"] != marca:
            mal.append(f"marca={d['marca']!r} esperado {marca!r}")
        if modelo is not None and d["modelo"] != modelo:
            mal.append(f"modelo={d['modelo']!r} esperado {modelo!r}")
        if d["categoria"] != cat:
            mal.append(f"categoria={d['categoria']!r} esperado {cat!r}")
        for k, v in specs.items():
            if d["specs"].get(k) != v:
                mal.append(f"{k}={d['specs'].get(k)!r} esperado {v!r}")
        if mal:
            fallos += 1
            problemas.append((titulo, mal, d))
        else:
            ok += 1
        if d["confianza"] < 0.6:
            bajo += 1

    print(f"{ok}/{len(CASOS)} casos OK · {fallos} fallos · "
          f"{bajo} con confianza baja (esos irian al 8B)")
    for titulo, mal, d in problemas:
        print(f"\n✖ {titulo}")
        for m in mal:
            print(f"    {m}")
        print(f"    salio: {d}")
    return fallos


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
