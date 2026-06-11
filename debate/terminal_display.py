"""
debate/terminal_display.py
==========================
In-place terminal display for the two-profile parallel debate.

Two DebateManager threads run simultaneously; this class coordinates
cursor movement so each agent row updates itself in place rather than
printing new lines.

Layout (printed once by init(), then updated in place):

  ── RISK-AVERSE  Round 0 ─────────────────────   ← header row
      FundamentalAgent   : ● analyzing…
      SentimentAgent     : ● analyzing…
      TechnicalAgent     : ● analyzing…
      MarketAgent        : ● analyzing…
      MacroAgent         : ● analyzing…
                                                   (blank separator)
  ── RISK-NEUTRAL  Round 0 ────────────────────   ← header row
      FundamentalAgent   : ● analyzing…
      SentimentAgent     : ● analyzing…
      TechnicalAgent     : ● analyzing…
      MarketAgent        : ● analyzing…
      MacroAgent         : ● analyzing…
                                                   (blank — cursor sits below here)

Cursor math
-----------
After init(), cursor is at position _bottom (= number of printed lines).
To update row r:
  - move up  (_bottom - r) lines  → \033[{n}A
  - clear + write                  → \033[2K{text}
  - move down (_bottom - r) lines → \033[{n}B
All three steps are inside a threading.Lock so two threads never
interleave mid-sequence.

After print_result() the cursor moves one line further down, so
_bottom is incremented to keep the math correct for any subsequent
in-place updates.
"""

import sys
import threading

# Global reference to the currently active grid so that code outside this
# module (e.g. base_agent.py) can route prints through the grid's lock
# instead of writing directly to stdout and corrupting cursor position.
_active_grid: "DebateGrid | None" = None

# ── ANSI helpers ──────────────────────────────────────────────────────────────
_G   = "\033[32m"   # green
_R   = "\033[31m"   # red
_DIM = "\033[2m"
_B   = "\033[1m"
_RST = "\033[0m"

_AGENTS   = ["FundamentalAgent", "SentimentAgent", "TechnicalAgent",
             "MarketAgent", "MacroAgent"]
_PROFILES = ("risk-averse", "risk-neutral")
_LABELS   = {"risk-averse": "RISK-AVERSE", "risk-neutral": "RISK-NEUTRAL"}

_SEP = "─" * 44


class DebateGrid:
    """Thread-safe in-place terminal grid for the debate display (1 or 2 profiles)."""

    def __init__(self, profiles=None):
        self._lock       = threading.Lock()
        self._row_map    = {}   # (profile, agent) -> row index from top of grid
        self._hdr_row    = {}   # profile -> row index of its header line
        self._bottom     = 0    # rows below row 0 where cursor currently sits
        self._profiles   = tuple(profiles) if profiles else _PROFILES

    # ── Public API ────────────────────────────────────────────────────────────

    def init(self) -> None:
        """Print the full static grid. Must be called from the main thread."""
        global _active_grid
        rows = []

        for profile in self._profiles:
            label = _LABELS.get(profile, profile.upper())
            rows.append(f"  {_B}── {label}  Round 0 {_SEP[:max(0,44-len(label)-9)]}{_RST}")
            self._hdr_row[profile] = len(rows) - 1
            for agent in _AGENTS:
                rows.append(f"      {agent:<20}: {_DIM}● analyzing…{_RST}")
                self._row_map[(profile, agent)] = len(rows) - 1
            rows.append("")   # blank separator

        sys.stdout.write("\n")
        for row in rows:
            sys.stdout.write(row + "\n")
        sys.stdout.flush()
        _active_grid = self

        # cursor is len(rows) lines below rows[0].
        # The leading \n moves rows[0] one line down from the init start,
        # but does NOT add to the distance between the cursor and rows[0].
        self._bottom = len(rows)

    def update_agent(self, profile: str, agent: str,
                     status: str, signal: str = "", rnd: int = 0) -> None:
        """Overwrite the agent's row in-place."""
        key = (profile, agent)
        if key not in self._row_map:
            return
        text = self._format_agent(agent, status, signal, rnd)
        with self._lock:
            self._write_row(self._row_map[key], text)

    def update_header(self, profile: str, rnd: int) -> None:
        """Overwrite the profile's header row with the current round number."""
        label  = _LABELS[profile]
        phase  = "Independent analysis" if rnd == 0 else f"Debate"
        sep    = _SEP[:max(0, 44 - len(label) - len(phase) - 11)]
        text   = f"  {_B}── {label}  Round {rnd} — {phase} {sep}{_RST}"
        with self._lock:
            self._write_row(self._hdr_row[profile], text)

    def print_result(self, profile: str, signal: str,
                     rnd: int, consensus_type: str,
                     buy_count: int = 0, sell_count: int = 0) -> None:
        """Print a result line below the grid (appends; does not overwrite)."""
        label   = _LABELS[profile]
        sig_col = _G if signal == "BUY" else _R
        sig_txt = f"{sig_col}{_B}{signal}{_RST}"

        if consensus_type == "unanimous":
            msg = f"  [{label}] → Unanimous: {sig_txt}  (round {rnd})"
        else:
            msg = (f"  [{label}] → Majority vote: {sig_txt}"
                   f"  (BUY {buy_count} – SELL {sell_count})")

        with self._lock:
            sys.stdout.write(msg + "\n")
            sys.stdout.flush()
            self._bottom += 1   # cursor moved one line further down

    def print_message(self, msg: str) -> None:
        """Print an arbitrary line below the grid without corrupting cursor math."""
        with self._lock:
            sys.stdout.write(msg + "\n")
            sys.stdout.flush()
            self._bottom += 1

    def close(self) -> None:
        """Deregister this grid as the active grid."""
        global _active_grid
        _active_grid = None

    # ── Private helpers ───────────────────────────────────────────────────────

    def _write_row(self, row: int, text: str) -> None:
        """Jump to row, overwrite, return. Caller must hold self._lock."""
        dist = self._bottom - row
        sys.stdout.write(
            f"\033[{dist}A"     # move up
            f"\033[1G"          # go to column 1
            f"\033[2K{text}"    # clear line + write
            f"\033[{dist}B"     # move back down
            f"\033[1G"
        )
        sys.stdout.flush()

    @staticmethod
    def _format_agent(agent: str, status: str, signal: str, rnd: int) -> str:
        name = f"{agent:<20}"
        if status == "analyzing":
            return f"      {name}: {_DIM}● analyzing…{_RST}"
        if status == "done":
            if signal == "BUY":
                return f"      {name}: {_G}✓ BUY {_RST}"
            return f"      {name}: {_R}✗ SELL{_RST}"
        if status == "round":
            tag = f"R{rnd}"
            if signal == "BUY":
                return f"      {name}: {_G}{tag} ✓ BUY {_RST}"
            return f"      {name}: {_R}{tag} ✗ SELL{_RST}"
        # fallback
        return f"      {name}: {status} {signal}"
