"""@tu_bot_publicador — le mandas fotos y publica.

Lo unico que haces: mandar las fotos y una linea diciendo que es.
Ejemplo de la linea:  "thinkpad t480 16gb 512 ssd, bateria buena"

El bot hace el resto:
  1. junta las fotos que mandaste juntas (album o de a una)
  2. Ollama saca marca, modelo y specs de tu linea
  3. busca el precio de mercado y propone el precio de venta
  4. te muestra el borrador con los botones
  5. cuando aprietas, publica en MercadoLibre y en Facebook Marketplace

Si no sabe a cuanto venderlo (producto que nunca vio), te pregunta el precio.
Nunca publica sin que aprietes.
"""
from __future__ import annotations

import json
import logging
import re
import time

import httpx
import redis

from app import llm, tg, vision
from app import specs as sp
from app.config import DATA, REDIS_URL, TG_PASS, p
from app.db import ex, kv_get, kv_set, q, q1
from app.extract import extraer
from app.pricing import piso_default, precio_lista

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("publicador")
R = redis.from_url(REDIS_URL, decode_responses=True)
B = tg.PUBLICADOR

DEBOUNCE_SEG = 8      # cuanto espera despues de la ultima foto antes de armar

AYUDA = """<b>Publicador</b>

<b>Mandame las fotos. Nada mas.</b> Las miro y reconozco que es.

Tarda unos minutos: el modelo que ve corre en CPU.

Si ademas escribes una linea, afino el precio — de la foto NO se puede
sacar cuanta RAM ni que disco tiene, y de eso depende cuanto vale:

  ejemplo:  16gb 512 ssd, bateria buena

Despues te muestro el borrador y tu aprietas [Publicar]. Nunca publico solo.

/borradores  los que estan esperando tu boton
/piso CODIGO MONTO   precio minimo de un item
/precio CODIGO MONTO precio de venta de un item
/cancelar    bota el borrador en curso"""

PROMPT = """De esta descripcion corta de un producto usado, saca los datos.

Descripcion: "{desc}"

Devuelve JSON:
  "marca": la marca real (si dice ThinkPad la marca es Lenovo, si dice MacBook es Apple)
  "modelo": el identificador comercial (ej "ThinkPad T480", "iPhone 13 Pro")
  "categoria": notebook, celular, monitor, tablet, componente, accesorio u otro
  "condicion": nuevo, usado o reacondicionado
  "specs": objeto con lo que diga (cpu, ram_gb, ssd_gb, pulgadas, color, estado_bateria)
  "titulo": titulo de venta de maximo 60 caracteres, sin gritar, sin la palabra oferta
  "bullets": 4 frases cortas y concretas
  "descripcion": 2 parrafos honestos, sin inventar nada, sin precios, sin contacto

No inventes specs que no esten en la descripcion."""

ESQUEMA = {
    "type": "object",
    "properties": {
        "marca": {"type": "string"}, "modelo": {"type": "string"},
        "categoria": {"type": "string"}, "condicion": {"type": "string"},
        "specs": {"type": "object"}, "titulo": {"type": "string"},
        "bullets": {"type": "array", "items": {"type": "string"}},
        "descripcion": {"type": "string"},
    },
    "required": ["marca", "modelo", "categoria", "condicion", "titulo", "descripcion"],
}


def _plata(n) -> str:
    return f"${int(n or 0):,}".replace(",", ".")


# ------------------------------------------------------------- borrador
def _clave(chat: str) -> str:
    return f"pub:borrador:{chat}"


def _borrador(chat: str) -> dict:
    crudo = R.get(_clave(chat))
    return json.loads(crudo) if crudo else {"fotos": [], "desc": "", "ts": 0}


def _guardar_borrador(chat: str, b: dict) -> None:
    R.set(_clave(chat), json.dumps(b), ex=3600)


def _bajar_foto(file_id: str, codigo: str) -> str | None:
    url = B.archivo_url(file_id)
    if not url:
        return None
    carpeta = DATA / "fotos" / codigo
    carpeta.mkdir(parents=True, exist_ok=True)
    destino = carpeta / f"{file_id[-14:]}.jpg"
    try:
        with httpx.Client(timeout=60.0) as c:
            destino.write_bytes(c.get(url).content)
    except Exception:
        return None
    return str(destino)


