"""
Backtesting Engine for K-AlphaAgents
====================================
Key settings vs. original sample:
  - rolling_window default: 30 trading days
  - plot_two_profiles(): side-by-side 2×2 figure (one column per risk profile)
"""
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from typing import Dict, List, Optional, Tuple


# ── Data Fetchers ─────────────────────────────────────────────────────────────

class KRXDataFetcher:
    """Fetch Korean stock / ETF prices via pykrx (6-digit KRX codes)."""

    def __init__(self):
        self._cache: Dict[str, pd.DataFrame] = {}

    def fetch(self, tickers: List[str], start: str, end: str) -> pd.DataFrame:
        from pykrx import stock as krx
        start_dt = start.replace("-", "")
        end_dt   = end.replace("-", "")
        key = f"krx_{','.join(sorted(tickers))}_{start}_{end}"
        if key not in self._cache:
            frames = {}
            for t in tickers:
                try:
                    df = krx.get_market_ohlcv_by_date(start_dt, end_dt, t)
                    if not df.empty:
                        frames[t] = df["종가"]
                except Exception as e:
                    print(f"[KRX] Could not fetch {t}: {e}")
            if not frames:
                raise RuntimeError("No KRX data fetched. Check ticker codes and date range.")
            self._cache[key] = pd.DataFrame(frames).dropna(how="all")
        return self._cache[key]


class YFinanceDataFetcher:
    """Fetch US stock prices via yfinance."""

    def __init__(self):
        self._cache: Dict[str, pd.DataFrame] = {}

    def fetch(self, tickers: List[str], start: str, end: str) -> pd.DataFrame:
        import yfinance as yf
        key = f"yf_{','.join(sorted(tickers))}_{start}_{end}"
        if key not in self._cache:
            raw = yf.download(tickers, start=start, end=end,
                              auto_adjust=True, progress=False)
            if isinstance(raw.columns, pd.MultiIndex):
                prices = raw["Close"]
            else:
                prices = raw[["Close"]].rename(columns={"Close": tickers[0]})
            self._cache[key] = prices.dropna(how="all")
        return self._cache[key]


class SyntheticDataFetcher:
    """GBM synthetic prices for offline testing."""

    def __init__(self, seed: int = 42):
        self.seed = seed

    def fetch(self, tickers: List[str], start: str, end: str) -> pd.DataFrame:
        rng   = np.random.default_rng(self.seed)
        dates = pd.bdate_range(start=start, end=end)
        n     = len(dates)
        prices = {}
        for t in tickers:
            mu    = rng.uniform(0.0002, 0.0008)
            sigma = rng.uniform(0.01, 0.025)
            S0    = rng.uniform(10_000, 80_000)
            shocks = rng.normal((mu - 0.5 * sigma**2), sigma, n)
            prices[t] = S0 * np.exp(np.cumsum(shocks))
        return pd.DataFrame(prices, index=dates)


def get_fetcher(market: str = "KRX"):
    market = market.upper()
    if market == "KRX":       return KRXDataFetcher()
    if market == "US":        return YFinanceDataFetcher()
    if market == "SYNTHETIC": return SyntheticDataFetcher()
    raise ValueError(f"Unknown market '{market}'. Choose: KRX, US, SYNTHETIC")


# ── Metrics ───────────────────────────────────────────────────────────────────

TRADING_DAYS = 252


