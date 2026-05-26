"""
calibration/formatter.py
========================
Formats per-agent calibration tables into clean text blocks for prompt injection.

No LLM calls — pure string formatting. Each agent receives only its own
domain's historical data. The formatted_context is stored back into the
CalibrationData dict and saved to disk.

Public API
----------
from calibration.formatter import attach_formatted_contexts

calibration_data = attach_formatted_contexts(calibration_data)
# Mutates calibration_data["per_agent"][agent]["formatted_context"] in-place.
# Returns the same dict.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

AgentRecord     = Dict[str, Any]
CalibrationData = Dict[str, Any]

AGENT_NAMES = [
    "TechnicalAgent",
    "FundamentalAgent",
    "SentimentAgent",
    "MarketAgent",
    "MacroAgent",
]


# ── Shared helpers ────────────────────────────────────────────────────────────

def _pct(value: Optional[float], decimals: int = 2) -> str:
    if value is None:
        return "N/A"
    return f"{value:+.{decimals}f}%"


def _tick(correct: Optional[bool]) -> str:
    if correct is True:
        return "✓"
    if correct is False:
        return "✗"
    return "?"


def _summary_line(stats: Dict[str, Any]) -> str:
    acc = stats.get("direction_accuracy")
    acc_str = f"{acc:.0%}" if acc is not None else "N/A"
    buy_ret  = stats.get("avg_return_on_buy")
    sell_ret = stats.get("avg_return_on_sell")
    return (
        f"Direction accuracy: {acc_str} "
        f"({stats.get('correct_count', 0)}/{stats.get('total_signals', 0)})  |  "
        f"Avg return on BUY: {_pct(buy_ret)}  |  "
        f"Avg return on SELL: {_pct(sell_ret)}"
    )


# ── Per-agent formatters ──────────────────────────────────────────────────────

def _format_technical(records: List[AgentRecord], stats: Dict[str, Any]) -> str:
    lines = [
        "=== YOUR SIGNAL HISTORY — TechnicalAgent ===",
        "The table below shows your past signals for this stock pool alongside actual outcomes.",
        "Use this as evidence — not as a rule — when forming your analysis this quarter.",
        "",
    ]

    # Group by holding period for readability
    periods: Dict[str, List[AgentRecord]] = {}
    for r in records:
        key = f"{r.get('holding_period_start', '?')} → {r.get('holding_period_end', '?')}"
        periods.setdefault(key, []).append(r)

    for period, period_records in periods.items():
        lines.append(f"Period: {period}")
        header = f"{'Stock':<12} {'RSI':>6} {'MA20Δ':>8} {'Signal':<7} {'Actual':>8} {'OK':>4}"
        lines.append(header)
        lines.append("-" * len(header))
        for r in period_records:
            ind = r.get("indicators", {})
            rsi    = f"{ind['rsi']:.1f}"   if "rsi"          in ind else "N/A"
            ma20   = _pct(ind.get("ma20_delta_pct"), 1) if "ma20_delta_pct" in ind else "N/A"
            signal = r.get("final_signal", "?")
            actual = _pct(r.get("actual_return"))
            ok     = _tick(r.get("direction_correct"))
            name   = r.get("company_name", r.get("stock_code", "?"))[:10]
            lines.append(f"{name:<12} {rsi:>6} {ma20:>8} {signal:<7} {actual:>8} {ok:>4}")
        lines.append("")

    lines.append(_summary_line(stats))
    lines.append("=" * 50)
    return "\n".join(lines)


def _format_fundamental(records: List[AgentRecord], stats: Dict[str, Any]) -> str:
    lines = [
        "=== YOUR SIGNAL HISTORY — FundamentalAgent ===",
        "The table below shows your past fundamental calls alongside actual outcomes.",
        "Use this as evidence — not as a rule — when forming your analysis this quarter.",
        "",
    ]

    periods: Dict[str, List[AgentRecord]] = {}
    for r in records:
        key = f"{r.get('holding_period_start', '?')} → {r.get('holding_period_end', '?')}"
        periods.setdefault(key, []).append(r)

    for period, period_records in periods.items():
        lines.append(f"Period: {period}")
        header = f"{'Stock':<12} {'Revenue':>9} {'Margin':>12} {'Debt?':>6} {'Signal':<7} {'Actual':>8} {'OK':>4}"
        lines.append(header)
        lines.append("-" * len(header))
        for r in period_records:
            ind     = r.get("indicators", {})
            rev     = ind.get("revenue_direction", "N/A")[:5]
            margin  = ind.get("margin_direction", "N/A")[:7]
            debt    = "Yes" if ind.get("debt_concern") is True else ("No" if ind.get("debt_concern") is False else "N/A")
            signal  = r.get("final_signal", "?")
            actual  = _pct(r.get("actual_return"))
            ok      = _tick(r.get("direction_correct"))
            name    = r.get("company_name", r.get("stock_code", "?"))[:10]
            lines.append(f"{name:<12} {rev:>9} {margin:>12} {debt:>6} {signal:<7} {actual:>8} {ok:>4}")
        lines.append("")

    lines.append(_summary_line(stats))
    lines.append("=" * 50)
    return "\n".join(lines)


def _format_sentiment(records: List[AgentRecord], stats: Dict[str, Any]) -> str:
    lines = [
        "=== YOUR SIGNAL HISTORY — SentimentAgent ===",
        "The table below shows your past sentiment calls alongside actual outcomes.",
        "Use this as evidence — not as a rule — when forming your analysis this quarter.",
        "",
    ]

    periods: Dict[str, List[AgentRecord]] = {}
    for r in records:
        key = f"{r.get('holding_period_start', '?')} → {r.get('holding_period_end', '?')}"
        periods.setdefault(key, []).append(r)

    for period, period_records in periods.items():
        lines.append(f"Period: {period}")
        header = f"{'Stock':<12} {'DART+':>6} {'DART-':>6} {'Short%':>7} {'Signal':<7} {'Actual':>8} {'OK':>4}"
        lines.append(header)
        lines.append("-" * len(header))
        for r in period_records:
            ind    = r.get("indicators", {})
            dpos   = str(ind.get("dart_positive_count", "N/A"))
            dneg   = str(ind.get("dart_negative_count", "N/A"))
            short  = f"{ind['short_ratio_pct']:.1f}%" if "short_ratio_pct" in ind else "N/A"
            signal = r.get("final_signal", "?")
            actual = _pct(r.get("actual_return"))
            ok     = _tick(r.get("direction_correct"))
            name   = r.get("company_name", r.get("stock_code", "?"))[:10]
            lines.append(f"{name:<12} {dpos:>6} {dneg:>6} {short:>7} {signal:<7} {actual:>8} {ok:>4}")
        lines.append("")

    lines.append(_summary_line(stats))
    lines.append("=" * 50)
    return "\n".join(lines)


def _format_market(records: List[AgentRecord], stats: Dict[str, Any]) -> str:
    lines = [
        "=== YOUR SIGNAL HISTORY — MarketAgent ===",
        "The table below shows your past competitive/industry calls alongside actual outcomes.",
        "Use this as evidence — not as a rule — when forming your analysis this quarter.",
        "",
    ]

    periods: Dict[str, List[AgentRecord]] = {}
    for r in records:
        key = f"{r.get('holding_period_start', '?')} → {r.get('holding_period_end', '?')}"
        periods.setdefault(key, []).append(r)

    for period, period_records in periods.items():
        lines.append(f"Period: {period}")
        header = f"{'Stock':<12} {'Industry':>10} {'CompPos':>10} {'Signal':<7} {'Actual':>8} {'OK':>4}"
        lines.append(header)
        lines.append("-" * len(header))
        for r in period_records:
            ind    = r.get("indicators", {})
            ind_cy = ind.get("industry_cycle", "N/A")[:8]
            comp   = ind.get("competitive_position", "N/A")[:8]
            signal = r.get("final_signal", "?")
            actual = _pct(r.get("actual_return"))
            ok     = _tick(r.get("direction_correct"))
            name   = r.get("company_name", r.get("stock_code", "?"))[:10]
            lines.append(f"{name:<12} {ind_cy:>10} {comp:>10} {signal:<7} {actual:>8} {ok:>4}")
        lines.append("")

    lines.append(_summary_line(stats))
    lines.append("=" * 50)
    return "\n".join(lines)


def _format_macro(records: List[AgentRecord], stats: Dict[str, Any]) -> str:
    lines = [
        "=== YOUR SIGNAL HISTORY — MacroAgent ===",
        "The table below shows your past macro calls alongside actual outcomes.",
        "Use this as evidence — not as a rule — when forming your analysis this quarter.",
        "",
    ]

    periods: Dict[str, List[AgentRecord]] = {}
    for r in records:
        key = f"{r.get('holding_period_start', '?')} → {r.get('holding_period_end', '?')}"
        periods.setdefault(key, []).append(r)

    for period, period_records in periods.items():
        lines.append(f"Period: {period}")
        header = f"{'Stock':<12} {'KRW':>12} {'Rates':>14} {'RiskApp':>9} {'Signal':<7} {'Actual':>8} {'OK':>4}"
        lines.append(header)
        lines.append("-" * len(header))
        for r in period_records:
            ind    = r.get("indicators", {})
            krw    = ind.get("krw_direction", "N/A").replace("krw_", "")[:8]
            rates  = ind.get("rate_environment", "N/A").replace("rates_", "")[:10]
            risk   = ind.get("global_risk_appetite", "N/A").replace("risk_", "")[:7]
            signal = r.get("final_signal", "?")
            actual = _pct(r.get("actual_return"))
            ok     = _tick(r.get("direction_correct"))
            name   = r.get("company_name", r.get("stock_code", "?"))[:10]
            lines.append(f"{name:<12} {krw:>12} {rates:>14} {risk:>9} {signal:<7} {actual:>8} {ok:>4}")
        lines.append("")

    lines.append(_summary_line(stats))
    lines.append("=" * 50)
    return "\n".join(lines)


_FORMATTERS = {
    "TechnicalAgent":   _format_technical,
    "FundamentalAgent": _format_fundamental,
    "SentimentAgent":   _format_sentiment,
    "MarketAgent":      _format_market,
    "MacroAgent":       _format_macro,
}


# ── Multi-quarter aggregator ──────────────────────────────────────────────────

def _merge_records_across_quarters(
    all_calibration_data: List[CalibrationData],
    agent_name: str,
) -> tuple[List[AgentRecord], Dict[str, Any]]:
    """Merge records from multiple quarters for a single agent."""
    all_records: List[AgentRecord] = []
    for cal in all_calibration_data:
        agent_data = cal.get("per_agent", {}).get(agent_name, {})
        all_records.extend(agent_data.get("records", []))

    # Recompute summary stats over all quarters combined
    correct = [r for r in all_records if r.get("direction_correct") is True]
    buy_rets  = [r["actual_return"] for r in all_records if r["final_signal"] == "BUY"  and r["actual_return"] is not None]
    sell_rets = [r["actual_return"] for r in all_records if r["final_signal"] == "SELL" and r["actual_return"] is not None]

    combined_stats = {
        "total_signals":      len(all_records),
        "direction_accuracy": round(len(correct) / len(all_records), 3) if all_records else None,
        "avg_return_on_buy":  round(sum(buy_rets)  / len(buy_rets),  2) if buy_rets  else None,
        "avg_return_on_sell": round(sum(sell_rets) / len(sell_rets), 2) if sell_rets else None,
        "buy_count":          len(buy_rets),
        "sell_count":         len(sell_rets),
        "correct_count":      len(correct),
        "quarters_covered":   len(all_calibration_data),
    }

    return all_records, combined_stats


# ── Public function ───────────────────────────────────────────────────────────

def build_formatted_contexts(
    all_calibration_data: List[CalibrationData],
) -> Dict[str, str]:
    """
    Build per-agent formatted context strings from one or more quarters
    of CalibrationData.

    Parameters
    ----------
    all_calibration_data : list of CalibrationData dicts (one per prior quarter),
                           ordered oldest → newest.

    Returns
    -------
    dict mapping agent_name → formatted context string.
    Agents with no records are omitted.
    """
    if not all_calibration_data:
        return {}

    contexts: Dict[str, str] = {}

    for agent_name in AGENT_NAMES:
        formatter = _FORMATTERS.get(agent_name)
        if formatter is None:
            continue

        records, stats = _merge_records_across_quarters(all_calibration_data, agent_name)
        if not records:
            continue

        try:
            contexts[agent_name] = formatter(records, stats)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Failed to format calibration for %s: %s", agent_name, exc
            )

    return contexts
