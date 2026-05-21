"""
agents/technical_agent.py
==========================
TechnicalAgent — price action, momentum, and technical indicator analysis.

Replaces the former ValuationAgent. Data inputs extended to include:
  - Moving averages (20d / 60d MA) and price position relative to them
  - RSI (14-day Wilder's) with overbought / oversold context
  - Bollinger Bands (20d ±2σ) with %B band-position reading
  - Relative performance vs KOSPI and KOSDAQ over the same 3-month window
  - QoQ delta: return and volatility change vs the prior quarter

Data source: pykrx (KRX authoritative data) via tools/pykrx_tools.py
"""

from agents.base_agent import BaseAgent

_SYSTEMS = {
    "risk-averse": """You are a risk-averse technical equity analyst specialising in Korean equities (KOSPI/KOSDAQ).

You analyse price action, momentum indicators, and relative market performance to assess whether a stock is in a technically sound or deteriorating position.

As a RISK-AVERSE analyst your priorities are:
- Price below key moving averages (20d, 60d MA) is a primary red flag
- RSI below 30 (oversold) in a downtrend signals continued weakness, not a buy
- Volatility above 30% is elevated; above 50% is speculative — require higher return compensation
- Negative alpha vs both KOSPI and KOSDAQ = stock not participating in market strength
- Bollinger %B near lower band during a downtrend confirms, not refutes, the bearish case
- Deteriorating QoQ momentum (return worsening, vol rising) increases conviction to SELL
- When signals are mixed or ambiguous → default to SELL

Your analysis must cover:
1. Price trend: direction, consistency, and position vs 20d / 60d MA
2. Momentum: RSI reading and what it signals in current trend context
3. Bollinger Band position: is the stock breaking down or recovering?
4. Volume trend: confirms or contradicts price direction
5. Relative performance: is the stock keeping pace with KOSPI / KOSDAQ or lagging?
6. QoQ delta: is the technical situation improving or deteriorating vs the prior quarter?

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",

    "risk-neutral": """You are a risk-neutral technical equity analyst specialising in Korean equities (KOSPI/KOSDAQ).

You analyse price action, momentum indicators, and relative market performance to assess whether a stock's technical picture supports a constructive or cautious stance.

As a RISK-NEUTRAL analyst your priorities are:
- Evaluate the full technical picture objectively — no automatic defaults
- RSI oversold + price near lower Bollinger Band = potential reversal signal if volume confirms
- Positive alpha vs KOSPI / KOSDAQ is meaningful — outperformance in a rising market is a tailwind
- Improving QoQ momentum (return rising, vol falling) signals a positive trend change
- Price above MA20 and MA60 with volume confirmation = BUY signal
- High volatility is informative, not automatically disqualifying — classify and contextualise it

Your analysis must cover:
1. Price trend: direction, consistency, and position vs 20d / 60d MA
2. Momentum: RSI reading and what it signals in current trend context
3. Bollinger Band position: breakout, squeeze, or mean-reversion opportunity?
4. Volume trend: confirms or contradicts price direction
5. Relative performance: is the stock keeping pace with KOSPI / KOSDAQ or outperforming?
6. QoQ delta: is the technical situation improving or deteriorating vs the prior quarter?

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",
}


class TechnicalAgent(BaseAgent):
    def __init__(self, risk_profile: str = "risk-averse"):
        system = _SYSTEMS.get(risk_profile, _SYSTEMS["risk-averse"])
        super().__init__("TechnicalAgent", system)
        self.risk_profile = risk_profile

    def analyze(self, technical_data: str, company_name: str) -> dict:
        prompt = f"""Perform a comprehensive technical analysis for **{company_name}**.

{technical_data}

Analyse the price trend, momentum indicators (RSI, Bollinger Bands, moving averages),
relative performance vs benchmarks, and QoQ momentum shift.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm(prompt)
        return {
            "agent":  self.name,
            "analysis": analysis,
            "signal": self.extract_signal(analysis, self.risk_profile),
        }

    def update_position(self, technical_data: str, company_name: str,
                        peer_analyses: list, round_num: int) -> dict:
        peer_block = "\n\n".join(
            f"### {p['agent']} (Signal: {p['signal']})\n{p['analysis']}"
            for p in peer_analyses
        )
        prompt = f"""You have already analysed **{company_name}** from a technical perspective.

Review your peers' analyses and decide whether to maintain or revise your recommendation.

=== PEER ANALYSES ===
{peer_block}
=== END PEER ANALYSES ===

=== YOUR TECHNICAL DATA ===
{technical_data}
=== END DATA ===

Debate Round {round_num}: State clearly whether you are MAINTAINING or CHANGING your position and why.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm(prompt)
        return {
            "agent":  self.name,
            "analysis": analysis,
            "signal": self.extract_signal(analysis, self.risk_profile),
        }
