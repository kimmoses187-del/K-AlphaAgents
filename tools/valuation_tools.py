"""
tools/valuation_tools.py
========================
Gap 2 — Quantitative valuation inputs for FundamentalAgent.

Builds two structured summaries from already-fetched DART data:

  DCF Summary
  -----------
  • 3-year revenue / FCF trend → projected growth rate
  • Conservative terminal value at WACC 10%, terminal growth 2%
  • Implied fair-value range (bear / base / bull)
  • Confidence flag: high (stable revenues) / medium / low (early-stage / loss-making)

  Peer Comps Summary
  ------------------
  • P/E and P/B of the 3 sector peers already fetched by MarketAgent
  • Stock's own trailing P/E and P/B (from yfinance if available)
  • Premium / discount vs peer median

These blocks are appended to fundamental_data so FundamentalAgent
has an explicit valuation anchor before forming its recommendation.

Public API
----------
from tools.valuation_tools import build_valuation_context

block = build_valuation_context(
    fs_years   = [fs_2022, fs_2023, fs_2024],  # DART fnlttSinglAcnt dicts
    peers      = [...],                          # from market_tools.get_peer_comparison()
    ticker_str = "214150.KQ",
    company_name = "클래시스",
)
"""

from __future__ import annotations

import logging
from typing import List, Dict, Optional

log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
_WACC         = 0.10   # 10% — conservative for Korean mid-cap equities
_TERMINAL_G   = 0.02   # 2% terminal growth rate
_BEAR_MULT    = 0.80   # bear: -20% to base
_BULL_MULT    = 1.25   # bull: +25% to base


# ── DART financial statement value extractor ──────────────────────────────────

_ACCOUNT_PATTERNS = {
    "revenue":    ["매출액", "매출", "영업수익", "수익(매출액)"],
    "op_income":  ["영업이익", "영업손익"],
    "net_income": ["당기순이익", "당기순손익", "순이익"],
    "fcf_proxy":  ["영업활동으로인한현금흐름", "영업활동현금흐름", "영업활동으로 인한 현금흐름"],
    "capex":      ["유형자산취득", "설비투자", "자본적지출"],
    "total_assets": ["자산총계"],
    "total_debt": ["부채총계", "총부채"],
    "equity":     ["자본총계", "총자본"],
}

def _extract_value(fs_items: List[Dict], account_patterns: List[str]) -> Optional[float]:
    """Find the first matching account and return its current-period value."""
    for item in fs_items:
        nm = item.get("account_nm", "").replace(" ", "")
        for pat in account_patterns:
            if pat.replace(" ", "") in nm:
                raw = (item.get("thstrm_amount") or "").replace(",", "").replace(" ", "")
                try:
                    return float(raw)
                except (ValueError, TypeError):
                    pass
    return None


def _get_fs_metrics(fs_data: Dict) -> Dict:
    """Extract key metrics from one DART fs response dict."""
    items = fs_data.get("list", [])
    if not items:
        return {}
    result = {}
    for key, patterns in _ACCOUNT_PATTERNS.items():
        val = _extract_value(items, patterns)
        if val is not None:
            result[key] = val
    return result


# ── DCF calculation ───────────────────────────────────────────────────────────

def _cagr(start: float, end: float, years: int) -> Optional[float]:
    if start <= 0 or end <= 0 or years <= 0:
        return None
    try:
        return (end / start) ** (1 / years) - 1
    except Exception:
        return None


