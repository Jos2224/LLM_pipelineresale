# Cazador — plan

Dos bots en Telegram, en `~/cazador` sobre **el servidor**.

| bot | que hace |
|---|---|
| **@tu_bot_cazador** | busca lo que se revende caro, te avisa, y negocia con el vendedor hasta cerrar precio |
| **@tu_bot_publicador** | le mandas fotos + una linea, y publica en MercadoLibre y Facebook Marketplace |

Cambio a la estructura original: **el DL320e no existe** (nigserHP se formateo el 15-ago).
Todo corre en el servidor — 41 GB de RAM libres, 870 GB de disco, la P1000 con Ollama.

---

## 1. LO QUE TIENES QUE HACER TU

> **Estado al 28-ago 22:30 — los 4 pasos estan HECHOS.**
> Bots emparejados (chat <chat-id>) · Facebook con la cuenta <cuenta-fb>
> aprobada · MercadoLibre conectado (cuenta <cuenta-ml>).
> Lo que queda abajo es la referencia para cuando haya que rehacerlo: el token de
> ML vence cada 6 meses y el bot avisa 7 dias antes.

### PASO 1 · Emparejar los bots — 30 segundos, hazlo ahora

En tu Telegram, abre cada bot y mandale `/start` seguido de tu palabra secreta:

```
/start LA_PALABRA
```

- a **@tu_bot_cazador** (el que caza ofertas y negocia)
- a **@tu_bot_publicador** (el que publica tus fotos)

La palabra es el valor de `TG_PASS` en `~/cazador/.env`. Para verla:

```fish
grep TG_PASS ~/cazador/.env
```

> **No la escribas en este archivo ni en ningun otro que vaya al repo.** Cualquiera
> que la tenga puede emparejarse con tus bots y aprobar publicaciones y negociaciones
> en tu nombre. Estuvo escrita aca hasta el 28-ago; si el repo llego a ser publico,
> cambiala en `.env` y reinicia con `~/cazador/bin/cazador arriba`.

El bot guarda tu chat solo. No hay que buscar ningun ID.
**Hasta que hagas esto, el sistema no te puede avisar de nada** — hoy dice
`telegram sin emparejar todavia` en cada ciclo.

### PASO 2 · Crear la app de MercadoLibre — 10 minutos

Yo no puedo: hay que entrar con tu cuenta de ML.

1. Entra a **https://developers.mercadolibre.cl** con tu cuenta
2. "Crear aplicacion"
3. Llena asi, tal cual:

```
Nombre:        Cazador
Redirect URI:  https://TU-SERVIDOR.ejemplo.net/
Scopes:        read, write, offline_access
```

4. Te da dos textos: `client_id` y `client_secret`

Esos dos **no son tu contraseña**: son una llave de una sola puerta y la revocas en un
clic. Me los pasas y los pongo yo, o los pones tu:

```fish
nano ~/cazador/.env      # ML_CLIENT_ID y ML_CLIENT_SECRET
~/cazador/bin/cazador arriba
```

### PASO 3 · Conectar tu cuenta de ML — 1 minuto

En **@tu_bot_cazador**:

1. `/conectar` → te manda un link
2. abres el link, aceptas
3. te devuelve a rematoonline. **Copia la barra de direcciones completa**
4. `/codigo <pegas la URL completa>`

Ahi el bot ya puede leer el mercado, publicar, contestar preguntas y avisarte de ventas.
El `state` se valida, asi que la URL sirve una sola vez.

### PASO 4 · Facebook — cuando quieras, es el ultimo

**`ssh -X` NO funciona en el servidor** — falta `xorg-xauth` y el drop-in
`10-server-hardening.conf` pone `X11Forwarding no`, que gana por ser el primero que
OpenSSH encuentra. Arreglarlo pide root. El camino que si funciona no lo pide, y anda
igual desde Windows, Mac o Linux: el navegador corre en el servidor y tu lo ves en TU
navegador por un tunel.

**Dos terminales:**

```fish
# Terminal 1, en el servidor
~/cazador/bin/login-fb.sh

# Terminal 2, en TU PC
ssh -L 6080:127.0.0.1:6080 remato
```

Y en tu navegador: **http://127.0.0.1:6080/vnc.html** → boton *Connect*.

> Si abres el tunel ANTES de que el contenedor exista, el puerto local escucha pero la
> conexion rebota y parece "tunel cortado". Levanta primero, conecta despues.

Se abre un Chrome en tu pantalla. Entras **con la cuenta desechable, NO la tuya**.
El script te muestra con que cuenta quedaste y te pregunta. Si respondes cualquier cosa
que no sea `SI`, borra el perfil y no queda nada guardado. Ver el candado en §4b.

Despues, pon tu cuenta personal en la lista negra:

```fish
nano ~/cazador/config/policy.yml   # facebook.cuentas_prohibidas: ["tu_numero"]
```

