"""
calibration/
============
Performance calibration pipeline for K-AlphaAgents.

Generates per-agent signal accuracy history from saved signal JSONs
and actual price returns. No LLM calls — pure data engineering.

Public API
----------
from calibration import load_or_generate_calibration

calibration_context = load_or_generate_calibration(
    stock_codes   = ["086900", "214150"],
    signal_dates  = ["2025-06-01", "2025-09-01"],   # sorted ascending
    reports_dir   = "reports",
)
# Returns dict[str, str] keyed by agent name, or {} on cold start.
"""

from calibration.pipeline import load_or_generate_calibration

__all__ = ["load_or_generate_calibration"]
