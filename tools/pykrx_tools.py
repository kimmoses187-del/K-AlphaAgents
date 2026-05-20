"""
tools/pykrx_tools.py
====================
KRX price and index data via pykrx — the authoritative source for Korea Exchange data.

Replaces yfinance for price fetching in TechnicalAgent:
  - No .KS / .KQ suffix ambiguity — uses raw 6-digit KRX stock code
  - Direct KRX source (more accurate for Korean equities)
  - Consistent with the pykrx already used in rebalance/event_monitor.py

KRX Index codes
---------------
  "1001"  KOSPI
  "2001"  KOSDAQ
"""

from datetime import datetime, timedelta
import pandas as pd
from pykrx import stock as krx

KOSPI_INDEX  = "1001"
KOSDAQ_INDEX = "2001"

_RENAME = {
    '시가': 'Open', '고가': 'High', '저가': 'Low',
    '종가': 'Close', '거래량': 'Volume',
}


def fetch_ohlcv(
    stock_code: str,
    as_of_date: datetime,
    months: int = 3,
    offset_months: int = 0,
) -> pd.DataFrame:
    """
    Fetch OHLCV for a stock in the window ending (as_of_date - offset_months).

    offset_months=0  →  current quarter   [as_of - 3M  →  as_of]
    offset_months=3  →  previous quarter  [as_of - 6M  →  as_of - 3M]

    Returns DataFrame with columns: Open, High, Low, Close, Volume
    Returns empty DataFrame on error.
    """
    end   = as_of_date - timedelta(days=30 * offset_months)
    start = end        - timedelta(days=30 * months)
    try:
        df = krx.get_market_ohlcv_by_date(
            start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), stock_code
        )
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns=_RENAME)
        return df[[c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]]
    except Exception as e:
        print(f"    [pykrx] {stock_code}: {e}")
        return pd.DataFrame()


def fetch_index_ohlcv(
    index_code: str,
    as_of_date: datetime,
    months: int = 3,
) -> pd.DataFrame:
    """
    Fetch OHLCV for a KRX market index.

    Common codes: KOSPI_INDEX ("1001"), KOSDAQ_INDEX ("2001")
    Returns empty DataFrame on error.
    """
    start = as_of_date - timedelta(days=30 * months)
    try:
        df = krx.get_index_ohlcv_by_date(
            start.strftime("%Y%m%d"), as_of_date.strftime("%Y%m%d"), index_code
        )
        if df.empty:
            return pd.DataFrame()
        df = df.rename(columns=_RENAME)
        return df[[c for c in ['Open', 'High', 'Low', 'Close', 'Volume'] if c in df.columns]]
    except Exception as e:
        print(f"    [pykrx] index {index_code}: {e}")
        return pd.DataFrame()
