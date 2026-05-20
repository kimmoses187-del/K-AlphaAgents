"""
tools/dart_report_planner.py
============================
Determines which DART reports to fetch based on as_of_date and analysis stage.

DART report codes
-----------------
  11011  사업보고서   Annual report (full FY)
  11012  반기보고서   H1 semi-annual (Jan–Jun)  ← covers Q1 + Q2
  11013  1분기보고서  Q1 quarterly  (Jan–Mar)
  11014  3분기보고서  Q3 quarterly  (Jul–Sep)

  Note: there is no standalone Q2 report in DART.
  Q2 data is only available via the H1 반기보고서.

Korean DART filing deadlines (from fiscal year-end Dec 31)
----------------------------------------------------------
  Annual  →  March 31  of the following year
  Q1      →  May 15    of the same year
  H1      →  August 14 of the same year
  Q3      →  November 14 of the same year

Stage rules
-----------
  "initial"     Full picture — 3 annual FYs + all available interim reports
  "rebalancing" Delta only   — 1 annual FY  + the single most recent interim report
"""

from datetime import datetime
from typing import List, Dict

# Report codes
REPRT_ANNUAL = "11011"
REPRT_H1     = "11012"
REPRT_Q1     = "11013"
REPRT_Q3     = "11014"

# Filing deadlines: (month, day)
DEADLINE_ANNUAL = (3,  31)
DEADLINE_Q1     = (5,  15)
DEADLINE_H1     = (8,  14)
DEADLINE_Q3     = (11, 14)


def plan_reports(as_of_date: datetime, stage: str = "initial") -> List[Dict]:
    """
    Return a list of report specs to fetch from DART.

    Parameters
    ----------
    as_of_date : analysis date (backtest / rebalancing start date)
    stage      : "initial" or "rebalancing"

    Returns
    -------
    List of dicts: [{"year": int, "reprt_code": str, "label": str}, ...]
    Ordered from most recent to oldest for readability.
    """
    year = as_of_date.year
    md   = (as_of_date.month, as_of_date.day)

    # ── Most recent FY with a published annual report ─────────────────────
    # Annual report for FY(N) is published by March 31 of year N+1
    most_recent_annual_fy = (year - 1) if md > DEADLINE_ANNUAL else (year - 2)

    reports: List[Dict] = []

    # ── Annual reports ────────────────────────────────────────────────────
    num_annual = 3 if stage == "initial" else 1
    for i in range(num_annual):
        fy = most_recent_annual_fy - i
        reports.append({
            "year":       fy,
            "reprt_code": REPRT_ANNUAL,
            "label":      f"FY{fy} Annual (사업보고서)",
        })

    # ── Interim reports for the gap year (most_recent_annual_fy + 1) ─────
    # The gap year is the current partial year not yet covered by an annual.
    gap_year = most_recent_annual_fy + 1

    # Compute absolute publication dates for the gap year
    q1_pub = datetime(gap_year, *DEADLINE_Q1)
    h1_pub = datetime(gap_year, *DEADLINE_H1)
    q3_pub = datetime(gap_year, *DEADLINE_Q3)

    if stage == "initial":
        # Collect ALL available interim reports; H1 supersedes standalone Q1
        # Add in reverse chronological order so most recent appears first
        if as_of_date >= q3_pub:
            reports.append({
                "year":       gap_year,
                "reprt_code": REPRT_Q3,
                "label":      f"{gap_year} Q3 (3분기보고서)",
            })
        if as_of_date >= h1_pub:
            # H1 covers Q1+Q2 → use instead of separate Q1
            reports.append({
                "year":       gap_year,
                "reprt_code": REPRT_H1,
                "label":      f"{gap_year} H1 (반기보고서)",
            })
        elif as_of_date >= q1_pub:
            # H1 not yet published → use Q1 only
            reports.append({
                "year":       gap_year,
                "reprt_code": REPRT_Q1,
                "label":      f"{gap_year} Q1 (1분기보고서)",
            })
        # else: nothing published for gap_year yet — only annual reports available

    else:  # "rebalancing" — single most recent interim report only
        if as_of_date >= q3_pub:
            reports.append({
                "year":       gap_year,
                "reprt_code": REPRT_Q3,
                "label":      f"{gap_year} Q3 (3분기보고서)",
            })
        elif as_of_date >= h1_pub:
            reports.append({
                "year":       gap_year,
                "reprt_code": REPRT_H1,
                "label":      f"{gap_year} H1 (반기보고서)",
            })
        elif as_of_date >= q1_pub:
            reports.append({
                "year":       gap_year,
                "reprt_code": REPRT_Q1,
                "label":      f"{gap_year} Q1 (1분기보고서)",
            })

    return reports


def build_coverage_note(reports: List[Dict], as_of_date: datetime) -> str:
    """
    Generate a plain-text coverage note for the LLM prompt, stating
    exactly which periods were fetched and which are not yet available.
    This helps the agent calibrate its confidence appropriately.
    """
    year = as_of_date.year
    md   = (as_of_date.month, as_of_date.day)
    most_recent_annual_fy = (year - 1) if md > DEADLINE_ANNUAL else (year - 2)
    gap_year = most_recent_annual_fy + 1

    # What is missing for gap_year
    q3_pub          = datetime(gap_year, *DEADLINE_Q3)
    next_annual_pub = datetime(gap_year + 1, *DEADLINE_ANNUAL)

    missing = []
    if as_of_date < datetime(gap_year, *DEADLINE_Q1):
        missing.append(f"{gap_year} Q1 report (not yet published as of {as_of_date.strftime('%Y-%m-%d')})")
    if as_of_date < datetime(gap_year, *DEADLINE_H1):
        missing.append(f"{gap_year} H1 report (not yet published)")
    if as_of_date < q3_pub:
        missing.append(f"{gap_year} Q3 report (not yet published)")
    if as_of_date < next_annual_pub:
        missing.append(f"FY{gap_year} Annual report (not yet published — Q4 data unavailable)")

    lines = ["[DART DATA COVERAGE]"]
    lines.append("Reports fetched:")
    for r in reports:
        lines.append(f"  ✓ {r['label']}")
    if missing:
        lines.append("Periods not yet available — flag data gaps in your analysis:")
        for m in missing:
            lines.append(f"  ✗ {m}")
    lines.append("")
    return "\n".join(lines)


def describe_plan(reports: List[Dict], as_of_date: datetime, stage: str) -> str:
    """One-line console summary of the fetch plan (for print statements)."""
    labels = ", ".join(r["label"] for r in reports)
    return f"DART [{stage}]: {labels}"
