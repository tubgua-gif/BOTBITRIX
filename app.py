# ==== 1) Imports ====
import os, re, requests, json, unicodedata
from dotenv import load_dotenv
from flask import Flask, request, render_template, jsonify
import google.generativeai as genai
from flask_cors import CORS
import logging
from requests.exceptions import Timeout, RequestException


# ==== 2) Configuración logging ====
logging.basicConfig(level=logging.INFO)


# ==== 3) Cargar variables de entorno (.env) ====
load_dotenv()

BITRIX = (os.getenv("BITRIX_WEBHOOK", "").strip() or "")
if BITRIX and not BITRIX.endswith("/"):
    BITRIX += "/"

GEMINI_KEY = os.getenv("GEMINI_KEY", "").strip()
if not GEMINI_KEY:
    raise ValueError("❌ No se encontró GEMINI_KEY en .env. Crea una en https://aistudio.google.com/app/apikey")


# ==== 4) Configuración de Gemini ====
genai.configure(api_key=GEMINI_KEY)
GEMINI_MODEL = "gemini-1.5-flash"
gemini_model = genai.GenerativeModel(GEMINI_MODEL)


# ==== 5) Funciones auxiliares Bitrix ====
def _bx_post(method, payload=None, timeout=20, auth_id=None, domain=None):
    try:
        if auth_id and domain:
            url = f"https://{domain}/rest/{method}.json?auth={auth_id}"
        else:
            if not BITRIX:
                raise RuntimeError("Falta BITRIX_WEBHOOK en .env y no se recibió AUTH_ID/DOMAIN")
            url = f"{BITRIX}{method}.json"

        r = requests.post(url, json=(payload or {}), timeout=timeout)
        r.raise_for_status()
        data = r.json()

        if "error" in data:
            raise RuntimeError(f"{data.get('error')}: {data.get('error_description')}")

        return data

    except Timeout:
        raise RuntimeError("⏰ Timeout al conectar con Bitrix")
    except RequestException as e:
        raise RuntimeError(f"❌ Error de conexión con Bitrix: {e}")


def _bx_get(method, payload=None, timeout=20, auth_id=None, domain=None):
    try:
        if auth_id and domain:
            url = f"https://{domain}/rest/{method}.json?auth={auth_id}"
        else:
            if not BITRIX:
                raise RuntimeError("Falta BITRIX_WEBHOOK en .env y no se recibió AUTH_ID/DOMAIN")
            url = f"{BITRIX}{method}.json"

        r = requests.get(url, params=(payload or {}), timeout=timeout)
        r.raise_for_status()
        data = r.json()

        if "error" in data:
            raise RuntimeError(f"{data.get('error')}: {data.get('error_description')}")

        return data

    except Timeout:
        raise RuntimeError("⏰ Timeout al conectar con Bitrix")
    except RequestException as e:
        raise RuntimeError(f"❌ Error de conexión con Bitrix: {e}")


def _paged_list(method, base_filter=None, select=None, page_size=50, max_pages=10, auth_id=None, domain=None):
    items, start, pages = [], 0, 0

    while pages < max_pages:
        payload = {"filter": base_filter or {}, "select": select or [], "start": start}
        data = _bx_post(method, payload, timeout=20, auth_id=auth_id, domain=domain)

        result = data.get("result", [])
        if isinstance(result, dict) and "tasks" in result:
            batch = result["tasks"]
        elif isinstance(result, dict) and "items" in result:
            batch = result["items"]
        else:
            batch = result if isinstance(result, list) else []

        items.extend(batch)

        next_start = data.get("next")
        if next_start is None:
            break

        start = next_start
        pages += 1

    return items


# ==== 6) Consultas a Bitrix ====
def consultar_tareas(user_id, limit=15, auth_id=None, domain=None):
    if not user_id:
        raise RuntimeError("No llegó user_id (identifícate primero).")

    select = ["ID", "TITLE", "STATUS", "DEADLINE", "RESPONSIBLE_ID"]
    flt = {"RESPONSIBLE_ID": user_id, "STATUS": 2}

    tasks = _paged_list("tasks.task.list", base_filter=flt, select=select,
                        page_size=50, max_pages=5, auth_id=auth_id, domain=domain)

    titulos = []
    for t in tasks[:limit]:
        task = t.get("task", t)
        title = task.get("title") or task.get("TITLE") or "Sin título"
        tid = task.get("id") or task.get("ID")
        dd = task.get("deadline") or task.get("DEADLINE") or ""
        titulos.append(f"📋 Tarea #{tid}: {title}" + (f" · Vence: {dd}" if dd else ""))

    return titulos


