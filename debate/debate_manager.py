from agents.fundamental_agent import FundamentalAgent
from agents.sentiment_agent import SentimentAgent
from agents.technical_agent import TechnicalAgent
from agents.market_agent import MarketAgent
from agents.macro_agent import MacroAgent
from config import MAX_DEBATE_ROUNDS, ALL_AGENTS

# Registry: agent name → (class, data-key returned by orchestrator._fetch_data).
_AGENT_REGISTRY = {
    "FundamentalAgent": (FundamentalAgent, "fundamental_data"),
    "SentimentAgent":   (SentimentAgent,   "sentiment_data"),
    "TechnicalAgent":   (TechnicalAgent,   "technical_data"),
    "MarketAgent":      (MarketAgent,      "market_data"),
    "MacroAgent":       (MacroAgent,       "macro_data"),
}


def _check_unanimous(results: list) -> tuple:
    """Return (is_unanimous: bool, signal: str | None)."""
    signals = [r["signal"] for r in results]
    if all(s == "BUY"  for s in signals):
        return True, "BUY"
    if all(s == "SELL" for s in signals):
        return True, "SELL"
    return False, None


def _majority_vote(results: list) -> str:
    """Return the majority signal.

    The agent count is constrained to be ODD at selection time (1/3/5), so a
    binary BUY/SELL vote always has a strict majority — no 2.5-2.5 tie.
    (Defensive fallback to SELL is kept in case an even set ever slips through,
    honouring the risk-averse default.)
    """
    signals   = [r["signal"] for r in results]
    buy_count = signals.count("BUY")
    return "BUY" if buy_count > len(signals) / 2 else "SELL"


def _peers_of(agent_name: str, results: list) -> list:
    return [r for r in results if r["agent"] != agent_name]


class DebateManager:
    def __init__(self, risk_profile: str = "risk-averse", agents=None):
        names = list(agents) if agents else list(ALL_AGENTS)
        # Preserve the canonical order regardless of the order picked.
        names = [a for a in ALL_AGENTS if a in names]
        self.agents = [(name, _AGENT_REGISTRY[name][0](risk_profile))
                       for name in names]
        self.risk_profile = risk_profile

    def run(self, company_name: str,
            fundamental_data: str,
            sentiment_data: str,
            technical_data: str,
            market_data: str,
            macro_data: str,
            progress_cb=None,
            display=None,
            calibration_context: dict = None) -> dict:
        """
        Run the collaboration + debate pipeline for the selected agents.

        display              : DebateGrid instance for in-place terminal updates.
                               When provided, all terminal output goes through the
                               grid (no plain prints from this method).
        progress_cb          : optional callback for web UI
                               ('agent_update', agent_name, status, signal, round_num)
        calibration_context  : optional dict mapping agent_name → calibration text block.
                               Each agent's block is prepended to its data so the agent
                               can reference its own past signal accuracy.

        Returns a dict with:
          company_name, final_signal, consensus_type
          ("unanimous" | "majority"), consensus_round, debate_log
        """
        profile = self.risk_profile

        # Map each agent to its data blob via the registry's data-key.
        _data_blobs = {
            "fundamental_data": fundamental_data,
            "sentiment_data":   sentiment_data,
            "technical_data":   technical_data,
            "market_data":      market_data,
            "macro_data":       macro_data,
        }

        # ── Inject calibration context into each agent's data block ───────────
        # The calibration is prepended to the cached data so agents see their
        # own past track record before forming this quarter's view.
        def _with_cal(agent_name: str, data: str) -> str:
            if not calibration_context:
                return data
            ctx = calibration_context.get(agent_name, "")
            if not ctx:
                return data
            return ctx + "\n\n---\n\n" + data

        # Per-agent data, calibration-injected — only for the selected agents.
        agent_data = {
            name: _with_cal(name, _data_blobs[_AGENT_REGISTRY[name][1]])
            for name, _ in self.agents
        }

        def _cb(agent: str, status: str, signal: str = "", rnd: int = 0):
            if display:
                display.update_agent(profile, agent, status, signal, rnd)
            elif not progress_cb and status not in ("analyzing", "analyzing…"):
                # Only print in plain terminal (no live grid, no web callback).
                # Web mode: progress_cb handles display via SocketIO.
                # Terminal mode: DebateGrid handles in-place rendering.
                # Both suppress the "analyzing" intermediate state to avoid duplicate lines.
                print(f"      {agent:<20}: {status} {signal}")
            if progress_cb:
                progress_cb("agent_update", agent, status, signal, rnd, profile)

        def _header(rnd: int):
            if display:
                display.update_header(profile, rnd)

        def _result_line(sig: str, rnd: int, ctype: str,
                         buy_n: int = 0, sell_n: int = 0):
            if display:
                display.print_result(profile, sig, rnd, ctype, buy_n, sell_n)
            else:
                if ctype == "unanimous":
                    print(f"  [{profile.upper()}] → Unanimous: {sig}  (round {rnd})")
                else:
                    print(f"  [{profile.upper()}] → Majority vote: {sig}"
                          f"  (BUY {buy_n} – SELL {sell_n})")

        debate_log = []

        # ── Phase 1: Independent analysis ────────────────────────────────
        _header(0)
        # Grid already shows all agents as "analyzing…"; no extra pre-print needed.
        # (Without a grid, print them now so the user sees the names.)
        if not display:
            print(f"  [{profile.upper()}]  Round 0 — Independent analysis")
            for name, _ in self.agents:
                _cb(name, "analyzing…", "", 0)

        current = []
        for name, agent in self.agents:
            r = agent.analyze(agent_data[name], company_name)
            _cb(name, "done", r["signal"], 0)
            current.append(r)

        debate_log.append({"round": 0, "label": "Independent Analysis", "results": current})

        unanimous, signal = _check_unanimous(current)
        if unanimous:
            _result_line(signal, 0, "unanimous")
            return self._result(company_name, signal, "unanimous", 0, debate_log)

        # ── Phase 2: Debate rounds ────────────────────────────────────────
        for rnd in range(1, MAX_DEBATE_ROUNDS + 1):
            _header(rnd)

            # All agents update against the PREVIOUS round's positions (current),
            # then current is reassigned once the round completes.
            next_results = []
            for name, agent in self.agents:
                _cb(name, "analyzing", "", rnd)
                r = agent.update_position(
                    agent_data[name], company_name, _peers_of(name, current), rnd)
                _cb(name, "round", r["signal"], rnd)
                next_results.append(r)

            current = next_results
            debate_log.append({"round": rnd, "label": f"Debate Round {rnd}", "results": current})

            unanimous, signal = _check_unanimous(current)
            if unanimous:
                _result_line(signal, rnd, "unanimous")
                return self._result(company_name, signal, "unanimous", rnd, debate_log)

        # ── No unanimous consensus after MAX_DEBATE_ROUNDS ───────────────
        final_signal = _majority_vote(current)
        signals      = [r["signal"] for r in current]
        buy_count    = signals.count("BUY")
        sell_count   = signals.count("SELL")
        _result_line(final_signal, MAX_DEBATE_ROUNDS, "majority", buy_count, sell_count)
        return self._result(company_name, final_signal, "majority", MAX_DEBATE_ROUNDS, debate_log)

    @staticmethod
    def _result(company_name, signal, consensus_type, round_num, log):
        return {
            "company_name":    company_name,
            "final_signal":    signal,
            "consensus_type":  consensus_type,
            "consensus_round": round_num,
            "debate_log":      log,
        }
