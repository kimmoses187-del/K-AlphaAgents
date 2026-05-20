"""
tools/market_tools.py
=====================
Market and industry data for MarketAgent.

Data sources (by priority)
--------------------------
  Sector / exchange  →  DART corp_info  (corp_cls + induty_code)   [reliable, primary]
  Peer returns       →  pykrx           (KRX authoritative)         [reliable, primary]
  KOSPI/KOSDAQ       →  passed in from orchestrator pykrx fetch     [reliable, reused]
  Peer names/ratios  →  yfinance        (optional, graceful fallback)

DART corp_cls values
--------------------
  "Y" = KOSPI   "K" = KOSDAQ   "N" = KONEX   "E" = ETC

DART induty_code — KSIC (Korean Standard Industry Classification)
-----------------------------------------------------------------
  Format: 1 uppercase letter + 2–4 digits  e.g. "C2610", "C2101", "J6312"
  First character = major division (C=Manufacturing, J=ICT, K=Finance …)
  First 3 characters = sub-division used for sector mapping
"""

import yfinance as yf
from pykrx import stock as krx
from datetime import datetime, timedelta
from typing import Optional


# ── KSIC → Sector mapping ─────────────────────────────────────────────────────
# Keys are tried longest-first: 3-char prefix (e.g. "C26"), then 1-char ("C").
# Reference: Statistics Korea KSIC Rev.10

KSIC_TO_SECTOR: dict[str, str] = {
    # Manufacturing sub-divisions (3-char prefix)
    "C10": "Consumer Defensive",    # Food manufacturing
    "C11": "Consumer Defensive",    # Beverage
    "C12": "Consumer Defensive",    # Tobacco
    "C13": "Consumer Defensive",    # Textile
    "C14": "Consumer Defensive",    # Clothing / apparel
    "C15": "Consumer Defensive",    # Leather / footwear
    "C16": "Basic Materials",       # Wood products
    "C17": "Basic Materials",       # Paper
    "C18": "Basic Materials",       # Printing
    "C19": "Energy",                # Coke / petroleum refining
    "C20": "Basic Materials",       # Chemical / petrochemical
    "C21": "Healthcare",            # Pharmaceutical / biotech
    "C22": "Basic Materials",       # Rubber / plastic
    "C23": "Basic Materials",       # Non-metallic mineral
    "C24": "Basic Materials",       # Basic metal (steel, aluminium)
    "C25": "Industrials",           # Fabricated metal products
    "C26": "Technology",            # Semiconductors / electronic components
    "C27": "Technology",            # Electronic / computer equipment
    "C28": "Healthcare",            # Medical / precision instruments
    "C29": "Consumer Cyclical",     # Motor vehicles / auto parts
    "C30": "Industrials",           # Other transport equipment (ships, aircraft)
    "C31": "Consumer Cyclical",     # Furniture
    "C32": "Industrials",           # Other manufacturing
    "C33": "Industrials",           # Repair / installation
    # Major divisions (1-char prefix — fallback when 3-char not matched)
    "A":   "Consumer Defensive",    # Agriculture / fishing
    "B":   "Basic Materials",       # Mining / quarrying
    "D":   "Utilities",             # Electricity / gas supply
    "E":   "Industrials",           # Water / sewage / waste
    "F":   "Industrials",           # Construction
    "G":   "Consumer Cyclical",     # Wholesale / retail
    "H":   "Industrials",           # Transportation / logistics
    "I":   "Consumer Cyclical",     # Accommodation / food service
    "J":   "Communication Services",# Information / communication / media
    "K":   "Financial Services",    # Finance / insurance
    "L":   "Real Estate",           # Real estate
    "M":   "Industrials",           # Professional / scientific services
    "N":   "Industrials",           # Administrative / support services
    "Q":   "Healthcare",            # Health / social work
    "R":   "Consumer Cyclical",     # Arts / sports / recreation
    "S":   "Consumer Cyclical",     # Other personal services
}

# Representative Korean sector peers  (used when DART sector is resolved)
# Entries include both KOSPI (.KS) and KOSDAQ (.KQ) names
KOREAN_SECTOR_PEERS: dict[str, list[str]] = {
    "Technology":             ["005930.KS", "000660.KS", "035420.KS", "066570.KS",
                               "035900.KQ", "041510.KQ"],
    "Communication Services": ["030200.KS", "017670.KS", "036570.KS", "035600.KQ"],
    "Consumer Cyclical":      ["005380.KS", "000270.KS", "012330.KS", "032640.KS",
                               "039130.KS"],
    "Consumer Defensive":     ["097950.KS", "003230.KS", "004370.KS", "007310.KS"],
    "Healthcare":             ["068270.KS", "207940.KS", "128940.KS",
                               "145020.KQ", "196170.KQ", "214150.KQ", "086900.KQ"],
    "Financial Services":     ["105560.KS", "055550.KS", "086790.KS", "138930.KS"],
    "Basic Materials":        ["003670.KS", "010130.KS", "011070.KS", "004020.KS"],
    "Energy":                 ["096770.KS", "267250.KS", "010950.KS"],
    "Industrials":            ["042660.KS", "329180.KS", "010140.KS", "047810.KS",
                               "064350.KQ"],
    "Real Estate":            ["016380.KS", "005440.KS"],
    "Utilities":              ["036460.KS", "015760.KS"],
}


