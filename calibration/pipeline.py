"""
calibration/pipeline.py
=======================
Top-level orchestrator for the calibration pipeline.

Determines which prior signal quarters are available, builds or loads
calibration data for each, and returns ready-to-inject per-agent contexts.

Public API
----------
from calibration.pipeline import load_or_generate_calibration

contexts = load_or_generate_calibration(
    stock_codes   = ["086900", "214150"],
    signal_dates  = ["2025-06-01", "2025-09-01", "2025-12-01"],  # sorted ascending
    reports_dir   = "reports",
    profile       = "risk-neutral",
)
# Returns dict[agent_name, formatted_context_str] — empty dict on cold start.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime
from typing import Dict, List, Optional

from calibration.builder import build_or_load_calibration, CalibrationData
from calibration.formatter import build_formatted_contexts

log = logging.getLogger(__name__)


def load_or_generate_calibration(
    stock_codes: List[str],
    signal_dates: List[str],      # sorted ascending, "YYYY-MM-DD"
    reports_dir: str = "reports",
    profile: str = "risk-neutral",
) -> Dict[str, str]:
    """
    Build per-agent calibration contexts from all available prior signal quarters.

    For each consecutive pair of signal dates (Q_n → Q_{n+1}), attempts to
    build/load calibration covering the signals at Q_n with outcomes measured
    at Q_{n+1}.  The last (most recent) signal date has no known outcome yet
    and is always skipped.

    Parameters
    ----------
    stock_codes  : list of 6-digit KRX codes being analysed this run
    signal_dates : all known signal as-of dates, sorted oldest→newest
    reports_dir  : root reports directory (default "reports")
    profile      : risk profile to extract signals for

    Returns
    -------
    dict mapping agent_name → formatted context string
    Empty dict if no calibration is available (cold start or all periods future).
    """
    if len(signal_dates) < 2:
        log.info("Only %d signal date(s) — no completed holding period to calibrate.", len(signal_dates))
        return {}

    if not stock_codes:
        return {}

    all_calibration_data: List[CalibrationData] = []

    # Iterate all but the last date — each needs a "next date" as holding_end
    for i in range(len(signal_dates) - 1):
        signal_date = signal_dates[i]
        end_date    = signal_dates[i + 1]

        cal = build_or_load_calibration(
            stock_codes=stock_codes,
            signal_as_of_date=signal_date,
            holding_end_date=end_date,
            reports_dir=reports_dir,
            profile=profile,
        )

        if cal is not None:
            all_calibration_data.append(cal)
            log.info(
                "Calibration ready for %s → %s (%d stocks)",
                signal_date, end_date, len(stock_codes),
            )
        else:
            log.info("Calibration skipped for %s → %s", signal_date, end_date)

    if not all_calibration_data:
        log.info("No calibration data available — proceeding without calibration context.")
        return {}

    log.info(
        "Building formatted calibration contexts from %d quarter(s).",
        len(all_calibration_data),
    )
    contexts = build_formatted_contexts(all_calibration_data)

    if contexts:
        agents_with_context = list(contexts.keys())
        log.info("Calibration context ready for: %s", ", ".join(agents_with_context))
    else:
        log.info("No agent contexts produced from calibration data.")

    return contexts


def get_existing_signal_dates(
    stock_codes: List[str],
    reports_dir: str = "reports",
) -> List[str]:
    """
    Scan reports/signals/ and return all as-of dates for which ALL stocks in
    the pool have saved signal files, sorted ascending.

    Used by main.py / web/runner.py to auto-detect prior quarters.
    """
    signals_dir = os.path.join(reports_dir, "signals")
    if not os.path.isdir(signals_dir):
        return []

    # For each stock, find its available dates
    stock_dates: Dict[str, set] = {}
    for code in stock_codes:
        dates_for_code: set = set()
        for ticker_dir in os.listdir(signals_dir):
            if not ticker_dir.startswith(code):
                continue
            ticker_path = os.path.join(signals_dir, ticker_dir)
            if not os.path.isdir(ticker_path):
                continue
            for date_dir in os.listdir(ticker_path):
                date_path = os.path.join(ticker_path, date_dir)
                if not os.path.isdir(date_path):
                    continue
                # Verify at least one JSON exists
                has_json = any(
                    f.endswith(".json")
                    for f in os.listdir(date_path)
                    if not f.startswith(".")
                )
                if has_json:
                    dates_for_code.add(date_dir)
        stock_dates[code] = dates_for_code

    if not stock_dates:
        return []

    # Only dates where ALL stocks have signals
    common_dates = set.intersection(*stock_dates.values()) if stock_dates else set()
    return sorted(common_dates)