Tu numero sale en facebook.com → F12 → Application → Cookies → `c_user`.

Y para encender FB de verdad (hoy esta apagado a proposito):

```fish
nano ~/cazador/config/policy.yml   # modo.fb_activo: true
~/cazador/bin/cazador arriba
```

**Mientras no hagas el paso 4**, el bot igual te manda el texto y las fotos listos para
pegar en Marketplace a mano. No te bloquea nada.

### Opcional — un comando con root
```fish
sudo tailscale serve --bg --https=443 --set-path=/cazador http://127.0.0.1:8010
```
Lo intente sin root: denegado. Verifique que produccion quedo intacta (sitio 200, ruteo
sin cambios). Con esto ML avisa al instante; sin esto el sistema pregunta cada 5 min.
Escribi `poll_ml.py` para que funcione igual sin root — **no dependes de esto**.

### Y despues, lo de todos los dias
- **Mandar fotos** a @tu_bot_publicador. Solo fotos: el bot las mira (§4e).
- **Apretar botones**: [Publicar], [Negociar], [Enviar].
- **Pagar y retirar** lo que el bot te consiga.
- **Re-login de ML cada 6 meses.** El bot avisa 7 dias antes.

---

## 2. Tus dos numeros

Todo el sistema cuelga de esto, tal como lo pediste:

Cambiados el **28-ago** a lo que pediste: piso 2x, negociar hasta 2,5x.

```
V_liq  = 0.65 × P50          lo que sacas si tienes que vender rapido

TECHO    = V_liq / 2.0       jamas pagas mas.  Al revender ganas 2x minimo.
OBJETIVO = V_liq / 2.5       donde el bot trata de cerrar. Vale 2,5x.
```

**Te llega alerta solo si el precio PUBLICADO ya esta bajo el techo.** Osea: sin negociar
siquiera, ya ganas 2x. Si el bot despues regatea hasta el objetivo, ganas 2,5x.
Comprando entre objetivo y techo el promedio cae en **~2,2x**, que es tu meta.

Medido de verdad con un ThinkPad T480 (P50 = 415.000):

```
V_liq = 269.750     techo = 134.875 (2x)     objetivo = 107.900 (2,5x)

  a  70.000 →  3,85x   ✅ alerta
  a 107.900 →  2,50x   ✅ alerta
  a 134.875 →  2,00x   ✅ alerta
  a 150.000 →  1,80x   ❌ nada
  a 179.833 →  1,50x   ❌ nada   ← esto SI avisaba antes del 28-ago
```

**Lo que cuesta:** con piso 2x van a llegar bastantes menos alertas que con 1,5x. Pasan
solo gangas de verdad. Es el precio de no mover inventario por menos de el doble.

Los dos numeros se cambian en `config/policy.yml` → `compra.multiplo_techo` y
`multiplo_objetivo`. Nada mas hay que tocar.

---

## 3. Mapa

```
   ML API  ──►  fetch_ml  ──►  normalize  ──►  price_index
   remates       (10,11)        (13, LLM)      (14, el ancla)
                                                     |
                                                  score (15)
                                              techo y objetivo
                                                     |
                                                 alert (16)  ──► @tu_bot_cazador
                                                     |              [Negociar]
                                            negociar_compra (17)
                                          saluda → regatea → cierra
                                                     |
                                                 "ACORDADO en $X"

   fotos ──► @tu_bot_publicador ──► gen_listing ──► publish_ml (22) ──► reply_bot (31)
             (LLM arma el texto)     (21, LLM)                          negotiate (32)
                                          |                                   |
                                     publish_fb (23) ─────────────────►  reply_fb (33)
                                          |                                   |
                                    [candado de cuenta]              reprice (25) · sync_stock (24)
```

**Reglas primero, el modelo solo en los bordes**

El LLM se llama en **6 lugares y nada mas** — el resto es script determinista:

| | Scripts (reglas puras) | El modelo |
|---|---|---|
| **cada peso** | `pricing.py`: techo, objetivo, escalera, piso, rebajas, aceptar/rechazar | **nunca** |
| **el precio de mercado** | `price_index.py` + `specs.py`: medianas y una recta | **nunca** |
| marca / modelo | `app/extract.py` — **15/15** casos | `normalize.py:147` si confianza < 0.6 |
| specs (RAM, disco, CPU) | `app/extract.py` | **nunca** — se descartan si las manda |
| que dijo el vendedor | `app/parseo.py` — **14/14**, incluye "130 lucas" y "1 palo" | `negociar_compra.py:220` si < 0.6 |
| mensajes de negociacion | plantillas con variantes | **nunca** |
| texto de venta (titulo, descripcion) | — | `gen_listing.py:65` siempre |
| leer tu frase junto a las fotos | — | `publicador.py:194` siempre |
| contestar a un comprador | — | `reply_bot.py:108` (ML) · `reply_fb.py:339` (FB) |