# ── Sector detection ──────────────────────────────────────────────────────────

def ksic_to_sector(induty_code: str) -> str:
    """
    Map a DART KSIC industry code to a sector category string.
    Tries 3-char prefix first (e.g. "C26"), then 1-char fallback ("C").
    Returns "Unknown" if no match.
    """
    code = (induty_code or "").upper().strip()
    for length in (3, 1):
        key = code[:length]
        if key in KSIC_TO_SECTOR:
            return KSIC_TO_SECTOR[key]
    return "Unknown"


def get_company_sector_info(corp_info: dict, ticker_str: Optional[str] = None) -> dict:
    """
    Build sector info dict primarily from DART corp_info.

    DART provides:
      corp_cls   → exchange  ("Y"=KOSPI, "K"=KOSDAQ)
      induty_code → KSIC code → sector category via ksic_to_sector()
      prd_nm     → main products string (used as industry description)

    If ticker_str is supplied, yfinance .info is tried for valuation ratios
    and a business description (graceful fallback — all fields None if unavailable).
    """
    induty_code = corp_info.get("induty_code", "")
    corp_cls    = corp_info.get("corp_cls", "")

    result = {
        "sector":            ksic_to_sector(induty_code),
        "industry":          corp_info.get("prd_nm", "N/A"),
        "exchange":          {"Y": "KOSPI", "K": "KOSDAQ", "N": "KONEX"}.get(corp_cls, "Unknown"),
        "induty_code":       induty_code,
        "description":       "",
        "full_time_employees": corp_info.get("enpls_nm"),
        # Ratio fields — populated from yfinance if available
        "market_cap":        None,
        "pe_ratio":          None,
        "forward_pe":        None,
        "pb_ratio":          None,
        "revenue_growth":    None,
        "earnings_growth":   None,
        "gross_margins":     None,
        "operating_margins": None,
    }

    if ticker_str:
        try:
            info = yf.Ticker(ticker_str).info
            result.update({
                "description":       info.get("longBusinessSummary", ""),
                "market_cap":        info.get("marketCap"),
                "pe_ratio":          info.get("trailingPE"),
                "forward_pe":        info.get("forwardPE"),
                "pb_ratio":          info.get("priceToBook"),
                "revenue_growth":    info.get("revenueGrowth"),
                "earnings_growth":   info.get("earningsGrowth"),
                "gross_margins":     info.get("grossMargins"),
                "operating_margins": info.get("operatingMargins"),
                "full_time_employees": info.get("fullTimeEmployees"),
            })
        except Exception:
            pass   # ratios remain None — agent notes N/A

    return result


# ── Peer comparison ───────────────────────────────────────────────────────────

def _strip_suffix(ticker: str) -> str:
    """'005930.KS' → '005930'"""
    return ticker.split(".")[0]


def get_peer_comparison(
    stock_code: str,
    sector: str,
    as_of_date: datetime,
    months: int = 3,
) -> list:
    """
    Fetch peer returns via pykrx (reliable) and names/ratios via yfinance (optional).

    Returns up to 3 peers from KOREAN_SECTOR_PEERS for the resolved sector.
    """
    start_str = (as_of_date - timedelta(days=30 * months)).strftime("%Y%m%d")
    end_str   = as_of_date.strftime("%Y%m%d")

    candidates = [
        t for t in KOREAN_SECTOR_PEERS.get(sector, [])
        if _strip_suffix(t) != stock_code
    ]

    peers = []
    for ticker_full in candidates[:3]:
        code = _strip_suffix(ticker_full)
        try:
            # ── pykrx: return (primary, reliable) ────────────────────────
            df  = krx.get_market_ohlcv_by_date(start_str, end_str, code)
            ret = None
            if not df.empty and "종가" in df.columns:
                ret = round(
                    (float(df["종가"].iloc[-1]) / float(df["종가"].iloc[0]) - 1) * 100, 2
                )

            # ── yfinance: name + ratios (optional, graceful fallback) ─────
            name   = code
            pe     = None
            pb     = None
            mktcap = None
            try:
                info   = yf.Ticker(ticker_full).info
                name   = info.get("shortName") or info.get("longName") or code
                pe     = info.get("trailingPE")
                pb     = info.get("priceToBook")
                mktcap = info.get("marketCap")
            except Exception:
                pass

            peers.append({
                "ticker":     code,
                "name":       name,
                "3m_return":  ret,
                "pe_ratio":   pe,
                "pb_ratio":   pb,
                "market_cap": mktcap,
            })
        except Exception:
            continue

    return peers