def _canon(d: dict) -> int | None:
    marca = (d.get("marca") or "").strip().title()[:60]
    modelo = " ".join((d.get("modelo") or "").split())[:120]
    if not marca or not modelo:
        return None
    if modelo.lower().startswith(marca.lower() + " "):
        modelo = modelo[len(marca) + 1:].strip()
    fila = q1(
        """SELECT id FROM producto_canon
           WHERE lower(marca)=lower(%s) AND similarity(lower(modelo), lower(%s)) > 0.72
           ORDER BY similarity(lower(modelo), lower(%s)) DESC LIMIT 1""",
        (marca, modelo, modelo),
    )
    if fila:
        return fila["id"]
    fila = q1(
        """INSERT INTO producto_canon (marca, modelo, categoria, specs)
           VALUES (%s,%s,%s,%s) ON CONFLICT (marca, modelo) DO UPDATE
             SET categoria = EXCLUDED.categoria RETURNING id""",
        (marca, modelo, (d.get("categoria") or "otro")[:40], json.dumps(d.get("specs") or {})),
    )
    return fila["id"] if fila else None


def _preview(chat: str, inv_id: int) -> None:
    inv = q1(
        """SELECT inv.id, inv.codigo, inv.titulo, inv.condicion, inv.piso_precio,
                  cardinality(inv.fotos) AS n_fotos,
                  pub.id AS pub_id, pub.precio, pub.descripcion
           FROM inventario inv
           LEFT JOIN publicacion pub ON pub.inventario=inv.id AND pub.marketplace='ml'
           WHERE inv.id=%s""",
        (inv_id,),
    )
    if not inv:
        return
    if not inv["precio"]:
        B.enviar(
            f"<b>{inv['titulo']}</b>\n{inv['n_fotos']} foto(s) · {inv['codigo']}\n\n"
            f"No conozco el precio de mercado de esto todavia.\n"
            f"¿En cuanto lo vendo? Mandame solo el numero.",
            chat=chat,
        )
        R.set(f"pub:esperando_precio:{chat}", inv["id"], ex=3600)
        return

    B.enviar(
        f"<b>{inv['titulo']}</b>\n"
        f"{inv['n_fotos']} foto(s) · {inv['condicion']} · {inv['codigo']}\n\n"
        f"Precio  <b>{_plata(inv['precio'])}</b>\n"
        f"Piso    {_plata(inv['piso_precio'])}  (no baja de aca)\n\n"
        f"{(inv['descripcion'] or '')[:700]}",
        tg.teclado([
            [("🚀 Publicar en ambos", f"pb_ambos:{inv['id']}")],
            [("ML", f"pb_ml:{inv['id']}"), ("Facebook", f"pb_fb:{inv['id']}")],
            [("✖ Descartar", f"pb_no:{inv['id']}")],
        ]),
        chat=chat,
    )