La guardia de las specs esta escrita en `normalize.py`: cuando el modelo resuelve un
titulo raro, se le toman marca y modelo y **se le descartan las specs**, porque un numero
inventado ahi ensucia el indice de precios y de ahi sale tu multiplo.

**Que modelo usa cada tarea** — `config/policy.yml` → `llm`. Medido el 28-ago en el servidor:

```
qwen3.8:27b    0,9 palabras/s   + 34 s la primera carga   (17 GB, corre en CPU)
qwen3 8B       5,2 palabras/s                             (5 GB, tambien CPU)
```

| tarea | modelo | por que |
|---|---|---|
| `catalogar` (normalize) | **8B** | 40 titulos cada 2 min. Con el 27B no termina un ciclo |
| `leer_vendedor` | **27B** | una vez cada tanto, y entender mal cuesta plata |
| `redactar` | **27B** | una por publicacion, la calidad se nota en la venta |
| `responder` | **27B** | le habla a un comprador de verdad |

> **Medido, no supuesto:** el 27B, ante *"te lo dejo en 130 y lo vienes a ver"*, devuelve
> `{"precio": 130}` — ciento treinta pesos. `app/parseo.py` devuelve 130.000. Un modelo
> mas grande **no** arregla esto: es lo que pasa cuando le pides un numero a algo que
> predice texto. Por eso la regla no cambia con el modelo — el numero lo pone el codigo.
>
> Esa prueba destapo un bug: `negociar_compra.py` caia al precio del modelo cuando las
> reglas no encontraban ninguno. Corregido el 28-ago: si el parser no ve precio, **no hay
> precio**, y la escalera de ofertas sigue igual.

Medido con los 8 items de prueba: **8 por reglas, 0 por 8B**, en segundos.
Antes, todo pasaba por el modelo.

**Nunca mas 4B.** El 8B corre a 5,2 tok/s en la P1000 (el 4B daba 20,7) porque se
desborda a CPU. Precisamente por lento hay que darle poco: mandarle los 40 items de
cada ciclo serian 20 minutos, mandarle los 3 raros son 40 segundos.

Cada vez que el 8B resuelve algo, queda anotado en `crudo.via = 'llm'`. Esa lista es
el material para agregar el caso a `extract.py` y que la proxima vez no lo necesite.

> **Probado y descartado:** al principio el LLM escribia los mensajes de negociacion.
> No sirve. De "ThinkPad T480 16GB" saca el numero **48016** y lo mete como si fuera
> plata, y una de cada tres veces invierte los roles (*"Vendo el ThinkPad..."* cuando
> estas comprando). Un mensaje de regateo son 15 palabras formulaicas: no hay nada que
> ganar y si hay plata que perder.

```fish
~/cazador/bin/cazador test      # corre los dos archivos de casos
```
Si algun dia falla un titulo raro, el arreglo va en `app/extract.py`, no en el prompt.

---

## 4. Los scripts

### Cazar

**10 · `fetch_ml.py` — cada 30 min.** API oficial de ML, 25 busquedas por ciclo, pausa de
1,2 s. Nunca scraping: ML bloquea scrapers y puede cerrarte la cuenta.

**11 · `fetch_aduanas.py` — cada 15 min, APAGADO.** Lee lotes y el **G en vivo** con la
hora exacta. Si el selector de G no matchea → `g = null` y la alerta sale con **G=?** en
rojo. Nunca inventa un G. Todos los selectores viven en `config/aduanas.yml`, el codigo no
tiene ninguno adentro — cuando me pases la URL solo se llena el YAML.
Modelo de remate: `B = P0 × √G`, bandas P25/P50/P80.

**13 · `normalize.py` — cada 2 min.** Ollama saca marca/modelo/specs. Corrige la marca con
una tabla de lineas (thinkpad→Lenovo, latitude→Dell, macbook→Apple...).
*Esto lo encontre probando: sin la tabla el mismo notebook quedaba en dos estantes con dos
P50 distintos y los precios salian mal.*

**14 · `price_index.py` — cada 6 h.** P25/P50/P80 de los ultimos 90 dias. Recorta 5% de cada
punta (siempre hay un repuesto a $1 y un vendedor loco a $9.999.999) y pesa doble las
publicaciones que ya vendieron.
*Limitacion honesta: ML no deja consultar publicaciones cerradas a terceros. Esto es la
mejor aproximacion posible sin romper reglas.*

Desde el 28-ago calcula **dos niveles** por producto — ver §4d, es el arreglo mas importante
que le ha entrado al sistema.

**15 · `score.py` — cada 3 min.** Aplica techo y objetivo. **El techo es inviolable**: si el
precio lo pasa, la oportunidad muere sola y no vuelve a avisar por mas que suba la puja.

