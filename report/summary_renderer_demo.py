"""
summary_renderer_demo.py
========================
Generates a sample executive summary PDF with dummy data so the layout
can be reviewed and tweaked before wiring to real analysis results.

NOTE: historical prototype — it has drifted behind the production renderer
(report/summary_renderer.py), which is the source of truth. This demo still
uses the older light palette (#0D1B2A / #C9A84C) and an S&P 500 benchmark;
production uses the dark brand palette (#0D1117 / #F0B429) and KOSPI/KOSDAQ.
Do not treat its styling as current.

Run:
    python3 report/summary_renderer_demo.py
Output:
    reports/SAMPLE_Exec_Sum.pdf
"""

import io
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.font_manager as fm
import matplotlib.dates as mdates
import numpy as np

# ── Korean font setup (matplotlib) ───────────────────────────────────────────
_KO_GOTHIC_TTF = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"
_KO_TTC        = "/System/Library/Fonts/AppleSDGothicNeo.ttc"
for _f in (_KO_TTC, _KO_GOTHIC_TTF):
    if os.path.exists(_f):
        fm.fontManager.addfont(_f)
matplotlib.rcParams["font.family"] = ["Apple SD Gothic Neo", "AppleGothic", "sans-serif"]

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import mm, cm
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame,
    Paragraph, Spacer, Table, TableStyle, Image, HRFlowable,
    KeepTogether,
)
from reportlab.platypus.flowables import BalancedColumns
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping

# ── Korean font setup (reportlab) ─────────────────────────────────────────────
_RL_FONT_REG  = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"
_RL_FONT_BOLD = "/System/Library/Fonts/Supplemental/AppleGothic.ttf"   # faked bold
_RL_FONT_UNI  = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"

pdfmetrics.registerFont(TTFont("KoreanSans",     _RL_FONT_REG))
pdfmetrics.registerFont(TTFont("KoreanSansBold", _RL_FONT_BOLD))
pdfmetrics.registerFont(TTFont("KoreanUnicode",  _RL_FONT_UNI))
addMapping("KoreanSans", 0, 0, "KoreanSans")
addMapping("KoreanSans", 1, 0, "KoreanSansBold")

KO  = "KoreanSans"
KOB = "KoreanSansBold"

# ── Colour palette (institutional dark-navy + gold accent) ────────────────────
C_NAVY      = colors.HexColor("#0D1B2A")   # header / footer bg
C_GOLD      = colors.HexColor("#C9A84C")   # accent line / highlight
C_BLUE_DARK = colors.HexColor("#1A3A5C")   # table header
C_BLUE_MID  = colors.HexColor("#2E6DA4")   # BUY badge bg
C_RED_MID   = colors.HexColor("#B03A2E")   # SELL badge bg
C_BLUE_LITE = colors.HexColor("#EBF5FB")   # RA column bg
C_GREEN_LITE= colors.HexColor("#EAFAF1")   # RN column bg
C_ORANGE_LT = colors.HexColor("#FDF2E9")   # bond row bg
C_GRAY_LITE = colors.HexColor("#F4F6F7")   # alt row bg
C_TEXT      = colors.HexColor("#1C2833")   # body text
C_SUBTEXT   = colors.HexColor("#5D6D7E")   # secondary text
W, H = A4                                  # 595 × 842 pt

MARGIN = 1.5 * cm


# ── Mock data ─────────────────────────────────────────────────────────────────

MOCK_COMPANIES = {
    "086900": "메디톡스",
    "145020": "휴젤",
    "214150": "클래시스",
    "214450": "파마리서치",
    "290650": "엘앤씨바이오",
}

MOCK_STOCKS = [
    # code, name, ra_sig, ra_conv, ra_wt, rn_sig, rn_conv, rn_wt
    ("086900", "메디톡스",    "SELL", 0.867, 0.000, "BUY",  0.733, 0.148),
    ("145020", "휴젤",        "BUY",  0.780, 0.320, "BUY",  0.810, 0.212),
    ("214150", "클래시스",    "BUY",  0.920, 0.460, "BUY",  0.880, 0.228),
    ("214450", "파마리서치",  "BUY",  0.640, 0.140, "BUY",  0.590, 0.132),
    ("290650", "엘앤씨바이오","SELL", 0.720, 0.000, "SELL", 0.680, 0.000),
]