def _armar(chat: str) -> None:
    """Cierra el borrador: LLM, precio, y muestra el preview."""
    b = _borrador(chat)
    if not b["fotos"]:
        return
    R.delete(_clave(chat))

    codigo = f"INV-{int(time.time())}"
    desc = (b["desc"] or "").strip()

    B.enviar(f"mirando las fotos... ({codigo})\n"
             f"El modelo que ve corre en CPU: esto tarda unos minutos.", chat=chat)
    rutas = [r for r in (_bajar_foto(f, codigo) for f in b["fotos"]) if r]
    if not rutas:
        B.enviar("no pude bajar ninguna foto, mandalas de nuevo", chat=chat)
        return

    # 1) La FOTO dice que es. Si escribiste una linea, va como ayuda.
    v = vision.identificar(rutas, desc) or {}
    if not v.get("marca") and not v.get("modelo") and not desc:
        B.enviar("no logre reconocer que es en las fotos.\n"
                 "Mandame una linea diciendolo (ej: thinkpad t480 16gb 512 ssd) "
                 "y lo armo con eso.", chat=chat)
        b["ts"] = time.time()
        b["pedido"] = True
        _guardar_borrador(chat, b)
        return

    # 2) El texto de venta se redacta con lo que la foto vio MAS tu linea.
    visto = ", ".join(v.get("estado_visible") or [])
    base_desc = " · ".join(x for x in [
        f"{v.get('marca','')} {v.get('modelo','')}".strip(),
        desc, visto, v.get("texto_en_pantalla") or ""] if x)
    d = llm.json_de(PROMPT.format(desc=base_desc[:700]), esquema=ESQUEMA,
                    modelo=llm.modelo_de("redactar")) or {}

    # Lo que vio la foto manda sobre lo que dedujo el redactor: uno miro el
    # equipo, el otro leyo un resumen.
    for campo in ("marca", "modelo", "categoria", "condicion"):
        if v.get(campo):
            d[campo] = v[campo]
    # Y las specs vienen de las reglas, nunca del ojo. Ver app/vision.py.
    d["specs"] = v.get("specs") or extraer(f"{desc}")["specs"]

    pid = _canon(d)
    titulo = (d.get("titulo") or base_desc)[:60]
    bullets = "\n".join(f"• {x}" for x in (d.get("bullets") or [])[:6])
    cuerpo = f"{bullets}\n\n{d.get('descripcion') or base_desc}".strip()

    # Las specs de TU equipo mandan: el precio de un 32GB/1TB no es el del
    # 8GB/256 aunque el modelo sea el mismo.
    p50, origen = sp.precio_mercado(pid, d["specs"])
    piso = piso_default(p50) if p50 else None
    precio = precio_lista(p50, piso) if p50 else None

    inv = q1(
        """INSERT INTO inventario (codigo, producto, titulo, condicion, piso_precio,
                                   fotos, estado, origen)
           VALUES (%s,%s,%s,%s,%s,%s,'borrador','telegram') RETURNING id""",
        (codigo, pid, titulo, (d.get("condicion") or "usado"), piso, rutas),
    )
    ex("""INSERT INTO publicacion (inventario, marketplace, titulo, descripcion, precio, estado)
          VALUES (%s,'ml',%s,%s,%s,'preparando')""", (inv["id"], titulo, cuerpo, precio))

    # Que vio y que no: para que sepas cuanto de esto es dato y cuanto suposicion.
    # Que vio y con cuanta seguridad. Un nombre leido de una foto puede salir
    # mal — medido: de unas cañas de golf "USTMamiya" leyo "Mimaga". Por eso
    # esto se te muestra ANTES del boton, y por eso el boton existe.
    conf = float(v.get("confianza") or 0)
    aviso = "" if conf >= 0.75 else "  ⚠️ no muy seguro: revisa el nombre antes de publicar\n"
    B.enviar(
        f"👁 mire {v.get('fotos_miradas', 0)} foto(s) y veo:\n"
        f"  <b>{v.get('marca','?')} {v.get('modelo','?')}</b> · {v.get('categoria','?')}"
        f" · {v.get('condicion','?')}  (seguridad {conf:.0%})\n"
        f"{aviso}"
        f"  estado a la vista: {visto or '—'}\n"
        f"  specs: {d['specs'] or 'no se saben'} ({v.get('specs_de','no se saben')})\n"
        f"  precio de mercado: {origen}\n\n"
        f"Si el nombre esta mal, /cancelar y mandame una linea con el correcto.",
        chat=chat)
    if not d["specs"]:
        B.enviar("💡 si me dices RAM y disco (ej: <code>16gb 512</code>) afino el "
                 "precio al estante exacto en vez de estimarlo.", chat=chat)
    _preview(chat, inv["id"])


# ------------------------------------------------------------- comandos
def cmd_start(chat: str, arg: str) -> None:
    if TG_PASS and arg.strip() != TG_PASS:
        # Antes decia solo "palabra incorrecta" y no explicaba nada: mandar
        # /start pelado devolvia eso y quedabas sin saber que faltaba.
        B.enviar(
            "Falta la palabra secreta.\n\n"
            "Mandame:  <code>/start LA_PALABRA</code>\n\n"
            "La palabra es el valor de <code>TG_PASS</code>. Para verla, en el "
            "servidor:\n<code>grep TG_PASS ~/cazador/.env</code>",
            chat=chat)
        return
    B.emparejar(chat)
    B.enviar("Emparejado ✅\n\n" + AYUDA, chat=chat)


