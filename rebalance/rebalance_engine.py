"""
rebalance/rebalance_engine.py
=============================
Manages quarterly LLM rebalancing + intra-quarter event-triggered re-weighting.

Flow per quarter
----------------
1. Run full 5-agent debate (OrchestratorAgent) with data as-of = quarter start
2. Construct portfolio (PortfolioAgent) → base weights for the quarter
3. Fetch intra-quarter KRX prices
4. Walk trading days:
     - check_triggers() detects PRICE_DROP / VOL_SPIKE / MOM_FLIP
     - On trigger: compute_momentum_scores() + adjust_weights() → new weights (no LLM)
5. Append all weight changes to the schedule

Returns
-------
weight_schedule : {"risk-averse": [(datetime, weights), ...],
                   "risk-neutral": [(datetime, weights), ...]}
quarterly_log   : list of per-quarter dicts for PDF / reporting
"""

import calendar
import os
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from orchestrator.orchestrator_agent import OrchestratorAgent
from portfolio.portfolio_agent import construct_portfolio, BOND_TICKER
from rebalance.event_monitor import check_triggers, compute_momentum_scores
from rebalance.weight_adjuster import adjust_weights

PROFILES    = ("risk-averse", "risk-neutral")
REPORTS_DIR = "reports"


# ── Date helpers ──────────────────────────────────────────────────────────────

def _add_months(dt: datetime, months: int) -> datetime:
    """Add `months` calendar months to `dt`, clamping day to valid range."""
    total_month = dt.month + months
    year  = dt.year + (total_month - 1) // 12
    month = (total_month - 1) % 12 + 1
    day   = min(dt.day, calendar.monthrange(year, month)[1])
    return datetime(year, month, day)


def get_quarter_dates(
    start: datetime, end: datetime
) -> List[Tuple[datetime, datetime]]:
    """Divide [start, end] into 3-month quarters."""
    quarters = []
    q_start  = start
    while q_start < end:
        q_end = min(_add_months(q_start, 3), end)
        quarters.append((q_start, q_end))
        q_start = q_end
    return quarters


# ── Main engine ───────────────────────────────────────────────────────────────

