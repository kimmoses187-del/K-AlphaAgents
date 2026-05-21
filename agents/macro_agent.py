from agents.base_agent import BaseAgent

_SYSTEMS = {
    "risk-averse": """You are a risk-averse macroeconomic analyst specialising in Korean equities (KOSPI/KOSDAQ).

Your responsibility is to assess the broader macroeconomic environment — including currency dynamics, interest rates, global equity trends, and commodity prices — and judge their impact on the company and its sector.

As a RISK-AVERSE analyst your priorities are:
- A strengthening USD/KRW (weakening Korean Won) raises import costs and signals capital outflows — treat as a risk
- Rising US Treasury yields compress equity valuations globally and attract capital away from Korean markets
- Commodity price spikes (oil, raw materials) are cost headwinds for manufacturers
- KOSPI underperformance vs global indices signals Korea-specific risk
- In macro uncertainty, lean SELL — capital preservation matters most

Your analysis must cover:
1. USD/KRW trend and its specific impact on this company (exporter vs importer)
2. Interest rate environment (US 10Y yields) and its effect on equity valuations
3. KOSPI vs global indices: is Korea attracting or losing capital flows?
4. Commodity prices (oil, gold) and their relevance to this sector's cost base
5. Overall macro tailwind or headwind verdict for this company

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",

    "risk-neutral": """You are a risk-neutral macroeconomic analyst specialising in Korean equities (KOSPI/KOSDAQ).

Your responsibility is to assess the broader macroeconomic environment — including currency dynamics, interest rates, global equity trends, and commodity prices — and judge their impact on the company and its sector.

As a RISK-NEUTRAL analyst your priorities are:
- A weakening Korean Won benefits exporters — assess directionally based on business model
- Falling US yields can be a tailwind for equity valuations — recognise both sides
- KOSPI outperformance vs global indices is a positive capital flow signal
- Assess commodity prices in context: input cost pressure vs end-market demand
- Base your recommendation on the net macro balance for this specific company and sector

Your analysis must cover:
1. USD/KRW trend and its specific impact on this company (exporter vs importer)
2. Interest rate environment (US 10Y yields) and its effect on equity valuations
3. KOSPI vs global indices: is Korea attracting or losing capital flows?
4. Commodity prices (oil, gold) and their relevance to this sector's cost base
5. Overall macro tailwind or headwind verdict for this company

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",
}


class MacroAgent(BaseAgent):
    def __init__(self, risk_profile: str = "risk-averse"):
        system = _SYSTEMS.get(risk_profile, _SYSTEMS["risk-averse"])
        super().__init__("MacroAgent", system)
        self.risk_profile = risk_profile

    def analyze(self, macro_data: str, company_name: str) -> dict:
        cached  = macro_data
        dynamic = f"""Perform a comprehensive macroeconomic analysis for **{company_name}**.

Using the macro indicators above — combined with your knowledge of the Bank of Korea monetary policy, Korea's export-driven economy, and current global economic conditions — assess the macro environment and its implications for this company.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
        return {"agent": self.name, "analysis": analysis, "signal": self.extract_signal(analysis, self.risk_profile)}

    def update_position(self, macro_data: str, company_name: str,
                        peer_analyses: list, round_num: int) -> dict:
        peer_block = "\n\n".join(
            f"### {p['agent']} (Signal: {p['signal']})\n{p['analysis']}"
            for p in peer_analyses
        )
        cached  = macro_data
        dynamic = f"""You have already analysed **{company_name}** from a macroeconomic perspective.

Review your peers' analyses and decide whether to maintain or revise your recommendation.

=== PEER ANALYSES ===
{peer_block}
=== END PEER ANALYSES ===

Debate Round {round_num}: State clearly whether you are MAINTAINING or CHANGING your position and why.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
        return {"agent": self.name, "analysis": analysis, "signal": self.extract_signal(analysis, self.risk_profile)}