class MetricsCalculator:
    def __init__(self, risk_free_rate: float = 0.035):
        self.rf = risk_free_rate
        self._daily_rf = (1 + risk_free_rate) ** (1 / TRADING_DAYS) - 1

    def portfolio_returns(self, prices: pd.DataFrame,
                          weights: Dict[str, float]) -> pd.Series:
        tickers   = list(weights.keys())
        w         = np.array([weights[t] for t in tickers])
        daily_ret = prices[tickers].pct_change().dropna()
        return (daily_ret * w).sum(axis=1).rename("portfolio")

    def cumulative_return(self, returns: pd.Series) -> pd.Series:
        return (1 + returns).cumprod() - 1

    def annualized_return(self, returns: pd.Series) -> float:
        n   = len(returns)
        cum = (1 + returns).prod()
        return cum ** (TRADING_DAYS / n) - 1

    def annualized_volatility(self, returns: pd.Series) -> float:
        return returns.std() * np.sqrt(TRADING_DAYS)

    def sharpe_ratio(self, returns: pd.Series) -> float:
        excess = returns - self._daily_rf
        if returns.std() == 0:
            return 0.0
        return (excess.mean() / returns.std()) * np.sqrt(TRADING_DAYS)

    def rolling_sharpe(self, returns: pd.Series, window: int = 30) -> pd.Series:
        """Rolling Sharpe Ratio with default 30-day window."""
        excess    = returns - self._daily_rf
        roll_mean = excess.rolling(window).mean()
        roll_std  = returns.rolling(window).std()
        return ((roll_mean / roll_std) * np.sqrt(TRADING_DAYS)).rename(returns.name)

    def max_drawdown(self, returns: pd.Series) -> float:
        cum  = (1 + returns).cumprod()
        peak = cum.cummax()
        return ((cum - peak) / peak).min()

    def summary(self, returns: pd.Series) -> dict:
        return {
            "Cumulative Return":     f"{self.cumulative_return(returns).iloc[-1]:.2%}",
            "Annualized Return":     f"{self.annualized_return(returns):.2%}",
            "Annualized Volatility": f"{self.annualized_volatility(returns):.2%}",
            "Sharpe Ratio":          f"{self.sharpe_ratio(returns):.3f}",
            "Max Drawdown":          f"{self.max_drawdown(returns):.2%}",
        }


# ── Portfolio Constructor ─────────────────────────────────────────────────────

class EqualWeightPortfolio:
    def build(self, signals: Dict[str, str]) -> Dict[str, float]:
        buys = [t for t, s in signals.items() if s.upper() == "BUY"]
        if not buys:
            raise ValueError("No BUY signals — portfolio would be empty.")
        w = 1.0 / len(buys)
        return {t: w for t in buys}


# ── Backtest Engine ───────────────────────────────────────────────────────────