MOCK_PORTFOLIOS = {
    "risk-averse":  {"equity": 0.60, "bond": 0.40, "sl": -0.05, "tp": 0.10, "taken": True},
    "risk-neutral": {"equity": 0.80, "bond": 0.20, "sl": -0.10, "tp": 0.20, "taken": True},
}

MOCK_NARRATIVE = (
    "Across the five-stock pool, the risk-averse and risk-neutral profiles reach strong "
    "agreement on 클래시스 and 휴젤, both unanimously rated BUY with convergence scores "
    "above 0.78, and these two names anchor the equity sleeves in both portfolios. "
    "파마리서치 clears the risk-neutral convergence threshold (0.59 ≥ 0.35) but falls "
    "short of the more demanding risk-averse bar (0.64 < 0.60), producing a meaningful "
    "profile divergence in allocation. 메디톡스 and 엘앤씨바이오 are excluded from equity "
    "positions in the risk-averse sleeve entirely — the former due to a SELL signal driven "
    "by structural earnings compression, the latter by below-threshold convergence. "
    "Investors should monitor botulinum-toxin regulatory risk in Korea as the primary "
    "cross-pool tail risk, given that three of the five holdings operate in adjacent "
    "aesthetic-medicine segments."
)

AS_OF = "2025-06-01"
RUN_DATE = "2026-05-01"


# ── Helper: matplotlib chart → ReportLab Image ───────────────────────────────

def _fig_to_rl_image(fig, width_pt, height_pt):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return Image(buf, width=width_pt, height=height_pt)


# ── Chart builders ────────────────────────────────────────────────────────────

def _make_pie(profile_label: str, stock_rows, bond_w: float) -> plt.Figure:
    """Single donut-style pie for one risk profile."""
    labels, sizes, clrs = [], [], []
    palette = ["#2E86C1", "#27AE60", "#8E44AD", "#E67E22", "#C0392B"]
    for i, (code, name, ra_sig, ra_conv, ra_wt, rn_sig, rn_conv, rn_wt) in enumerate(stock_rows):
        w = ra_wt if "Averse" in profile_label else rn_wt
        if w > 0:
            labels.append(f"{name}\n{w*100:.1f}%")
            sizes.append(w)
            clrs.append(palette[i % len(palette)])
    labels.append(f"Bond\n{bond_w*100:.0f}%")
    sizes.append(bond_w)
    clrs.append("#BDC3C7")

    fig, ax = plt.subplots(figsize=(3.8, 3.8))
    fig.patch.set_facecolor("white")
    wedges, texts = ax.pie(
        sizes, labels=labels, colors=clrs,
        startangle=90,
        wedgeprops={"edgecolor": "white", "linewidth": 2, "width": 0.55},
        textprops={"fontsize": 7.5},
    )
    eq = "60 / 40" if "Averse" in profile_label else "80 / 20"
    ax.text(0, 0, f"EQ/BD\n{eq}", ha="center", va="center",
            fontsize=8, fontweight="bold", color="#1C2833")
    ax.set_title(profile_label, fontsize=10, fontweight="bold", pad=8)
    fig.tight_layout(pad=0.4)
    return fig


