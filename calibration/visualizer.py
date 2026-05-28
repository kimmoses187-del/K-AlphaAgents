"""
calibration/visualizer.py
=========================
Gap 6 — Calibration accuracy charts.

Generates PNG charts from CalibrationData and saves them alongside
the calibration JSON in reports/calibration/{signal_date}/.

Charts produced:
  1. agent_accuracy.png  — bar chart: directional accuracy % per agent
  2. signal_outcomes.png — grouped bars: BUY-correct, SELL-correct, total per agent

Saved filenames are returned for logging / linking in summaries.

Public API
----------
from calibration.visualizer import generate_calibration_charts

paths = generate_calibration_charts(calibration_data, output_dir)
# Returns list of saved PNG paths (empty list on failure).
"""

from __future__ import annotations

import logging
import os
from typing import List

import matplotlib
matplotlib.use("Agg")          # headless — no display needed
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np

log = logging.getLogger(__name__)

# ── Brand colours (K-AlphaAgents palette) ────────────────────────────────────
_NAVY   = "#0d1117"
_GOLD   = "#f0b429"
_GREEN  = "#2ea043"
_RED    = "#f85149"
_GREY   = "#8b949e"
_WHITE  = "#e6edf3"
_PANEL  = "#161b22"

# Agent short labels
_AGENT_SHORT = {
    "FundamentalAgent": "Fundamental",
    "SentimentAgent":   "Sentiment",
    "TechnicalAgent":   "Technical",
    "MarketAgent":      "Market",
    "MacroAgent":       "Macro",
}


def _setup_ax(ax, title: str):
    """Apply consistent dark-theme styling to an axis."""
    ax.set_facecolor(_PANEL)
    ax.set_title(title, color=_WHITE, fontsize=11, pad=10, fontweight="bold")
    ax.tick_params(colors=_GREY, labelsize=9)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(_GREY)
    ax.xaxis.label.set_color(_GREY)
    ax.yaxis.label.set_color(_GREY)


def generate_calibration_charts(calibration_data, output_dir: str) -> List[str]:
    """
    Build accuracy charts from a CalibrationData object and save as PNGs.

    Parameters
    ----------
    calibration_data : CalibrationData (from calibration/builder.py)
    output_dir       : directory to save charts (e.g. reports/calibration/2025-06-01/)

    Returns
    -------
    List of absolute paths to saved PNG files.
    """
    saved: List[str] = []

    try:
        from calibration.builder import CalibrationData
        cd: CalibrationData = calibration_data

        agents = list(cd.per_agent_summary.keys())
        if not agents:
            return []

        summaries = cd.per_agent_summary

        # ── Chart 1: Direction Accuracy ──────────────────────────────────────
        fig, ax = plt.subplots(figsize=(8, 4.5))
        fig.patch.set_facecolor(_NAVY)
        _setup_ax(ax, "Agent Direction Accuracy (%)")

        labels   = [_AGENT_SHORT.get(a, a) for a in agents]
        accuracy = []
        for a in agents:
            s = summaries[a]
            acc = s.get("direction_accuracy", 0.0) * 100
            accuracy.append(acc)

        bars = ax.bar(labels, accuracy, color=_GOLD, width=0.55, zorder=3)
        ax.set_ylim(0, 110)
        ax.set_ylabel("% Directionally Correct", color=_GREY)
        ax.axhline(50, color=_RED, linewidth=1.2, linestyle="--", label="Random baseline (50%)")
        ax.axhline(65, color=_GREEN, linewidth=1.0, linestyle=":", label="Target threshold (65%)")
        ax.legend(facecolor=_PANEL, labelcolor=_WHITE, fontsize=8, loc="upper right")

        for bar, val in zip(bars, accuracy):
            color = _GREEN if val >= 65 else (_GOLD if val >= 50 else _RED)
            bar.set_color(color)
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 2,
                f"{val:.0f}%",
                ha="center", va="bottom", color=_WHITE, fontsize=9, fontweight="bold"
            )

        # Coverage annotation
        n_stocks = len(cd.stocks_covered)
        n_q      = getattr(cd, "_n_quarters", 1)
        ax.text(
            0.98, 0.03,
            f"n = {n_stocks} stock(s)",
            transform=ax.transAxes, ha="right", va="bottom",
            color=_GREY, fontsize=8
        )

        plt.tight_layout(pad=1.5)
        path1 = os.path.join(output_dir, "agent_accuracy.png")
        fig.savefig(path1, dpi=130, bbox_inches="tight", facecolor=_NAVY)
        plt.close(fig)
        saved.append(path1)
        log.debug("Calibration chart saved: %s", path1)

        # ── Chart 2: BUY-correct vs SELL-correct breakdown ──────────────────
        buy_acc  = []
        sell_acc = []
        n_buy    = []
        n_sell   = []
        for a in agents:
            s = summaries[a]
            buy_acc.append((s.get("avg_return_on_buy", 0) > 0) * 100)
            sell_acc.append((s.get("avg_return_on_sell", 0) <= 0) * 100)
            n_buy.append(s.get("n_buy", 0))
            n_sell.append(s.get("n_sell", 0))

        fig2, ax2 = plt.subplots(figsize=(8, 4.5))
        fig2.patch.set_facecolor(_NAVY)
        _setup_ax(ax2, "Avg Return by Signal Outcome (BUY vs SELL)")

        x    = np.arange(len(agents))
        w    = 0.35
        b1   = ax2.bar(x - w/2, [s.get("avg_return_on_buy",  0)*100 for s in summaries.values()],
                       width=w, label="Avg return on BUY", color=_GREEN, zorder=3)
        b2   = ax2.bar(x + w/2, [s.get("avg_return_on_sell", 0)*100 for s in summaries.values()],
                       width=w, label="Avg return on SELL", color=_RED, zorder=3)

        ax2.axhline(0, color=_WHITE, linewidth=0.8, linestyle="-")
        ax2.set_xticks(x)
        ax2.set_xticklabels(labels)
        ax2.set_ylabel("Average Holding-Period Return (%)", color=_GREY)
        ax2.legend(facecolor=_PANEL, labelcolor=_WHITE, fontsize=8)

        # Label bars
        for bars in (b1, b2):
            for bar in bars:
                h = bar.get_height()
                if h != 0:
                    ax2.text(
                        bar.get_x() + bar.get_width() / 2,
                        h + (0.5 if h >= 0 else -2),
                        f"{h:+.1f}%",
                        ha="center", va="bottom" if h >= 0 else "top",
                        color=_WHITE, fontsize=8
                    )

        plt.tight_layout(pad=1.5)
        path2 = os.path.join(output_dir, "signal_outcomes.png")
        fig2.savefig(path2, dpi=130, bbox_inches="tight", facecolor=_NAVY)
        plt.close(fig2)
        saved.append(path2)
        log.debug("Signal outcomes chart saved: %s", path2)

    except Exception as exc:
        log.warning("Calibration chart generation failed: %s", exc)

    return saved
