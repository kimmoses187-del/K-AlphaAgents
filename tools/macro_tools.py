"""
tools/macro_tools.py
====================
Macro indicators for MacroAgent.

Gap 5 additions:
  • Bank of Korea (BOK) ECOS API — base rate, CPI, industrial production
  • Dynamic risk-free rate retrieval for use in backtest/engine.py
  • Graceful fallback: if BOK_API_KEY not set, skips BoK section cleanly

BoK ECOS API registration: https://ecos.bok.or.kr/api/#/
Set BOK_API_KEY in your .env to enable.
"""

import logging
import os
import requests
import yfinance as yf
from datetime import datetime, timedelta

from config import BOK_API_KEY  # may be None if not set

log = logging.getLogger(__name__)

# ── yfinance macro tickers ─────────────────────────────────────────────────────
MACRO_TICKERS: dict = {
    "USD/KRW":         "KRW=X",
    "KOSPI":           "^KS11",
    "KOSDAQ":          "^KQ11",
    "S&P 500":         "^GSPC",
    "NASDAQ":          "^IXIC",
    "US 10Y Treasury": "^TNX",
    "Gold (USD)":      "GC=F",
    "Crude Oil (WTI)": "CL=F",
}

# ── BoK ECOS API ───────────────────────────────────────────────────────────────
_BOK_BASE = "https://ecos.bok.or.kr/api/StatisticSearch"

# (stat_code, cycle, item_code_1, label, unit)
_BOK_SERIES = [
    ("722Y001", "M", "0101000", "BoK Base Rate",              "%"),
    ("901Y009", "M", "0000000", "Korean CPI (YoY)",           "%"),
    ("403Y003", "M", "AAAA",    "Industrial Production Index", "index"),
    ("817Y002", "M", "10103",   "Korean 3-Month CD Rate",     "%"),
]

_DEFAULT_RISK_FREE_RATE = 0.035  # fallback if BoK API unavailable


def _bok_fetch(stat_code: str, cycle: str, item_code: str,
               start_ym: str, end_ym: str) -> list[dict]:
    """
    Query one BoK ECOS series.  Returns list of {"date": "YYYYMM", "value": float}.
    Returns [] on any error.
    """
    if not BOK_API_KEY:
        return []
    url = (
        f"{_BOK_BASE}/{BOK_API_KEY}/json/kr/1/5/"
        f"{stat_code}/{cycle}/{start_ym}/{end_ym}/{item_code}"
    )
    try:
        r = requests.get(url, timeout=15)
        r.raise_for_status()
        body = r.json()
        rows = body.get("StatisticSearch", {}).get("row", [])
        result = []
        for row in rows:
            try:
                result.append({
                    "date":  row.get("TIME", ""),
                    "value": float(row.get("DATA_VALUE", 0)),
                })
            except (TypeError, ValueError):
                pass
        return result
    except Exception as exc:
        log.debug("BoK ECOS fetch error (%s): %s", stat_code, exc)
        return []


def fetch_bok_indicators(as_of_date: datetime, months: int = 3) -> dict:
    """
    Fetch Korean domestic macro indicators from BoK ECOS.

    Returns dict: {label: {"current": float, "prev": float, "change": float, "unit": str}}
    Returns {} if BOK_API_KEY not set or all calls fail.
    """
    if not BOK_API_KEY:
        return {}

    end_ym   = as_of_date.strftime("%Y%m")
    start_dt = as_of_date - timedelta(days=30 * months)
    start_ym = start_dt.strftime("%Y%m")

    results = {}
    for stat_code, cycle, item_code, label, unit in _BOK_SERIES:
        rows = _bok_fetch(stat_code, cycle, item_code, start_ym, end_ym)
        if not rows:
            continue
        vals = [r["value"] for r in rows if r["value"] is not None]
        if not vals:
            continue
        current = vals[-1]
        prev    = vals[0]
        results[label] = {
            "current": current,
            "prev":    prev,
            "change":  round(current - prev, 3),
            "unit":    unit,
        }

    return results


