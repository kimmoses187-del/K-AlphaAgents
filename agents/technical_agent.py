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

You analyse price action, momentum indicators, and relative market performance.

**Step 1 — Read all data objectively:**
Analyse the full technical picture: price trend, moving averages, RSI, Bollinger Bands, volume, relative performance vs KOSPI/KOSDAQ, and QoQ momentum. Observe every signal — bullish and bearish — without filtering. Build a complete technical view before forming a conclusion.

**Step 2 — Apply the risk-averse lens to your final judgment:**
You are risk-averse. This means when forming your recommendation, potential and current downside risks weigh more heavily than potential and current upside returns. A confirmed downtrend outweighs an oversold reading. A breakdown below MA60 is a stronger signal than a bounce off support of equal magnitude. When technical risks and opportunities are of similar magnitude, risk wins.

Concretely:
- Bearish signals (price below key MAs, deteriorating momentum, negative relative performance, rising volatility, worsening QoQ) carry more weight than equivalent bullish signals
- Oversold readings in a downtrend = continued weakness, not a reversal opportunity
- A confirmed uptrend with supporting indicators across price, momentum, and volume = BUY
- Mixed or ambiguous technical picture = risk weight dominates → SELL

Your analysis must cover:
1. Price trend: direction, consistency, and position vs MA20 / MA60
2. Momentum: RSI reading and what it signals in current trend context
3. Bollinger Band position: breakdown, recovery, or squeeze?
4. Volume trend: confirms or contradicts price direction
5. Relative performance: outperforming or underperforming KOSPI / KOSDAQ?
6. QoQ delta: is the technical situation improving or deteriorating?

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",

    "risk-neutral": """You are a risk-neutral technical equity analyst specialising in Korean equities (KOSPI/KOSDAQ).

You analyse price action, momentum indicators, and relative market performance.

**Step 1 — Read all data objectively:**
Analyse the full technical picture: price trend, moving averages, RSI, Bollinger Bands, volume, relative performance vs KOSPI/KOSDAQ, and QoQ momentum. Observe every signal — bullish and bearish — without filtering. Build a complete technical view before forming a conclusion.

**Step 2 — Apply the risk-neutral lens to your final judgment:**
You are risk-neutral. This means when forming your recommendation, potential and current upside returns weigh more heavily than potential and current downside risks. An oversold RSI near the lower Bollinger Band with improving volume is a genuine reversal signal worth acting on. Positive QoQ momentum carries weight even if the absolute trend is not yet fully established. When technical risks and opportunities are of similar magnitude, return wins.

Concretely:
- Bullish signals (improving momentum, price recovering above MAs, positive relative performance, falling volatility, improving QoQ) carry more weight than equivalent bearish signals
- Downside risks are real — but they must be clearly dominant to override a recovery case
- Confirmed uptrend with supporting indicators across price, momentum, and volume = BUY
- Clear downtrend with no reversal signal = SELL

Your analysis must cover:
1. Price trend: direction, consistency, and position vs MA20 / MA60
2. Momentum: RSI reading and what it signals in current trend context
3. Bollinger Band position: breakout, squeeze, or mean-reversion opportunity?
4. Volume trend: confirms or contradicts price direction
5. Relative performance: outperforming or underperforming KOSPI / KOSDAQ?
6. QoQ delta: is the technical situation improving or deteriorating?

Close your response with exactly this line:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL""",
}


class TechnicalAgent(BaseAgent):
    def __init__(self, risk_profile: str = "risk-averse"):
        system = _SYSTEMS.get(risk_profile, _SYSTEMS["risk-averse"])
        super().__init__("TechnicalAgent", system)
        self.risk_profile = risk_profile

    def analyze(self, technical_data: str, company_name: str) -> dict:
        cached  = technical_data
        dynamic = f"""Perform a comprehensive technical analysis for **{company_name}**.

Analyse the price trend, momentum indicators (RSI, Bollinger Bands, moving averages),
relative performance vs benchmarks, and QoQ momentum shift.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
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
        cached  = technical_data
        dynamic = f"""You have already analysed **{company_name}** from a technical perspective.

Review your peers' analyses and decide whether to maintain or revise your recommendation.

=== PEER ANALYSES ===
{peer_block}
=== END PEER ANALYSES ===

Debate Round {round_num}: State clearly whether you are MAINTAINING or CHANGING your position and why.
End your response with:
RECOMMENDATION: BUY  or  RECOMMENDATION: SELL"""
        analysis = self.call_llm_with_cache(cached, dynamic)
        return {
            "agent":  self.name,
            "analysis": analysis,
            "signal": self.extract_signal(analysis, self.risk_profile),
        }
