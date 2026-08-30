"""@tu_bot_cazador — el que caza ofertas y negocia.

Tu solo aprietas botones. El bot:
  1. encuentra lo que se revende por 1,5x o mas de lo que piden
  2. te lo manda con los tres numeros: piden / techo / objetivo
  3. si aprietas [Negociar], le escribe al vendedor, saluda, regatea solo
  4. te avisa cuando hay trato cerrado

Emparejar la primera vez:  /start LA_PALABRA  (la que esta en TG_PASS).
"""
from __future__ import annotations

import logging
import re
import time

import redis
import yaml

from app import tg
from app.config import CONFIG, REDIS_URL, TG_PASS
from app.db import ex, kv_get, kv_set, q, q1

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("cazador")
R = redis.from_url(REDIS_URL, decode_responses=True)
B = tg.CAZADOR

AYUDA = """<b>Cazador</b> — ofertas y negociacion

/estado        que esta pasando ahora
/conectar      link para conectar tu cuenta de MercadoLibre
/codigo URL    pega aca la URL a la que te devolvio ML
/negociaciones las conversaciones abiertas
/watch texto   agrega una busqueda
/sugerencias   keywords sacadas de tus propias ventas
/pausa         calla las alertas 12 h

Todo lo demas son botones."""


def _plata(n) -> str:
    return f"${int(n or 0):,}".replace(",", ".")


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
    B.enviar("Emparejado ✅ Este chat recibe las ofertas.\n\n" + AYUDA, chat=chat)


def _linea_ml() -> str:
    """Estado REAL de la conexion con ML, no solo si alguna vez se conecto.

    Decia "ML conectado: si" mirando unicamente si habia un ml_user_id
    guardado. El 29-ago la conexion llevaba 11 horas muerta y /estado seguia
    diciendo que si. Una pantalla de estado que no sabe distinguir "vivo" de
    "hubo" es peor que no tenerla.
    """
    from datetime import datetime, timezone

    ml = q1("SELECT ml_user_id, refresh_token, expira_en FROM oauth_ml WHERE id=1")
    if not ml or not ml["ml_user_id"]:
        return "ML conectado: NO — manda /conectar"
    if ml["refresh_token"]:
        return f"ML conectado: si, cuenta {ml['ml_user_id']} (se renueva sola)"
    vivo = ml["expira_en"] and ml["expira_en"] > datetime.now(timezone.utc)
    if vivo:
        return (f"ML conectado: cuenta {ml['ml_user_id']} — ⚠️ SIN renovacion, "
                "se muere hoy. Falta offline_access en la app de ML")
    return (f"ML conectado: NO (el token de la cuenta {ml['ml_user_id']} vencio "
            "y no se puede renovar). Marca offline_access en "
            "developers.mercadolibre.cl y manda /conectar")


def cmd_estado(chat: str) -> None:
    n_av = q1("SELECT count(*) n FROM oportunidad WHERE estado='avisada'")["n"]
    n_ng = q1("SELECT count(*) n FROM negociacion WHERE estado IN ('por_saludar','saludo','ofertando')")["n"]
    n_ac = q1("SELECT count(*) n FROM negociacion WHERE estado='acordado'")["n"]
    n_wl = len((yaml.safe_load((CONFIG / "watchlist.yml").read_text()) or {}).get("keywords", []))
    fallos = q("""SELECT job, count(*) n FROM job_log
                  WHERE NOT ok AND ts > now() - interval '24 hours'
                  GROUP BY job ORDER BY n DESC LIMIT 5""")
    l = [
        _linea_ml(),
        f"Cazando: {n_wl} busquedas",
        f"Ofertas esperando tu boton: {n_av}",
        f"Negociando ahora: {n_ng} · acordadas: {n_ac}",
    ]
    if fallos:
        l += ["", "Errores 24h: " + ", ".join(f"{f['job']}×{f['n']}" for f in fallos)]
    B.enviar("\n".join(l), chat=chat)


def cmd_conectar(chat: str) -> None:
    from app import ml_api
    from app.config import BASE_PUBLICA, ML_CLIENT_ID
    if not ML_CLIENT_ID:
        B.enviar("falta ML_CLIENT_ID en .env — creame la app en developers.mercadolibre.cl", chat=chat)
        return
    if BASE_PUBLICA:
        B.enviar(f"Abre esto una sola vez y acepta:\n{BASE_PUBLICA}/oauth/login", chat=chat)
        return
    B.enviar(
        "1) abre este link y acepta:\n"
        f"{ml_api.url_login()}\n\n"
        "2) te va a devolver a rematoonline. Copia la barra de direcciones COMPLETA "
        "y pegamela aca con /codigo adelante.\n\n"
        "ejemplo:  /codigo https://rematoonline...?code=TG-abc123",
        chat=chat,
    )


