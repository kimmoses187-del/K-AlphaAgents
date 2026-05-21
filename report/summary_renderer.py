"""
report/summary_renderer.py
==========================
Produces the executive summary PDF from real analysis results.
Design is identical to summary_renderer_demo.py; all mock data
replaced with live inputs from the orchestrator.

Public API
----------
build_pdf(pdf_path, company_names, portfolios, narrative,
          as_of_date, backtest_results) -> str
"""

import io
import os
from datetime import datetime
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import matplotlib.dates as mdates
import numpy as np

# ── Korean font — cross-platform lookup ──────────────────────────────────────
_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _find_font(*candidates: str) -> str:
    """Return the first existing font path, or raise FileNotFoundError."""
    for path in candidates:
        if os.path.exists(path):
            return path
    raise FileNotFoundError(
        "No Korean font found. Tried:\n" + "\n".join(f"  {p}" for p in candidates)
    )

_KOREAN_FONT = _find_font(
    # Linux (Render): apt-get install -y fonts-nanum
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/nanum/NanumGothic.ttf",
    # macOS system fonts (local dev)
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
    "/System/Library/Fonts/AppleSDGothicNeo.ttc",
)

_UNICODE_FONT = _find_font(
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/nanum/NanumGothic.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",
)

# ── matplotlib Korean font ────────────────────────────────────────────────────
fm.fontManager.addfont(_KOREAN_FONT)
matplotlib.rcParams["font.family"] = ["NanumGothic", "Apple SD Gothic Neo",
                                       "AppleGothic", "sans-serif"]

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    BaseDocTemplate, PageTemplate, Frame, PageBreak,
    Paragraph, Spacer, Table, TableStyle, Image, HRFlowable,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.fonts import addMapping

# ── reportlab Korean font ─────────────────────────────────────────────────────
pdfmetrics.registerFont(TTFont("KoreanSans",     _KOREAN_FONT))
pdfmetrics.registerFont(TTFont("KoreanSansBold", _KOREAN_FONT))
pdfmetrics.registerFont(TTFont("KoreanUnicode",  _UNICODE_FONT))
addMapping("KoreanSans", 0, 0, "KoreanSans")
addMapping("KoreanSans", 1, 0, "KoreanSansBold")

KO  = "KoreanSans"
KOB = "KoreanSansBold"

# ── Colour palette ────────────────────────────────────────────────────────────
C_NAVY      = colors.HexColor("#0D1B2A")
C_GOLD      = colors.HexColor("#C9A84C")
C_BLUE_DARK = colors.HexColor("#1A3A5C")
C_BLUE_MID  = colors.HexColor("#2E6DA4")
C_RED_MID   = colors.HexColor("#B03A2E")
C_BLUE_LITE = colors.HexColor("#EBF5FB")
C_GREEN_LITE= colors.HexColor("#EAFAF1")
C_ORANGE_LT = colors.HexColor("#FDF2E9")
C_GRAY_LITE = colors.HexColor("#F4F6F7")
C_TEXT      = colors.HexColor("#1C2833")
C_SUBTEXT   = colors.HexColor("#5D6D7E")

W, H   = A4
MARGIN = 1.5 * cm

BOND_TICKER = "114260"
BOND_NAME   = "KODEX 국고채3년"

# ── Internal helpers ──────────────────────────────────────────────────────────

def _fig_to_rl_image(fig, width_pt: float, height_pt: float) -> Image:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=180, bbox_inches="tight")
    buf.seek(0)
    plt.close(fig)
    return Image(buf, width=width_pt, height=height_pt)


def _styles() -> dict:
    base = getSampleStyleSheet()

    def ps(name, **kw):
        return ParagraphStyle(name, parent=base["Normal"], **kw)

    return {
        "section":    ps("section",   fontSize=11, textColor=C_NAVY,
                          fontName=KOB, spaceAfter=4),
        "body":       ps("body",      fontSize=8.5, textColor=C_TEXT,
                          fontName=KO, leading=13.5, spaceAfter=4,
                          alignment=TA_JUSTIFY),
        "caption":    ps("caption",   fontSize=7.5, textColor=C_SUBTEXT,
                          fontName=KO, leading=11, spaceAfter=2),
        "cell_hdr":   ps("cell_hdr",  fontSize=8, textColor=colors.white,
                          fontName=KOB, alignment=TA_CENTER),
        "cell":       ps("cell",      fontSize=8, textColor=C_TEXT,
                          fontName=KO, alignment=TA_CENTER, leading=11),
        "cell_l":     ps("cell_l",    fontSize=8, textColor=C_TEXT,
                          fontName=KO, alignment=TA_LEFT, leading=11),
        "buy":        ps("buy",       fontSize=8, textColor=colors.white,
                          fontName=KOB, alignment=TA_CENTER),
        "sell":       ps("sell",      fontSize=8, textColor=colors.white,
                          fontName=KOB, alignment=TA_CENTER),
    }


