"""
rebalance/weight_adjuster.py
============================
Adjusts portfolio weights without LLM calls using momentum scores.

Redistributes the full portfolio weight (1.0) among currently held BUY stocks
proportional to their momentum scores. If all scores are zero, the portfolio
moves to a flat (no-position) state until the next quarterly rebalance.
"""

from typing import Dict


def adjust_weights(
    current_portfolio: dict,
    momentum_scores: Dict[str, float],
) -> Dict[str, float]:
    """
    Redistribute portfolio weight among currently held BUY stocks
    proportional to their momentum scores.

    Rules
    -----
    - Stocks with score = 0 are zeroed out until the next quarterly rebalance
    - If all scores = 0 → all weights set to 0 (no position — full cash)
    - Remaining stocks share 100% weight proportional to their momentum score

    Parameters
    ----------
    current_portfolio : portfolio dict from construct_portfolio()
    momentum_scores   : {ticker: normalised_score} from compute_momentum_scores()

    Returns
    -------
    {ticker: weight} — new stock-only allocation; BUY stocks sum to 1.0
                       (or all zeros if no positive momentum)
    """
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
        # No positive momentum — zero all held positions (full cash)
        return {code: 0.0 for code in held}

    total_score = sum(positive.values())
    new_weights = {
        code: round(score / total_score, 6)
        for code, score in positive.items()
    }

    # Zero out stocks that lost their momentum score
    for code in held:
        if code not in new_weights:
            new_weights[code] = 0.0

    return new_weights
