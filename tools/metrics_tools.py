"""
tools/metrics_tools.py
======================
Price metrics and technical indicators for TechnicalAgent.

Indicators computed
-------------------
  Moving Averages : 20-day and 60-day simple MA, % vs MA, consecutive days below MA20
  RSI             : 14-day Wilder's RSI (overbought >70, oversold <30)
  Bollinger Bands : 20-day ±2σ; %B position (0=lower band, 1=upper band); normalised width
  Relative Perf.  : stock alpha vs KOSPI and KOSDAQ over the same 3-month window
  QoQ Delta       : period return and annualised vol change vs the prior quarter
"""

import pandas as pd
import numpy as np
from typing import Optional

TRADING_DAYS = 252


# ── Core return / vol ─────────────────────────────────────────────────────────

def calculate_annualized_return(price_history: pd.DataFrame) -> float:
    """Annualized cumulative return: (1 + R_cum)^(252/n) - 1"""
    prices = price_history["Close"].dropna()
    if len(prices) < 2:
        return 0.0
    r_cum = (prices.iloc[-1] / prices.iloc[0]) - 1
    return float((1 + r_cum) ** (TRADING_DAYS / len(prices)) - 1)


def calculate_annualized_volatility(price_history: pd.DataFrame) -> float:
    """Annualized daily-return std: sigma_daily * sqrt(252)"""
    prices = price_history["Close"].dropna()
    if len(prices) < 2:
        return 0.0
    return float(prices.pct_change().dropna().std() * np.sqrt(TRADING_DAYS))


# ── Technical indicators ──────────────────────────────────────────────────────

def _rsi(prices: pd.Series, period: int = 14) -> Optional[float]:
    """Wilder's RSI.  Returns None if fewer than period+1 data points."""
    if len(prices) < period + 1:
        return None
    delta = prices.diff().dropna()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_g = gain.iloc[:period].mean()
    avg_l = loss.iloc[:period].mean()
    for i in range(period, len(gain)):
        avg_g = (avg_g * (period - 1) + gain.iloc[i]) / period
        avg_l = (avg_l * (period - 1) + loss.iloc[i]) / period
    if avg_l == 0:
        return 100.0
    return float(100 - 100 / (1 + avg_g / avg_l))


def _bollinger(prices: pd.Series, period: int = 20) -> Optional[dict]:
    """
    20-day Bollinger Bands ±2σ.
    Returns None if fewer than `period` data points.
    %B = (price - lower) / (upper - lower): 0 = at lower band, 1 = at upper band.
    """
    if len(prices) < period:
        return None
    ma     = prices.rolling(period).mean()
    std    = prices.rolling(period).std()
    upper  = float((ma + 2 * std).iloc[-1])
    lower  = float((ma - 2 * std).iloc[-1])
    middle = float(ma.iloc[-1])
    current = float(prices.iloc[-1])
    pct_b   = (current - lower) / (upper - lower) if upper != lower else 0.5
    return {
        "bb_upper":  round(upper, 2),
        "bb_middle": round(middle, 2),
        "bb_lower":  round(lower, 2),
        "bb_pct_b":  round(pct_b, 3),
        "bb_width":  round((upper - lower) / middle, 4),  # normalised width
    }


def _moving_averages(prices: pd.Series) -> dict:
    """20d and 60d SMA; % deviation from current price; consecutive days below MA20."""
    current = float(prices.iloc[-1])
    result  = {}
    for period, key in [(20, "ma20"), (60, "ma60")]:
        if len(prices) >= period:
            ma_val = float(prices.rolling(period).mean().iloc[-1])
            result[key]             = round(ma_val, 2)
            result[f"pct_vs_{key}"] = round((current - ma_val) / ma_val * 100, 2)
    # Consecutive trading days the stock has closed below MA20
    if "ma20" in result:
        ma20_series = prices.rolling(20).mean()
        below       = prices < ma20_series
        consecutive = 0
        for v in reversed(below.values):
            if v:
                consecutive += 1
            else:
                break
        result["days_below_ma20"] = consecutive
    return result


# ── Main calculator ───────────────────────────────────────────────────────────

