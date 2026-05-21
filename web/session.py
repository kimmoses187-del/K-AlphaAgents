"""
web/session.py
==============
WebSession — bridges the web UI and the Python pipeline.

The pipeline runs in a background thread and calls:
  session.ask()          → sends question to UI, blocks until user answers
  session.message()      → sends a status/info bubble
  session.progress()     → sends a one-line progress update
  session.debate_start() → tells UI to initialise agent cards for a stock
  session.agent_update() → updates an agent card during debate
  session.stock_result() → sends the final result card for a stock
  session.done()         → signals the session is complete
"""

import threading


class WebSession:
    def __init__(self, sid: str, socketio):
        self.sid      = sid
        self.socketio = socketio
        self._event   = threading.Event()
        self._answer  = None
        self.active   = True

    # ── Emit helpers ──────────────────────────────────────────────────────────

    def _emit(self, event: str, data: dict):
        self.socketio.emit(event, data, room=self.sid)

    def message(self, text: str, msg_type: str = "info", subtext: str = ""):
        """Send a system message bubble to the chat."""
        self._emit("s_message", {"text": text, "msg_type": msg_type, "subtext": subtext})

    def progress(self, text: str):
        """Overwrite the last progress line (shown in a non-blocking status strip)."""
        self._emit("s_progress", {"text": text})

    def debate_start(self, ticker: str, name: str):
        """Tell the UI to show the 5 agent cards in 'waiting' state."""
        self._emit("s_debate_start", {"ticker": ticker, "name": name})

    def agent_update(self, agent: str, status: str,
                     signal: str = "", round_num: int = 0):
        """Update a single agent card."""
        self._emit("s_agent_update", {
            "agent": agent, "status": status,
            "signal": signal, "round": round_num,
        })

    def stock_result(self, ticker: str, name: str, results: list,
                     signal_file: str = ""):
        """Send the analysis result card for a completed stock."""
        self._emit("s_stock_result", {
            "ticker": ticker, "name": name, "results": results,
            "signal_file": signal_file,
        })

    def done(self, pdf_path: str = ""):
        """Signal that the session pipeline has finished."""
        self._emit("s_done", {"pdf_path": pdf_path})

    # ── Input handling ────────────────────────────────────────────────────────

    def ask(self, text: str, subtext: str = "",
            input_type: str = "text", options: list = None) -> str:
        """
        Send a question to the UI and block until the user responds.
        input_type: 'text' (free input) | 'buttons' (option buttons)
        Returns the user's answer string.
        """
        self._event.clear()
        self._answer = None
        self._emit("s_question", {
            "text":       text,
            "subtext":    subtext,
            "input_type": input_type,
            "options":    options or [],
        })
        self._event.wait()
        return self._answer or ""

    def receive(self, value: str):
        """Called by the SocketIO server when the client sends any input."""
        self._answer = value
        self._event.set()
