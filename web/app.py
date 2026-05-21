"""
web/app.py
==========
Flask + SocketIO server for K-AlphaAgents web UI.

Run:
    python3 web/app.py

Then open:  http://localhost:5001
"""

import eventlet
eventlet.monkey_patch()

import os
import sys

# Make sure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit

from web.session import WebSession
from web.runner  import run_web_session

app       = Flask(__name__, template_folder="../templates")
app.config["SECRET_KEY"] = "kalpha-agents-2026"
socketio  = SocketIO(app, async_mode="eventlet", cors_allowed_origins="*")

_sessions: dict[str, WebSession] = {}


@app.route("/")
def index():
    return render_template("ui.html")


@socketio.on("connect")
def on_connect():
    sid = request.sid
    _sessions[sid] = WebSession(sid, socketio)
    emit("s_ready", {})


@socketio.on("disconnect")
def on_disconnect():
    _sessions.pop(request.sid, None)


@socketio.on("c_start")
def on_start():
    session = _sessions.get(request.sid)
    if session:
        socketio.start_background_task(run_web_session, session)


@socketio.on("c_input")
def on_input(data):
    session = _sessions.get(request.sid)
    if session:
        session.receive(str(data.get("value", "")))


if __name__ == "__main__":
    print("\n  K-AlphaAgents Web UI")
    print("  ─────────────────────────────")
    print("  Open: http://localhost:5001\n")
    socketio.run(app, host="0.0.0.0", port=5001, debug=False)