def calculate_price_metrics(
    price_history: pd.DataFrame,
    prev_quarter: Optional[pd.DataFrame] = None,
    kospi_history: Optional[pd.DataFrame] = None,
    kosdaq_history: Optional[pd.DataFrame] = None,
) -> dict:
    """
    Compute all price and technical metrics for TechnicalAgent.

    Parameters
    ----------
    price_history  : current-quarter OHLCV (3M ending as_of_date)  — pykrx
    prev_quarter   : previous-quarter OHLCV (3M ending 3M before as_of_date) — for QoQ delta
    kospi_history  : KOSPI OHLCV for the same window — for relative performance
    kosdaq_history : KOSDAQ OHLCV for the same window — for relative performance
    """
    if price_history.empty:
        return {}

    prices  = price_history["Close"].dropna()
    volumes = price_history["Volume"].dropna()

    ann_return = calculate_annualized_return(price_history)
    ann_vol    = calculate_annualized_volatility(price_history)

    metrics: dict = {
        # ── Price basics ──────────────────────────────────────────────────
        "current_price":         round(float(prices.iloc[-1]), 2),
        "start_price":           round(float(prices.iloc[0]),  2),
        "period_return_pct":     round(((prices.iloc[-1] / prices.iloc[0]) - 1) * 100, 2),
        "annualized_return":     round(ann_return, 4),
        "annualized_volatility": round(ann_vol, 4),
        "avg_daily_volume":      round(float(volumes.mean()), 0),
        "price_high":            round(float(prices.max()), 2),
        "price_low":             round(float(prices.min()), 2),
        "num_trading_days":      len(prices),
    }

    # ── Moving averages ───────────────────────────────────────────────────
    metrics.update(_moving_averages(prices))

    # ── RSI ───────────────────────────────────────────────────────────────
    rsi_val = _rsi(prices)
    if rsi_val is not None:
        metrics["rsi"] = round(rsi_val, 1)

    # ── Bollinger Bands ───────────────────────────────────────────────────
    bb = _bollinger(prices)
    if bb:
        metrics.update(bb)

    # ── Relative performance vs KOSPI / KOSDAQ ────────────────────────────
    stock_ret = (float(prices.iloc[-1]) / float(prices.iloc[0])) - 1
    for label, hist in [("kospi", kospi_history), ("kosdaq", kosdaq_history)]:
        if hist is not None and not hist.empty:
            idx_prices = hist["Close"].dropna()
            if len(idx_prices) >= 2:
                idx_ret = float((idx_prices.iloc[-1] / idx_prices.iloc[0]) - 1)
                metrics[f"{label}_period_return"] = round(idx_ret * 100, 2)
                metrics[f"alpha_vs_{label}"]      = round((stock_ret - idx_ret) * 100, 2)

    # ── QoQ delta ─────────────────────────────────────────────────────────
    if prev_quarter is not None and not prev_quarter.empty:
        prev_ret    = calculate_annualized_return(prev_quarter)
        prev_vol    = calculate_annualized_volatility(prev_quarter)
        prev_prices = prev_quarter["Close"].dropna()
        prev_period = float((prev_prices.iloc[-1] / prev_prices.iloc[0] - 1) * 100)

        metrics["prev_period_return_pct"]     = round(prev_period, 2)
        metrics["prev_annualized_return"]     = round(prev_ret, 4)
        metrics["prev_annualized_volatility"] = round(prev_vol, 4)
        metrics["return_qoq_delta"]           = round(metrics["period_return_pct"] - prev_period, 2)
        metrics["vol_qoq_delta"]              = round((ann_vol - prev_vol) * 100, 2)

    return metrics


# ── Formatter ─────────────────────────────────────────────────────────────────