def cmd_codigo(chat: str, arg: str) -> None:
    from app import ml_api
    texto = arg.strip()
    m = re.search(r"[?&]code=([^&\s]+)", texto)
    code = m.group(1) if m else (texto.split()[0] if texto else "")
    if not code:
        B.enviar("pegame la URL completa despues de /codigo", chat=chat)
        return
    # El state viaja en la misma URL. Se exige que sea el que generamos, para
    # que nadie te pueda dejar conectado a la cuenta de ML de otra persona.
    ms = re.search(r"[?&]state=([^&\s]+)", texto)
    # El mensaje distingue los dos casos. Antes decia lo mismo para ambos y no
    # habia forma de saber si faltaba pegar mas URL o si el link ya vencio.
    if not ms:
        B.enviar(
            "En lo que pegaste viene el <b>code</b> pero no el <b>state</b>, "
            "y sin los dos no puedo seguir.\n\n"
            "Pegame la <b>barra de direcciones COMPLETA</b>, tal cual, desde "
            "<code>https://</code> hasta el final. Se ve asi:\n\n"
            "<code>/codigo https://TU-SERVIDOR.ejemplo.net/oauth/callback"
            "?code=TG-xxxx&amp;state=yyyy</code>\n\n"
            "Si la pagina te dio error 404, da igual: lo que sirve es la direccion.",
            chat=chat)
        return
    if not ml_api.verificar_state(ms.group(1)):
        B.enviar("Ese link ya se uso o no salio de este bot.\n"
                 "Manda /conectar y hazlo de nuevo — el codigo dura pocos minutos.",
                 chat=chat)
        return
    try:
        tok = ml_api.canjear_codigo(code)
    except Exception as e:
        B.enviar(f"no sirvio: {str(e)[:250]}\nEl codigo dura pocos minutos — pide otro con /conectar",
                 chat=chat)
        return
    # El canje puede salir "bien" y aun asi dejar una conexion que se muere
    # sola en 6 h. Eso paso el 29-ago y nadie se entero hasta la mañana
    # siguiente, con medio dia de caza perdido. Ahora se avisa aca mismo.
    aviso = ml_api.aviso_sin_refresh(tok)
    if aviso:
        B.enviar(f"conectado a la cuenta {tok.get('user_id')}\n\n{aviso}", chat=chat)
        return
    B.enviar(f"conectado ✅ cuenta {tok.get('user_id')}\n"
             "Renovacion automatica activa. Ya empiezo a cazar.", chat=chat)


def cmd_negociaciones(chat: str) -> None:
    filas = q(
        """SELECT ng.id, ng.estado, ng.ronda, ng.precio_pedido, ng.precio_objetivo,
                  ng.precio_techo, ng.precio_acordado, i.titulo
           FROM negociacion ng
           JOIN oportunidad o ON o.id=ng.oportunidad
           JOIN item_raw i ON i.id=o.item_raw
           WHERE ng.estado IN ('por_saludar','saludo','ofertando','acordado')
           ORDER BY ng.ultimo_mov DESC LIMIT 15"""
    )
    if not filas:
        B.enviar("no hay negociaciones abiertas", chat=chat)
        return
    l = ["<b>Negociando</b>", ""]
    for f in filas:
        cierre = f" → {_plata(f['precio_acordado'])}" if f["precio_acordado"] else ""
        l.append(f"· <b>{f['estado']}</b> r{f['ronda']} · {f['titulo'][:42]}\n"
                 f"  pide {_plata(f['precio_pedido'])} · objetivo {_plata(f['precio_objetivo'])}"
                 f" · techo {_plata(f['precio_techo'])}{cierre}")
    B.enviar("\n".join(l), chat=chat)


def _escribir_watchlist(nuevo_q: str) -> None:
    ruta = CONFIG / "watchlist.yml"
    data = yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}
    data.setdefault("keywords", [])
    if any(k.get("q") == nuevo_q for k in data["keywords"]):
        return
    data["keywords"].append({"q": nuevo_q, "condicion": "all", "activa": True})
    ruta.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def cmd_watch(chat: str, texto: str) -> None:
    texto = texto.strip().lower()
    if not texto:
        B.enviar("uso: /watch thinkpad t14", chat=chat)
        return
    try:
        _escribir_watchlist(texto)
        B.enviar(f"cazando «{texto}» desde el proximo ciclo ✅", chat=chat)
    except Exception as e:
        B.enviar(f"no pude escribir la watchlist: {e}", chat=chat)


def cmd_sugerencias(chat: str) -> None:
    sug = kv_get("watchlist_sugerida") or []
    if not sug:
        B.enviar("todavia no leo tu cuenta de ML. Conectala con /conectar.", chat=chat)
        return
    for palabra in sug[:12]:
        B.enviar(f"¿cazar «{palabra}»?",
                 tg.teclado([[("✅ Si", f"wl_add:{palabra}"), ("✖ No", "wl_no:0")]]), chat=chat)


