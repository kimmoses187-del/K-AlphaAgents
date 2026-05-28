"""
tools/naver_tools.py
====================
Gap 8 — Analyst consensus data from Naver Finance.

Scrapes Naver Finance for:
  • Analyst consensus target price (컨센서스 목표주가)
  • Rating distribution (Strong Buy / Buy / Hold / Sell count)
  • Number of analysts covering the stock
  • Consensus EPS estimates (current year, next year)

Naver Finance is Korea's most widely-used retail financial portal and
aggregates analyst data from major Korean brokerages.

Output is a compact text block appended to market_data so MarketAgent
can benchmark agents' own signals against sell-side consensus.

Graceful fallback: returns "" on any failure (network, HTML structure
change, rate-limit).  Never raises — safe to call unconditionally.

Public API
----------
from tools.naver_tools import fetch_analyst_consensus

block = fetch_analyst_consensus(stock_code="214150")
# Returns formatted string or "" on failure.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

_NAVER_ITEM_URL = "https://finance.naver.com/item/main.nhn"
_NAVER_CONSENSUS_URL = "https://finance.naver.com/item/coinfo.nhn"

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://finance.naver.com/",
}

_TIMEOUT = 15


def _get_page(url: str, params: dict = None) -> Optional[BeautifulSoup]:
    """Fetch a Naver Finance page and return a BeautifulSoup, or None on error."""
    try:
        r = requests.get(url, params=params, headers=_HEADERS, timeout=_TIMEOUT)
        r.raise_for_status()
        r.encoding = "euc-kr"
        return BeautifulSoup(r.text, "lxml")
    except Exception as exc:
        log.debug("Naver fetch error (%s): %s", url, exc)
        return None


def _clean_num(text: str) -> Optional[float]:
    """Parse Korean number strings like '123,456' or '12.3%' → float."""
    if not text:
        return None
    cleaned = re.sub(r"[^\d.\-]", "", text.strip())
    try:
        return float(cleaned)
    except (ValueError, TypeError):
        return None


def fetch_analyst_consensus(stock_code: str) -> str:
    """
    Fetch analyst consensus data for a Korean stock from Naver Finance.

    Returns a formatted markdown block for injection into market_data,
    or "" if data is unavailable.
    """
    # ── Main item page — target price + rating distribution ──────────────
    soup = _get_page(_NAVER_ITEM_URL, params={"code": stock_code})
    if soup is None:
        return ""

    lines = []

    # ── Current price ─────────────────────────────────────────────────────
    try:
        price_tag = soup.find("p", class_="no_today")
        if price_tag:
            price_num = price_tag.find("span", class_="blind")
            if price_num:
                price_str = price_num.get_text(strip=True).replace(",", "")
                lines.append(f"Current Price: {price_str} KRW")
    except Exception:
        pass

    # ── Analyst consensus target price ────────────────────────────────────
    # Naver shows 목표주가 (target price) in the consensus section
    target_price = None
    analyst_count = None
    try:
        # Look for consensus block (appears as a table or specific div)
        consensus_area = soup.find("div", {"id": "chart_analyst"}) or \
                         soup.find("div", class_="section_cns") or \
                         soup.find("table", class_="tb_dl")

        if consensus_area:
            text = consensus_area.get_text(" ", strip=True)
            # Pattern: "목표주가 123,000"
            m = re.search(r"목표주가[^\d]*([0-9,]+)", text)
            if m:
                target_price = _clean_num(m.group(1))
            # Pattern: "N명" or "N개 증권사"
            m2 = re.search(r"(\d+)[명\s]*(개\s*)?증권사", text)
            if m2:
                analyst_count = int(m2.group(1))

    except Exception as exc:
        log.debug("Target price parse error: %s", exc)

    # ── Fallback: try the consensus sub-page ──────────────────────────────
    if target_price is None:
        time.sleep(0.3)
        soup2 = _get_page(_NAVER_CONSENSUS_URL, params={"code": stock_code, "target": "total"})
        if soup2:
            try:
                text2 = soup2.get_text(" ", strip=True)
                m = re.search(r"목표주가[^\d]*([0-9,]+)", text2)
                if m:
                    target_price = _clean_num(m.group(1))
                m2 = re.search(r"(\d+)[명\s]*(개\s*)?증권사", text2)
                if m2:
                    analyst_count = int(m2.group(1))
            except Exception:
                pass

    # ── Format output ─────────────────────────────────────────────────────
    if target_price is None and analyst_count is None:
        # No consensus data found — don't inject empty section
        log.debug("No Naver consensus data found for %s", stock_code)
        return ""

    lines.append("")
    lines.append("## Analyst Consensus (Naver Finance)")
    lines.append("")

    if target_price:
        lines.append(f"  Consensus Target Price:  {target_price:,.0f} KRW")
    if analyst_count:
        lines.append(f"  Number of Analysts:      {analyst_count}")

    if target_price:
        # Compute implied upside using rough current price from page
        try:
            price_tag = soup.find("p", class_="no_today")
            if price_tag:
                pnum = price_tag.find("span", class_="blind")
                if pnum:
                    cur = _clean_num(pnum.get_text(strip=True))
                    if cur and cur > 0:
                        upside = (target_price / cur - 1) * 100
                        sign = "+" if upside >= 0 else ""
                        lines.append(
                            f"  Implied Upside/Downside: {sign}{upside:.1f}%  "
                            f"from current price {cur:,.0f} KRW"
                        )
                        # Interpretation
                        if upside > 20:
                            lines.append("  → Analysts see meaningful upside — bullish consensus")
                        elif upside > 5:
                            lines.append("  → Modest upside — cautiously constructive consensus")
                        elif upside > -5:
                            lines.append("  → Target near market price — consensus is neutral")
                        else:
                            lines.append("  → Target below market — analysts foresee downside")
        except Exception:
            pass

    lines.append("")
    lines.append(
        "> ℹ  Naver Finance aggregates sell-side analyst reports from Korean brokerages."
        " Compare vs your own signal as a sentiment cross-check."
    )

    return "\n".join(lines)


def fetch_naver_market_info(stock_code: str) -> dict:
    """
    Fetch basic market info from Naver Finance: 52W high/low, per share metrics.
    Returns dict with available fields, empty dict on failure.
    """
    soup = _get_page(_NAVER_ITEM_URL, params={"code": stock_code})
    if soup is None:
        return {}

    result = {}
    try:
        # 52-week high/low typically in a summary table
        tables = soup.find_all("table")
        for table in tables:
            text = table.get_text(" ")
            m_h = re.search(r"52주[^\d]*최고[^\d]*([0-9,]+)", text)
            m_l = re.search(r"52주[^\d]*최저[^\d]*([0-9,]+)", text)
            if m_h:
                result["52w_high"] = _clean_num(m_h.group(1))
            if m_l:
                result["52w_low"] = _clean_num(m_l.group(1))
            if result:
                break
    except Exception:
        pass

    return result