**16 · `alert.py` — cada 2 min.** Foto + piden / se revende a / **multiplo** / techo /
objetivo / ganancia bruta y neta. Botones `[Negociar] [Ignorar] [Seguir]`.

### Negociar la compra — script 17, el nuevo

**`negociar_compra.py` — cada 3 min.** Asi negocias en ML Chile: preguntas publicas en la
publicacion del vendedor. Tal como lo pediste, **saluda primero**:

```
ronda 0   "Buenas, ¿sigue disponible?"          ← solo saluda, no ofrece nada
          (el vendedor responde)
ronda 1   ofrece el OBJETIVO        $130.000
ronda 2   sube a mitad de camino    $155.000
ronda 3   ofrece el TECHO           $175.000    ← y ni un peso mas, nunca
```

*Por que saludar primero: un "hola, ¿lo dejas en 130 lucas?" en frio lo ignoran o lo toman
a mal. Un saludo con una pregunta normal abre conversacion, y recien ahi la oferta cae en
un hilo que ya existe.*

- Ollama **lee** la respuesta del vendedor y la clasifica: ¿sigue disponible? ¿acepta
  ofertas? ¿dijo un precio?
- El **codigo** decide el numero. Redondea hacia abajo a cifra natural
  (134.875 → **130.000**): asi suena a oferta pensada y nunca cruza el techo.
- Si el vendedor pide algo **bajo tu techo → cierra** y te avisa
  *"ACORDADO en $X — te toca pagar y retirar"*.
- Si no baja lo suficiente en 3 rondas → cierra educado y no escribe mas.
- Sin respuesta en 48 h → se cierra sola.

**Frenos:** 12 negociaciones nuevas por dia como maximo, 8 minutos entre mensaje y mensaje.
ML tiene anti-spam y una cuenta que dispara preguntas se marca. Todo en
`config/policy.yml` → `compra_negociacion`.

### Publicar — el bot 2

**Tu mandas: las fotos. Nada mas.** Desde el 28-ago el bot las MIRA (§4e).
Si ademas escribes una linea (`16gb 512 ssd`), afina el precio.

**El bot hace:**
1. junta las fotos que mandaste juntas (espera 8 s desde la ultima y arma solo)
2. **mira las fotos** y reconoce marca, modelo, categoria, condicion y estado a la vista
3. escribe titulo (≤60 char), bullets y descripcion con lo que vio + lo que escribiste
4. busca el precio de mercado del estante que corresponde y propone precio + piso
5. te dice **que vio y que no sabe**, y si le faltan specs te lo avisa
6. si nunca vio ese producto, **te pregunta el precio** y respondes con el numero pelado
7. te muestra el borrador con `[Publicar en ambos] [ML] [Facebook] [Descartar]`

**Nunca publica sin que aprietes.** Sin fotos no publica jamas.

### 4e · Ver las fotos — `app/vision.py`

Era lo que faltaba para que "solo enviar fotos" fuera literal. Antes el bot te pedia una
linea escrita y sin ella no hacia nada.

**El reparto, y aca esta lo que importa:**

| | quien lo dice |
|---|---|
| marca, modelo, categoria, condicion | **la foto** |
| estado fisico (golpes, rayas, teclado gastado) | **la foto** |
| RAM, disco, CPU | **NUNCA la foto** |

Por que tan estricto: de las specs sale el estante de precios (§4d), del estante sale tu
multiplo, y del multiplo sale si compras o no. Una RAM inventada mirando una tapa cerrada
se convierte en un P50 equivocado y en una compra mala. **Las specs se descartan aunque el
modelo jure que las ve** — hay un caso de prueba justo para eso.

Las specs salen de dos fuentes de TEXTO, y las dos pasan por las reglas de `extract.py`:
1. la linea que escribiste, si escribiste alguna
2. lo que el modelo **transcribe** de una etiqueta o de una pantalla que aparezca en la foto

Si no hay ninguna de las dos, quedan desconocidas: el precio se calcula al nivel del
modelo, el bot te dice *"specs: no se saben"* y te ofrece afinarlo con una linea.

**Velocidad, sin adornos:** el 27B corre en CPU. Mirar 2 fotos y contestar toma **minutos**.
Por eso las fotos se achican a 1024 px antes de mandarlas (para leer "T480" en una tapa
sobra) y se mandan 2 como maximo. Se configura en `policy.yml` → `vision`.

**El modelo TIENE que ver.** `llm.ver` debe apuntar a `qwen3.8:27b`: el 8B no tiene ojos y
si lo pones ahi ignora las fotos en silencio y no identifica nada.

**22 · `publish_ml.py`** — sube las fotos (las tuyas por multipart, las de ML por URL),
crea el item, pone la descripcion. Si ML rechaza, te dice el motivo y no reintenta ciego.