# ------------------------------------------------------------- botones
def _abrir_negociacion(op_id: str) -> str:
    o = q1(
        """SELECT o.id, o.p_max, o.objetivo, i.precio, i.id_externo, f.tipo
           FROM oportunidad o JOIN item_raw i ON i.id=o.item_raw
           JOIN fuente f ON f.id=i.fuente WHERE o.id=%s""",
        (op_id,),
    )
    if not o:
        return "no la encuentro"
    if o["tipo"] != "ml" or not o["id_externo"]:
        return "solo negocio en MercadoLibre por ahora"
    # Sin techo ni objetivo la escalera de ofertas no tiene de donde agarrarse
    # y reventaria a mitad de la negociacion, con el vendedor esperando.
    if not o["p_max"] or not o["objetivo"]:
        return "sin techo calculado todavia, espera el proximo indice de precios"
    ex(
        """INSERT INTO negociacion (oportunidad, item_externo, precio_pedido,
                                    precio_objetivo, precio_techo)
           VALUES (%s,%s,%s,%s,%s) ON CONFLICT (oportunidad) DO NOTHING""",
        (o["id"], o["id_externo"], o["precio"], o["objetivo"], o["p_max"]),
    )
    ex("UPDATE oportunidad SET estado='negociando' WHERE id=%s", (op_id,))
    return "el bot saluda al vendedor en unos minutos"


def boton(cb: dict) -> None:
    data = cb.get("data", "")
    chat = str(cb["message"]["chat"]["id"])
    mid = cb["message"]["message_id"]
    accion, _, arg = data.partition(":")
    aviso = "listo"

    if accion == "op_negociar":
        aviso = _abrir_negociacion(arg)
    elif accion == "op_ignorar":
        ex("UPDATE oportunidad SET estado='ignorar' WHERE id=%s", (arg,))
        aviso = "ignorada"
    elif accion == "op_watch":
        ex("UPDATE oportunidad SET estado='watchlist' WHERE id=%s", (arg,))
        aviso = "en seguimiento"

    elif accion == "neg_compre":
        fila = q1("SELECT oportunidad, precio_acordado FROM negociacion WHERE id=%s", (arg,))
        if fila:
            ex("UPDATE oportunidad SET estado='comprada' WHERE id=%s", (fila["oportunidad"],))
        aviso = "anotado, va al inventario"
    elif accion == "neg_cancelar":
        ex("UPDATE negociacion SET estado='cancelada' WHERE id=%s", (arg,))
        aviso = "cancelada"

    elif accion == "wl_add":
        _escribir_watchlist(arg)
        aviso = f"cazando {arg}"
    elif accion == "wl_no":
        aviso = "ok"

    elif accion == "cal_ok":
        try:
            p25, p50, p80 = (float(x) for x in arg.split("_"))
            ruta = CONFIG / "policy.yml"
            data = yaml.safe_load(ruta.read_text(encoding="utf-8"))
            data["remate"].update({"p25": p25, "p50": p50, "p80": p80})
            ruta.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
            aviso = "modelo calibrado"
        except Exception as e:
            aviso = f"no pude: {e}"[:60]
    elif accion == "cal_no":
        aviso = "sin cambios"

    B.responder_boton(cb["id"], aviso)
    B.editar_botones(chat, mid, None)


# ------------------------------------------------------------- loop
def main() -> None:
    offset = int(kv_get("tg_offset_cazador", 0) or 0)
    log.info("bot cazador arriba (offset %s)", offset)
    while True:
        try:
            for u in B.updates(offset):
                offset = u["update_id"] + 1
                if "callback_query" in u:
                    boton(u["callback_query"])
                    continue
                msg = u.get("message") or u.get("edited_message")
                if not msg:
                    continue
                chat = str(msg["chat"]["id"])
                texto = (msg.get("text") or "").strip()

                if texto.startswith("/start"):
                    cmd_start(chat, texto[6:])
                    continue
                if B.chat_id() and chat != B.chat_id():
                    continue        # chat desconocido: se ignora en silencio

                if texto.startswith("/estado"):
                    cmd_estado(chat)
                elif texto.startswith("/conectar"):
                    cmd_conectar(chat)
                elif texto.startswith("/codigo"):
                    cmd_codigo(chat, texto[7:])
                elif texto.startswith("/negociaciones"):
                    cmd_negociaciones(chat)
                elif texto.startswith("/watch"):
                    cmd_watch(chat, texto[6:])
                elif texto.startswith("/sugerencias"):
                    cmd_sugerencias(chat)
                elif texto.startswith("/pausa"):
                    R.set("cazador:alertas_pausadas", "1", ex=12 * 3600)
                    B.enviar("alertas en pausa 12 h", chat=chat)
                elif texto:
                    B.enviar(AYUDA, chat=chat)
            kv_set("tg_offset_cazador", offset)
        except Exception as e:
            log.warning("loop: %s", e)
            time.sleep(5)


if __name__ == "__main__":
    main()
