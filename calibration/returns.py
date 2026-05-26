"""
calibration/returns.py
======================
Fetch actual holding-period returns for a list of KRX stock codes.

Uses pykrx (same source as the backtest engine) — no new data dependency.
Returns percentage returns as floats (e.g. -31.09 means -31.09%).

Public API
----------
from calibration.returns import fetch_holding_returns

returns = fetch_holding_returns(
    stock_codes = ["086900", "214150"],
    start_date  = "2025-06-01",   # first trading day on or after this date
    end_date    = "2025-09-01",   # last trading day on or before this date
)
# → {"086900": -31.09, "214150": -11.69}
# Missing / failed tickers are omitted from the result dict.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Dict, List

log = logging.getLogger(__name__)


def fetch_holding_returns(
    stock_codes: List[str],
    start_date: str,      # "YYYY-MM-DD"
    end_date: str,        # "YYYY-MM-DD"
) -> Dict[str, float]:
    """
    Return the percentage price return for each stock over [start_date, end_date].

    The return is computed as:
        (closing price on last available trading day in range  /
         closing price on first available trading day in range) - 1  × 100

    Parameters
    ----------
    stock_codes : list of 6-digit KRX codes
    start_date  : "YYYY-MM-DD" — holding period start
    end_date    : "YYYY-MM-DD" — holding period end

    Returns
    -------
    dict mapping stock_code → float (percentage return)
    Stocks that fail to fetch or have no data in range are omitted.
    """
    if not stock_codes:
        return {}

    # Validate that the holding period has ended
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    if end_dt > date.today():
        log.info(
            "Holding period end %s is in the future — skipping return fetch.", end_date
        )
        return {}

    try:
        from pykrx import stock as krx
    except ImportError:
        log.warning("pykrx not available — cannot fetch holding returns.")
        return {}

    start_str = start_date.replace("-", "")
    end_str   = end_date.replace("-", "")

    results: Dict[str, float] = {}

    for code in stock_codes:
        try:
            df = krx.get_market_ohlcv_by_date(start_str, end_str, code)
            if df is None or df.empty:
                log.warning("No price data for %s in %s→%s", code, start_date, end_date)
                continue

            close = df["종가"].dropna()
            if len(close) < 2:
                log.warning(
                    "Insufficient price data for %s (%d rows)", code, len(close)
                )
                continue

            start_price = float(close.iloc[0])
            end_price   = float(close.iloc[-1])

            if start_price <= 0:
                log.warning("Invalid start price %.2f for %s", start_price, code)
                continue

            ret = (end_price / start_price - 1.0) * 100.0
            results[code] = round(ret, 4)

        except Exception as exc:
            log.warning("Failed to fetch return for %s: %s", code, exc)

    return results
