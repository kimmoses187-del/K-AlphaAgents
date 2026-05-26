"""
calibration/builder.py
======================
Joins extracted agent indicators with actual holding-period returns to
produce per-agent calibration tables, then saves to reports/calibration/.

Save-on-generate: after building, immediately writes calibration.json.
Load-if-exists:   before building, checks for an existing valid file.

Public API
----------
from calibration.builder import build_or_load_calibration

calibration_data = build_or_load_calibration(
    stock_codes      = ["086900", "214150"],
    signal_as_of_date = "2025-06-01",
    holding_end_date  = "2025-09-01",
    reports_dir       = "reports",
    profile           = "risk-neutral",
)
# Returns CalibrationData dict or None if skipped (future period / no signals).
"""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from calibration.extractor import extract_indicators_from_signal
from calibration.returns import fetch_holding_returns

log = logging.getLogger(__name__)

# Type aliases
AgentRecord      = Dict[str, Any]
CalibrationData  = Dict[str, Any]


# ── Helper: find signal JSON for a stock + date ───────────────────────────────

def _find_signal_json(
    reports_dir: str,
    stock_code: str,
    as_of_date: str,
) -> Optional[Path]:
    """
    Locate reports/signals/{ticker}_*/{as_of_date}/*.json for a given stock code.
    Returns the first match or None.
    """
    signals_dir = Path(reports_dir) / "signals"
    if not signals_dir.exists():
        return None

    for ticker_dir in signals_dir.iterdir():
        if not ticker_dir.is_dir():
            continue
        if not ticker_dir.name.startswith(stock_code):
            continue

        date_dir = ticker_dir / as_of_date
        if not date_dir.exists():
            continue

        for f in date_dir.iterdir():
            if f.suffix == ".json" and not f.name.startswith("."):
                return f

    return None


# ── Calibration file path ─────────────────────────────────────────────────────

def _calibration_path(reports_dir: str, signal_as_of_date: str) -> Path:
    return Path(reports_dir) / "calibration" / signal_as_of_date / "calibration.json"


# ── Load existing calibration ─────────────────────────────────────────────────

def _load_existing(
    path: Path,
    stock_codes: List[str],
) -> Optional[CalibrationData]:
    """
    Load and validate an existing calibration.json.
    Returns None if file is missing, unreadable, or stock pool has changed.
    """
    if not path.exists():
        return None

    try:
        with open(path, encoding="utf-8") as fp:
            data = json.load(fp)
    except Exception as exc:
        log.warning("Could not load calibration file %s: %s", path, exc)
        return None

    saved_stocks = set(data.get("stocks_covered", []))
    current_stocks = set(stock_codes)

    if saved_stocks != current_stocks:
        log.info(
            "Stock pool changed (saved=%s, current=%s) — will regenerate calibration.",
            sorted(saved_stocks), sorted(current_stocks),
        )
        return None

    log.info("Calibration loaded from cache: %s", path)
    return data


# ── Summary statistics ────────────────────────────────────────────────────────

def _compute_summary_stats(records: List[AgentRecord]) -> Dict[str, Any]:
    """Compute direction accuracy and average returns by signal."""
    buy_returns  = [r["actual_return"] for r in records if r["final_signal"] == "BUY"  and r["actual_return"] is not None]
    sell_returns = [r["actual_return"] for r in records if r["final_signal"] == "SELL" and r["actual_return"] is not None]
    correct      = [r for r in records if r.get("direction_correct") is True]

    return {
        "total_signals":        len(records),
        "direction_accuracy":   round(len(correct) / len(records), 3) if records else None,
        "avg_return_on_buy":    round(sum(buy_returns)  / len(buy_returns),  2) if buy_returns  else None,
        "avg_return_on_sell":   round(sum(sell_returns) / len(sell_returns), 2) if sell_returns else None,
        "buy_count":            len(buy_returns),
        "sell_count":           len(sell_returns),
        "correct_count":        len(correct),
    }


# ── Core builder ──────────────────────────────────────────────────────────────

