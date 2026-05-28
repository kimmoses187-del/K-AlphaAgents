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

import logging
import yfinance as yf
from pykrx import stock as krx
from datetime import datetime, timedelta
from typing import Optional

log = logging.getLogger(__name__)


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


# ── Gap 4: Dynamic peer detection via pykrx sector classifications ────────────

def _get_dynamic_peers(
    stock_code: str,
    sector: str,
    induty_code: str,
    exchange: str,
    max_peers: int = 5,
) -> list[str]:
    """
    Find peers dynamically using pykrx's market sector classifications.

    Strategy (tried in order):
    1. pykrx.get_market_sector_classifications() — KRX official sector table.
       Match on the same KRX sector code as the target stock.
    2. Fallback: KOREAN_SECTOR_PEERS hardcoded list for the resolved sector.

    Returns a list of raw 6-digit stock codes (no .KS/.KQ suffix).
    """
    # ── Strategy 1: pykrx sector classifications ──────────────────────────
    try:
        market = "KOSPI" if exchange == "KOSPI" else "KOSDAQ"
        df = krx.get_market_sector_classifications(datetime.today().strftime("%Y%m%d"), market)
        # Columns: 종목코드, 종목명, 시가총액, 분류코드, 분류명 (varies by pykrx version)
        if df is not None and not df.empty:
            # Normalise column names
            cols = {c.lower(): c for c in df.columns}
            code_col    = cols.get("종목코드") or cols.get("ticker") or df.columns[0]
            sec_col_key = next((c for c in df.columns if "분류" in c or "섹터" in c or "sector" in c.lower()), None)

            if sec_col_key and code_col:
                # Find this stock's sector label
                row = df[df[code_col] == stock_code]
                if not row.empty:
                    own_sector_label = row.iloc[0][sec_col_key]
                    # All tickers in the same sector, excluding the stock itself
                    same_sector = df[
                        (df[sec_col_key] == own_sector_label) &
                        (df[code_col] != stock_code)
                    ][code_col].tolist()

                    # Prefer larger-cap companies as peers (if market cap col exists)
                    cap_col = next((c for c in df.columns if "시가총액" in c or "cap" in c.lower()), None)
                    if cap_col:
                        same_sector_df = df[
                            (df[sec_col_key] == own_sector_label) &
                            (df[code_col] != stock_code)
                        ].copy()
                        same_sector_df[cap_col] = same_sector_df[cap_col].apply(
                            lambda x: float(str(x).replace(",", "") or 0)
                        )
                        same_sector_df = same_sector_df.sort_values(cap_col, ascending=False)
                        same_sector = same_sector_df[code_col].tolist()

                    peers = [str(c).zfill(6) for c in same_sector[:max_peers]]
                    if peers:
                        log.debug("Dynamic peers (pykrx sector '%s'): %s", own_sector_label, peers)
                        return peers
    except Exception as exc:
        log.debug("pykrx sector classification failed: %s", exc)

    # ── Strategy 2: fall back to hardcoded sector list ────────────────────
    fallback = [
        _strip_suffix(t) for t in KOREAN_SECTOR_PEERS.get(sector, [])
        if _strip_suffix(t) != stock_code
    ]
    log.debug("Peer fallback (hardcoded '%s'): %s", sector, fallback[:max_peers])
    return fallback[:max_peers]


def get_peer_comparison(
    stock_code: str,
    sector: str,
    as_of_date: datetime,
    months: int = 3,
    induty_code: str = "",
    exchange: str = "KOSPI",
) -> list:
    """
    Fetch peer returns via pykrx (reliable) and names/ratios via yfinance (optional).

    Gap 4: Peers are now detected dynamically from KRX sector classifications
    before falling back to the hardcoded KOREAN_SECTOR_PEERS list.

    Parameters
    ----------
    stock_code   : 6-digit KRX code of the stock being analysed
    sector       : resolved sector label (from ksic_to_sector)
    as_of_date   : analysis date
    months       : lookback window for return calculation
    induty_code  : DART KSIC code (used to refine sector matching)
    exchange     : "KOSPI" or "KOSDAQ"
    """
    start_str = (as_of_date - timedelta(days=30 * months)).strftime("%Y%m%d")
    end_str   = as_of_date.strftime("%Y%m%d")

    # Dynamic peer detection (Gap 4)
    peer_codes = _get_dynamic_peers(stock_code, sector, induty_code, exchange, max_peers=5)

    peers = []
    for code in peer_codes[:3]:    # cap at 3 for MarketAgent context size
        # Build ticker string for yfinance
        suffix      = ".KS" if exchange == "KOSPI" else ".KQ"
        ticker_full = f"{code}{suffix}"
        try:
            # ── pykrx: return (primary, reliable) ────────────────────────
            df  = krx.get_market_ohlcv_by_date(start_str, end_str, code)
            ret = None
            if not df.empty and "종가" in df.columns:
                ret = round(
                    (float(df["종가"].iloc[-1]) / float(df["종가"].iloc[0]) - 1) * 100, 2
                )

            # ── pykrx: name from ticker list (no yfinance round-trip needed) ──
            name = code
            try:
                name = krx.get_market_ticker_name(code) or code
            except Exception:
                pass

            # ── yfinance: valuation ratios (optional, graceful fallback) ──
            pe     = None
            pb     = None
            mktcap = None
            try:
                info   = yf.Ticker(ticker_full).info
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
