"""
tools/sentiment_tools.py
========================
Sentiment data for SentimentAgent — three reliable Korean-market sources.

Sources
-------
  D  DART disclosure list  →  corporate events over the analysis quarter
     Endpoint: /api/list.json
     Signals: material-event reports, insider trading, securities issuance,
              litigation, fair-disclosure notices

  E  pykrx investor net flow  →  foreign / institutional / retail net buying
     krx.get_market_net_purchases_of_equities_by_investor()
     Signals: sustained foreign / institutional accumulation or distribution

  F  pykrx short selling      →  short volume ratio as bearish-pressure gauge
     krx.get_market_short_selling_volume_by_date()
     Signals: rising short ratio → rising bearish conviction

All three fall back gracefully: if data is unavailable the section is noted
as "N/A" so the agent can still reason from the sections that did load.
"""

import requests
from datetime import datetime, timedelta
from typing import Optional

from pykrx import stock as krx

from config import DART_API_KEY

DART_BASE = "https://opendart.fss.or.kr/api"


# ── D: DART disclosure list ───────────────────────────────────────────────────

# Disclosure types that carry genuine sentiment signal
_MATERIAL_PBLNTF = {
    "A": "주요사항보고서",          # Material-event report (대규모 손실, 소송 등)
    "B": "주요사항보고서(외감법)",
    "C": "자회사주요사항보고서",
}
_PRIORITY_REPORT_NAMES = [
    "임원·주요주주특정증권등소유상황보고서",   # Insider trading disclosure
    "증권발행실적보고서",                         # Securities issuance (dilution risk)
    "전환사채권발행결정",                         # CB issuance
    "유상증자결정",                               # Rights offering
    "무상증자결정",                               # Bonus issue (positive)
    "자기주식취득결정",                           # Buyback (positive)
    "자기주식처분결정",                           # Disposal of treasury shares (dilutive)
    "소송등의제기",                               # Litigation / regulatory action
    "불성실공시법인지정",                         # Unfair disclosure designation (red flag)
    "공정공시",                                   # Fair-disclosure notice
    "기업설명회개최",                             # IR event
    "영업양도",                                   # Business transfer (M&A signal)
    "합병결정",                                   # Merger decision
]


def _categorise_dart(report_nm: str, pblntf_ty: str) -> str:
    """Return a short category tag for the disclosure."""
    nm = report_nm.strip()
    if "소송" in nm or "제재" in nm or "벌금" in nm:
        return "Litigation/Regulatory"
    if "유상증자" in nm or "전환사채" in nm or "증권발행" in nm:
        return "Dilution Risk"
    if "자기주식취득" in nm:
        return "Buyback (positive)"
    if "자기주식처분" in nm:
        return "Treasury Share Disposal"
    if "합병" in nm or "영업양도" in nm:
        return "M&A Activity"
    if "임원" in nm or "주요주주" in nm:
        return "Insider Ownership Change"
    if "공정공시" in nm or "기업설명회" in nm:
        return "Fair Disclosure / IR"
    if "무상증자" in nm:
        return "Bonus Issue (positive)"
    if pblntf_ty in _MATERIAL_PBLNTF:
        return "Material Event"
    return "Corporate Disclosure"