class RebalanceEngine:
    """
    Orchestrates quarterly LLM rebalancing + intra-quarter event-triggered
    re-weighting for a fixed pool of Korean stocks.
    """

    def __init__(self, orchestrator: OrchestratorAgent):
        self.orchestrator = orchestrator

    def run(
        self,
        stock_codes: List[str],
        corp_infos: Dict[str, dict],
        start_date: datetime,
        end_date: datetime,
        use_event_triggers: bool = True,
    ) -> Tuple[Dict[str, list], List[dict]]:
        """
        Main entry point.

        Parameters
        ----------
        stock_codes        : KRX 6-digit codes to analyse each quarter
        corp_infos         : {code: corp_info dict from DART lookup}
        start_date         : first quarterly analysis date
        end_date           : backtest end date (no analysis beyond this)
        use_event_triggers : whether to run intra-quarter monitoring

        Returns
        -------
        weight_schedule : {"risk-averse": [(date, weights_dict), ...],
                           "risk-neutral": [(date, weights_dict), ...]}
        quarterly_log   : [{quarter, start, end, results, portfolios}, ...]
        """
        quarters      = get_quarter_dates(start_date, end_date)
        quarterly_log = []
        weight_schedule = {p: [] for p in PROFILES}

        print(f"\n{'='*60}")
        print(f"  REBALANCING ENGINE")
        print(f"  {len(quarters)} quarter(s)  "
              f"{start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")
        print(f"  Stocks : {', '.join(stock_codes)}")
        print(f"  Event triggers: {'ON' if use_event_triggers else 'OFF'}")
        print(f"{'='*60}")

        for q_num, (q_start, q_end) in enumerate(quarters, 1):
            print(f"\n{'─'*60}")
            print(f"  [Q{q_num}]  "
                  f"{q_start.strftime('%Y-%m-%d')} → {q_end.strftime('%Y-%m-%d')}")
            print(f"{'─'*60}")

            # ── Quarterly LLM rebalance ───────────────────────────────────
            all_results, portfolios = self._run_quarter_analysis(
                stock_codes, corp_infos, q_start
            )

            # Quarter-start weights → schedule
            for profile in PROFILES:
                weight_schedule[profile].append(
                    (q_start, dict(portfolios[profile]["weights"]))
                )
                po = portfolios[profile]
                print(f"  [{profile.upper():<14}] "
                      f"EQ {po['equity_weight']*100:.0f}% / "
                      f"Bond {po['bond_weight']*100:.0f}%  "
                      f"| held: "
                      f"{sum(1 for a in po['stock_allocations'].values() if a['weight']>0)} stocks")

            quarterly_log.append({
                "quarter":    q_num,
                "start":      q_start,
                "end":        q_end,
                "results":    all_results,
                "portfolios": portfolios,
            })

            # ── Intra-quarter event monitoring ────────────────────────────
            if use_event_triggers and q_end > q_start:
                event_weights = self._monitor_quarter(
                    q_start, q_end, portfolios, stock_codes
                )
                total_events = sum(len(v) for v in event_weights.values())
                for profile in PROFILES:
                    weight_schedule[profile].extend(event_weights[profile])
                if total_events:
                    print(f"  [EventMonitor] {total_events} re-weight event(s) recorded.")
                else:
                    print(f"  [EventMonitor] No triggers fired this quarter.")

        return weight_schedule, quarterly_log

    # ── Private helpers ───────────────────────────────────────────────────────

    def _run_quarter_analysis(
        self,
        stock_codes: List[str],
        corp_infos: Dict[str, dict],
        as_of_date: datetime,
    ) -> Tuple[dict, dict]:
        """Run full 5-agent debate for every stock; return (all_results, portfolios)."""
        all_results = {}
        for code in stock_codes:
            result = self.orchestrator.analyze_stock(
                code, as_of_date, corp_infos[code]
            )
            all_results[code] = result

        stock_debate_results = {
            code: r["debate_results"] for code, r in all_results.items()
        }
        portfolios = construct_portfolio(stock_debate_results)
        return all_results, portfolios

    def _monitor_quarter(
        self,
        q_start: datetime,
        q_end: datetime,
        portfolios: dict,
        stock_codes: List[str],
    ) -> Dict[str, list]:
        """
        Fetch intra-quarter KRX prices and build event-triggered weight adjustments.

        Returns {"risk-averse": [(date, weights), ...], "risk-neutral": [...]}
        """
        from pykrx import stock as krx

        start_str = q_start.strftime("%Y%m%d")
        end_str   = q_end.strftime("%Y%m%d")

        # Fetch all tickers (stocks + bond ETF)
        all_tickers = list(set(stock_codes + [BOND_TICKER]))
        raw_prices  = {}
        for ticker in all_tickers:
            try:
                df = krx.get_market_ohlcv_by_date(start_str, end_str, ticker)
                if not df.empty:
                    raw_prices[ticker] = df["종가"]
            except Exception as e:
                print(f"    [EventMonitor] Cannot fetch {ticker}: {e}")

        if not raw_prices:
            return {p: [] for p in PROFILES}

        price_df = pd.DataFrame(raw_prices).dropna(how="all")

        event_weights = {p: [] for p in PROFILES}

        for profile in PROFILES:
            portfolio = portfolios[profile]
            holdings  = {
                code: alloc["weight"]
                for code, alloc in portfolio["stock_allocations"].items()
                if alloc["weight"] > 0
            }
            if not holdings:
                continue

            # Entry prices = first available trading day in the quarter
            entry_prices = {}
            for ticker in holdings:
                col = price_df[ticker].dropna() if ticker in price_df.columns else pd.Series()
                if not col.empty:
                    entry_prices[ticker] = col.iloc[0]

            last_trigger_dates: Dict[str, Optional[pd.Timestamp]] = {
                t: None for t in holdings
            }

            # Walk every trading day in the quarter
            for ts in price_df.index:
                triggered = check_triggers(
                    holdings, price_df, entry_prices, last_trigger_dates, ts
                )
                if not triggered:
                    continue

                momentum  = compute_momentum_scores(holdings, price_df, ts)
                new_w     = adjust_weights(portfolio, momentum)
                event_weights[profile].append((ts.to_pydatetime(), new_w))

                print(f"    [EventMonitor | {profile}] "
                      f"{ts.strftime('%Y-%m-%d')}  trigger → {triggered}")

                for t in triggered:
                    last_trigger_dates[t] = ts

        return event_weights
