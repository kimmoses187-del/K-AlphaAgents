"""
web/app.py
==========
Flask + SocketIO server for K-AlphaAgents web UI.

Run:
    python3 web/app.py

Then open:  http://localhost:5001
"""

import os
import sys

# Make sure the project root is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, send_file, abort
from flask_socketio import SocketIO, emit

from web.session import WebSession
from web.runner  import run_web_session

app       = Flask(__name__, template_folder="../templates")
app.config["SECRET_KEY"] = "kalpha-agents-2026"
socketio  = SocketIO(app, async_mode="threading", cors_allowed_origins="*")

_sessions: dict[str, WebSession] = {}


@app.route("/")
def index():
    return render_template("ui.html")


@app.route("/download")
def download():
    """
    Serve a file from the reports/ directory for direct download.
    Usage: GET /download?file=reports/Exec+Sum_2025-01-01.pdf
    Only files inside the reports/ folder are allowed (no path traversal).
    """
    rel = request.args.get("file", "")
    # Resolve to absolute path and ensure it stays inside REPORTS_DIR
    reports_abs = os.path.realpath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports"
    ))
    target = os.path.realpath(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), rel
    ))
    if not target.startswith(reports_abs + os.sep) and target != reports_abs:
        abort(403)
    if not os.path.isfile(target):
        abort(404)
    return send_file(target, as_attachment=True)


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
    port = int(os.environ.get("PORT", 5001))
    print("\n  K-AlphaAgents Web UI")
    print("  ─────────────────────────────")
    print(f"  Open: http://localhost:{port}\n")
    socketio.run(app, host="0.0.0.0", port=port, debug=False,
                 allow_unsafe_werkzeug=True)