def _gold_rule() -> HRFlowable:
    return HRFlowable(width="100%", thickness=1.2, color=C_GOLD, spaceAfter=6)


def _section_title(text: str, sty: dict) -> list:
    return [Paragraph(text.upper(), sty["section"]), _gold_rule()]


def _badge(signal: str, sty: dict) -> Paragraph:
    style = sty["buy"] if signal == "BUY" else sty["sell"]
    return Paragraph(f"<b>{signal}</b>", style)


def _badge_bg(signal: str):
    return C_BLUE_MID if signal == "BUY" else C_RED_MID


def _make_header_footer(as_of_str: str, run_str: str):
    """Return a page-callback closure with dates baked in."""
    def _callback(canvas, doc):
        canvas.saveState()

        # Navy header bar
        canvas.setFillColor(C_NAVY)
        canvas.rect(0, H - 2.4 * cm, W, 2.4 * cm, fill=1, stroke=0)
        canvas.setFillColor(C_GOLD)
        canvas.rect(0, H - 2.55 * cm, W, 0.15 * cm, fill=1, stroke=0)

        canvas.setFillColor(colors.white)
        canvas.setFont(KOB, 13)
        canvas.drawString(MARGIN, H - 1.35 * cm,
                          "K-AlphaAgents  —  Portfolio Executive Summary")
        canvas.setFont(KO, 8)
        canvas.setFillColor(C_GOLD)
        canvas.drawRightString(W - MARGIN, H - 0.9 * cm,
                               f"Data as of  {as_of_str}   |   Generated  {run_str}")
        canvas.drawRightString(W - MARGIN, H - 1.45 * cm,
                               "CONFIDENTIAL  —  For Internal Use Only")

        # Navy footer bar
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
    return _callback


# ── Chart builders ────────────────────────────────────────────────────────────

def _make_pie(profile_label: str, company_names: dict,
              portfolios: dict, profile_key: str) -> plt.Figure:
    """Donut pie for one risk profile, built from live portfolio data."""
    po      = portfolios[profile_key]
    allocs  = po["stock_allocations"]
    bond_w  = po["bond_weight"]
    palette = ["#2E86C1", "#27AE60", "#8E44AD", "#E67E22", "#C0392B"]

    labels, sizes, clrs = [], [], []
    for i, (code, name) in enumerate(company_names.items()):
        w = allocs[code]["weight"]
        if w > 0:
            labels.append(f"{name}\n{w*100:.1f}%")
            sizes.append(w)
            clrs.append(palette[i % len(palette)])

    labels.append(f"Bond\n{bond_w*100:.0f}%")
    sizes.append(bond_w)
    clrs.append("#BDC3C7")

    eq_pct = int(po["equity_weight"] * 100)
    bd_pct = int(bond_w * 100)

    fig, ax = plt.subplots(figsize=(3.8, 3.8))
    fig.patch.set_facecolor("white")
    ax.pie(sizes, labels=labels, colors=clrs, startangle=90,
           wedgeprops={"edgecolor": "white", "linewidth": 2, "width": 0.55},
           textprops={"fontsize": 7.5})
    ax.text(0, 0, f"EQ/BD\n{eq_pct} / {bd_pct}",
            ha="center", va="center", fontsize=8, fontweight="bold", color="#1C2833")
    ax.set_title(profile_label, fontsize=10, fontweight="bold", pad=8)
    fig.tight_layout(pad=0.4)
    return fig


def _make_backtest_fig(backtest_results: dict) -> plt.Figure:
    """Build the 2×2 backtest chart from live BacktestEngine results."""
    from backtest.engine import plot_two_profiles
    fig = plot_two_profiles(
        averse_engine=backtest_results["risk-averse"],
        neutral_engine=backtest_results["risk-neutral"],
        company_name="",       # title already on the PDF page
        save_path=None,
        kospi_cum=backtest_results.get("kospi_cum"),
        kospi_rolling=backtest_results.get("kospi_rolling"),
        kosdaq_cum=backtest_results.get("kosdaq_cum"),
        kosdaq_rolling=backtest_results.get("kosdaq_rolling"),
    )
    return fig


