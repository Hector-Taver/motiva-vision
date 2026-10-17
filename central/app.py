import csv
import io
import json
import os
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import paho.mqtt.client as mqtt
from dotenv import load_dotenv
from flask import Flask, Response, abort, jsonify, render_template, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
BROKER = os.getenv("MQTT_BROKER", "broker.hivemq.com")
PORT = int(os.getenv("MQTT_PORT", "1883"))
RM = os.getenv("RM", "562881")
T_EVENTS, T_CMD, T_STATUS = (f"motiva/{RM}/{n}" for n in ("eventos", "comandos", "status"))
DB = ROOT / "central" / "motiva.db"
FRAMES = ROOT / "evidence" / "frames"
TZ = timezone(timedelta(hours=-3))
ONLINE_WINDOW = 15  # seconds without heartbeat -> ESP32 shown as offline

# operator action -> (command sent to ESP32, new event state)
ACTIONS = {
    "confirmar": ("CONFIRMAR", "CONFIRMADO", ("SUSPEITA",)),
    "rejeitar": ("REJEITAR", "REJEITADO", ("SUSPEITA",)),
    "encerrar": ("ENCERRAR", "ENCERRADO", ("CONFIRMADO",)),
}

app = Flask(__name__)
mqtt_state = {"connected": False}
esp = {"last_seen": None, "last_seen_iso": None, "estado": None}


def now():
    return datetime.now(TZ).isoformat(timespec="seconds")


# ---------- database ----------
def db():
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def execute(sql, params=()):
    conn = db()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def query(sql, params=()):
    conn = db()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def init_db():
    conn = db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS events(
        event_id TEXT PRIMARY KEY, received_at TEXT, timestamp TEXT, camera_id TEXT,
        local TEXT, tipo TEXT, confianca REAL, estado TEXT, recurso TEXT, regra TEXT,
        justificativa TEXT, indicadores TEXT, evidencia TEXT);
    CREATE TABLE IF NOT EXISTS commands(
        command_id TEXT PRIMARY KEY, event_id TEXT, acao TEXT, sent_at TEXT,
        acked_at TEXT, ack_estado TEXT);
    """)
    conn.commit()
    conn.close()


# ---------- MQTT ----------
mqttc = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                    client_id=f"motiva-central-{RM}-{uuid.uuid4().hex[:6]}")
if os.getenv("MQTT_USER"):
    mqttc.username_pw_set(os.getenv("MQTT_USER"), os.getenv("MQTT_PASS"))


def send_command(event_id, acao):
    cid = uuid.uuid4().hex[:12]
    sent = now()
    execute("INSERT INTO commands(command_id,event_id,acao,sent_at) VALUES(?,?,?,?)",
            (cid, event_id, acao, sent))  # stored BEFORE publishing so a fast ack is never lost
    msg = {"command_id": cid, "event_id": event_id, "comando": acao, "timestamp": sent}
    mqttc.publish(T_CMD, json.dumps(msg), qos=1, retain=False)
    return cid


def handle_event(ev):
    eid = ev.get("event_id")
    if not eid:
        return
    rec = ev.get("recomendacao") or {}
    exists = query("SELECT 1 FROM events WHERE event_id=?", (eid,))
    fields = (ev.get("tipo"), ev.get("confianca"), rec.get("recurso"), rec.get("regra"),
              rec.get("justificativa"), json.dumps(ev.get("indicadores", {})),
              json.dumps(ev.get("evidencia", {})))
    if not exists:
        execute("""INSERT INTO events(tipo,confianca,recurso,regra,justificativa,indicadores,
                   evidencia,event_id,received_at,timestamp,camera_id,local,estado)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                fields + (eid, now(), ev.get("timestamp"), ev.get("camera_id"),
                          ev.get("local"), "SUSPEITA"))
        send_command(eid, "SUSPEITA")  # preliminary signal, clearly distinct from CONFIRMAR
    elif ev.get("atualizacao"):
        execute("""UPDATE events SET tipo=?,confianca=?,recurso=?,regra=?,justificativa=?,
                   indicadores=?,evidencia=? WHERE event_id=?""", fields + (eid,))
    # else: duplicate delivery of an existing event -> ignored


