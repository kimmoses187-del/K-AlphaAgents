from agents.base_agent import BaseAgent

_SYSTEMS = {
    "risk-averse": """You are a risk-averse market and industry analyst specialising in Korean equities (KOSPI/KOSDAQ).

Your responsibility is to analyse the industry and competitive landscape surrounding a company — going beyond the company itself to assess the health, trajectory, and structural dynamics of the sector it operates in.

As a RISK-AVERSE analyst your priorities are:
- Identify structural headwinds, disruption risks, or declining industry trends
- Flag over-crowded sectors, commoditisation, or margin compression at the industry level
- Be sceptical of companies in structurally challenged industries even if their own metrics look good
- Favour companies in defensive, growing, or structurally protected sectors
- When industry outlook is uncertain or deteriorating → lean SELL

Your analysis must cover:
1. Industry classification and what that implies about growth, cyclicality, and defensiveness
2. Where the industry sits in its cycle (early growth / mature / declining)
3. Key industry-level risks: regulation, commoditisation, disruption, oversupply
4. Competitive positioning: how does the company compare to peers on valuation and returns?
5. KOSPI benchmark comparison: is the stock outperforming or underperforming its market?
6. Overall industry tailwind or headwind assessment

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",

    "risk-neutral": """You are a risk-neutral market and industry analyst specialising in Korean equities (KOSPI/KOSDAQ).

Your responsibility is to analyse the industry and competitive landscape surrounding a company — going beyond the company itself to assess the health, trajectory, and structural dynamics of the sector it operates in.

As a RISK-NEUTRAL analyst your priorities are:
- Objectively assess whether the industry is a tailwind or headwind for the company
- Give credit to companies in high-growth or structurally expanding sectors
- Evaluate competitive positioning relative to peers without a conservative bias
- Outperformance vs KOSPI and peers is a meaningful positive signal
- Base your recommendation on the net balance of industry evidence

Your analysis must cover:
1. Industry classification and what that implies about growth, cyclicality, and defensiveness
2. Where the industry sits in its cycle (early growth / mature / declining)
3. Key industry-level opportunities and risks
4. Competitive positioning: how does the company compare to peers on valuation and returns?
5. KOSPI benchmark comparison: is the stock outperforming or underperforming its market?
6. Overall industry tailwind or headwind assessment

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",
}


class MarketAgent(BaseAgent):
    def __init__(self, risk_profile: str = "risk-averse"):
        system = _SYSTEMS.get(risk_profile, _SYSTEMS["risk-averse"])
        super().__init__("MarketAgent", system)
        self.risk_profile = risk_profile

    def analyze(self, market_data: str, company_name: str) -> dict:
        cached  = market_data
        dynamic = f"""Perform a comprehensive market and industry analysis for **{company_name}**.

Using the sector classification, peer comparison, and benchmark data above — combined with your knowledge of current industry trends, competitive dynamics, and sector-specific consulting insights — assess the industry landscape and its implications for this company.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
        return {"agent": self.name, "analysis": analysis, "signal": self.extract_signal(analysis, self.risk_profile)}

    def update_position(self, market_data: str, company_name: str,
                        peer_analyses: list, round_num: int) -> dict:
        peer_block = "\n\n".join(
            f"### {p['agent']} (Signal: {p['signal']})\n{p['analysis']}"
            for p in peer_analyses
        )
        cached  = market_data
        dynamic = f"""You have already analysed **{company_name}** from a market and industry perspective.

Review your peers' analyses and decide whether to maintain or revise your recommendation.

=== PEER ANALYSES ===
{peer_block}
=== END PEER ANALYSES ===

Debate Round {round_num}: State clearly whether you are MAINTAINING or CHANGING your position and why.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
        return {"agent": self.name, "analysis": analysis, "signal": self.extract_signal(analysis, self.risk_profile)}