**23 · `publish_fb.py`** — con `fb_activo: false` (hoy) te manda el texto listo para copiar
y pegar con boton `[Ya lo pegue]`. Con `true`, publica solo: 5 por dia, ritmo humano, y si
el DOM de FB cambio aborta ese item y avisa en vez de dejar algo a medias.

**24 · `sync_stock.py` — cada 5 min, el critico.** Vendido en ML → pausa FB al toque, con
lock en Redis. Sin esto vendes dos veces y quedas mal.

**25 · `reprice.py` — diario 09:00.** 20 dias sin venta → baja 5%, **nunca cruza el piso**.
Al llegar al piso avisa una vez y para.

**31 · `reply_bot.py` / 32 · `negotiate.py`** — cuando alguien te pregunta u oferta a TI
en **MercadoLibre**. Responde solo con datos de la ficha; si no sabe, dice `NO_SE` y escala.
Ofertas por reglas duras: ≥ piso acepta · 90-100% contraoferta al piso · bajo 90% rechaza.

**33 · `reply_fb.py` — cada 15 min, el nuevo.** Lo mismo en **Facebook Marketplace**.
Como FB no tiene API, entra por el navegador con el perfil guardado, y lo primero que
hace —antes de leer una sola letra— es pasar el candado de cuenta.

Diferencias con ML, que son las que obligaron a escribir un script aparte:

- **FB no separa pregunta de oferta.** Lo decide `app/parseo.py` leyendo el texto:
  *"te doy 250 lucas"* → oferta de 250.000 · *"¿en cuánto lo dejas?"* → pregunta.
- **FB no te dice de qué publicación es el hilo.** Se empareja por el título del producto
  contra tu inventario. Si el parecido no llega a `facebook.parecido_minimo` (0,5)
  **no adivina**: te lo manda a Telegram y te pregunta. Contestar la ficha equivocada es
  peor que no contestar.
- **Quién escribió cada burbuja no se lee del diseño.** Se compara contra lo que ya está
  en la base: si el texto salió de nosotros, ya está guardado, así que lo que sobra es del
  comprador. Un cambio de diseño de FB no rompe esto.
- **Frenos propios:** 8 hilos por pasada · tope de 20 respuestas al día · 45-150 s entre
  mensaje y mensaje · tecleo con retardo de persona.

Contestar es lo que más urge de FB: un comprador que no recibe respuesta en 15 minutos se
fue al siguiente vendedor.

### 4b · El candado de cuenta — `app/fb_guard.py`

Tu regla: **la cuenta personal de Facebook no se automatiza.** Está cableada, no escrita.

Los tres jobs de FB (`fetch_fb`, `publish_fb`, `reply_fb`) llaman al candado antes de
tocar nada. El candado mira la cookie `c_user` —el número de cuenta— y **no el nombre en
pantalla**: un candado que depende del diseño de FB es un candado que se abre solo el día
que FB cambia el diseño. Aborta si:

| situación | qué pasa |
|---|---|
| nunca aprobaste una cuenta | aborta |
| hiciste login pero no escribiste `SI` | aborta |
| el navegador quedó con una cuenta distinta a la aprobada | aborta |
| esa cuenta está en `cuentas_prohibidas` | aborta |
| no hay cookie (sesión caída) | aborta |

Falla cerrada: si no puede saber con certeza qué cuenta es, no hace nada. Perder un ciclo
de publicaciones es barato; publicar desde tu perfil no.

`bin/test_fb.py` prueba los 8 casos, incluido el peor: que alguien apruebe la personal por
error y la lista negra la ataje igual.

### 4d · El indice por specs — el arreglo del 28-ago

**El bug:** `producto_canon` es `UNIQUE(marca, modelo)`. Las specs se extraian bien y
despues **no se usaban como llave**. Un T480 de 8GB/256 y uno de 32GB/1TB compartian
estante y compartian P50. Fallaba en las dos direcciones, con precios reales de ML:

```
te alertaba una compra que NO rinde 2x
  T480 i5 8GB publicado a 120.000
    verdad  (estante propio):  P50 297.000  ->  1,61x   nada
    sistema (mezclado)      :  P50 402.000  ->  2,18x   ALERTA  ← comprabas mal

y se perdia la ganga de verdad
  T480 i7 32GB 1TB publicado a 150.000
    verdad  (estante propio):  P50 515.000  ->  2,23x   ALERTA
    sistema (mezclado)      :  P50 402.000  ->  1,74x   nada    ← ni te avisaba
```

**El arreglo, en dos niveles.** `score.py` pide el precio de mercado asi:

| nivel | de donde | cuando |
|---|---|---|
| 1 · **estante propio** | mediana del tramo exacto (`r32-d1024`) | si ese tramo tiene ≥3 observaciones. Es un dato medido |
| 2 · **modelo ajustado** | mediana del modelo × factor de specs | si el tramo no junta muestras |

Los tramos son gruesos a proposito (`r8-d256`, `r16-d512`, `r32-d1024`): con escalones
finos ningun estante junta muestras y el nivel 1 no se activa nunca.

