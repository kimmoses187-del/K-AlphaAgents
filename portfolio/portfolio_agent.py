from config import MAX_DEBATE_ROUNDS

# Conviction weighting by data connectedness to the firm.
# Direct agents (company-specific data) share 65% total weight equally.
# Indirect agents (sector/macro context) share 35% total weight equally.
#   Direct  → FundamentalAgent, SentimentAgent, TechnicalAgent : 0.65 / 3 ≈ 0.2167 each
#   Indirect → MacroAgent, MarketAgent                         : 0.35 / 2 = 0.1750 each
# Weights are normalised so they sum to exactly 1.0.
_raw = {
    "FundamentalAgent": 0.65 / 3,
    "SentimentAgent":   0.65 / 3,
    "TechnicalAgent":   0.65 / 3,
    "MacroAgent":       0.35 / 2,
    "MarketAgent":      0.35 / 2,
}
_total = sum(_raw.values())
AGENT_WEIGHTS = {k: v / _total for k, v in _raw.items()}

PROFILES = ["risk-averse", "risk-neutral"]


def compute_conviction(debate_result: dict) -> float:
    """
    Option B — Agent expertise weighting.

    conviction = (weighted_vote × 0.6) + (round_score × 0.4)

    weighted_vote : sum of AGENT_WEIGHTS for agents whose final signal
                    matches the portfolio's final_signal (range 0–1,
                    since AGENT_WEIGHTS sum to 1.0)
    round_score   : 1.0 at round 0 (instant consensus), decays to 0.0
                    at MAX_DEBATE_ROUNDS (grinding majority vote)
    """
    final_signal  = debate_result["final_signal"]
    rounds_taken  = debate_result["consensus_round"]
    final_results = debate_result["debate_log"][-1]["results"]

    vote_score = sum(
        AGENT_WEIGHTS.get(r["agent"], 0.20)
        for r in final_results if r["signal"] == final_signal
    )
    round_score = (
        1.0 - (rounds_taken / MAX_DEBATE_ROUNDS)
        if MAX_DEBATE_ROUNDS > 0 else 1.0
    )
    return round(vote_score * 0.6 + round_score * 0.4, 3)


def construct_portfolio(stock_debate_results: dict) -> dict:
    """
    Build conviction-weighted multi-stock portfolios for both risk profiles.

    Parameters
    ----------
    stock_debate_results : {stock_code: {"risk-averse": debate_result,
                                         "risk-neutral": debate_result}}

    Returns
    -------
    {
        "risk-averse": {
            "weights": {ticker: float},    # BUY stocks only; sum to 1.0
                                           # (empty dict if no BUY signals)
            "stock_allocations": {
                stock_code: {
                    "signal":     str,
                    "conviction": float,
                    "weight":     float,   # 0.0 for SELL stocks
                }
            },
            "position_taken": bool,        # True if at least one BUY stock
        },
        "risk-neutral": { ... }
    }

    Allocation logic
    ----------------
    1. Compute conviction for every stock under each profile.
    2. Select all BUY stocks (no conviction threshold).
    3. Distribute weight among BUY stocks proportional to conviction — summing to 1.0.
    4. If no stock qualifies → empty portfolio (position_taken = False).
    """
    portfolios = {}

    for profile in PROFILES:
        # Step 1 & 2: conviction + signal per stock
        convictions = {}
        signals     = {}
        for code, debate_results in stock_debate_results.items():
            dr                = debate_results[profile]
            convictions[code] = compute_conviction(dr)
            signals[code]     = dr["final_signal"]

        buy_stocks = {
            code: convictions[code]
            for code in convictions
            if signals[code] == "BUY"
        }

        # Step 3: conviction-proportional weights summing to 1.0
        if buy_stocks:
            total_conv    = sum(buy_stocks.values())
            stock_weights = {
                code: round(conv / total_conv, 6)
                for code, conv in buy_stocks.items()
            }
        else:
            stock_weights = {}

        # Per-stock summary (SELL stocks included at weight 0.0)
        stock_allocations = {
            code: {
                "signal":     signals[code],
                "conviction": convictions[code],
                "weight":     stock_weights.get(code, 0.0),
            }
            for code in stock_debate_results
        }

        portfolios[profile] = {
            "weights":           stock_weights,
            "stock_allocations": stock_allocations,
            "position_taken":    bool(stock_weights),
        }

    return portfolios