# ── Section builders ──────────────────────────────────────────────────────────

def _build_signal_table(company_names: dict, portfolios: dict,
                        sty: dict, usable_w: float) -> Table:
    col_w = [1.8*cm, 3.2*cm,
             2.0*cm, 1.8*cm, 1.8*cm,
             2.0*cm, 1.8*cm, 1.8*cm]

    hdr = [Paragraph(t, sty["cell_hdr"]) for t in
           ["Ticker", "Company",
            "RA Signal", "RA Conv", "RA Wt",
            "RN Signal", "RN Conv", "RN Wt"]]
    data   = [hdr]
    cmds   = [
        ("BACKGROUND",    (0, 0), (-1, 0), C_BLUE_DARK),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_GRAY_LITE]),
        ("BACKGROUND",    (2, 1), (4, -1), C_BLUE_LITE),
        ("BACKGROUND",    (5, 1), (7, -1), C_GREEN_LITE),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#D5D8DC")),
        ("LINEABOVE",     (0, 0), (-1, 0), 1.5, C_BLUE_DARK),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
    ]

    ra_po = portfolios["risk-averse"]
    rn_po = portfolios["risk-neutral"]

    for i, (code, name) in enumerate(company_names.items(), start=1):
        ra = ra_po["stock_allocations"][code]
        rn = rn_po["stock_allocations"][code]
        row = [
            Paragraph(code, sty["cell"]),
            Paragraph(name, sty["cell_l"]),
            _badge(ra["signal"], sty),
            Paragraph(f"{ra['conviction']:.3f}", sty["cell"]),
            Paragraph(f"{ra['weight']*100:.1f}%" if ra["weight"] > 0 else "—", sty["cell"]),
            _badge(rn["signal"], sty),
            Paragraph(f"{rn['conviction']:.3f}", sty["cell"]),
            Paragraph(f"{rn['weight']*100:.1f}%" if rn["weight"] > 0 else "—", sty["cell"]),
        ]
        data.append(row)
        cmds.append(("BACKGROUND", (2, i), (2, i), _badge_bg(ra["signal"])))
        cmds.append(("BACKGROUND", (5, i), (5, i), _badge_bg(rn["signal"])))

    # Bond row
    ra_bw = ra_po["bond_weight"]
    rn_bw = rn_po["bond_weight"]
    bond_row = [
        Paragraph(BOND_TICKER, sty["cell"]),
        Paragraph(BOND_NAME, sty["cell_l"]),
        Paragraph("—", sty["cell"]), Paragraph("—", sty["cell"]),
        Paragraph(f"{ra_bw*100:.0f}%", sty["cell"]),
        Paragraph("—", sty["cell"]), Paragraph("—", sty["cell"]),
        Paragraph(f"{rn_bw*100:.0f}%", sty["cell"]),
    ]
    bond_idx = len(data)
    data.append(bond_row)
    cmds.append(("BACKGROUND", (0, bond_idx), (-1, bond_idx), C_ORANGE_LT))
    cmds.append(("FONT",       (0, bond_idx), (-1, bond_idx), KO, 8))

    tbl = Table(data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle(cmds))
    return tbl


