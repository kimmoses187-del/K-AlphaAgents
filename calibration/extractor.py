"""
calibration/extractor.py
========================
Parses saved signal JSON files and extracts each agent's key indicators
from their Round 0 analysis text.

No LLM calls — pure regex + text parsing on already-saved data.

Public API
----------
from calibration.extractor import extract_indicators_from_signal

records = extract_indicators_from_signal(signal_json_path)
# Returns list of AgentRecord dicts, one per agent.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Type alias
AgentRecord = Dict[str, Any]


# ── Per-agent indicator extractors ────────────────────────────────────────────

def _extract_technical(text: str) -> Dict[str, Any]:
    """Extract TechnicalAgent-specific indicators from Round 0 analysis text."""
    indicators: Dict[str, Any] = {}

    # RSI value
    m = re.search(r"RSI[^0-9]{0,20}(\d+\.?\d*)", text[:3000])
    if m:
        indicators["rsi"] = float(m.group(1))

    # MA20 relative position (% above/below)
    m = re.search(r"MA20[^0-9+-]{0,40}([+-]?\d+\.?\d+)%", text[:3000])
    if m:
        indicators["ma20_delta_pct"] = float(m.group(1))

    # MA60 relative position
    m = re.search(r"MA60[^0-9+-]{0,40}([+-]?\d+\.?\d+)%", text[:3000])
    if m:
        indicators["ma60_delta_pct"] = float(m.group(1))

    # Prior quarter return — first large % value in opening section
    pcts = re.findall(r"([+-]?\d+\.?\d+)%", text[:1000])
    if pcts:
        indicators["leading_pct_values"] = [float(p) for p in pcts[:4]]

    # Bollinger %B
    m = re.search(r"%B[^0-9]{0,20}(\d+\.?\d*)", text[:3000])
    if m:
        indicators["bb_pct_b"] = float(m.group(1))

    # Days below MA20 (oversold duration)
    m = re.search(r"(\d+)\s*(?:trading\s*)?days?\s*below\s*MA20", text[:3000], re.IGNORECASE)
    if m:
        indicators["days_below_ma20"] = int(m.group(1))

    return indicators


def _extract_fundamental(text: str) -> Dict[str, Any]:
    """Extract FundamentalAgent-specific indicators from Round 0 analysis text."""
    indicators: Dict[str, Any] = {}

    # Revenue trend direction
    for keyword, label in [
        (r"revenue[^.]{0,80}(grow|increas|expand|rise|rose|surged)", "revenue_trend_up"),
        (r"revenue[^.]{0,80}(declin|decreas|fell|shrink|contract)", "revenue_trend_down"),
    ]:
        if re.search(keyword, text[:4000], re.IGNORECASE):
            indicators["revenue_direction"] = "up" if "up" in label else "down"
            break

    # Margin direction
    for keyword, direction in [
        (r"margin[^.]{0,80}(expand|improv|increas|widen)", "expanding"),
        (r"margin[^.]{0,80}(compress|squeez|declin|narrow|deteriorat)", "compressing"),
    ]:
        if re.search(keyword, text[:4000], re.IGNORECASE):
            indicators["margin_direction"] = direction
            break

    # Debt / leverage concern
    if re.search(r"(high\s*debt|overleverag|debt[\s-]laden|leverage\s*concern|debt[^.]{0,40}risk)", text[:4000], re.IGNORECASE):
        indicators["debt_concern"] = True
    elif re.search(r"(debt[\s-]free|minimal\s*debt|low\s*debt|clean\s*balance)", text[:4000], re.IGNORECASE):
        indicators["debt_concern"] = False

    # Earnings quality
    if re.search(r"(cash\s*flow[^.]{0,60}(strong|robust|positive|solid)|free\s*cash\s*flow[^.]{0,30}(positive|generat))", text[:4000], re.IGNORECASE):
        indicators["earnings_quality"] = "strong"
    elif re.search(r"(cash\s*flow[^.]{0,60}(weak|negative|concern|deterior))", text[:4000], re.IGNORECASE):
        indicators["earnings_quality"] = "weak"

    # Governance / litigation flag
    if re.search(r"(litigation|lawsuit|legal\s*risk|regulatory\s*action|investigation|sanction)", text[:4000], re.IGNORECASE):
        indicators["litigation_flag"] = True

    return indicators


def _extract_sentiment(text: str) -> Dict[str, Any]:
    """Extract SentimentAgent-specific indicators from Round 0 analysis text."""
    indicators: Dict[str, Any] = {}

    # Foreign / institutional net flow direction
    for pattern, direction in [
        (r"foreign[^.]{0,60}(buy|accumulat|net\s*purchas|inflow)", "foreign_buying"),
        (r"foreign[^.]{0,60}(sell|distribut|net\s*sell|outflow)", "foreign_selling"),
        (r"institutional[^.]{0,60}(buy|accumulat|net\s*purchas)", "institutional_buying"),
        (r"institutional[^.]{0,60}(sell|distribut|net\s*sell)", "institutional_selling"),
    ]:
        if re.search(pattern, text[:4000], re.IGNORECASE):
            indicators.setdefault("flow_signals", []).append(direction)

    # Short selling level
    m = re.search(r"short[^.]{0,40}(\d+\.?\d+)%", text[:3000], re.IGNORECASE)
    if m:
        indicators["short_ratio_pct"] = float(m.group(1))

    # DART disclosure sentiment
    positive_dart = len(re.findall(
        r"(dividend|buyback|new\s*contract|partnership|approval|award)", text[:4000], re.IGNORECASE
    ))
    negative_dart = len(re.findall(
        r"(audit|penalty|fine|recall|lawsuit|investigation|violation)", text[:4000], re.IGNORECASE
    ))
    if positive_dart or negative_dart:
        indicators["dart_positive_count"] = positive_dart
        indicators["dart_negative_count"] = negative_dart

    return indicators


def _extract_market(text: str) -> Dict[str, Any]:
    """Extract MarketAgent-specific indicators from Round 0 analysis text."""
    indicators: Dict[str, Any] = {}

    # Industry cycle stage
    for pattern, stage in [
        (r"(early[\s-]stage|nascent|emerging\s*market|growth\s*phase)", "early"),
        (r"(mature|saturat|peak|late[\s-]cycle|slowdown)", "mature"),
        (r"(recovery|turnaround|inflection|rebound)", "recovery"),
    ]:
        if re.search(pattern, text[:4000], re.IGNORECASE):
            indicators["industry_cycle"] = stage
            break

    # Competitive position
    for pattern, pos in [
        (r"(market\s*leader|dominant|#1|number\s*one|leading\s*position)", "leader"),
        (r"(strong\s*competitive|well[\s-]position|competitive\s*advantage|moat)", "strong"),
        (r"(intensif|price\s*war|losing\s*share|undercut|erode)", "challenged"),
    ]:
        if re.search(pattern, text[:4000], re.IGNORECASE):
            indicators["competitive_position"] = pos
            break

    # Peer performance direction
    m = re.search(r"peer[^.]{0,60}([+-]?\d+\.?\d+)%", text[:3000], re.IGNORECASE)
    if m:
        indicators["peer_return_pct"] = float(m.group(1))

    return indicators


def _extract_macro(text: str) -> Dict[str, Any]:
    """Extract MacroAgent-specific indicators from Round 0 analysis text."""
    indicators: Dict[str, Any] = {}

    # KRW/USD direction
    for pattern, direction in [
        (r"KRW[^.]{0,60}(weaken|depreciat|fell|decline|lower)", "krw_weak"),
        (r"KRW[^.]{0,60}(strengthen|appreciat|rose|higher|gain)", "krw_strong"),
        (r"(weaker\s*won|won\s*weakness|won\s*depreciation)", "krw_weak"),
        (r"(stronger\s*won|won\s*strength|won\s*appreciation)", "krw_strong"),
    ]:
        if re.search(pattern, text[:4000], re.IGNORECASE):
            indicators["krw_direction"] = direction
            break

    # US yield / rate environment
    for pattern, direction in [
        (r"(rate\s*cut|rate\s*declin|yield[^.]{0,30}fell|lower\s*rate|dovish)", "rates_falling"),
        (r"(rate\s*hike|rate\s*rise|yield[^.]{0,30}rose|higher\s*rate|hawkish)", "rates_rising"),
    ]:
        if re.search(pattern, text[:4000], re.IGNORECASE):
            indicators["rate_environment"] = direction
            break

    # Global risk appetite
    for pattern, sentiment in [
        (r"(risk[\s-]on|risk\s*appetite|global\s*rally|S&P[^.]{0,30}(gain|rose|up))", "risk_on"),
        (r"(risk[\s-]off|risk\s*aversion|global\s*sell|S&P[^.]{0,30}(fell|down|declin))", "risk_off"),
    ]:
        if re.search(pattern, text[:4000], re.IGNORECASE):
            indicators["global_risk_appetite"] = sentiment
            break

    # KOSPI/KOSDAQ vs agent's stock context
    m = re.search(r"KOSPI[^.]{0,30}([+-]?\d+\.?\d+)%", text[:3000])
    if m:
        indicators["kospi_return_pct"] = float(m.group(1))

    return indicators


# ── Agent dispatcher ──────────────────────────────────────────────────────────

_EXTRACTORS = {
    "TechnicalAgent":   _extract_technical,
    "FundamentalAgent": _extract_fundamental,
    "SentimentAgent":   _extract_sentiment,
    "MarketAgent":      _extract_market,
    "MacroAgent":       _extract_macro,
}


# ── Public function ───────────────────────────────────────────────────────────

def extract_indicators_from_signal(
    signal_json_path: str | Path,
    profile: str = "risk-neutral",
) -> List[AgentRecord]:
    """
    Parse a signal JSON file and extract per-agent Round 0 indicators.

    Parameters
    ----------
    signal_json_path : path to the signal JSON file
    profile          : "risk-neutral" or "risk-averse"

    Returns
    -------
    List of AgentRecord dicts, one per agent found in Round 0.
    Each record contains:
      - stock_code, company_name, as_of_date
      - agent (name)
      - signal (BUY / SELL) from Round 0
      - final_signal (BUY / SELL) — debate outcome for this profile
      - indicators (dict) — agent-specific extracted values
    """
    path = Path(signal_json_path)
    if not path.exists():
        log.warning("Signal file not found: %s", path)
        return []

    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception as exc:
        log.warning("Failed to load signal JSON %s: %s", path, exc)
        return []

    stock_code   = data.get("stock_code", "")
    company_name = data.get("company_name", "")
    as_of_date   = data.get("as_of_date", "")

    debate = data.get("debate_results", {}).get(profile)
    if not debate:
        log.debug("No debate results for profile %s in %s", profile, path)
        return []

    final_signal = debate.get("final_signal", "")
    debate_log   = debate.get("debate_log", [])

    if not debate_log:
        return []

    round0_results = debate_log[0].get("results", [])

    records: List[AgentRecord] = []
    for agent_result in round0_results:
        agent_name = agent_result.get("agent", "")
        round0_signal = agent_result.get("signal", "")
        analysis_text = agent_result.get("analysis", "")

        extractor = _EXTRACTORS.get(agent_name)
        if extractor is None:
            log.debug("No extractor for agent %s", agent_name)
            continue

        try:
            indicators = extractor(analysis_text)
        except Exception as exc:
            log.warning("Indicator extraction failed for %s/%s: %s", agent_name, stock_code, exc)
            indicators = {}

        records.append({
            "stock_code":    stock_code,
            "company_name":  company_name,
            "as_of_date":    as_of_date,
            "agent":         agent_name,
            "round0_signal": round0_signal,
            "final_signal":  final_signal,
            "indicators":    indicators,
        })

    return records