def cmd_borradores(chat: str) -> None:
    filas = q(
        """SELECT inv.codigo, inv.titulo, pub.precio FROM inventario inv
           JOIN publicacion pub ON pub.inventario=inv.id AND pub.marketplace='ml'
           WHERE pub.estado IN ('preparando','borrador') ORDER BY inv.creado DESC LIMIT 15"""
    )
    if not filas:
        B.enviar("no hay borradores", chat=chat)
        return
    B.enviar("<b>Borradores</b>\n\n" + "\n".join(
        f"· {f['codigo']} {f['titulo'][:45]} — {_plata(f['precio'])}" for f in filas), chat=chat)


def cmd_numero(chat: str, arg: str, campo: str) -> None:
    partes = arg.split()
    if len(partes) != 2 or not partes[1].isdigit():
        B.enviar(f"uso: /{campo} INV-123 45000", chat=chat)
        return
    codigo, monto = partes[0].upper(), int(partes[1])
    if campo == "piso":
        n = ex("UPDATE inventario SET piso_precio=%s, piso_manual=true WHERE upper(codigo)=%s",
               (monto, codigo))
    else:
        n = ex("""UPDATE publicacion SET precio=%s
                  WHERE inventario=(SELECT id FROM inventario WHERE upper(codigo)=%s)""",
               (monto, codigo))
    B.enviar("listo ✅" if n else "no encontre ese codigo", chat=chat)


def _precio_a_mano(chat: str, texto: str) -> bool:
    """Cuando el bot pidio un precio y respondes con el numero pelado."""
    inv_id = R.get(f"pub:esperando_precio:{chat}")
    if not inv_id:
        return False
    d = re.sub(r"[^0-9]", "", texto)
    if not d or int(d) < 500:
        return False
    monto = int(d)
    piso = round(monto * float(p("venta.factor_piso", 0.85)))
    ex("UPDATE inventario SET piso_precio=%s WHERE id=%s", (piso, inv_id))
    ex("UPDATE publicacion SET precio=%s WHERE inventario=%s AND marketplace='ml'", (monto, inv_id))
    R.delete(f"pub:esperando_precio:{chat}")
    _preview(chat, int(inv_id))
    return True


# ------------------------------------------------------------- botones
def _mandar_a_publicar(inv_id: str, mercados: list[str]) -> str:
    inv = q1("SELECT id, titulo FROM inventario WHERE id=%s", (inv_id,))
    if not inv:
        return "no lo encuentro"
    base = q1("""SELECT titulo, descripcion, precio FROM publicacion
                 WHERE inventario=%s AND marketplace='ml'""", (inv_id,))
    if not base:
        return "se perdio el borrador, mandalo de nuevo"
    if not base["precio"]:
        return "falta el precio: /precio CODIGO MONTO"
    for m in mercados:
        ex(
            """INSERT INTO publicacion (inventario, marketplace, titulo, descripcion, precio, estado)
               VALUES (%s,%s,%s,%s,%s,'borrador')
               ON CONFLICT (inventario, marketplace) DO UPDATE
                 SET estado = CASE WHEN publicacion.estado IN ('preparando','rechazada')
                                   THEN 'borrador' ELSE publicacion.estado END""",
            (inv_id, m, base["titulo"], base["descripcion"], base["precio"]),
        )
    ex("""UPDATE publicacion SET estado='borrador'
          WHERE inventario=%s AND marketplace='ml' AND estado='preparando'""", (inv_id,))
    ex("UPDATE inventario SET estado='borrador' WHERE id=%s", (inv_id,))
    return "en cola: " + " y ".join(mercados)


