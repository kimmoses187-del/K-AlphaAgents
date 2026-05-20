"""
rebalance/event_monitor.py
==========================
Monitors intra-quarter price data and detects rebalancing triggers.

Three trigger conditions (all configurable via TRIGGER_CONFIG):
  1. PRICE_DROP  — stock fell > PRICE_DROP_PCT from quarter entry price
  2. VOL_SPIKE   — 20-day annualised volatility exceeds VOL_SPIKE_PCT
  3. MOM_FLIP    — price closes below 20-day MA for CONFIRM_DAYS consecutive days

No LLM calls — pure price mechanics.
"""

import numpy as np
import pandas as pd
from datetime import datetime
from typing import Dict, List, Optional

TRADING_DAYS = 252

TRIGGER_CONFIG = {
    "price_drop_pct":      -0.08,   # -8% from quarter entry price
    "vol_spike_pct":        0.40,   # 40% annualised vol (20-day window)
    "momentum_window":       20,    # days for MA and vol calculation
    "momentum_confirm_days":  3,    # consecutive days below MA to confirm flip
    "cooldown_days":          5,    # min trading days between triggers per ticker
}


def check_triggers(
    holdings: Dict[str, float],
    prices: pd.DataFrame,
    quarter_entry_prices: Dict[str, float],
    last_trigger_dates: Dict[str, Optional[pd.Timestamp]],
    as_of: pd.Timestamp,
) -> List[str]:
    """
    Check all held stocks against trigger conditions for a specific date.

    Parameters
    ----------
    holdings             : {ticker: current_weight}  — only held stocks checked
    prices               : daily close prices (index=date, cols=tickers)
    quarter_entry_prices : {ticker: price at quarter start}
    last_trigger_dates   : {ticker: last trigger timestamp or None}
    as_of                : date being evaluated (inclusive upper bound)

    Returns
    -------
    List of triggered ticker codes.
    """
    cfg      = TRIGGER_CONFIG
    window   = cfg["momentum_window"]
    confirm  = cfg["momentum_confirm_days"]
    cooldown = cfg["cooldown_days"]

    hist = prices[prices.index <= as_of]
    if len(hist) < window:
        return []

    triggered = []

    for ticker in holdings:
        if ticker not in prices.columns:
            continue

        # ── Cooldown check ────────────────────────────────────────────────
        last = last_trigger_dates.get(ticker)
        if last is not None:
            days_since = len(prices[(prices.index > last) & (prices.index <= as_of)])
            if days_since < cooldown:
                continue

        series = hist[ticker].dropna()
        if len(series) < window:
            continue

        current_price = series.iloc[-1]
        entry_price   = quarter_entry_prices.get(ticker, current_price)

        # ── Trigger 1: Price drop from quarter entry ──────────────────────
        price_chg = (current_price - entry_price) / entry_price
        if price_chg <= cfg["price_drop_pct"]:
            triggered.append(ticker)
            continue

        # ── Trigger 2: Volatility spike ───────────────────────────────────
        recent      = series.iloc[-window:]
        daily_rets  = recent.pct_change().dropna()
        if len(daily_rets) >= window - 1:
            ann_vol = daily_rets.std() * np.sqrt(TRADING_DAYS)
            if ann_vol >= cfg["vol_spike_pct"]:
                triggered.append(ticker)
                continue

        # ── Trigger 3: Momentum flip (N consecutive closes below 20d MA) ──
        ma = series.rolling(window).mean()
        if len(ma.dropna()) >= confirm:
            below_ma = series.iloc[-confirm:] < ma.iloc[-confirm:]
            if below_ma.all():
                triggered.append(ticker)

    return triggered


def compute_momentum_scores(
    holdings: Dict[str, float],
    prices: pd.DataFrame,
    as_of: pd.Timestamp,
    window: int = 20,
) -> Dict[str, float]:
    """
    Compute a momentum-based conviction proxy for each held stock.

    Score = 20-day return / (20-day annualised vol + ε)
    Floored at 0 — negative momentum → zero weight candidate.
    Returns normalised scores summing to 1.0.

    Parameters
    ----------
    holdings : {ticker: weight}  — current held stocks
    prices   : daily close prices DataFrame
    as_of    : evaluate up to this date (inclusive)
    window   : lookback period in trading days
    """
    hist   = prices[prices.index <= as_of]
    scores = {}

    for ticker in holdings:
        if ticker not in prices.columns:
            scores[ticker] = 0.0
            continue

        series = hist[ticker].dropna()
        if len(series) < window + 1:
            scores[ticker] = 0.0
            continue

        recent       = series.iloc[-window:]
        daily_rets   = recent.pct_change().dropna()
        period_ret   = series.iloc[-1] / series.iloc[-window] - 1
        ann_vol      = daily_rets.std() * np.sqrt(TRADING_DAYS) + 1e-6
        scores[ticker] = max(0.0, period_ret / ann_vol)

    total = sum(scores.values())
    if total == 0:
        # All momentum non-positive → equal weight fallback
        n = len(holdings)
        return {t: 1.0 / n for t in holdings} if n > 0 else {}

    return {t: s / total for t, s in scores.items()}