def handle_status(msg):
    t = msg.get("tipo")
    if t == "lwt":  # broker-published last will: ESP32 vanished
        esp["estado"] = "FALHA"
        esp["last_seen"] = None
        return
    esp["last_seen"] = time.time()
    esp["last_seen_iso"] = now()
    esp["estado"] = msg.get("estado", esp["estado"])
    if t == "ack" and msg.get("command_id"):
        execute("""UPDATE commands SET acked_at=?, ack_estado=?
                   WHERE command_id=? AND acked_at IS NULL""",
                (now(), msg.get("estado"), msg["command_id"]))


def on_connect(client, userdata, flags, reason_code, properties):
    mqtt_state["connected"] = not reason_code.is_failure
    client.subscribe([(T_EVENTS, 1), (T_STATUS, 1)])  # re-subscribe on every reconnect


def on_disconnect(client, userdata, flags, reason_code, properties):
    mqtt_state["connected"] = False


def on_message(client, userdata, m):
    try:
        data = json.loads(m.payload.decode())
        if not isinstance(data, dict):
            return
        (handle_event if m.topic == T_EVENTS else handle_status)(data)
    except Exception as exc:  # public broker: never crash on garbage input
        print("Ignored bad message:", exc)


mqttc.on_connect, mqttc.on_disconnect, mqttc.on_message = on_connect, on_disconnect, on_message


# ---------- web ----------
def load_events():
    events = query("SELECT * FROM events ORDER BY rowid DESC")
    cmds = query("SELECT * FROM commands ORDER BY sent_at, rowid")
    for e in events:
        e["indicadores"] = json.loads(e["indicadores"] or "{}")
        e["evidencia"] = json.loads(e["evidencia"] or "{}")
        fr = e["evidencia"].get("frame")
        e["frame_url"] = f"/evidence/{os.path.basename(fr)}" if fr else None
        e["comandos"] = [c for c in cmds if c["event_id"] == e["event_id"]]
    return events


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/state")
def api_state():
    seen = esp["last_seen"]
    online = seen is not None and time.time() - seen < ONLINE_WINDOW
    return jsonify({"mqtt": mqtt_state["connected"],
                    "esp": {"online": online, "estado": esp["estado"],
                            "last_seen": esp["last_seen_iso"]},
                    "events": load_events()})


@app.route("/api/events/<event_id>/<action>", methods=["POST"])
def api_action(event_id, action):
    if action not in ACTIONS:
        abort(400)
    cmd, new_state, allowed = ACTIONS[action]
    rows = query("SELECT estado FROM events WHERE event_id=?", (event_id,))
    if not rows:
        abort(404)
    if rows[0]["estado"] not in allowed:
        abort(409)  # prevents double clicks / reopening closed events
    execute("UPDATE events SET estado=? WHERE event_id=?", (new_state, event_id))
    send_command(event_id, cmd)
    return jsonify({"ok": True, "estado": new_state})


@app.route("/evidence/<path:name>")
def evidence(name):
    return send_from_directory(FRAMES, name)


@app.route("/export.json")
def export_json():
    return Response(json.dumps(load_events(), ensure_ascii=False, indent=2),
                    mimetype="application/json",
                    headers={"Content-Disposition": "attachment; filename=motiva_historico.json"})


@app.route("/export.csv")
def export_csv():
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["event_id", "timestamp", "camera_id", "local", "tipo", "confianca", "estado",
                "recurso", "regra", "justificativa", "video", "segundo", "comandos"])
    for e in reversed(load_events()):
        cmds = "; ".join(f"{c['acao']}@{c['sent_at']}" +
                         (f" (ack {c['acked_at']})" if c["acked_at"] else " (sem ack)")
                         for c in e["comandos"])
        ev = e["evidencia"]
        w.writerow([e["event_id"], e["timestamp"], e["camera_id"], e["local"], e["tipo"],
                    e["confianca"], e["estado"], e["recurso"], e["regra"], e["justificativa"],
                    ev.get("video"), ev.get("segundo"), cmds])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=motiva_historico.csv"})


if __name__ == "__main__":
    init_db()
    mqttc.connect_async(BROKER, PORT, keepalive=30)
    mqttc.loop_start()
    app.run(host="127.0.0.1", port=5050, debug=False)  # 5000 clashes with AirPlay on macOS
