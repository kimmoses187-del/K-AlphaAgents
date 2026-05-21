from config import MAX_DEBATE_ROUNDS

# KODEX 국고채3년 — 3-Year Korean Government Bond ETF (KRX: 114260)
BOND_TICKER = "114260"

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

PROFILE_CONFIG = {
    "risk-averse": {
        "equity_weight": 0.60,   # 60% stocks / 40% bond
        "bond_weight":   0.40,
        "stop_loss":    -0.05,
        "take_profit":   0.10,
    },
    "risk-neutral": {
        "equity_weight": 0.80,   # 80% stocks / 20% bond
        "bond_weight":   0.20,
        "stop_loss":    -0.10,
        "take_profit":   0.20,
    },
}


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
            "weights": {ticker: float},        # all weights sum to 1.0
            "stock_allocations": {             # per-stock info for reporting
                stock_code: {
                    "signal":     str,
                    "conviction": float,
                    "weight":     float,       # 0.0 for SELL / low-conviction
                }
            },
            "equity_weight":  float,
            "bond_weight":    float,
            "position_taken": bool,
            "stop_loss":      float,
            "take_profit":    float,
        },
        "risk-neutral": { ... }
    }

    Allocation logic
    ----------------
    1. Compute conviction for every stock in each profile.
    2. Select all BUY stocks (no conviction threshold).
    3. Distribute the profile's equity_weight among qualifying stocks
       proportional to their conviction scores.
    4. Remaining weight goes to the Korean bond ETF (114260).
    5. If no stock qualifies → 100% bond (capital preservation).
    """
    portfolios = {}

    for profile in ["risk-averse", "risk-neutral"]:
        cfg = PROFILE_CONFIG[profile]

        # Step 1 & 2: conviction + signal per stock
        convictions = {}
        signals     = {}
        for code, debate_results in stock_debate_results.items():
            dr              = debate_results[profile]
            convictions[code] = compute_conviction(dr)
            signals[code]     = dr["final_signal"]

        buy_stocks = {
            code: convictions[code]
            for code in convictions
            if signals[code] == "BUY"
        }

        # Step 3: conviction-proportional weights within equity bucket
        if buy_stocks:
            total_conv = sum(buy_stocks.values())
            equity_w   = cfg["equity_weight"]
            bond_w     = cfg["bond_weight"]
            stock_weights = {
                code: round(equity_w * (conv / total_conv), 6)
                for code, conv in buy_stocks.items()
            }
        else:
            equity_w      = 0.0
            bond_w        = 1.0
            stock_weights = {}

        # Step 4: full weight dict for BacktestEngine
        weights = {**stock_weights, BOND_TICKER: bond_w}

        # Per-stock summary (includes SELL / low-conviction stocks at 0 weight)
        stock_allocations = {
            code: {
                "signal":     signals[code],
                "conviction": convictions[code],
                "weight":     stock_weights.get(code, 0.0),
            }
            for code in stock_debate_results
        }

        portfolios[profile] = {
            "weights":           weights,
            "stock_allocations": stock_allocations,
            "equity_weight":     equity_w,
            "bond_weight":       bond_w,
            "position_taken":    equity_w > 0,
            "stop_loss":         cfg["stop_loss"],
            "take_profit":       cfg["take_profit"],
        }

    return portfolios


class PortfolioAgent:
    """Single-stock portfolio helper (kept for standalone use)."""

    def __init__(self, risk_profile: str):
        if risk_profile not in PROFILE_CONFIG:
            raise ValueError(f"Unknown risk profile: {risk_profile}")
        self.risk_profile = risk_profile
        self.config = PROFILE_CONFIG[risk_profile]

    def construct(self, debate_result: dict, stock_code: str) -> dict:
        result = construct_portfolio({stock_code: {
            self.risk_profile: debate_result,
            # provide dummy for the other profile so construct_portfolio works
            next(p for p in PROFILE_CONFIG if p != self.risk_profile): debate_result,
        }})
        return result[self.risk_profile]
