from config import MAX_DEBATE_ROUNDS, ALL_PROFILES

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

PROFILES = list(ALL_PROFILES)


def compute_conviction(debate_result: dict) -> float:
    """
    Option B — Agent expertise weighting.

    conviction = (weighted_vote × 0.6) + (round_score × 0.4)

    weighted_vote : share of AGENT_WEIGHTS held by agents whose final signal
                    matches the portfolio's final_signal (range 0–1). The
                    weights are re-normalised over the agents that actually
                    debated, so an odd-sized subset (1/3/5 agents) still yields
                    a full 1.0 when its agents agree.
    round_score   : 1.0 at round 0 (instant consensus), decays to 0.0
                    at MAX_DEBATE_ROUNDS (grinding majority vote)
    """
    final_signal  = debate_result["final_signal"]
    rounds_taken  = debate_result["consensus_round"]
    final_results = debate_result["debate_log"][-1]["results"]

    # Re-normalise over the agents present (full set sums to 1.0 → no change;
    # a subset sums to <1.0 → scale up so agreement still maxes at 1.0).
    present_total = sum(AGENT_WEIGHTS.get(r["agent"], 0.20) for r in final_results)
    matched       = sum(
        AGENT_WEIGHTS.get(r["agent"], 0.20)
        for r in final_results if r["signal"] == final_signal
    )
    vote_score = matched / present_total if present_total > 0 else 0.0
    round_score = (
        1.0 - (rounds_taken / MAX_DEBATE_ROUNDS)
        if MAX_DEBATE_ROUNDS > 0 else 1.0
    )
    return round(vote_score * 0.6 + round_score * 0.4, 3)


def construct_portfolio(stock_debate_results: dict, profiles=None) -> dict:
    """
    Build conviction-weighted multi-stock portfolios, one per risk profile.

    Parameters
    ----------
    stock_debate_results : {stock_code: {profile: debate_result, ...}}
    profiles             : which profiles to build. Defaults to whichever
                           profiles are present in stock_debate_results, so a
                           single-profile analysis yields a single portfolio.

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
    if profiles is None:
        # Derive from the data: the profiles actually present in the first stock.
        first = next(iter(stock_debate_results.values()), {})
        profiles = list(first.keys()) or list(ALL_PROFILES)

    portfolios = {}

    for profile in profiles:
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