**El factor del nivel 2** sale de un solo puntaje, no de dos coeficientes:

```
puntaje = log2(RAM) + 0,5 × log2(disco)
factor  = 2 ^ ( coef × (puntaje_del_equipo − puntaje_tipico_del_modelo) )
```

`coef` se **mide en las observaciones del propio producto** (mediana por puntaje, recta
sobre log del precio) y solo si no hay datos suficientes cae al de `policy.yml`.

> **Probado y descartado:** la primera version media RAM y disco por separado. Esta mal —
> en la vida real van juntos, el equipo caro trae mas de las dos, asi que cada coeficiente
> se llevaba el credito del otro: predecia 3,0x de diferencia entre el flaco y el gordo
> cuando la verdad era 1,7x. Peor todavia, la referencia era el escalon "mas frecuente" y
> con dos grupos empatados elegia uno al azar — el equipo del medio terminaba valorado
> **por debajo del mas barato**. Lo agarro `bin/smoke_specs.py` antes de tocar plata real.
> Con un puntaje unico no puede pasar: hay una sola pendiente que repartir.

**Lo que cuesta, dicho claro:** el peso relativo entre RAM y disco (el 0,5) se supone, no
se mide. Medirlo pediria muchos mas datos de los que un producto usado junta en 90 dias.

**La alerta ahora te dice de donde salio el numero**, porque no es lo mismo:

```
📊 mercado: $515.000 · estante r32-d1024 (6 datos)      ← dato medido
≈  mercado: $402.000 · modelo ajustado x1.00 (12 datos) ← estimacion
```

La etiqueta habla del **camino**, no del tamaño del ajuste: un ajuste que da x1,00 sigue
siendo una estimacion y tu decision de comprar puede cambiar sabiendolo.

De paso: la alerta tenia "Techo (1,5x)" y "Objetivo (2x)" escritos a mano, asi que desde
que cambiaste los multiplos te estaba mintiendo. Ahora las etiquetas salen de `policy.yml`.

### 4c · Los selectores de FB viven en un solo archivo

`config/facebook.yml`. Ni `reply_fb.py` ni `publish_fb.py` ni `fetch_fb.py` tienen un
selector adentro. Cuando FB cambie el diseño —va a pasar— el arreglo es editar ese YAML:
sin tocar Python, sin reconstruir la imagen, sin reiniciar. Cada campo acepta **varios
selectores separados por coma**: se prueban en orden y basta que uno pegue, así el primer
cambio de diseño normalmente no rompe nada.

---

## 5. Riesgos

| Riesgo | Tapa |
|---|---|
| ML marca tu cuenta por preguntar mucho | 12 negociaciones nuevas/dia · 8 min entre mensajes · 3 rondas maximo |
| Pagar de mas por adrenalina | el techo nunca sube. La escalera nunca lo cruza. Probado hasta la ronda 4 |
| El LLM inventa una cifra | no escribe ninguna. Plantillas + guardia que ignora specs (T480, 16GB) |
| Ban de Facebook | apagado · cuenta aparte · ritmo humano · 5 publicaciones y 20 respuestas al dia · alternativa copiar-y-pegar |
| **Que el bot toque tu FB personal** | candado por cookie `c_user`, aprobacion a mano con `SI`, lista negra, y falla cerrada. 8 casos probados |
| FB cambia el diseño y algo publica a medias | todos los selectores en `config/facebook.yml`, varios por campo, y aborta el item avisandote cual fallo |
| Vender dos veces | script 24, lock en Redis, cada 5 min |
| Publicar algo malo | nada se publica sin tu boton |
| Que algo se rompa callado | todo queda en `job_log`; `/estado` te lo muestra |
| Exponer el servidor | postgres, redis y api escuchan **solo en 127.0.0.1** |

---

## 5b. Auditoria del 26-ago — 6 bugs encontrados y corregidos

Ninguno habia dado la cara todavia porque el sistema aun no tiene datos reales.
Los seis estaban esperando el primer dia de uso.

| # | Que pasaba | Donde |
|---|---|---|
| 1 | **`Decimal - float` → TypeError.** Postgres devuelve `numeric` como Decimal. **Rompia TODAS las alertas**: la primera oferta que encontrara habria muerto sin avisarte | `alert.py` |
| 2 | **Castear un `interval` a `date`** es invalido en Postgres. Tiraba abajo el reporte del domingo entero | `report.py` |
| 3 | **Contar filas para saber si un item era nuevo.** Con `ON CONFLICT DO UPDATE` el rowcount es 1 igual, asi que **todo salia como "nuevo"** y las estadisticas mentian | `fetch_ml.py`, `fetch_fb.py` |
| 4 | **El freno de las primeras 20 publicaciones se abria solo:** contaba los borradores que esperaban tu boton como si ya estuvieran publicados | `publish_ml.py` |
| 5 | **OAuth sin validar `state`.** Alguien que logre que tu navegador visite el callback con SU codigo te deja el sistema conectado a la cuenta de ML de otro, y el bot publica y negocia ahi | `api/main.py`, `ml_api.py` |
| 6 | **Fotos sin descripcion = silencio eterno.** Mandabas fotos y el bot esperaba un `/listo` que nadie sabia que existia | `bot/publicador.py` |