def _build_profile_cards(portfolios: dict, sty: dict, usable_w: float) -> Table:
    cards = []
    for label, key in [("Risk-Averse", "risk-averse"), ("Risk-Neutral", "risk-neutral")]:
        po     = portfolios[key]
        eq_pct = f"{po['equity_weight']*100:.0f}%"
        bd_pct = f"{po['bond_weight']*100:.0f}%"
        card_data = [
            [Paragraph(label, ParagraphStyle("ch", fontName=KOB,
                                              fontSize=9, textColor=colors.white))],
            [Paragraph(f"Equity  {eq_pct}   Bond  {bd_pct}", sty["cell"])],
        ]
        card = Table(card_data, colWidths=[usable_w / 2 - 0.3 * cm])
        card.setStyle(TableStyle([
            ("BACKGROUND",    (0, 0), (0, 0), C_BLUE_DARK),
            ("BACKGROUND",    (0, 1), (0, 1), C_GRAY_LITE),
            ("TOPPADDING",    (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("LEFTPADDING",   (0, 0), (-1, -1), 8),
            ("BOX",           (0, 0), (-1, -1), 0.6, C_BLUE_DARK),
        ]))
        cards.append(card)

    tbl = Table([cards],
                colWidths=[usable_w / 2 - 0.15 * cm, usable_w / 2 - 0.15 * cm],
                hAlign="LEFT")
    tbl.setStyle(TableStyle([("LEFTPADDING",  (0, 0), (-1, -1), 0),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 6)]))
    return tbl


def _build_metrics_table(company_names: dict, portfolios: dict,
                          sty: dict, usable_w: float) -> Table:
    n_total = len(company_names)
    ra_po   = portfolios["risk-averse"]
    rn_po   = portfolios["risk-neutral"]

    ra_allocs = ra_po["stock_allocations"]
    rn_allocs = rn_po["stock_allocations"]

    ra_in_equity = [c for c, a in ra_allocs.items() if a["weight"] > 0]
    rn_in_equity = [c for c, a in rn_allocs.items() if a["weight"] > 0]

    def avg_conv(allocs, codes):
        if not codes:
            return "—"
        return f"{sum(allocs[c]['conviction'] for c in codes) / len(codes):.3f}"

    rows = [
        ["Metric",           "Risk-Averse",                         "Risk-Neutral"],
        ["Stocks in Equity", f"{len(ra_in_equity)} of {n_total}",   f"{len(rn_in_equity)} of {n_total}"],
        ["Avg Conviction",   avg_conv(ra_allocs, ra_in_equity),      avg_conv(rn_allocs, rn_in_equity)],
        ["Equity Allocation",f"{ra_po['equity_weight']*100:.0f}%",  f"{rn_po['equity_weight']*100:.0f}%"],
        ["Bond Allocation",  f"{ra_po['bond_weight']*100:.0f}%",    f"{rn_po['bond_weight']*100:.0f}%"],
    ]
    col_w = [usable_w * 0.40, usable_w * 0.30, usable_w * 0.30]
    tbl   = Table(rows, colWidths=col_w)
    tbl.setStyle(TableStyle([
        ("BACKGROUND",     (0, 0), (-1, 0), C_BLUE_DARK),
        ("TEXTCOLOR",      (0, 0), (-1, 0), colors.white),
        ("FONTNAME",       (0, 0), (-1, -1), KO),
        ("FONTNAME",       (0, 0), (-1,  0), KOB),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, C_GRAY_LITE]),
        ("BACKGROUND",     (1, 1), (1, -1), C_BLUE_LITE),
        ("BACKGROUND",     (2, 1), (2, -1), C_GREEN_LITE),
        ("GRID",           (0, 0), (-1, -1), 0.4, colors.HexColor("#D5D8DC")),
        ("FONTSIZE",       (0, 0), (-1, -1), 8.5),
        ("TOPPADDING",     (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING",  (0, 0), (-1, -1), 5),
        ("ALIGN",          (1, 0), (-1, -1), "CENTER"),
    ]))
    return tbl


# ── Rebalancing history table ─────────────────────────────────────────────────

def _build_rebalance_history(
    company_names: dict,
    quarterly_log: list,
    sty: dict,
    usable_w: float,
    profile: str,
) -> Table:
    """
    Build a compact rebalancing history table for one risk profile.

    Rows = quarters (Q1 / Q2 / Q3 …)
    Cols = Quarter date | Stock1 | Stock2 | … | Bond
    Cell = "BUY 18.3%" in blue, "SELL —" in red, or "— —" if not in pool
    """
    profile_label = "Risk-Averse" if profile == "risk-averse" else "Risk-Neutral"
    codes  = list(company_names.keys())
    names  = [company_names[c] for c in codes]

    # ── Header row ──
    hdr_style = ParagraphStyle("rh", fontName=KOB, fontSize=7.5,
                                textColor=colors.white, alignment=1)
    hdr = (
        [Paragraph("Quarter", hdr_style)]
        + [Paragraph(n, hdr_style) for n in names]
        + [Paragraph(BOND_NAME, hdr_style)]
    )

    # ── Data rows ──
    buy_sty  = ParagraphStyle("rb", fontName=KOB, fontSize=7.5,
                               textColor=colors.HexColor("#1A5276"), alignment=1)
    sell_sty = ParagraphStyle("rs", fontName=KO,  fontSize=7.5,
                               textColor=colors.HexColor("#922B21"), alignment=1)
    cell_sty = ParagraphStyle("rc", fontName=KO,  fontSize=7.5,
                               textColor=C_TEXT, alignment=1)

    rows = [hdr]
    style_cmds = [
        ("BACKGROUND",    (0, 0), (-1, 0),  C_NAVY),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#BDC3C7")),
        ("ROWBACKGROUNDS",(0, 1), (-1, -1), [colors.white, C_GRAY_LITE]),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 4),
    ]

    for q in quarterly_log:
        q_label  = f"Q{q['quarter']}\n{q['start'].strftime('%Y-%m-%d')}"
        allocs   = q["portfolios"][profile]["stock_allocations"]
        bond_w   = q["portfolios"][profile]["bond_weight"]

        row = [Paragraph(q_label, cell_sty)]
        for code in codes:
            alloc = allocs.get(code, {})
            sig   = alloc.get("signal", "—")
            wt    = alloc.get("weight", 0.0)
            if sig == "BUY" and wt > 0:
                row.append(Paragraph(f"BUY\n{wt*100:.1f}%", buy_sty))
            elif sig == "SELL":
                row.append(Paragraph("SELL\n—", sell_sty))
            else:
                row.append(Paragraph("—", cell_sty))
        row.append(Paragraph(f"{bond_w*100:.0f}%", cell_sty))
        rows.append(row)

    n_cols    = 2 + len(codes)
    date_w    = 1.6 * cm
    bond_w_pt = 1.8 * cm
    stock_w   = (usable_w - date_w - bond_w_pt) / max(len(codes), 1)
    col_widths = [date_w] + [stock_w] * len(codes) + [bond_w_pt]

    tbl = Table(rows, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle(style_cmds))
    return tbl


