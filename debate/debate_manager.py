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
        self.fundamental = FundamentalAgent(risk_profile)
        self.sentiment   = SentimentAgent(risk_profile)
        self.technical   = TechnicalAgent(risk_profile)
        self.market      = MarketAgent(risk_profile)
        self.macro       = MacroAgent(risk_profile)
        self.risk_profile = risk_profile

    def run(self, company_name: str,
            fundamental_data: str,
            sentiment_data: str,
            technical_data: str,
            market_data: str,
            macro_data: str) -> dict:
        """
        Run the full 5-agent collaboration + debate pipeline.

        Returns a dict with:
          company_name, final_signal, consensus_type
          ("unanimous" | "majority"), consensus_round, debate_log
        """
        debate_log = []

        # ── Phase 1: Independent analysis ────────────────────────────────
        print("  [Round 0] Independent analysis...")
        fund_r   = self.fundamental.analyze(fundamental_data, company_name)
        sent_r   = self.sentiment.analyze(sentiment_data,     company_name)
        tech_r   = self.technical.analyze(technical_data,     company_name)
        market_r = self.market.analyze(market_data,           company_name)
        macro_r  = self.macro.analyze(macro_data,             company_name)

        current = [fund_r, sent_r, tech_r, market_r, macro_r]
        debate_log.append({"round": 0, "label": "Independent Analysis", "results": current})
        self._print_signals(current)

        unanimous, signal = _check_unanimous(current)
        if unanimous:
            print(f"  → Unanimous consensus: {signal} (no debate needed)")
            return self._result(company_name, signal, "unanimous", 0, debate_log)

        # ── Phase 2: Debate rounds ────────────────────────────────────────
        for rnd in range(1, MAX_DEBATE_ROUNDS + 1):
            print(f"  [Round {rnd}] Debate...")

            fund_r   = self.fundamental.update_position(
                fundamental_data, company_name, _peers_of("FundamentalAgent", current), rnd)
            sent_r   = self.sentiment.update_position(
                sentiment_data,   company_name, _peers_of("SentimentAgent",   current), rnd)
            tech_r   = self.technical.update_position(
                technical_data,   company_name, _peers_of("TechnicalAgent",   current), rnd)
            market_r = self.market.update_position(
                market_data,      company_name, _peers_of("MarketAgent",      current), rnd)
            macro_r  = self.macro.update_position(
                macro_data,       company_name, _peers_of("MacroAgent",       current), rnd)

            current = [fund_r, sent_r, tech_r, market_r, macro_r]
            debate_log.append({"round": rnd, "label": f"Debate Round {rnd}", "results": current})
            self._print_signals(current)

            unanimous, signal = _check_unanimous(current)
            if unanimous:
                print(f"  → Unanimous consensus: {signal}")
                return self._result(company_name, signal, "unanimous", rnd, debate_log)

        # ── No unanimous consensus after MAX_DEBATE_ROUNDS ───────────────
        final_signal = _majority_vote(current)
        signals      = [r["signal"] for r in current]
        buy_count    = signals.count("BUY")
        sell_count   = signals.count("SELL")
        print(f"  → No unanimous consensus after {MAX_DEBATE_ROUNDS} rounds.")
        print(f"  → Majority vote: {final_signal}  (BUY: {buy_count}, SELL: {sell_count})")
        return self._result(company_name, final_signal, "majority", MAX_DEBATE_ROUNDS, debate_log)

    @staticmethod
    def _print_signals(results: list):
        for r in results:
            print(f"      {r['agent']:<20}: {r['signal']}")

    @staticmethod
    def _result(company_name, signal, consensus_type, round_num, log):
        return {
            "company_name":    company_name,
            "final_signal":    signal,
            "consensus_type":  consensus_type,
            "consensus_round": round_num,
            "debate_log":      log,
        }