Ademas, tres defensas nuevas:
- Si una oportunidad no tiene techo calculado, el boton `[Negociar]` te lo dice en vez
  de reventar a mitad de la conversacion con el vendedor esperando.
- Parametros de tiempo con `make_interval(days => %s)` en vez de meterlos dentro de las
  comillas de `interval '...'`, donde viajan como texto.
- Si se perdio el borrador o falta el precio, el boton de publicar lo dice y no publica.

---

## 5c. Auditoria del 28-ago — el boton [Enviar] no enviaba nada

Encontrado al construir el lado Facebook. **Es el bug mas caro de los que aparecieron**,
porque pegaba justo donde tu ganas plata: contestarle al comprador.

Apretar `📤 Enviar` en Telegram empujaba un id a la cola de Redis `cazador:enviar_msg`.
**Nadie leia esa cola.** Nunca. El mensaje se quedaba ahi para siempre y el comprador no
recibia nada. Con `negociar_auto: false` —el default, y el modo en que ibas a arrancar—
ese era el 100% de las respuestas.

Encima, el boton mandaba el id de la **pregunta del comprador**, no el de la respuesta:
aunque alguien hubiera leido la cola, no habia forma de saber que texto mandar, porque la
respuesta redactada no se guardaba en ningun lado — solo se mostraba en Telegram y se
perdia.

Arreglado en tres piezas:

1. **La respuesta se guarda.** Un `mensaje` de salida en estado `nuevo`, unido a la
   pregunta que la origino (`responde_a`, migracion `db/003-fb.sql`).
2. **El boton apunta al borrador**, no a la pregunta.
3. **La cola se vacia.** `reply_bot` la drena al empezar cada pasada (cada 10 min) y manda
   por la API de ML; `reply_fb` hace lo mismo por el navegador para FB. Si el envio falla,
   el borrador **queda vivo** para reintentar en vez de perderse.

Ademas: `negotiate.py` ahora filtra `marketplace='ml'`. Sin eso, las ofertas de Facebook
las contestaban los dos scripts, cada uno por su lado, a la misma persona.

---

## 5d. 28-ago: MercadoLibre cerro su API de busqueda publica

**Esto cambia un supuesto del negocio, no es un bug que se arregle.**

Al conectar la cuenta por primera vez, las 12 busquedas trajeron 0 items. La causa:

```
403 PolicyAgent  /sites/MLC/search       <- de aca salia el precio de mercado
403 PolicyAgent  /items/{id}
403 PolicyAgent  /sites/MLC/categories
403 PolicyAgent  /users/{id}/items/search
```

Con token y **sin token** — o sea no es un permiso que falte, es que ML lo cerro
para terceros. Probado las dos formas antes de concluirlo.

**Lo que si funciona con tu cuenta:**

| endpoint | para que |
|---|---|
| `/orders/search` | tus ventas -> avisarte, y el indice de precios (§5e) |
| `/questions/search` | preguntas de compradores -> `reply_bot` |
| `/users/me` | tu cuenta |
| `POST /items` | **publicar si funciona** — el 400 que devuelve es de validacion (`family_name`), no de permiso |

**Consecuencia directa:** el precio de mercado ya no puede venir de ML. Lo que se
construyo horas antes ese mismo dia — que Facebook alimente el indice — dejo de ser
una mejora y paso a ser la fuente principal. Y se sumo una segunda, mejor todavia.

## 5e. Tus ventas cerradas: el mejor precio que existe

`app/jobs/ventas_ml.py` (script 35, cada 2 h). De `/orders/search` sale el
`unit_price` de cada venta tuya.

```
una publicacion activa  dice lo que alguien PIDE
una venta cerrada       dice lo que alguien PAGO
```

Todo el indice se construia con precios pedidos, con la nota honesta de que era "la
mejor aproximacion posible sin romper reglas". Esto **no es una aproximacion**. Por eso
entra con peso 3, el maximo que `price_index.py` multiplica.

Limitacion clara: solo ves TUS ventas. Al principio son pocas, asi que el sistema se
apoya en Facebook. A medida que vendas, este indice se vuelve el bueno.

**De donde sale el precio, en orden:**

```
1. tus ventas cerradas en ML     lo que se PAGO      (peso 3)
2. publicaciones de Facebook     lo que se PIDE      (peso 1)
3. sin datos -> el bot te pregunta el precio y no inventa nada
```