def fetch_dart_disclosures(
    corp_code: str,
    as_of_date: datetime,
    months: int = 3,
    page_count: int = 30,
) -> list[dict]:
    """
    Fetch DART public disclosures for the given quarter window.

    Returns a list of dicts:
      {report_nm, rcept_dt, pblntf_ty, category, flr_nm}
    Sorted newest-first. Empty list on any error.
    """
    end_de   = as_of_date.strftime("%Y%m%d")
    start_de = (as_of_date - timedelta(days=30 * months)).strftime("%Y%m%d")

    try:
        r = requests.get(
            f"{DART_BASE}/list.json",
            params={
                "crtfc_key": DART_API_KEY,
                "corp_code":  corp_code,
                "bgn_de":     start_de,
                "end_de":     end_de,
                "page_count": page_count,
            },
            timeout=20,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "000":
            return []

        results = []
        for item in body.get("list", []):
            results.append({
                "report_nm":  item.get("report_nm", "").strip(),
                "rcept_dt":   item.get("rcept_dt", ""),
                "pblntf_ty":  item.get("pblntf_ty", ""),
                "flr_nm":     item.get("flr_nm", "").strip(),  # filer name
                "category":   _categorise_dart(
                    item.get("report_nm", ""),
                    item.get("pblntf_ty", ""),
                ),
            })
        return sorted(results, key=lambda x: x["rcept_dt"], reverse=True)

    except Exception:
        return []


# ── E: pykrx investor net flow ────────────────────────────────────────────────

_INVESTOR_COLS = {
    "금융투자":  "Securities Firms",
    "보험":      "Insurance",
    "투신":      "Asset Mgmt (투신)",
    "사모":      "Private Equity",
    "은행":      "Banks",
    "기타금융":  "Other Financial",
    "연기금등":  "Pension Funds",
    "기관합계":  "Institutional Total",
    "기타법인":  "Corporate (기타법인)",
    "개인":      "Retail",
    "외국인":    "Foreign",
    "전체":      "Grand Total",
}


def fetch_investor_flow(
    stock_code: str,
    as_of_date: datetime,
    months: int = 3,
) -> dict:
    """
    Fetch net purchasing by investor type via pykrx.

    Returns a dict of cumulative net shares/KRW over the quarter:
      {
        "foreign_net_buy":       int,   # shares, + = net buy, - = net sell
        "institutional_net_buy": int,
        "retail_net_buy":        int,
        "foreign_net_value":     int,   # KRW (백만원)
        "institutional_net_value": int,
        "retail_net_value":      int,
        "available":             bool,
      }
    Returns {"available": False} on failure.
    """
    start_str = (as_of_date - timedelta(days=30 * months)).strftime("%Y%m%d")
    end_str   = as_of_date.strftime("%Y%m%d")
    try:
        df = krx.get_market_net_purchases_of_equities_by_investor(
            start_str, end_str, stock_code
        )
        if df is None or df.empty:
            return {"available": False}

        # pykrx returns rows = investor types, cols = 매도, 매수, 순매수, 거래량합계 etc.
        # Row labels: 금융투자, 보험, 투신, ..., 기관합계, 개인, 외국인, 전체
        # '순매수' = net purchase in KRW (백만원)
        # Some builds return English names directly; we handle both

        def _get_net(label_ko: str) -> Optional[int]:
            if label_ko in df.index:
                row = df.loc[label_ko]
                # Try common column names for net buy KRW
                for col in ("순매수", "순매수거래대금", "NetBuy"):
                    if col in row.index:
                        return int(row[col])
            return None

        foreign_val       = _get_net("외국인")
        institutional_val = _get_net("기관합계")
        retail_val        = _get_net("개인")

        return {
            "foreign_net_value":       foreign_val,
            "institutional_net_value": institutional_val,
            "retail_net_value":        retail_val,
            "available":               any(v is not None for v in
                                           [foreign_val, institutional_val, retail_val]),
        }
    except Exception:
        return {"available": False}


# ── F: pykrx short selling ────────────────────────────────────────────────────

def fetch_short_selling(
    stock_code: str,
    as_of_date: datetime,
    months: int = 3,
) -> dict:
    """
    Fetch short-selling volume ratio over the quarter via pykrx.

    Returns:
      {
        "avg_short_ratio":  float,   # avg daily short volume as % of total volume
        "max_short_ratio":  float,
        "recent_short_ratio": float, # last 5-day average
        "trend":            str,     # "Rising" / "Falling" / "Stable"
        "available":        bool,
      }
    """
    start_str = (as_of_date - timedelta(days=30 * months)).strftime("%Y%m%d")
    end_str   = as_of_date.strftime("%Y%m%d")
    try:
        df = krx.get_market_short_selling_volume_by_date(
            start_str, end_str, stock_code
        )
        if df is None or df.empty:
            return {"available": False}

        # pykrx returns: 공매도거래량, 총거래량, 공매도비중 (ratio %)
        ratio_col = None
        for col in ("공매도비중", "ShortRatio", "비중"):
            if col in df.columns:
                ratio_col = col
                break

        if ratio_col is None:
            # Fall back: compute ratio from volume columns
            vol_cols = [c for c in df.columns if "거래량" in c or "Volume" in c.lower()]
            if len(vol_cols) >= 2:
                short_col = vol_cols[0]
                total_col = vol_cols[1]
                df["_ratio"] = df[short_col] / df[total_col].replace(0, float("nan")) * 100
                ratio_col = "_ratio"
            else:
                return {"available": False}

        series     = df[ratio_col].dropna()
        if len(series) < 2:
            return {"available": False}

        avg_ratio    = float(series.mean())
        max_ratio    = float(series.max())
        recent_ratio = float(series.iloc[-5:].mean())

        # Trend: compare last 1/3 vs first 1/3
        third = max(len(series) // 3, 1)
        early = float(series.iloc[:third].mean())
        late  = float(series.iloc[-third:].mean())
        if late > early * 1.10:
            trend = "Rising"
        elif late < early * 0.90:
            trend = "Falling"
        else:
            trend = "Stable"

        return {
            "avg_short_ratio":    round(avg_ratio,    2),
            "max_short_ratio":    round(max_ratio,    2),
            "recent_short_ratio": round(recent_ratio, 2),
            "trend":              trend,
            "available":          True,
        }
    except Exception:
        return {"available": False}


# ── Formatter ─────────────────────────────────────────────────────────────────

def format_sentiment_data_for_llm(
    company_name: str,
    disclosures: list[dict],
    investor_flow: dict,
    short_selling: dict,
    as_of_date: datetime,
    months: int = 3,
) -> str:
    """
    Combine all three sources into structured text for SentimentAgent.

    Produces 3 labelled sections:
      1. Corporate Disclosures (DART)
      2. Investor Flow by Type (pykrx)
      3. Short-Selling Pressure (pykrx)
    """
    start_label = (as_of_date - timedelta(days=30 * months)).strftime("%Y-%m-%d")
    end_label   = as_of_date.strftime("%Y-%m-%d")
    window_note = f"Analysis window: {start_label} → {end_label}"

    lines = [
        f"## Sentiment Data — {company_name}",
        f"({window_note})",
        "",
    ]

    # ── Section D: DART disclosures ───────────────────────────────────────
    lines += ["### D — Corporate Disclosures (DART 공시목록)", ""]
    if not disclosures:
        lines += ["No disclosures found in DART for this period.", ""]
    else:
        lines.append(f"{'Date':<12} {'Category':<28} {'Report'}")
        lines.append("-" * 80)
        for d in disclosures[:20]:   # cap at 20 to avoid token bloat
            date = d["rcept_dt"]
            date_fmt = f"{date[:4]}-{date[4:6]}-{date[6:]}" if len(date) == 8 else date
            cat  = d["category"][:26]
            nm   = d["report_nm"][:55]
            lines.append(f"{date_fmt:<12} {cat:<28} {nm}")
        if len(disclosures) > 20:
            lines.append(f"... and {len(disclosures)-20} more disclosures (truncated)")
        lines.append("")

    # ── Section E: investor net flow ──────────────────────────────────────
    lines += ["### E — Investor Net Flow (pykrx, 3-month cumulative)", ""]
    if not investor_flow.get("available"):
        lines += ["Investor flow data not available for this stock.", ""]
    else:
        def _fmt_val(v):
            if v is None:
                return "N/A"
            billion = v / 100_000_000   # 백만원 → 억원  (DART unit is 백만원 but pykrx is 원)
            # pykrx net purchase unit is raw KRW
            billion = v / 1_000_000_000
            sign = "+" if v >= 0 else ""
            return f"{sign}{billion:,.1f}B KRW"

        fv  = investor_flow.get("foreign_net_value")
        iv  = investor_flow.get("institutional_net_value")
        rv  = investor_flow.get("retail_net_value")

        lines.append(f"  Foreign        net buy:  {_fmt_val(fv)}")
        lines.append(f"  Institutional  net buy:  {_fmt_val(iv)}")
        lines.append(f"  Retail         net buy:  {_fmt_val(rv)}")
        lines.append("")

        # Interpret
        signals = []
        if fv is not None and fv > 0:
            signals.append("Foreign accumulation → bullish signal")
        elif fv is not None and fv < 0:
            signals.append("Foreign distribution → bearish signal")
        if iv is not None and iv > 0:
            signals.append("Institutional buying → supportive")
        elif iv is not None and iv < 0:
            signals.append("Institutional selling → caution")
        if rv is not None and rv > 0 and (fv or 0) < 0 and (iv or 0) < 0:
            signals.append("Retail buying while institutions/foreigners sell → contrarian caution")
        if signals:
            lines += ["  Interpretation:"]
            for s in signals:
                lines.append(f"    • {s}")
        lines.append("")

    # ── Section F: short selling ──────────────────────────────────────────
    lines += ["### F — Short-Selling Pressure (pykrx)", ""]
    if not short_selling.get("available"):
        lines += ["Short-selling data not available for this stock.", ""]
    else:
        avg  = short_selling["avg_short_ratio"]
        mx   = short_selling["max_short_ratio"]
        rcnt = short_selling["recent_short_ratio"]
        trnd = short_selling["trend"]

        lines.append(f"  Average short ratio (3M):   {avg:.2f}%")
        lines.append(f"  Maximum short ratio (3M):   {mx:.2f}%")
        lines.append(f"  Recent (last 5d) ratio:     {rcnt:.2f}%")
        lines.append(f"  Trend:                      {trnd}")
        lines.append("")

        # Interpretation
        if avg > 5.0:
            lines.append("  ⚠  High short interest (>5%) — elevated bearish conviction.")
        elif avg > 2.5:
            lines.append("  Moderate short interest (2.5–5%) — monitor trend direction.")
        else:
            lines.append("  Low short interest (<2.5%) — limited bearish positioning.")

        if trnd == "Rising":
            lines.append("  Short ratio is RISING — increasing bearish pressure recently.")
        elif trnd == "Falling":
            lines.append("  Short ratio is FALLING — bearish positioning is easing.")
        lines.append("")

    return "\n".join(lines)


def fetch_sentiment_data(
    corp_code: str,
    stock_code: str,
    company_name: str,
    as_of_date: datetime,
    months: int = 3,
) -> str:
    """
    Master function: fetch all three sentiment sources and return formatted string.

    Called by orchestrator to replace the old yfinance news pipeline.
    """
    disclosures   = fetch_dart_disclosures(corp_code, as_of_date, months)
    investor_flow = fetch_investor_flow(stock_code,  as_of_date, months)
    short_selling = fetch_short_selling(stock_code,  as_of_date, months)

    return format_sentiment_data_for_llm(
        company_name, disclosures, investor_flow, short_selling, as_of_date, months
    )