def consultar_leads_abiertos(assigned_by_id=None, limit=15, auth_id=None, domain=None):
    flt = {"STATUS_SEMANTIC_ID": "PROCESS"}
    if assigned_by_id:
        flt["ASSIGNED_BY_ID"] = assigned_by_id

    select = ["ID", "TITLE", "STATUS_ID", "ASSIGNED_BY_ID", "DATE_CREATE"]
    leads = _paged_list("crm.lead.list", base_filter=flt, select=select,
                        page_size=50, max_pages=5, auth_id=auth_id, domain=domain)

    # fallback si no devuelve nada
    if not leads:
        for st in ["NEW", "IN_PROCESS", "PROCESSING"]:
            flt2 = {"STATUS_ID": st}
            if assigned_by_id:
                flt2["ASSIGNED_BY_ID"] = assigned_by_id
            leads += _paged_list("crm.lead.list", base_filter=flt2, select=select,
                                 page_size=50, max_pages=2, auth_id=auth_id, domain=domain)

    out = []
    for ld in leads[:limit]:
        title = ld.get("TITLE") or f"Lead #{ld.get('ID','')}"
        status = ld.get("STATUS_ID", "")
        asg = ld.get("ASSIGNED_BY_ID", "")
        out.append(f"📋 Lead #{ld.get('ID')} - {title} · Estado: {status} (Asignado a: {asg})")

    return out


def consultar_deals(user_id, limit=15, auth_id=None, domain=None):
    if not user_id:
        raise RuntimeError("No llegó user_id (identifícate primero).")

    select = ["ID", "TITLE", "STATUS_ID", "ASSIGNED_BY_ID"]
    flt = {"ASSIGNED_BY_ID": user_id}

    deals = _paged_list("crm.deal.list", base_filter=flt, select=select,
                        page_size=50, max_pages=5, auth_id=auth_id, domain=domain)

    titulos = []
    for d in deals[:limit]:
        title = d.get("TITLE") or "Sin título"
        did = d.get("ID")
        status = d.get("STATUS_ID") or "Sin estado"
        titulos.append(f"💼 Deal #{did}: {title} · Estado: {status}")

    return titulos


# ==== 7) Utilidades de texto ====
def normalize_text(text):
    return ''.join(
        c for c in unicodedata.normalize('NFD', text.lower())
        if unicodedata.category(c) != 'Mn'
    )


# ==== 8) Flask App ====
app = Flask(__name__)
CORS(app, origins=["https://tubelite.bitrix24.es"])  # Restringido a tu dominio


@app.route('/favicon.ico')
def favicon():
    return "", 204


@app.route('/', methods=["GET", "POST"])
def index():
    return render_template('chatbot.html')


@app.route("/ping", methods=["GET"])
def ping():
    return "pong", 200


# ---- Endpoints Bitrix ----
@app.route('/users', methods=['GET'])
def list_users():
    try:
        auth_id = request.args.get("AUTH_ID") or request.args.get("auth_id")
        domain = request.args.get("DOMAIN") or request.args.get("domain")

        users, start = [], 0
        while True:
            payload = {"start": start}
            data = _bx_get("user.get", payload=payload, timeout=20, auth_id=auth_id, domain=domain)
            batch = data.get("result", [])

            if not batch:
                break

            for u in batch:
                users.append({
                    "id": u.get("ID"),
                    "name": f"{u.get('NAME','')} {u.get('LAST_NAME','')}".strip()
                })

            if "next" in data and data["next"] is not None:
                start = data["next"]
            else:
                break

        return jsonify({"ok": True, "users": users})

    except Exception as e:
        logging.exception("Error en /users")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/whoami', methods=['GET'])
def whoami():
    try:
        auth_id = request.args.get("AUTH_ID") or request.args.get("auth_id")
        domain = request.args.get("DOMAIN") or request.args.get("domain")

        data = _bx_get("user.current", timeout=20, auth_id=auth_id, domain=domain)
        d = data.get("result", {})

        return jsonify({
            "ok": True,
            "id": d.get("ID"),
            "name": f"{d.get('NAME','')} {d.get('LAST_NAME','')}".strip()
        })

    except Exception as e:
        logging.exception("Error en /whoami")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/bitrix/install", methods=["POST", "GET"])
def bitrix_install():
    try:
        domain = request.args.get("DOMAIN")
        protocol = request.args.get("PROTOCOL")
        lang = request.args.get("LANG")
        app_sid = request.args.get("APP_SID")

        logging.info(f"Instalación Bitrix: {domain} {protocol} {lang} {app_sid}")

        return render_template("install_success.html",
                               domain=domain,
                               protocol=protocol,
                               lang=lang,
                               app_sid=app_sid)

    except Exception as e:
        logging.exception("Error en /bitrix/install")
        return jsonify({"error": str(e)}), 500