def build_or_load_calibration(
    stock_codes: List[str],
    signal_as_of_date: str,       # "YYYY-MM-DD"
    holding_end_date: str,         # "YYYY-MM-DD" — next signal date (or today)
    reports_dir: str = "reports",
    profile: str = "risk-neutral",
) -> Optional[CalibrationData]:
    """
    Build (or load from cache) a CalibrationData object for one signal quarter.

    Steps
    -----
    1. Check for existing calibration.json with matching stock pool → return if found.
    2. Verify holding period has ended (skip if end_date is in the future).
    3. Fetch actual returns for the holding period via pykrx.
    4. Parse each stock's signal JSON → extract per-agent indicators.
    5. Join indicators + actual returns → build per-agent records.
    6. Compute summary stats per agent.
    7. Save to reports/calibration/{signal_as_of_date}/calibration.json.
    8. Return the CalibrationData dict.

    Returns None if:
    - Holding period has not yet ended
    - No signal files found for any stock
    - Price data completely unavailable
    """
    cal_path = _calibration_path(reports_dir, signal_as_of_date)

    # ── Step 1: load existing ─────────────────────────────────────────────────
    existing = _load_existing(cal_path, stock_codes)
    if existing is not None:
        return existing

    # ── Step 2: holding period check ──────────────────────────────────────────
    end_dt = datetime.strptime(holding_end_date, "%Y-%m-%d").date()
    if end_dt > date.today():
        log.info(
            "Holding period for %s ends %s (future) — skipping calibration generation.",
            signal_as_of_date, holding_end_date,
        )
        return None

    log.info(
        "Generating calibration for %s → %s (stocks: %s)",
        signal_as_of_date, holding_end_date, stock_codes,
    )

    # ── Step 3: fetch actual returns ──────────────────────────────────────────
    actual_returns = fetch_holding_returns(
        stock_codes=stock_codes,
        start_date=signal_as_of_date,
        end_date=holding_end_date,
    )
    if not actual_returns:
        log.warning("No return data available for %s → %s", signal_as_of_date, holding_end_date)
        return None

    # ── Steps 4 & 5: extract indicators and join with returns ─────────────────
    # per_agent_records: {agent_name: [record, ...]}
    per_agent_records: Dict[str, List[AgentRecord]] = {}

    for stock_code in stock_codes:
        signal_path = _find_signal_json(reports_dir, stock_code, signal_as_of_date)
        if signal_path is None:
            log.debug("No signal file for %s at %s", stock_code, signal_as_of_date)
            continue

        agent_records = extract_indicators_from_signal(signal_path, profile=profile)

        actual_ret = actual_returns.get(stock_code)

        for rec in agent_records:
            # Annotate with actual return and direction correctness
            rec["holding_period_start"] = signal_as_of_date
            rec["holding_period_end"]   = holding_end_date
            rec["actual_return"]        = actual_ret

            if actual_ret is not None:
                signal = rec["final_signal"]
                rec["direction_correct"] = (
                    (signal == "BUY"  and actual_ret > 0) or
                    (signal == "SELL" and actual_ret < 0)
                )
            else:
                rec["direction_correct"] = None

            agent = rec["agent"]
            per_agent_records.setdefault(agent, []).append(rec)

    if not per_agent_records:
        log.warning("No agent records built for %s — missing signal files?", signal_as_of_date)
        return None

    # ── Step 6: summary stats per agent ──────────────────────────────────────
    per_agent_output: Dict[str, Any] = {}
    for agent_name, records in per_agent_records.items():
        per_agent_output[agent_name] = {
            "records":       records,
            "summary_stats": _compute_summary_stats(records),
            # formatted_context is added later by formatter.py
        }

    # ── Step 7: assemble and save ─────────────────────────────────────────────
    calibration_data: CalibrationData = {
        "signal_as_of_date":  signal_as_of_date,
        "holding_period_end": holding_end_date,
        "generated_at":       date.today().isoformat(),
        "profile":            profile,
        "stocks_covered":     sorted(stock_codes),
        "actual_returns":     actual_returns,
        "per_agent":          per_agent_output,
    }

    cal_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(cal_path, "w", encoding="utf-8") as fp:
            json.dump(calibration_data, fp, ensure_ascii=False, indent=2)
        log.info("Calibration saved: %s", cal_path)
    except Exception as exc:
        log.warning("Failed to save calibration file %s: %s", cal_path, exc)

    return calibration_data