def _make_backtest_chart() -> plt.Figure:
    """Mock cumulative-return + rolling-Sharpe chart for two profiles."""
    import pandas as pd

    np.random.seed(42)
    # Use a real date index so x-axis anchoring works the same as the live engine
    date_idx = pd.date_range("2025-06-01", periods=214, freq="B")  # ~214 trading days
    days     = len(date_idx)

    PORTFOLIO_COLOR = "#2E86C1"
    EW_COLOR        = "#E67E22"
    SP500_COLOR     = "#27AE60"
    WINDOW          = 30

    def _cum(mu, sigma):
        r = np.random.normal(mu, sigma, days)
        return pd.Series(np.cumprod(1 + r) - 1, index=date_idx)

    def _rolling_sharpe(cum_series, w=WINDOW):
        """Returns NaN for the first w-1 days, then rolling Sharpe."""
        r = cum_series.pct_change().fillna(0)
        rs = (r.rolling(w).mean() / r.rolling(w).std()) * np.sqrt(252)
        # First w-1 values are NaN → left side of x-axis stays blank
        return rs

    ra_cum  = _cum(0.0003, 0.009)
    rn_cum  = _cum(0.0006, 0.013)
    ew_cum  = _cum(0.0002, 0.011)
    sp5_cum = _cum(0.0004, 0.010)

    fig, axes = plt.subplots(2, 2, figsize=(13, 7), sharex="col")
    fig.patch.set_facecolor("white")
    fig.suptitle("Backtest Results  —  2025-06-01 → 2026-01-01",
                 fontsize=12, fontweight="bold")

    x_start = date_idx[0]
    x_end   = date_idx[-1]

    for col, (cum, label) in enumerate([
        (ra_cum, "Risk-Averse"),
        (rn_cum, "Risk-Neutral"),
    ]):
        ax_top = axes[0, col]
        ax_bot = axes[1, col]

        # ── Cumulative return ─────────────────────────────────────────────
        ax_top.plot(date_idx, cum,     color=PORTFOLIO_COLOR, lw=2.0, label=label)
        ax_top.plot(date_idx, ew_cum,  color=EW_COLOR,        lw=1.6, label="EW Benchmark")
        ax_top.plot(date_idx, sp5_cum, color=SP500_COLOR,     lw=1.6, label="S&P 500")
        ax_top.axhline(0, color="#555555", lw=0.7)
        ax_top.yaxis.set_major_formatter(plt.FuncFormatter(lambda y, _: f"{y:.0%}"))
        ax_top.set_title(f"{label} — Cumulative Return", fontsize=10, fontweight="bold")
        ax_top.set_ylabel("Cumulative Return", fontsize=9)
        ax_top.set_xlim(x_start, x_end)
        ax_top.set_facecolor("#FAFAFA")
        ax_top.grid(axis="y", color="#e0e0e0", lw=0.6)
        ax_top.legend(fontsize=8, loc="upper left", framealpha=0.85, edgecolor="#cccccc")

        # ── Rolling Sharpe (NaN for first 30 days → blank left margin) ───
        rs     = _rolling_sharpe(cum)
        rs_ew  = _rolling_sharpe(ew_cum)
        rs_sp5 = _rolling_sharpe(sp5_cum)
        ax_bot.plot(date_idx, rs,     color=PORTFOLIO_COLOR, lw=2.0, label=label)
        ax_bot.plot(date_idx, rs_ew,  color=EW_COLOR,        lw=1.6, label="EW Benchmark")
        ax_bot.plot(date_idx, rs_sp5, color=SP500_COLOR,     lw=1.6, label="S&P 500")
        ax_bot.axhline(0, color="#555555", lw=0.7)
        ax_bot.set_title(f"{label} — Rolling Sharpe (30d)", fontsize=10, fontweight="bold")
        ax_bot.set_ylabel("Sharpe Ratio", fontsize=9)
        ax_bot.set_xlim(x_start, x_end)
        ax_bot.set_facecolor("#FAFAFA")
        ax_bot.grid(axis="y", color="#e0e0e0", lw=0.6)
        ax_bot.legend(fontsize=8, loc="upper left", framealpha=0.85, edgecolor="#cccccc")
        ax_bot.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax_bot.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax_bot.xaxis.get_majorticklabels(), rotation=30, fontsize=7)

    fig.tight_layout(rect=[0, 0, 1, 0.96])
    return fig


# ── Page template: header + footer ───────────────────────────────────────────

def _draw_header_footer(canvas, doc):
    canvas.saveState()
    # ── Navy header bar ───────────────────────────────────────────────────
    canvas.setFillColor(C_NAVY)
    canvas.rect(0, H - 2.4 * cm, W, 2.4 * cm, fill=1, stroke=0)

    # Gold accent stripe
    canvas.setFillColor(C_GOLD)
    canvas.rect(0, H - 2.55 * cm, W, 0.15 * cm, fill=1, stroke=0)

    # Header text
    canvas.setFillColor(colors.white)
    canvas.setFont(KOB, 13)
    canvas.drawString(MARGIN, H - 1.35 * cm, "K-AlphaAgents  —  Portfolio Executive Summary")

    canvas.setFont(KO, 8)
    canvas.setFillColor(C_GOLD)
    canvas.drawRightString(W - MARGIN, H - 0.9 * cm, f"Data as of  {AS_OF}   |   Generated  {RUN_DATE}")
    canvas.drawRightString(W - MARGIN, H - 1.45 * cm, "CONFIDENTIAL  —  For Internal Use Only")

    # ── Navy footer bar ───────────────────────────────────────────────────
    canvas.setFillColor(C_NAVY)
    canvas.rect(0, 0, W, 1.2 * cm, fill=1, stroke=0)

    canvas.setFillColor(C_GOLD)
    canvas.rect(0, 1.2 * cm, W, 0.1 * cm, fill=1, stroke=0)

    canvas.setFillColor(colors.white)
    canvas.setFont(KO, 7.5)
    canvas.drawString(MARGIN, 0.42 * cm,
                      "K-AlphaAgents  |  Korean Equity Multi-Agent Analysis System")
    canvas.drawRightString(W - MARGIN, 0.42 * cm, f"Page {doc.page}")

    canvas.restoreState()