# ── Public entry point ────────────────────────────────────────────────────────

def build_pdf(
    pdf_path: str,
    company_names: dict,
    portfolios: dict,
    narrative: str,
    as_of_date: datetime,
    backtest_results: Optional[dict] = None,
    quarterly_log: Optional[list] = None,
) -> str:
    """
    Build the executive summary PDF and save to pdf_path.

    Parameters
    ----------
    pdf_path         : output file path
    company_names    : {stock_code: company_name}
    portfolios       : output of portfolio_agent.construct_portfolio()
                       (used for final-quarter allocation section)
    narrative        : LLM-generated cross-profile text (from orchestrator)
    as_of_date       : analysis as-of date (backtest start or rebalancing start)
    backtest_results : output of run_backtest() / run_rebalanced_backtest(), or None
    quarterly_log    : list of quarterly dicts from RebalanceEngine.run().
                       If provided, a rebalancing history table is added (Page 1)
                       and the signal table shows the final-quarter signals.

    Returns
    -------
    pdf_path
    """
    is_rebalanced = quarterly_log is not None and len(quarterly_log) > 1

    sty       = _styles()
    usable_w  = W - 2 * MARGIN
    as_of_str = as_of_date.strftime("%Y-%m-%d")
    run_str   = datetime.now().strftime("%Y-%m-%d")

    doc = BaseDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=3.0 * cm, bottomMargin=1.8 * cm,
        title=f"K-AlphaAgents — Portfolio Executive Summary  ({as_of_str})",
        author="K-AlphaAgents",
    )
    frame = Frame(MARGIN, 1.8 * cm, usable_w, H - 4.8 * cm, id="main")
    doc.addPageTemplates([PageTemplate(
        id="main", frames=[frame],
        onPage=_make_header_footer(as_of_str, run_str),
    )])

    story = []

    # ═══════════════════════════════════════════════════════════
    # PAGE 1 — SIGNAL TABLE + PORTFOLIO ALLOCATION
    # ═══════════════════════════════════════════════════════════

    if is_rebalanced:
        # ── Rebalancing history (all quarters) ───────────────────────────
        q_start_str = quarterly_log[0]["start"].strftime("%Y-%m-%d")
        story += _section_title(
            f"1.  Rebalancing History  ({q_start_str} → {as_of_str})", sty
        )
        story.append(Paragraph(
            f"Portfolio rebalanced {len(quarterly_log)} time(s).  "
            "Each quarter the 5-agent debate re-ran with fresh data.  "
            "Intra-quarter event triggers (price drop / vol spike / momentum flip) "
            "adjusted weights without LLM calls.",
            sty["body"],
        ))
        story.append(Spacer(1, 0.3 * cm))

        for profile in ("risk-averse", "risk-neutral"):
            label = "Risk-Averse" if profile == "risk-averse" else "Risk-Neutral"
            story.append(Paragraph(f"<b>{label}</b>", sty["body"]))
            story.append(Spacer(1, 0.15 * cm))
            story.append(_build_rebalance_history(
                company_names, quarterly_log, sty, usable_w, profile
            ))
            story.append(Spacer(1, 0.35 * cm))

        story.append(Spacer(1, 0.2 * cm))
        story += _section_title("2.  Final Quarter Signals & Conviction", sty)
    else:
        story += _section_title("1.  Stock Signals & Conviction", sty)

    story.append(_build_signal_table(company_names, portfolios, sty, usable_w))
    story.append(Spacer(1, 0.5 * cm))

    alloc_num = "3." if is_rebalanced else "2."
    story += _section_title(f"{alloc_num}  Portfolio Allocation  (Final Quarter)", sty)
    story.append(_build_profile_cards(portfolios, sty, usable_w))
    story.append(Spacer(1, 0.5 * cm))

    # Donut pie charts
    pw = usable_w / 2 - 0.4 * cm
    ph = pw * 0.9
    pie_l = _make_pie("Risk-Averse",  company_names, portfolios, "risk-averse")
    pie_r = _make_pie("Risk-Neutral", company_names, portfolios, "risk-neutral")
    pie_tbl = Table(
        [[_fig_to_rl_image(pie_l, pw, ph), _fig_to_rl_image(pie_r, pw, ph)]],
        colWidths=[pw + 0.4 * cm, pw],
    )
    pie_tbl.setStyle(TableStyle([
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    story.append(pie_tbl)

    # ═══════════════════════════════════════════════════════════
    # PAGE 2 — NARRATIVE + METRICS + BACKTEST
    # ═══════════════════════════════════════════════════════════
    story.append(PageBreak())

    narr_num    = "4." if is_rebalanced else "3."
    metrics_num = "5." if is_rebalanced else "4."
    bt_num      = "6." if is_rebalanced else "5."

    story += _section_title(f"{narr_num}  Cross-Profile Investment Narrative", sty)
    story.append(Paragraph(narrative, sty["body"]))
    story.append(Spacer(1, 0.6 * cm))

    story += _section_title(f"{metrics_num}  Portfolio Metrics at a Glance  (Final Quarter)", sty)
    story.append(_build_metrics_table(company_names, portfolios, sty, usable_w))
    story.append(Spacer(1, 0.6 * cm))

    if backtest_results is not None:
        bt_engine = backtest_results.get("risk-averse") or backtest_results.get("risk-neutral")
        bt_start  = bt_engine.start if bt_engine else as_of_str
        bt_end    = bt_engine.end   if bt_engine else "—"
        bt_label  = "Rebalanced Backtest" if is_rebalanced else "Backtest Results"
        story += _section_title(
            f"{bt_num}.  {bt_label}  ({bt_start} → {bt_end})", sty
        )
        bt_fig = _make_backtest_fig(backtest_results)
        story.append(_fig_to_rl_image(bt_fig, usable_w, usable_w * 0.52))
        story.append(Spacer(1, 0.2 * cm))
        caption_extra = (
            "  Portfolio line shows time-varying weights across all quarterly rebalances."
            if is_rebalanced else ""
        )
        story.append(Paragraph(
            "Benchmarks: equal-weight of all analysed stocks (EW Benchmark, orange), "
            "KOSPI (green), and KOSDAQ (purple).  "
            "Rolling Sharpe computed over a 30-trading-day window; "
            "the left margin is intentionally blank during the warm-up period."
            + caption_extra,
            sty["caption"],
        ))
    else:
        story += _section_title(f"{bt_num}.  Backtest", sty)
        story.append(Paragraph(
            "Backtesting was skipped — no stocks qualified for equity allocation "
            "in either risk profile.  All capital is preserved in the Korean "
            "3-Year Government Bond ETF (KODEX 국고채3년, 114260).",
            sty["body"],
        ))

    doc.build(story)
    print(f"  [PDF] Saved → {pdf_path}")
    return pdf_path