def _build_dcf_summary(fs_list: List[Dict], company_name: str) -> str:
    """
    Compute a simple 5-year DCF from up to 3 years of DART annual data.
    Returns a formatted markdown block.
    """
    # Extract metrics per year (oldest first)
    year_metrics: List[Dict] = []
    for fs in fs_list:
        m = _get_fs_metrics(fs)
        if m:
            year_metrics.append(m)

    if len(year_metrics) < 2:
        return ""   # not enough data for trend

    # Revenue trend
    revenues = [m["revenue"] for m in year_metrics if "revenue" in m]
    if len(revenues) < 2:
        return ""

    rev_cagr = _cagr(revenues[0], revenues[-1], len(revenues) - 1)
    if rev_cagr is None:
        return ""

    # Clamp growth assumption: use half of historical CAGR, min 0%, max 30%
    projected_g = max(0.0, min(0.30, rev_cagr * 0.5))

    # Operating margin from most recent year
    op_margins = []
    for m in year_metrics:
        if "revenue" in m and "op_income" in m and m["revenue"] != 0:
            op_margins.append(m["op_income"] / m["revenue"])
    if not op_margins:
        return ""
    avg_margin = sum(op_margins) / len(op_margins)
    # Confidence flag
    if avg_margin > 0.05 and rev_cagr > 0:
        confidence = "HIGH"
        conf_note  = "stable positive margin + revenue growth"
    elif avg_margin > 0:
        confidence = "MEDIUM"
        conf_note  = "positive but thin margins or volatile revenue"
    else:
        confidence = "LOW"
        conf_note  = "negative margins — DCF estimates unreliable; treat as indicative only"

    # FCF proxy: operating CF - capex (or op_income * (1 - tax_rate) if CF not available)
    latest = year_metrics[-1]
    if "fcf_proxy" in latest and "capex" in latest:
        base_fcf = latest["fcf_proxy"] - abs(latest.get("capex", 0))
    elif "fcf_proxy" in latest:
        base_fcf = latest["fcf_proxy"]
    elif "op_income" in latest:
        base_fcf = latest["op_income"] * 0.75  # rough after-tax proxy
    else:
        return ""

    if base_fcf <= 0:
        # Can't do a meaningful DCF on negative FCF — still report the trend
        lines = [
            "## Quantitative Valuation Context",
            f"**DCF Confidence: {confidence}** — {conf_note}",
            "",
            f"Historical Revenue CAGR ({len(revenues)-1}Y): {rev_cagr*100:+.1f}%",
            f"Average Operating Margin: {avg_margin*100:.1f}%",
            "",
            "⚠  Current FCF is negative or unavailable — intrinsic value model not computed.",
            "Analyse on a price-to-sales or strategic value basis.",
        ]
        return "\n".join(lines)

    # 5-year DCF
    discount = 1.0
    pv_sum   = 0.0
    fcf      = base_fcf
    for yr in range(1, 6):
        fcf      *= (1 + projected_g)
        discount *= (1 + _WACC)
        pv_sum   += fcf / discount

    terminal_val = (fcf * (1 + _TERMINAL_G)) / (_WACC - _TERMINAL_G)
    pv_terminal  = terminal_val / discount
    intrinsic    = pv_sum + pv_terminal

    # Equity value = intrinsic - net debt (simplified)
    net_debt = 0.0
    if "total_debt" in latest and "equity" in latest:
        net_debt = max(0, latest["total_debt"] - latest.get("equity", 0))

    equity_val = max(0, intrinsic - net_debt)

    lines = [
        "## Quantitative Valuation Context",
        f"**DCF Confidence: {confidence}** — {conf_note}",
        "",
        "### Revenue & Margin Trend",
        f"  Historical Revenue CAGR ({len(revenues)-1}Y): {rev_cagr*100:+.1f}%",
        f"  Projected Growth (5Y, conservative): {projected_g*100:.1f}%",
        f"  Avg Operating Margin: {avg_margin*100:.1f}%",
        f"  Base FCF (latest): {base_fcf:,.0f} KRW",
        "",
        "### Simple DCF (WACC 10%, terminal growth 2%)",
        f"  PV of 5-year FCFs:      {pv_sum:>15,.0f} KRW",
        f"  PV of Terminal Value:   {pv_terminal:>15,.0f} KRW",
        f"  Enterprise Value:       {intrinsic:>15,.0f} KRW",
        f"  Less Net Debt proxy:    {net_debt:>15,.0f} KRW",
        f"  Implied Equity Value:   {equity_val:>15,.0f} KRW",
        "",
        "### Fair-Value Scenarios",
        f"  Bear (–20%): {equity_val * _BEAR_MULT:>15,.0f} KRW",
        f"  Base:        {equity_val:>15,.0f} KRW",
        f"  Bull (+25%): {equity_val * _BULL_MULT:>15,.0f} KRW",
        "",
        "> ⚠  This model uses publicly reported DART figures only. Treat as a",
        "> directional anchor, not a precise target. Cross-check against peers below.",
    ]
    return "\n".join(lines)