## 5f. PKCE, y un error que se perdia

El primer canje de codigo fallo con `400 Bad Request` y nada mas. Reproducido a mano,
ML decia: `code_verifier is a required parameter`. La app tiene **"Requiere PKCE"**
encendido en el panel y `ml_api` no lo implementaba.

Implementado en `url_login()` + `canjear_codigo()`: se inventa un secreto, se manda su
huella SHA-256 al pedir el login, y el secreto entero recien al canjear. Aunque alguien
intercepte el codigo, no puede usarlo: le falta el secreto, que nunca viajo por el
navegador.

> **El error se perdia por `raise_for_status()`**, que tira el cuerpo de la respuesta a
> la basura. Por eso el primer intento solo decia "400" y hubo que reproducirlo a mano.
> Ahora el motivo de ML sube tal cual hasta el Telegram.

Misma leccion, otra vez, en `ventas_ml.py`: un `except Exception` a secas reportaba
"sin login de ML todavia" cuando lo que fallaba era que llame a una funcion que no
existe (`mi_id` en vez de `usuario_id`). **Atrapar solo la excepcion que se sabe
manejar** — si no, un error de programacion se disfraza de problema de configuracion y
manda a buscar al lado equivocado.

---

## 6. Comandos

```fish
~/cazador/bin/cazador arriba              # levanta todo
~/cazador/bin/cazador logs bot-cazador    # ver un bot
~/cazador/bin/cazador job reply_fb        # correr un script a mano
~/cazador/bin/cazador test                # los 3 archivos de casos (49 en total)
~/cazador/bin/cazador errores             # ultimos fallos
~/cazador/bin/cazador psql                # consola de la base
```

**@tu_bot_cazador:** `/estado` `/conectar` `/codigo` `/negociaciones` `/watch` `/sugerencias` `/pausa`
**@tu_bot_publicador:** manda fotos · `/borradores` `/precio` `/piso` `/cancelar` `/listo`

---

## 7. Que se probo de verdad

- 16 tablas en postgres 16, migracion de negociacion aplicada. ✓
- Los 6 contenedores arriba; los dos bots contestando a Telegram (`getMe` OK en ambos). ✓
- Worker agenda 17 tareas y **corre cada una al arrancar**, escalonadas de a 20 s.
  Primeras 4 ejecuciones reales en `job_log`: `fetch_ml` "sin login de ML todavia",
  `alert` "telegram sin emparejar" — exactamente el estado correcto. ✓
- Pipeline con 8 publicaciones falsas de un ThinkPad T480: identifico 8/8 →
  P50 = 415.000 → marco **2** oportunidades (3,85x y 2,00x) y descarto las 6 caras. ✓
- Escalera de negociacion: 130.000 → 155.000 → 175.000, y en la ronda 4 **se queda en el
  techo**. ✓
- Los 3 mensajes salen con la cifra exacta; la guardia de numeros distingue
  `$134.875` de `T480 16GB 512GB`. ✓
- **`bin/test_extract.py`: 15/15** titulos reales de ML — ThinkPad, Latitude, EliteBook,
  MacBook, iPhone, Galaxy, VivoBook, monitor LG, SSD Kingston, RAM DDR4, cargador.
  2 quedan con confianza baja y esos son justo los que irian al 8B. ✓
- **`bin/test_parseo.py`: 14/14** respuestas de vendedor, incluidas "350 lucas",
  "1 palo", "son 380 mil no bajo mas" y una con specs adentro que no confunde con plata.
  1 caso (uno largo y ambiguo) cae al 8B, que es exactamente para lo que esta. ✓
- `normalize` sobre los 8 items: **8 por reglas, 0 por 8B**. ✓
- Funnel publico: intento sin root denegado, sitio verificado en 200 antes y despues. ✓
- Datos de prueba borrados. ✓

Del 28-ago:

- Multiplos 2x / 2,5x cargados y verificados en el worker en vivo. ✓
- **`bin/test_fb.py`: 20/20.** 8 del candado de cuenta (incluido "aprobaron la personal
  por error"), 6 de emparejar hilo con publicacion (2 de ellos tienen que NO adivinar),
  6 de leer ofertas de compradores. ✓
- **`bin/smoke_fb.py`: 9/9** contra la base real — el circuito pregunta → borrador →
  boton → cola → envio, y que un envio fallido deje el borrador vivo. Datos borrados. ✓
- Migracion `003-fb.sql` aplicada; 16 tablas, 3 columnas y 2 indices nuevos. ✓
- Los 6 contenedores reconstruidos y arriba, worker con 18 tareas, `cazador errores` vacio. ✓

**Falta probar contra ML de verdad** — eso necesita `client_id` y `secret`.
**Falta probar contra FB de verdad** — eso necesita el login de la cuenta desechable, y
recien ahi se sabe si los selectores de `config/facebook.yml` pegan con el FB de hoy.