# ── Styles ────────────────────────────────────────────────────────────────────

def _styles():
    base = getSampleStyleSheet()

    def ps(name, parent="Normal", **kw):
        return ParagraphStyle(name, parent=base[parent], **kw)

    return {
        "section":   ps("section",   fontSize=11, textColor=C_NAVY,
                         fontName=KOB, spaceAfter=4),
        "body":      ps("body",      fontSize=8.5, textColor=C_TEXT,
                         fontName=KO, leading=13.5, spaceAfter=4,
                         alignment=TA_JUSTIFY),
        "caption":   ps("caption",   fontSize=7.5, textColor=C_SUBTEXT,
                         fontName=KO, leading=11, spaceAfter=2),
        "cell_hdr":  ps("cell_hdr",  fontSize=8, textColor=colors.white,
                         fontName=KOB, alignment=TA_CENTER),
        "cell":      ps("cell",      fontSize=8, textColor=C_TEXT,
                         fontName=KO, alignment=TA_CENTER, leading=11),
        "cell_l":    ps("cell_l",    fontSize=8, textColor=C_TEXT,
                         fontName=KO, alignment=TA_LEFT, leading=11),
        "buy":       ps("buy",       fontSize=8, textColor=colors.white,
                         fontName=KOB, alignment=TA_CENTER),
        "sell":      ps("sell",      fontSize=8, textColor=colors.white,
                         fontName=KOB, alignment=TA_CENTER),
        "metric_lbl":ps("metric_lbl",fontSize=8, textColor=C_SUBTEXT,
                         fontName=KO, alignment=TA_LEFT),
        "metric_val":ps("metric_val",fontSize=9, textColor=C_TEXT,
                         fontName=KOB, alignment=TA_RIGHT),
    }


def _gold_rule():
    return HRFlowable(width="100%", thickness=1.2, color=C_GOLD, spaceAfter=6)


def _section_title(text, sty):
    return [Paragraph(text.upper(), sty["section"]), _gold_rule()]


# ── Signal badge ──────────────────────────────────────────────────────────────

def _badge(signal: str, sty) -> Paragraph:
    if signal == "BUY":
        return Paragraph(
            f'<font color="white"><b>{signal}</b></font>', sty["buy"]
        )
    return Paragraph(
        f'<font color="white"><b>{signal}</b></font>', sty["sell"]
    )


def _badge_bg(signal: str):
    return C_BLUE_MID if signal == "BUY" else C_RED_MID


# ── Build PDF ─────────────────────────────────────────────────────────────────