@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return "Webhook activo", 200

    data = request.json or {}
    mensaje = (data.get('message') or '').strip()
    auth_id = data.get('AUTH_ID') or data.get('auth_id')
    domain = data.get('DOMAIN') or data.get('domain')
    user_id = data.get('user_id')

    # autoidentificación si falta user_id
    if not user_id and auth_id and domain:
        try:
            info = _bx_get("user.current", timeout=20, auth_id=auth_id, domain=domain)
            user_id = info.get("result", {}).get("ID")
        except Exception:
            pass

    if not mensaje:
        return jsonify({"respuesta": "⚠️ No recibí mensaje."})

    if not user_id:
        return jsonify({"respuesta": "⚠️ Necesito saber quién eres. Abre el chat dentro de Bitrix para autoidentificarte."})

    msg_l = normalize_text(mensaje)

    # --- Consultar tareas ---
    if "tareas" in msg_l:
        try:
            tareas = consultar_tareas(user_id, auth_id=auth_id, domain=domain)
            if tareas:
                return jsonify({"respuesta": "📝 Tareas asignadas:\n\n\n- " + "\n\n\n- ".join(tareas) + "\n\n\n"})
            return jsonify({"respuesta": "🗒️ No encontré tareas asignadas.\n\n\n"})
        except Exception as e:
            return jsonify({"respuesta": f"❌ Error consultando tareas: {e}"})


    # --- Consultar leads abiertos ---
    if re.search(r"\bleads?\b.*\babiert", msg_l) or "leads abiertos" in msg_l:
        try:
            leads = consultar_leads_abiertos(assigned_by_id=user_id, auth_id=auth_id, domain=domain)
            if leads:
                return jsonify({"respuesta": "📋 Leads abiertos:\n\n\n- " + "\n\n\n- ".join(leads) + "\n\n\n"})
            return jsonify({"respuesta": "📋 No encontré leads abiertos.\n\n\n"})
        except Exception as e:
            return jsonify({"respuesta": f"❌ Error consultando leads: {e}"})


    # --- Consultar deals (notificaciones) ---
    if "notificaciones" in msg_l:
        try:
            deals = consultar_deals(user_id, auth_id=auth_id, domain=domain)
            if deals:
                deals_text = "\n\n".join(f"• {d}" for d in deals)
                return jsonify({"respuesta": f"💼 Notificaciones:\n\n{deals_text}"})
            return jsonify({"respuesta": "No encontré Notificaciones."})
        except Exception as e:
            return jsonify({"respuesta": f"❌ Error consultando Notificaciones: {e}"})


    # --- Consultar pendientes ---
    if "pendiente" in msg_l or "asignado" in msg_l:
        try:
            tareas = consultar_tareas(user_id, auth_id=auth_id, domain=domain)
            leads = consultar_leads_abiertos(assigned_by_id=user_id, auth_id=auth_id, domain=domain)

            respuesta = "📝 Pendientes asignados:\n\n\n"
            if tareas:
                respuesta += "🗒️ Tareas:\n- " + "\n\n\n- ".join(tareas) + "\n\n\n"
            else:
                respuesta += "🗒️ No tienes tareas asignadas.\n\n\n"

            if leads:
                respuesta += "📋 Leads abiertos:\n\n\n- " + "\n\n\n- ".join(leads) + "\n\n\n"
            else:
                respuesta += "📋 No tienes leads abiertos.\n\n\n"

            return jsonify({"respuesta": respuesta})

        except Exception as e:
            return jsonify({"respuesta": f"❌ Error consultando pendientes: {e}"})


    # --- Respuesta con Gemini ---
    try:
        resp = gemini_model.generate_content(mensaje)

        if hasattr(resp, "prompt_feedback") and resp.prompt_feedback and getattr(resp.prompt_feedback, "block_reason", None):
            return jsonify({"respuesta": f"⚠️ La respuesta fue bloqueada por la política ({resp.prompt_feedback.block_reason})."})

        texto = (getattr(resp, "text", None) or "").strip()
        if not texto:
            try:
                texto = (resp.candidates[0].content.parts[0].text or "").strip()
            except Exception:
                texto = "No tengo respuesta."

        return jsonify({"respuesta": texto})

    except Exception as e:
        return jsonify({"respuesta": f"❌ Error con Gemini: {e}"})


# ==== 9) Iniciar servidor Flask ====
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False, threaded=True)