# ── Formatter ─────────────────────────────────────────────────────────────────

def format_market_data_for_llm(
    sector_info: dict,
    kospi_return: Optional[float],
    kosdaq_return: Optional[float],
    peers: list,
    company_name: str,
) -> str:
    """Format market data into structured text for MarketAgent."""
    lines = []

    # ── Sector & exchange classification ─────────────────────────────────
    exchange    = sector_info.get("exchange", "Unknown")
    induty_code = sector_info.get("induty_code", "N/A")
    lines += [
        "## Sector & Industry Classification",
        f"Sector:              {sector_info.get('sector', 'N/A')}",
        f"Industry:            {sector_info.get('industry', 'N/A')}",
        f"Exchange:            {exchange}  (DART corp_cls → KSIC {induty_code})",
        f"Full-time Employees: {sector_info.get('full_time_employees', 'N/A')}",
        f"Market Cap:          {sector_info.get('market_cap', 'N/A')}",
        "",
    ]

    # ── Valuation ratios (from yfinance if available) ─────────────────────
    has_ratios = any(
        sector_info.get(k) is not None
        for k in ("pe_ratio", "pb_ratio", "revenue_growth", "gross_margins")
    )
    if has_ratios:
        lines += [
            "## Key Valuation Ratios",
            f"Trailing P/E:        {sector_info.get('pe_ratio', 'N/A')}",
            f"Forward P/E:         {sector_info.get('forward_pe', 'N/A')}",
            f"Price/Book:          {sector_info.get('pb_ratio', 'N/A')}",
            f"Revenue Growth YoY:  {sector_info.get('revenue_growth', 'N/A')}",
            f"Earnings Growth:     {sector_info.get('earnings_growth', 'N/A')}",
            f"Gross Margin:        {sector_info.get('gross_margins', 'N/A')}",
            f"Operating Margin:    {sector_info.get('operating_margins', 'N/A')}",
            "",
        ]

    # ── Business description ──────────────────────────────────────────────
    desc = sector_info.get("description", "")
    if desc:
        lines += ["## Business Description", desc[:600], ""]

    # ── Benchmark comparison ──────────────────────────────────────────────
    # Show both KOSPI and KOSDAQ; highlight the primary benchmark based on exchange
    primary   = "KOSPI" if exchange == "KOSPI" else "KOSDAQ"
    secondary = "KOSDAQ" if primary == "KOSPI" else "KOSPI"
    pri_ret   = kospi_return  if primary == "KOSPI"  else kosdaq_return
    sec_ret   = kosdaq_return if secondary == "KOSDAQ" else kospi_return

    lines.append("## Benchmark Comparison (3-Month, same window as stock)")
    lines.append(
        f"{'★ ' if primary == 'KOSPI' else ''}"
        f"KOSPI  3M Return:  {f'{kospi_return:+.2f}%' if kospi_return  is not None else 'N/A'}"
        f"{'  ← primary (stock is KOSPI-listed)' if primary == 'KOSPI' else ''}"
    )
    lines.append(
        f"{'★ ' if primary == 'KOSDAQ' else ''}"
        f"KOSDAQ 3M Return:  {f'{kosdaq_return:+.2f}%' if kosdaq_return is not None else 'N/A'}"
        f"{'  ← primary (stock is KOSDAQ-listed)' if primary == 'KOSDAQ' else ''}"
    )
    lines.append("")

    # ── Sector peer comparison ────────────────────────────────────────────
    if peers:
        lines += ["## Sector Peer Comparison (3-Month, via pykrx)", ""]
        lines.append(f"{'Company':<35} {'3M Return':>12} {'P/E':>8} {'P/B':>8}")
        lines.append("-" * 68)
        for p in peers:
            ret_str = f"{p['3m_return']:+.2f}%" if p["3m_return"] is not None else "N/A"
            pe_str  = f"{p['pe_ratio']:.1f}"    if p["pe_ratio"]  is not None else "N/A"
            pb_str  = f"{p['pb_ratio']:.2f}"    if p["pb_ratio"]  is not None else "N/A"
            lines.append(f"{p['name']:<35} {ret_str:>12} {pe_str:>8} {pb_str:>8}")
        lines.append("")
    else:
        lines += [
            "## Sector Peer Comparison",
            f"No peers found for sector '{sector_info.get('sector', 'Unknown')}'. "
            "Analyse using industry knowledge.",
            "",
        ]

    return "\n".join(lines)