def format_metrics_for_llm(metrics: dict, stock_code: str) -> str:
    """Format all technical metrics into structured text for TechnicalAgent."""
    if not metrics:
        return "Price and volume data not available."

    def pct(val, decimals=2):
        return "N/A" if val is None else f"{val:.{decimals}f}%"

    def price(val):
        return "N/A" if val is None else f"{val:,.2f} KRW"

    lines = [f"## Technical Analysis Data  ({stock_code}, 3-month window)", ""]

    # ── Section 1: Price & Return ─────────────────────────────────────────
    lines += [
        "### 1. Price & Return",
        f"Current Price:           {price(metrics.get('current_price'))}",
        f"Quarter Start Price:     {price(metrics.get('start_price'))}",
        f"Period Return:           {pct(metrics.get('period_return_pct'))}",
        f"Annualized Return:       {pct(metrics.get('annualized_return', 0) * 100)}",
        f"Annualized Volatility:   {pct(metrics.get('annualized_volatility', 0) * 100)}",
        f"3M High:                 {price(metrics.get('price_high'))}",
        f"3M Low:                  {price(metrics.get('price_low'))}",
        f"Avg Daily Volume:        {metrics.get('avg_daily_volume', 0):>15,.0f} shares",
        f"Trading Days:            {metrics.get('num_trading_days', 'N/A')}",
        "",
    ]

    # ── Section 2: Technical Indicators ──────────────────────────────────
    lines.append("### 2. Technical Indicators")

    # Moving averages
    if "ma20" in metrics:
        dir20 = "above" if metrics.get("pct_vs_ma20", 0) >= 0 else "below"
        lines.append(f"20-day MA:               {price(metrics.get('ma20'))}  "
                     f"({pct(metrics.get('pct_vs_ma20'))} {dir20})")
    if "ma60" in metrics:
        dir60 = "above" if metrics.get("pct_vs_ma60", 0) >= 0 else "below"
        lines.append(f"60-day MA:               {price(metrics.get('ma60'))}  "
                     f"({pct(metrics.get('pct_vs_ma60'))} {dir60})")
    if "days_below_ma20" in metrics:
        lines.append(f"Consecutive days < MA20: {metrics['days_below_ma20']} days")

    # RSI
    if "rsi" in metrics:
        rsi_val  = metrics["rsi"]
        rsi_zone = ("Overbought (>70)" if rsi_val > 70
                    else "Oversold (<30)" if rsi_val < 30
                    else "Neutral (30–70)")
        lines.append(f"RSI (14-day):            {rsi_val:.1f}  [{rsi_zone}]")

    # Bollinger Bands
    if "bb_upper" in metrics:
        pct_b    = metrics.get("bb_pct_b", 0.5)
        bb_zone  = ("Near upper band (>0.8)" if pct_b > 0.8
                    else "Near lower band (<0.2)" if pct_b < 0.2
                    else "Within bands")
        lines += [
            f"Bollinger Upper (20d):   {price(metrics.get('bb_upper'))}",
            f"Bollinger Middle:        {price(metrics.get('bb_middle'))}",
            f"Bollinger Lower:         {price(metrics.get('bb_lower'))}",
            f"BB %B:                   {pct_b:.2f}  [{bb_zone}]  (0=lower, 0.5=mid, 1=upper)",
            f"Band Width (norm.):      {metrics.get('bb_width', 0):.4f}",
        ]

    lines.append("")

    # ── Section 3: Relative Performance ──────────────────────────────────
    has_rel = any(k in metrics for k in ("kospi_period_return", "kosdaq_period_return"))
    if has_rel:
        lines.append("### 3. Relative Performance (same 3M window)")
        lines.append(f"Stock Period Return:      {pct(metrics.get('period_return_pct'))}")
        if "kospi_period_return" in metrics:
            alpha = metrics["alpha_vs_kospi"]
            lines.append(f"KOSPI Period Return:     {pct(metrics.get('kospi_period_return'))}")
            lines.append(f"Alpha vs KOSPI:          {pct(alpha)}  "
                         f"({'outperformed' if alpha >= 0 else 'underperformed'})")
        if "kosdaq_period_return" in metrics:
            alpha = metrics["alpha_vs_kosdaq"]
            lines.append(f"KOSDAQ Period Return:    {pct(metrics.get('kosdaq_period_return'))}")
            lines.append(f"Alpha vs KOSDAQ:         {pct(alpha)}  "
                         f"({'outperformed' if alpha >= 0 else 'underperformed'})")
        lines.append("")

    # ── Section 4: QoQ Delta ──────────────────────────────────────────────
    if "prev_period_return_pct" in metrics:
        delta_r = metrics.get("return_qoq_delta", 0)
        delta_v = metrics.get("vol_qoq_delta", 0)
        prev_vol_pct = metrics.get("prev_annualized_volatility", 0) * 100
        curr_vol_pct = metrics.get("annualized_volatility", 0) * 100
        lines += [
            "### 4. Quarter-over-Quarter Delta",
            f"Previous Quarter Return: {pct(metrics.get('prev_period_return_pct'))}",
            f"Current Quarter Return:  {pct(metrics.get('period_return_pct'))}",
            f"Return Change (QoQ):     {pct(delta_r)}  "
            f"({'improving' if delta_r >= 0 else 'deteriorating'})",
            f"Previous Quarter Vol:    {pct(prev_vol_pct)}",
            f"Current Quarter Vol:     {pct(curr_vol_pct)}",
            f"Volatility Change (QoQ): {pct(delta_v)}  "
            f"({'rising risk' if delta_v > 0 else 'falling risk'})",
            "",
        ]

    return "\n".join(lines)
