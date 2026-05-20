"""
rebalance/weight_adjuster.py
============================
Adjusts portfolio weights without LLM calls using momentum scores.

Only re-distributes the equity bucket — bond weight absorbs any freed-up equity
when all momentum scores are zero (full defensive tilt).
"""

from typing import Dict
from portfolio.portfolio_agent import BOND_TICKER


def adjust_weights(
    current_portfolio: dict,
    momentum_scores: Dict[str, float],
) -> Dict[str, float]:
    """
    Redistribute equity budget among currently held BUY stocks
    proportional to their momentum scores.

    Rules
    -----
    - Bond weight is recomputed as 1 - actual_equity (may increase if stocks are dropped)
    - Stocks with score = 0 are zeroed out until the next quarterly rebalance
    - If all scores = 0 → 100% bond (full defensive)

    Parameters
    ----------
    current_portfolio : portfolio dict from construct_portfolio()
    momentum_scores   : {ticker: normalised_score} from compute_momentum_scores()

    Returns
    -------
    {ticker: weight} — new full allocation summing to 1.0
    """
    equity_budget = current_portfolio["equity_weight"]

    # Currently held stocks (weight > 0 from last quarterly rebalance)
    held = {
        code: alloc
        for code, alloc in current_portfolio["stock_allocations"].items()
        if alloc["weight"] > 0
    }

    # Filter to stocks with positive momentum
    positive = {
        t: momentum_scores.get(t, 0.0)
        for t in held
        if momentum_scores.get(t, 0.0) > 0
    }

    if not positive:
        # Full defensive — move everything to bond
        new_weights = {BOND_TICKER: 1.0}
        for code in held:
            new_weights[code] = 0.0
        return new_weights

    total_score = sum(positive.values())
    new_stock_weights = {
        code: round(equity_budget * (score / total_score), 6)
        for code, score in positive.items()
    }

    # Zero out stocks that lost their momentum score
    for code in held:
        if code not in new_stock_weights:
            new_stock_weights[code] = 0.0

    actual_equity  = sum(new_stock_weights.values())
    adjusted_bond  = round(1.0 - actual_equity, 6)

    return {**new_stock_weights, BOND_TICKER: adjusted_bond}