def build_sample_pdf(out_path: str) -> str:
    sty  = _styles()
    usable_w = W - 2 * MARGIN

    doc = BaseDocTemplate(
        out_path,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=3.0 * cm, bottomMargin=1.8 * cm,
        title="K-AlphaAgents — Portfolio Executive Summary (SAMPLE)",
        author="K-AlphaAgents",
    )
    frame = Frame(MARGIN, 1.8 * cm, usable_w, H - 4.8 * cm, id="main")
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame],
                                       onPage=_draw_header_footer)])

    story = []

    # ═══════════════════════════════════════════════════════════
    # PAGE 1 — SIGNAL TABLE + PORTFOLIO ALLOCATION
    # ═══════════════════════════════════════════════════════════

    # ── Section 1: Stock Signals ─────────────────────────────────────────────
    story += _section_title("1.  Stock Signals & Convergence", sty)

    col_w = [1.8*cm, 3.2*cm,
             2.0*cm, 1.8*cm, 1.8*cm,
             2.0*cm, 1.8*cm, 1.8*cm]

    hdr = [
        Paragraph("Ticker",   sty["cell_hdr"]),
        Paragraph("Company",  sty["cell_hdr"]),
        Paragraph("RA Signal",sty["cell_hdr"]),
        Paragraph("RA Conv",  sty["cell_hdr"]),
        Paragraph("RA Wt",    sty["cell_hdr"]),
        Paragraph("RN Signal",sty["cell_hdr"]),
        Paragraph("RN Conv",  sty["cell_hdr"]),
        Paragraph("RN Wt",    sty["cell_hdr"]),
    ]
    tbl_data = [hdr]
    tbl_style_cmds = [
        ("BACKGROUND", (0,0), (-1,0), C_BLUE_DARK),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, C_GRAY_LITE]),
        ("BACKGROUND", (2,1), (4,-1), C_BLUE_LITE),
        ("BACKGROUND", (5,1), (7,-1), C_GREEN_LITE),
        ("GRID",       (0,0), (-1,-1), 0.4, colors.HexColor("#D5D8DC")),
        ("LINEABOVE",  (0,0), (-1,0), 1.5, C_BLUE_DARK),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",(0,0),(-1,-1), 5),
        ("VALIGN",     (0,0), (-1,-1), "MIDDLE"),
    ]

    ra_bw = MOCK_PORTFOLIOS["risk-averse"]["bond"]
    rn_bw = MOCK_PORTFOLIOS["risk-neutral"]["bond"]

    for i, (code, name, ra_sig, ra_conv, ra_wt, rn_sig, rn_conv, rn_wt) in \
            enumerate(MOCK_STOCKS, start=1):
        row = [
            Paragraph(code, sty["cell"]),
            Paragraph(name, sty["cell_l"]),
            _badge(ra_sig, sty),
            Paragraph(f"{ra_conv:.3f}", sty["cell"]),
            Paragraph(f"{ra_wt*100:.1f}%" if ra_wt > 0 else "—", sty["cell"]),
            _badge(rn_sig, sty),
            Paragraph(f"{rn_conv:.3f}", sty["cell"]),
            Paragraph(f"{rn_wt*100:.1f}%" if rn_wt > 0 else "—", sty["cell"]),
        ]
        tbl_data.append(row)
        tbl_style_cmds.append(("BACKGROUND", (2, i), (2, i), _badge_bg(ra_sig)))
        tbl_style_cmds.append(("BACKGROUND", (5, i), (5, i), _badge_bg(rn_sig)))

    # Bond row
    bond_row = [
        Paragraph("114260", sty["cell"]),
        Paragraph("KODEX 국고채3년", sty["cell_l"]),
        Paragraph("—", sty["cell"]),
        Paragraph("—", sty["cell"]),
        Paragraph(f"{ra_bw*100:.0f}%", sty["cell"]),
        Paragraph("—", sty["cell"]),
        Paragraph("—", sty["cell"]),
        Paragraph(f"{rn_bw*100:.0f}%", sty["cell"]),
    ]
    tbl_data.append(bond_row)
    bond_row_idx = len(tbl_data) - 1
    tbl_style_cmds.append(
        ("BACKGROUND", (0, bond_row_idx), (-1, bond_row_idx), C_ORANGE_LT)
    )
    tbl_style_cmds.append(
        ("FONT", (0, bond_row_idx), (-1, bond_row_idx), "Helvetica-Oblique", 8)
    )

    sig_table = Table(tbl_data, colWidths=col_w, repeatRows=1)
    sig_table.setStyle(TableStyle(tbl_style_cmds))
    story.append(sig_table)
    story.append(Spacer(1, 0.5*cm))

    # ── Section 2: Portfolio Allocation ─────────────────────────────────────
    story += _section_title("2.  Portfolio Allocation", sty)

    # Profile summary cards (side by side)
    profile_rows = []
    for p_label, p_key, eq_pct, bd_pct in [
        ("Risk-Averse",  "risk-averse",  "60%", "40%"),
        ("Risk-Neutral", "risk-neutral", "80%", "20%"),
    ]:
        card_data = [
            [Paragraph(p_label, ParagraphStyle("h", fontName=KOB,
                                                fontSize=9, textColor=colors.white))],
            [Paragraph(f"Equity  {eq_pct}   Bond  {bd_pct}", sty["cell"])],
        ]
        card = Table(card_data, colWidths=[usable_w/2 - 0.3*cm])
        card.setStyle(TableStyle([
            ("BACKGROUND",    (0,0),(0,0), C_BLUE_DARK),
            ("BACKGROUND",    (0,1),(0,2), C_GRAY_LITE),
            ("TOPPADDING",    (0,0),(-1,-1), 5),
            ("BOTTOMPADDING", (0,0),(-1,-1), 5),
            ("LEFTPADDING",   (0,0),(-1,-1), 8),
            ("BOX",           (0,0),(-1,-1), 0.6, C_BLUE_DARK),
        ]))
        profile_rows.append(card)

    card_table = Table([profile_rows],
                       colWidths=[usable_w/2 - 0.15*cm, usable_w/2 - 0.15*cm],
                       hAlign="LEFT")
    card_table.setStyle(TableStyle([("LEFTPADDING",  (0,0),(-1,-1), 0),
                                    ("RIGHTPADDING", (0,0),(-1,-1), 6)]))
    story.append(card_table)
    story.append(Spacer(1, 0.5*cm))

    # Pie charts (donut)
    pie_l = _make_pie("Risk-Averse",  MOCK_STOCKS, ra_bw)
    pie_r = _make_pie("Risk-Neutral", MOCK_STOCKS, rn_bw)
    pw = usable_w / 2 - 0.4*cm
    ph = pw * 0.9

    pie_tbl = Table(
        [[_fig_to_rl_image(pie_l, pw, ph), _fig_to_rl_image(pie_r, pw, ph)]],
        colWidths=[pw + 0.4*cm, pw],
    )
    pie_tbl.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"MIDDLE"),
                                  ("LEFTPADDING",(0,0),(-1,-1),0),
                                  ("RIGHTPADDING",(0,0),(-1,-1),0)]))
    story.append(pie_tbl)

    # ═══════════════════════════════════════════════════════════
    # PAGE 2 — NARRATIVE + BACKTEST
    # ═══════════════════════════════════════════════════════════
    from reportlab.platypus import PageBreak
    story.append(PageBreak())

    # ── Section 3: Cross-Profile Narrative ──────────────────────────────────
    story += _section_title("3.  Cross-Profile Investment Narrative", sty)
    story.append(Paragraph(MOCK_NARRATIVE, sty["body"]))
    story.append(Spacer(1, 0.6*cm))

    # ── Section 4: Key Metrics ────────────────────────────────────────────────
    story += _section_title("4.  Portfolio Metrics at a Glance", sty)

    metrics = [
        ["Metric",              "Risk-Averse",  "Risk-Neutral"],
        ["Stocks in Equity",    "3 of 5",       "4 of 5"],
        ["Avg Convergence",     "0.780",        "0.724"],
        ["Equity Allocation",   "60%",          "80%"],
        ["Bond Allocation",     "40%",          "20%"],
    ]
    m_col_w = [usable_w * 0.40, usable_w * 0.30, usable_w * 0.30]
    m_tbl = Table(metrics, colWidths=m_col_w)
    m_tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0,0), (-1,0), C_BLUE_DARK),
        ("TEXTCOLOR",      (0,0), (-1,0), colors.white),
        ("FONTNAME",       (0,0), (-1,-1), KO),
        ("FONTNAME",       (0,0), (-1,0), KOB),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, C_GRAY_LITE]),
        ("BACKGROUND",     (1,1), (1,-1), C_BLUE_LITE),
        ("BACKGROUND",     (2,1), (2,-1), C_GREEN_LITE),
        ("GRID",           (0,0), (-1,-1), 0.4, colors.HexColor("#D5D8DC")),
        ("FONTSIZE",       (0,0), (-1,-1), 8.5),
        ("TOPPADDING",     (0,0), (-1,-1), 5),
        ("BOTTOMPADDING",  (0,0), (-1,-1), 5),
        ("ALIGN",          (1,0), (-1,-1), "CENTER"),
    ]))
    story.append(m_tbl)
    story.append(Spacer(1, 0.6*cm))

    # ── Section 5: Backtest ───────────────────────────────────────────────────
    story += _section_title("5.  Backtest Results  (2025-06-01 → 2026-01-01)", sty)

    bt_fig = _make_backtest_chart()
    bt_img = _fig_to_rl_image(bt_fig, usable_w, usable_w * 0.52)
    story.append(bt_img)
    story.append(Spacer(1, 0.2*cm))
    story.append(Paragraph(
        "Benchmarks: equal-weight of all 5 analysed stocks (EW Benchmark, grey dash-dot) "
        "and S&P 500 (black dashed).  Rolling Sharpe computed over a 30-trading-day window.",
        sty["caption"]
    ))

    # ── Build ─────────────────────────────────────────────────────────────────
    doc.build(story)
    return out_path


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    os.makedirs("reports", exist_ok=True)
    out = os.path.join("reports", "SAMPLE_Exec_Sum.pdf")
    path = build_sample_pdf(out)
    print(f"\n  Sample PDF generated → {path}")
    print("  Open it and let me know what you'd like to change.\n")