def get_risk_free_rate(as_of_date: datetime) -> float:
    """
    Return the Korean risk-free rate (91-day CD rate) for the given date.

    Tries BoK ECOS first (stat 817Y002 = 91-day CD rate).
    Falls back to _DEFAULT_RISK_FREE_RATE (3.5%) if API unavailable.
    """
    if not BOK_API_KEY:
        return _DEFAULT_RISK_FREE_RATE

    end_ym   = as_of_date.strftime("%Y%m")
    start_ym = (as_of_date - timedelta(days=90)).strftime("%Y%m")
    rows = _bok_fetch("817Y002", "M", "10103", start_ym, end_ym)
    if rows:
        rate_pct = rows[-1]["value"]   # already in % (e.g. 3.5)
        return rate_pct / 100.0
    return _DEFAULT_RISK_FREE_RATE


def fetch_macro_indicators(as_of_date: datetime, months: int = 3) -> dict:
    """
    Fetch all macro indicators: yfinance global + BoK domestic (Gap 5).
    Returns merged dict keyed by indicator name.
    """
    start_str = (as_of_date - timedelta(days=30 * months)).strftime("%Y-%m-%d")
    end_str   = (as_of_date + timedelta(days=1)).strftime("%Y-%m-%d")

    # ── yfinance: global indicators ───────────────────────────────────────
    results = {}
    for name, sym in MACRO_TICKERS.items():
        try:
            hist = yf.Ticker(sym).history(start=start_str, end=end_str)
            if hist.empty:
                continue
            current = float(hist["Close"].iloc[-1])
            start   = float(hist["Close"].iloc[0])
            ret     = round((current / start - 1) * 100, 2)
            results[name] = {
                "current":   round(current, 4),
                "3m_return": ret,
                "direction": "▲" if ret >= 0 else "▼",
                "source":    "yfinance",
            }
        except Exception:
            continue

    # ── BoK ECOS: Korean domestic indicators (Gap 5) ──────────────────────
    bok_data = fetch_bok_indicators(as_of_date, months)
    for label, data in bok_data.items():
        results[label] = {
            "current":   data["current"],
            "3m_return": data["change"],
            "direction": "▲" if data["change"] >= 0 else "▼",
            "unit":      data["unit"],
            "source":    "BoK ECOS",
        }

    return results


def format_macro_data_for_llm(macro_data: dict, sector: str) -> str:
    # Separate global vs BoK indicators for cleaner output
    yf_items  = {k: v for k, v in macro_data.items() if v.get("source") != "BoK ECOS"}
    bok_items = {k: v for k, v in macro_data.items() if v.get("source") == "BoK ECOS"}

    lines = [
        "## Macroeconomic Indicators (3-Month Window)",
        "",
        f"{'Indicator':<28} {'Current':>14} {'3M Change':>12}",
        "-" * 57,
    ]

    for name, data in yf_items.items():
        direction = data.get("direction", "")
        current   = data.get("current", "N/A")
        ret       = data.get("3m_return", "N/A")
        lines.append(f"{name:<28} {str(current):>14} {direction} {ret}%")

    # ── BoK section (only shown if API available) ─────────────────────────
    if bok_items:
        lines += ["", "## Korean Domestic Indicators (Bank of Korea, ECOS)", ""]
        lines += [
            f"{'Indicator':<28} {'Latest':>10} {'Change':>10}",
            "-" * 52,
        ]
        for name, data in bok_items.items():
            unit   = data.get("unit", "")
            curr   = f"{data['current']:.2f}{unit}"
            change = f"{data.get('3m_return', 0):+.3f}{unit}"
            lines.append(f"{name:<28} {curr:>10} {change:>10}")
        lines += [
            "",
            "BoK Base Rate context: Rate changes signal shifts in domestic",
            "credit conditions and KOSPI discount rate assumptions.",
            "Korean CPI: inflation trend affects consumer spending sectors.",
        ]
    else:
        lines += [
            "",
            "*(Bank of Korea data not available — set BOK_API_KEY in .env to enable)*",
        ]

    lines += [
        "",
        "## Key Macro Signals for Korean Equities",
        "",
        "USD/KRW: A rising KRW=X (weakening KRW) benefits Korean exporters but raises "
        "import costs. A falling USD/KRW (strengthening KRW) pressures export revenues.",
        "",
        "US 10Y Treasury: Higher US yields attract capital away from emerging markets "
        "including Korea, typically pressuring equity valuations.",
        "",
        "KOSPI vs S&P 500: Divergence between KOSPI and US indices often signals "
        "Korea-specific risk or opportunity beyond global trends.",
        "",
        f"## Sector Under Analysis: {sector}",
        "Consider how the above macro conditions specifically affect this sector's "
        "demand outlook, cost structure, and capital flows.",
    ]

    return "\n".join(lines)
