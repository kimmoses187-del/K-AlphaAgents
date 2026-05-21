from agents.fundamental_agent import FundamentalAgent
from agents.sentiment_agent import SentimentAgent
from agents.technical_agent import TechnicalAgent
from agents.market_agent import MarketAgent
from agents.macro_agent import MacroAgent
from config import MAX_DEBATE_ROUNDS


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

    With 5 binary-signal agents:
      5-0 or 4-1 → clear majority
      3-2         → majority of 3
    A true tie (2.5-2.5) is impossible with an odd number of agents.
    Falls back to SELL to honour risk-averse profile default.
    """
    signals   = [r["signal"] for r in results]
    buy_count = signals.count("BUY")
    return "BUY" if buy_count > len(signals) / 2 else "SELL"


def _peers_of(agent_name: str, results: list) -> list:
    return [r for r in results if r["agent"] != agent_name]


class DebateManager:
    def __init__(self, risk_profile: str = "risk-averse"):
        self.fundamental  = FundamentalAgent(risk_profile)
        self.sentiment    = SentimentAgent(risk_profile)
        self.technical    = TechnicalAgent(risk_profile)
        self.market       = MarketAgent(risk_profile)
        self.macro        = MacroAgent(risk_profile)
        self.risk_profile = risk_profile

    def run(self, company_name: str,
            fundamental_data: str,
            sentiment_data: str,
            technical_data: str,
            market_data: str,
            macro_data: str,
            progress_cb=None,
            display=None) -> dict:
        """
        Run the full 5-agent collaboration + debate pipeline.

        display     : DebateGrid instance for in-place terminal updates.
                      When provided, all terminal output goes through the
                      grid (no plain prints from this method).
        progress_cb : optional callback for web UI
                      ('agent_update', agent_name, status, signal, round_num)

        Returns a dict with:
          company_name, final_signal, consensus_type
          ("unanimous" | "majority"), consensus_round, debate_log
        """
        profile = self.risk_profile

        def _cb(agent: str, status: str, signal: str = "", rnd: int = 0):
            if display:
                display.update_agent(profile, agent, status, signal, rnd)
            else:
                print(f"      {agent:<20}: {status} {signal}")
            if progress_cb:
                progress_cb("agent_update", agent, status, signal, rnd)

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
            for name in ["FundamentalAgent","SentimentAgent","TechnicalAgent",
                         "MarketAgent","MacroAgent"]:
                _cb(name, "analyzing…", "", 0)

        fund_r   = self.fundamental.analyze(fundamental_data, company_name)
        _cb("FundamentalAgent", "done", fund_r["signal"], 0)
        sent_r   = self.sentiment.analyze(sentiment_data,     company_name)
        _cb("SentimentAgent",   "done", sent_r["signal"], 0)
        tech_r   = self.technical.analyze(technical_data,     company_name)
        _cb("TechnicalAgent",   "done", tech_r["signal"], 0)
        market_r = self.market.analyze(market_data,           company_name)
        _cb("MarketAgent",      "done", market_r["signal"], 0)
        macro_r  = self.macro.analyze(macro_data,             company_name)
        _cb("MacroAgent",       "done", macro_r["signal"], 0)

        current = [fund_r, sent_r, tech_r, market_r, macro_r]
        debate_log.append({"round": 0, "label": "Independent Analysis", "results": current})

        unanimous, signal = _check_unanimous(current)
        if unanimous:
            _result_line(signal, 0, "unanimous")
            return self._result(company_name, signal, "unanimous", 0, debate_log)

        # ── Phase 2: Debate rounds ────────────────────────────────────────
        for rnd in range(1, MAX_DEBATE_ROUNDS + 1):
            _header(rnd)

            _cb("FundamentalAgent", "analyzing", "", rnd)
            fund_r   = self.fundamental.update_position(
                fundamental_data, company_name, _peers_of("FundamentalAgent", current), rnd)
            _cb("FundamentalAgent", "round", fund_r["signal"], rnd)

            _cb("SentimentAgent", "analyzing", "", rnd)
            sent_r   = self.sentiment.update_position(
                sentiment_data,   company_name, _peers_of("SentimentAgent",   current), rnd)
            _cb("SentimentAgent",   "round", sent_r["signal"], rnd)

            _cb("TechnicalAgent", "analyzing", "", rnd)
            tech_r   = self.technical.update_position(
                technical_data,   company_name, _peers_of("TechnicalAgent",   current), rnd)
            _cb("TechnicalAgent",   "round", tech_r["signal"], rnd)

            _cb("MarketAgent", "analyzing", "", rnd)
            market_r = self.market.update_position(
                market_data,      company_name, _peers_of("MarketAgent",      current), rnd)
            _cb("MarketAgent",      "round", market_r["signal"], rnd)

            _cb("MacroAgent", "analyzing", "", rnd)
            macro_r  = self.macro.update_position(
                macro_data,       company_name, _peers_of("MacroAgent",       current), rnd)
            _cb("MacroAgent",       "round", macro_r["signal"], rnd)

            current = [fund_r, sent_r, tech_r, market_r, macro_r]
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