# ── Peer comps summary ────────────────────────────────────────────────────────

def _build_comps_summary(peers: List[Dict], ticker_str: str) -> str:
    """
    Build a peer multiple comparison table from already-fetched peer data
    (from market_tools.get_peer_comparison).
    """
    valid_peers = [p for p in peers if p.get("pe_ratio") or p.get("pb_ratio")]
    if not valid_peers:
        return ""

    lines = [
        "### Peer Multiple Comparison (P/E and P/B)",
        "",
        f"{'Company':<35} {'3M Ret':>9} {'P/E':>8} {'P/B':>8}",
        "-" * 65,
    ]

    pe_vals, pb_vals = [], []
    for p in valid_peers:
        ret_s = f"{p['3m_return']:+.1f}%" if p.get("3m_return") is not None else "N/A"
        pe_s  = f"{p['pe_ratio']:.1f}"    if p.get("pe_ratio")  is not None else "N/A"
        pb_s  = f"{p['pb_ratio']:.2f}"    if p.get("pb_ratio")  is not None else "N/A"
        lines.append(f"{p.get('name', p['ticker']):<35} {ret_s:>9} {pe_s:>8} {pb_s:>8}")
        if p.get("pe_ratio"):
            pe_vals.append(p["pe_ratio"])
        if p.get("pb_ratio"):
            pb_vals.append(p["pb_ratio"])

    if pe_vals or pb_vals:
        lines.append("-" * 65)
        pe_med = sorted(pe_vals)[len(pe_vals) // 2] if pe_vals else None
        pb_med = sorted(pb_vals)[len(pb_vals) // 2] if pb_vals else None
        pe_med_s = f"{pe_med:.1f}" if pe_med else "N/A"
        pb_med_s = f"{pb_med:.2f}" if pb_med else "N/A"
        lines.append(f"{'Peer Median':<35} {'':>9} {pe_med_s:>8} {pb_med_s:>8}")
        lines.append("")
        lines.append(
            "The stock's own P/E and P/B are shown in the Market section. "
            "Compare to peer medians above to assess relative valuation."
        )

    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def build_valuation_context(
    fs_years: List[Dict],
    peers: List[Dict],
    ticker_str: str,
    company_name: str,
) -> str:
    """
    Build a combined DCF + comps block to append to fundamental_data.

    Parameters
    ----------
    fs_years     : list of DART fnlttSinglAcnt response dicts (oldest → newest)
    peers        : peer list from market_tools.get_peer_comparison()
    ticker_str   : e.g. "214150.KQ" (for display only)
    company_name : Korean company name

    Returns
    -------
    str — formatted block, or "" if insufficient data.
    """
    parts = []

    dcf = _build_dcf_summary(fs_years, company_name)
    if dcf:
        parts.append(dcf)

    comps = _build_comps_summary(peers, ticker_str)
    if comps:
        parts.append(comps)

    if not parts:
        return ""

    header = (
        f"\n---\n"
        f"## Valuation Analysis — {company_name}\n"
        f"*(Computed from DART financials + peer multiples; "
        f"intended as a directional anchor for FundamentalAgent)*\n\n"
    )
    return header + "\n\n".join(parts)