def boton(cb: dict) -> None:
    data = cb.get("data", "")
    chat = str(cb["message"]["chat"]["id"])
    mid = cb["message"]["message_id"]
    accion, _, arg = data.partition(":")

    if accion == "pb_ambos":
        aviso = _mandar_a_publicar(arg, ["ml", "fb"])
    elif accion == "pb_ml":
        aviso = _mandar_a_publicar(arg, ["ml"])
    elif accion == "pb_fb":
        aviso = _mandar_a_publicar(arg, ["fb"])
    elif accion == "pb_no":
        ex("DELETE FROM publicacion WHERE inventario=%s AND estado='preparando'", (arg,))
        ex("UPDATE inventario SET estado='pausado' WHERE id=%s", (arg,))
        aviso = "descartado"
    elif accion == "fb_hecho":
        ex("UPDATE publicacion SET estado='activa' WHERE id=%s", (arg,))
        aviso = "anotado"

    # Preguntas y ofertas de compradores en TUS publicaciones.
    elif accion == "msg_enviar":
        # arg = id del BORRADOR de salida. reply_bot lo saca de la cola y lo
        # manda a ML en su proxima pasada (cada 10 min).
        R.rpush("cazador:enviar_msg", arg)
        aviso = "se envia"
    elif accion == "fb_enviar":
        # Facebook no tiene API: lo manda reply_fb desde el navegador.
        R.rpush("cazador:fb_enviar", arg)
        aviso = "se manda en el proximo ciclo de FB"
    elif accion in ("msg_mio", "msg_ignorar"):
        # Tambien descarta el borrador que colgaba de este mensaje, si no
        # quedaria 'nuevo' para siempre esperando un boton que ya no llega.
        ex("""UPDATE mensaje SET estado='ignorado', respondido_por='jose'
              WHERE id=%s OR responde_a=%s""", (arg, arg))
        aviso = "queda para ti"
    else:
        aviso = "ok"

    B.responder_boton(cb["id"], aviso)
    B.editar_botones(chat, mid, None)


# ------------------------------------------------------------- loop
def main() -> None:
    offset = int(kv_get("tg_offset_publicador", 0) or 0)
    log.info("bot publicador arriba (offset %s)", offset)
    while True:
        try:
            for u in B.updates(offset, timeout=10):
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    boton(u["callback_query"])
                    continue
                msg = u.get("message") or u.get("edited_message")
                if not msg:
                    continue
                chat = str(msg["chat"]["id"])
                texto = (msg.get("text") or msg.get("caption") or "").strip()

                if texto.startswith("/start"):
                    cmd_start(chat, texto[6:])
                    continue
                if B.chat_id() and chat != B.chat_id():
                    continue

                if texto.startswith("/borradores"):
                    cmd_borradores(chat)
                    continue
                if texto.startswith("/piso"):
                    cmd_numero(chat, texto[5:], "piso")
                    continue
                if texto.startswith("/precio"):
                    cmd_numero(chat, texto[7:], "precio")
                    continue
                if texto.startswith("/cancelar"):
                    R.delete(_clave(chat))
                    B.enviar("borrador botado", chat=chat)
                    continue
                if texto.startswith("/listo"):
                    _armar(chat)
                    continue
                if texto.startswith("/ayuda") or texto.startswith("/help"):
                    B.enviar(AYUDA, chat=chat)
                    continue

                b = _borrador(chat)
                nuevo = False
                if "photo" in msg:
                    b["fotos"].append(msg["photo"][-1]["file_id"])   # la mas grande
                    nuevo = True
                doc = msg.get("document")
                if doc and str(doc.get("mime_type", "")).startswith("image/"):
                    b["fotos"].append(doc["file_id"])                # foto sin comprimir
                    nuevo = True
                if texto and not texto.startswith("/"):
                    if _precio_a_mano(chat, texto):
                        continue
                    b["desc"] = (b["desc"] + " " + texto).strip()
                    nuevo = True
                if nuevo:
                    b["ts"] = time.time()
                    _guardar_borrador(chat, b)

            # Se acabaron las fotos del album: armar solo, sin que digas nada.
            destino = B.chat_id()
            if destino:
                b = _borrador(destino)
                quieto = time.time() - b["ts"]
                # Con fotos basta. La linea de texto es opcional: si la mandas
                # afina las specs, y si no, el modelo mira las fotos y las
                # identifica solo. Eso era lo que faltaba para que "mandar
                # fotos y nada mas" funcionara de verdad.
                if b["fotos"] and quieto > DEBOUNCE_SEG and not b.get("pedido"):
                    _armar(destino)
            kv_set("tg_offset_publicador", offset)
        except Exception as e:
            log.warning("loop: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
