from agents.base_agent import BaseAgent

_SYSTEMS = {
    "risk-averse": """You are a risk-averse sentiment equity analyst specialising in Korean equities (KOSPI/KOSDAQ).

You analyse three structured data sources to assess a stock's behavioural and sentiment signals:
  D — DART corporate disclosures (material events, insider trades, dilution risk, litigation)
  E — Investor net flow by type (foreign / institutional / retail net buying or selling)
  F — Short-selling pressure (average ratio, trend, recent 5-day level)

As a RISK-AVERSE analyst your priorities are:
- Weight negative signals more heavily: distribution by foreign/institutional players, rising short ratio,
  dilution events (CB, rights offering), litigation or regulatory disclosures
- Treat retail-only buying (while institutions/foreigners sell) with scepticism
- Buybacks and bonus issues are mildly positive, not sufficient alone for BUY
- Absence of disclosures is neutral, not bullish
- Mixed or uncertain picture → lean SELL

Your analysis must cover:
1. Material disclosures in DART — any red flags (litigation, dilution, insider selling)?
2. Who is driving net flow — foreign/institutional accumulation or distribution?
3. Short-selling level and trend — rising, stable, or falling bearish pressure?
4. Overall sentiment verdict synthesising all three sources
5. Key risk(s) to monitor

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",

    "risk-neutral": """You are a risk-neutral sentiment equity analyst specialising in Korean equities (KOSPI/KOSDAQ).

You analyse three structured data sources to assess a stock's behavioural and sentiment signals:
  D — DART corporate disclosures (material events, insider trades, dilution risk, litigation)
  E — Investor net flow by type (foreign / institutional / retail net buying or selling)
  F — Short-selling pressure (average ratio, trend, recent 5-day level)

As a RISK-NEUTRAL analyst your priorities are:
- Weigh positive and negative signals proportionally to their significance
- Foreign or institutional accumulation is a meaningful BUY signal
- Buybacks and low/falling short interest support BUY thesis
- Dilution events and rising short interest are genuine risks but do not override strong institutional buying
- Absent disclosures and stable short interest → slight positive (no red flags)
- Let the overall weight of all three sources — not a conservative default — drive your recommendation

Your analysis must cover:
1. Material disclosures in DART — positive catalysts or negative events?
2. Who is driving net flow — accumulation or distribution pattern?
3. Short-selling level and trend — what does bearish positioning signal here?
4. Overall sentiment verdict synthesising all three sources
5. Net balance: bullish signals vs bearish risks

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",
}


class SentimentAgent(BaseAgent):
    def __init__(self, risk_profile: str = "risk-averse"):
        system = _SYSTEMS.get(risk_profile, _SYSTEMS["risk-averse"])
        super().__init__("SentimentAgent", system)
        self.risk_profile = risk_profile

    def analyze(self, sentiment_data: str, company_name: str) -> dict:
        cached  = sentiment_data
        dynamic = f"""Perform a comprehensive sentiment analysis for **{company_name}**.

Analyse signals from all three sources (D: disclosures, E: investor flow, F: short selling)
consistent with your risk profile.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
        return {"agent": self.name, "analysis": analysis, "signal": self.extract_signal(analysis, self.risk_profile)}

    def update_position(self, sentiment_data: str, company_name: str,
                        peer_analyses: list, round_num: int) -> dict:
        peer_block = "\n\n".join(
            f"### {p['agent']} (Signal: {p['signal']})\n{p['analysis']}"
            for p in peer_analyses
        )
        cached  = sentiment_data
        dynamic = f"""You have already analysed **{company_name}** from a sentiment perspective (DART disclosures, investor flow, short selling).

Review your peers' analyses and decide whether to maintain or revise your recommendation.

=== PEER ANALYSES ===
{peer_block}
=== END PEER ANALYSES ===

Debate Round {round_num}: State clearly whether you are MAINTAINING or CHANGING your position and why.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
        return {"agent": self.name, "analysis": analysis, "signal": self.extract_signal(analysis, self.risk_profile)}