class BacktestEngine:
    """
    Ties together data fetching, portfolio construction, and metrics.

    Default rolling_window is 30 trading days (updated from paper's 21d).
    """

    def __init__(
        self,
        start_date: str,
        end_date: str,
        risk_free_rate: float = 0.035,
        rolling_window: int = 30,
        market: str = "KRX",
    ):
        self.start          = start_date
        self.end            = end_date
        self.rolling_window = rolling_window
        self.fetcher        = get_fetcher(market)
        self.portfolio_builder = EqualWeightPortfolio()
        self.metrics        = MetricsCalculator(risk_free_rate=risk_free_rate)
        self.prices: Optional[pd.DataFrame] = None
        self.results: Dict[str, Dict]       = {}

    def add_portfolio(self, name: str, signals: Dict[str, str],
                      custom_weights: Optional[Dict[str, float]] = None):
        weights = custom_weights or self.portfolio_builder.build(signals)
        self.results[name] = {"weights": weights, "signals": signals}

    def run(self) -> Dict[str, dict]:
        all_tickers = set()
        for v in self.results.values():
            all_tickers.update(v["weights"].keys())

        self.prices = self.fetcher.fetch(list(all_tickers), self.start, self.end)

        summaries = {}
        for name, data in self.results.items():
            valid = {t: w for t, w in data["weights"].items()
                     if t in self.prices.columns}
            if not valid:
                print(f"[WARNING] No valid tickers for '{name}', skipping.")
                continue
            total = sum(valid.values())
            valid = {t: w / total for t, w in valid.items()}

            ret  = self.metrics.portfolio_returns(self.prices, valid)
            cum  = self.metrics.cumulative_return(ret)
            roll = self.metrics.rolling_sharpe(ret, self.rolling_window)

            self.results[name].update({
                "returns":           ret,
                "cumulative_return": cum,
                "rolling_sharpe":    roll,
                "valid_weights":     valid,
            })
            summaries[name] = self.metrics.summary(ret)
        return summaries

    def run_with_schedule(
        self,
        weight_schedule: List[Tuple],
    ) -> "pd.Series":
        """
        Run a time-varying backtest using a rebalancing weight schedule.

        Parameters
        ----------
        weight_schedule : [(datetime, {ticker: weight}), ...] sorted ascending by date.
                          Each entry represents a weight change effective from that date.

        Returns
        -------
        pd.Series of daily portfolio returns over [self.start, self.end].
        """
        if not weight_schedule:
            raise ValueError("weight_schedule is empty.")

        # Collect all unique tickers across the entire schedule
        all_tickers: set = set()
        for _, w in weight_schedule:
            all_tickers.update(w.keys())

        self.prices = self.fetcher.fetch(list(all_tickers), self.start, self.end)

        # Sort schedule ascending
        schedule = sorted(weight_schedule, key=lambda x: x[0])

        returns     = []
        dates       = []
        prev_row    = None

        for i, date in enumerate(self.prices.index):
            curr_row = self.prices.iloc[i]

            if prev_row is None:
                prev_row = curr_row
                continue

            # Find most recent applicable weights (latest entry with date <= today)
            applicable_w = None
            for sched_date, weights in reversed(schedule):
                if pd.Timestamp(sched_date) <= date:
                    applicable_w = weights
                    break

            if applicable_w is None:
                returns.append(0.0)
                dates.append(date)
                prev_row = curr_row
                continue

            valid = {t: w for t, w in applicable_w.items()
                     if t in self.prices.columns and w > 0}
            total = sum(valid.values())
            if total == 0:
                returns.append(0.0)
                dates.append(date)
                prev_row = curr_row
                continue

            valid = {t: w / total for t, w in valid.items()}
            day_ret = sum(
                ((curr_row[t] / prev_row[t]) - 1) * w
                for t, w in valid.items()
                if t in curr_row.index and t in prev_row.index and prev_row[t] > 0
            )
            returns.append(day_ret)
            dates.append(date)
            prev_row = curr_row

        return pd.Series(returns, index=dates, name="rebalanced_portfolio")

    def print_summary(self):
        names = [n for n in self.results if "returns" in self.results[n]]
        col_w = 20
        metrics_order = ["Cumulative Return", "Annualized Return",
                         "Annualized Volatility", "Sharpe Ratio", "Max Drawdown"]
        print("\n" + "=" * (25 + col_w * len(names)))
        print(f"{'BACKTEST SUMMARY':^{25 + col_w * len(names)}}")
        print(f"{'Period: ' + self.start + ' → ' + self.end:^{25 + col_w * len(names)}}")
        print("=" * (25 + col_w * len(names)))
        print(f"\n{'Metric':<25}" + "".join(f"{n:>{col_w}}" for n in names))
        print("-" * (25 + col_w * len(names)))
        for m in metrics_order:
            row = f"{m:<25}"
            for name in names:
                val = self.metrics.summary(self.results[name]["returns"]).get(m, "N/A")
                row += f"{val:>{col_w}}"
            print(row)
        print("\nHoldings:")
        for name in names:
            tickers = list(self.results[name]["valid_weights"].keys())
            print(f"  {name}: {', '.join(tickers)}")
        print("=" * (25 + col_w * len(names)) + "\n")


# ── Two-Profile Plot ──────────────────────────────────────────────────────────

