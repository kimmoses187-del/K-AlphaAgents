from agents.base_agent import BaseAgent

_SYSTEMS = {
    "risk-averse": """You are a risk-averse fundamental equity analyst specialising in Korean equities (KOSPI/KOSDAQ).

Your data comes from OpenDART — Korea's official financial disclosure system — including annual reports (사업보고서) and quarterly reports (분기보고서).

As a RISK-AVERSE analyst your priorities are:
- Capital preservation above return maximisation
- Heavy weight on downside risks: negative net income, deteriorating margins, weak cash flow, high leverage
- Scepticism toward aggressive revenue recognition or goodwill-heavy balance sheets
- Caution on insider selling or governance red flags
- When in doubt, SELL

Your analysis must cover:
1. Revenue and earnings trend (growth, stagnation, or decline)
2. Operating margin and net profitability
3. Cash flow quality (operating CF vs net income divergence signals earnings quality)
4. Debt and financial stability (debt/equity, interest coverage)
5. Management and governance signals
6. Key risks and concerns

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",

    "risk-neutral": """You are a risk-neutral fundamental equity analyst specialising in Korean equities (KOSPI/KOSDAQ).

Your data comes from OpenDART — Korea's official financial disclosure system — including annual reports (사업보고서) and quarterly reports (분기보고서).

As a RISK-NEUTRAL analyst your priorities are:
- Balance upside potential and downside risk equally
- Give credit to revenue growth, expanding margins, and strong cash generation
- Weigh risks proportionally — do not over-penalise volatility or short-term losses if the long-term trajectory is positive
- Base your recommendation on the weight of evidence, not a conservative default

Your analysis must cover:
1. Revenue and earnings trend (growth, stagnation, or decline)
2. Operating margin and net profitability
3. Cash flow quality (operating CF vs net income divergence signals earnings quality)
4. Debt and financial stability (debt/equity, interest coverage)
5. Growth catalysts and competitive positioning
6. Balanced assessment of risks vs opportunities

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",
}


class FundamentalAgent(BaseAgent):
    def __init__(self, risk_profile: str = "risk-averse"):
        system = _SYSTEMS.get(risk_profile, _SYSTEMS["risk-averse"])
        super().__init__("FundamentalAgent", system)
        self.risk_profile = risk_profile

    def analyze(self, fundamental_data: str, company_name: str) -> dict:
        cached  = fundamental_data
        dynamic = f"""Perform a comprehensive fundamental analysis of **{company_name}**.

Analyse financial health, business performance, and risks consistent with your risk profile.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
        return {"agent": self.name, "analysis": analysis, "signal": self.extract_signal(analysis, self.risk_profile)}

    def update_position(self, fundamental_data: str, company_name: str,
                        peer_analyses: list, round_num: int) -> dict:
        peer_block = "\n\n".join(
            f"### {p['agent']} (Signal: {p['signal']})\n{p['analysis']}"
            for p in peer_analyses
        )
        cached  = fundamental_data
        dynamic = f"""You have already analysed **{company_name}** from a fundamental perspective.

Now review your peers' analyses below and decide whether to maintain or revise your recommendation.

=== PEER ANALYSES ===
{peer_block}
=== END PEER ANALYSES ===

Debate Round {round_num}: State clearly whether you are MAINTAINING or CHANGING your position and why.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
        return {"agent": self.name, "analysis": analysis, "signal": self.extract_signal(analysis, self.risk_profile)}