def plot_two_profiles(
    averse_engine: BacktestEngine,
    neutral_engine: BacktestEngine,
    company_name: str,
    save_path: Optional[str] = None,
    kospi_cum: Optional[pd.Series] = None,
    kospi_rolling: Optional[pd.Series] = None,
    kosdaq_cum: Optional[pd.Series] = None,
    kosdaq_rolling: Optional[pd.Series] = None,
) -> plt.Figure:
    """
    Side-by-side 2×2 backtest figure with three benchmarks overlaid.

    Layout
    ------
    Col 0 (left)  → Risk-Averse portfolio
    Col 1 (right) → Risk-Neutral portfolio
    Row 0 (top)   → Cumulative Return
    Row 1 (bottom)→ Rolling Sharpe Ratio (30 trading days)

    Benchmarks (same data overlaid on both columns)
    --------
    EW Benchmark — equal-weight of all analyzed stocks (added as a portfolio
                   inside the engine so KRX data is used consistently)
    KOSPI        — ^KS11 via yfinance (green)
    KOSDAQ       — ^KQ11 via yfinance (purple)
    """
    fig, axes = plt.subplots(2, 2, figsize=(16, 10), sharex="col")
    fig.suptitle(
        f"Backtest Results — {company_name}\n"
        f"{averse_engine.start}  →  {averse_engine.end}",
        fontsize=14, fontweight="bold",
    )

    pairs = [
        ("Risk-Averse",  averse_engine,  axes[:, 0]),
        ("Risk-Neutral", neutral_engine, axes[:, 1]),
    ]

    # All lines are solid; distinguish by color only
    # Portfolio → blue, EW Benchmark → orange, KOSPI → green, KOSDAQ → purple
    PORTFOLIO_COLOR = "#2E86C1"
    EW_COLOR        = "#E67E22"
    KOSPI_COLOR     = "#27AE60"
    KOSDAQ_COLOR    = "#8E44AD"

    for label, engine, (ax_top, ax_bot) in pairs:
        # Anchor x-axis to the engine's start date so the rolling-Sharpe
        # blank warm-up period is visible as empty space, not clipped.
        x_start = pd.Timestamp(engine.start)
        x_end   = pd.Timestamp(engine.end)

        # ── Agent portfolio(s) and EW benchmark ──────────────────────────
        for name, data in engine.results.items():
            if "cumulative_return" not in data:
                continue
            is_ew = name == "EW Benchmark"
            c  = EW_COLOR        if is_ew else PORTFOLIO_COLOR
            lw = 1.6             if is_ew else 2.0
            ax_top.plot(data["cumulative_return"].index,
                        data["cumulative_return"].values,
                        linewidth=lw, linestyle="-", color=c, label=name)
            ax_bot.plot(data["rolling_sharpe"].index,
                        data["rolling_sharpe"].values,
                        linewidth=lw, linestyle="-", color=c, label=name)

        # ── KOSPI overlay (solid, green) ──────────────────────────────────
        if kospi_cum is not None:
            ax_top.plot(kospi_cum.index, kospi_cum.values,
                        linewidth=1.6, linestyle="-", color=KOSPI_COLOR,
                        label="KOSPI")
        if kospi_rolling is not None:
            ax_bot.plot(kospi_rolling.index, kospi_rolling.values,
                        linewidth=1.6, linestyle="-", color=KOSPI_COLOR,
                        label="KOSPI")

        # ── KOSDAQ overlay (solid, purple) ────────────────────────────────
        if kosdaq_cum is not None:
            ax_top.plot(kosdaq_cum.index, kosdaq_cum.values,
                        linewidth=1.6, linestyle="-", color=KOSDAQ_COLOR,
                        label="KOSDAQ")
        if kosdaq_rolling is not None:
            ax_bot.plot(kosdaq_rolling.index, kosdaq_rolling.values,
                        linewidth=1.6, linestyle="-", color=KOSDAQ_COLOR,
                        label="KOSDAQ")

        # ── Formatting ───────────────────────────────────────────────────
        for ax, ylabel, title_suffix, formatter in [
            (ax_top, "Cumulative Return", "Cumulative Return",
             plt.FuncFormatter(lambda y, _: f"{y:.0%}")),
            (ax_bot, "Sharpe Ratio", "Rolling Sharpe (30d)", None),
        ]:
            ax.set_xlim(x_start, x_end)
            ax.axhline(0, color="#555555", linewidth=0.7, linestyle="-")
            ax.set_title(f"{label}  —  {title_suffix}",
                         fontsize=11, fontweight="bold")
            ax.set_ylabel(ylabel, fontsize=9)
            ax.legend(fontsize=8, loc="upper left",
                      framealpha=0.85, edgecolor="#cccccc")
            ax.set_facecolor("#FAFAFA")
            ax.grid(axis="y", color="#e0e0e0", linewidth=0.6)
            if formatter:
                ax.yaxis.set_major_formatter(formatter)

        ax_bot.xaxis.set_major_formatter(mdates.DateFormatter("%Y-%m"))
        ax_bot.xaxis.set_major_locator(mdates.MonthLocator())
        plt.setp(ax_bot.xaxis.get_majorticklabels(), rotation=30, fontsize=8)

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"[Saved] {save_path}")

    return fig
