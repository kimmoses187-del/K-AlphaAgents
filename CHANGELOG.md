# Changelog

All notable changes to AlphaAgents are documented here.  
Format: `[YYYY-MM-DD] — Summary`

---

## [2026-05-20] — Add portfolio rebalancing ([R] mode)

- New `rebalance/` module with three components:
  - `rebalance_engine.py` — orchestrates quarterly LLM rebalance + intra-quarter event monitoring
  - `event_monitor.py` — detects 3 triggers: price drop >8%, 20d vol >40%, price below 20d MA ×3 days
  - `weight_adjuster.py` — redistributes equity weights via momentum scores without LLM calls
- `backtest/engine.py` — added `run_with_schedule()` for time-varying weight backtest
- `backtest/runner.py` — added `run_rebalanced_backtest()` for chained rebalancing periods
- `main.py` — added `[R] Rebalancing` mode with full interactive flow
- Quarterly rebalance: full 5-agent debate re-runs every 3 calendar months with fresh data
- Event triggers: intra-quarter momentum-based re-weighting fires without any LLM calls
- Output: `Exec Sum_Rebalanced_{start-date}.pdf` with final-quarter portfolio + backtest chart

---

## [2026-05-03] — Remove min conviction threshold

- All BUY stocks now qualify for equity allocation regardless of conviction score
- Conviction score is still computed and still drives conviction-proportional weighting — just no longer used as an entry gate
- Removed `min_conviction` from `PROFILE_CONFIG` in `portfolio/portfolio_agent.py`
- Updated orchestrator console output and README accordingly

---

## [2026-05-03] — Replace S&P 500 benchmark with KOSPI and KOSDAQ

- Backtest benchmarks changed from S&P 500 (`^GSPC`) to KOSPI (`^KS11`, green) and KOSDAQ (`^KQ11`, purple)
- Updated `backtest/runner.py`, `backtest/engine.py`, `report/summary_renderer.py`, and README

---

## [2026-05-03] — Initial full system push

- 5-agent debate pipeline: FundamentalAgent, SentimentAgent, ValuationAgent, MarketAgent, MacroAgent
- OrchestratorAgent directing full fetch → debate → portfolio → backtest → PDF flow
- PortfolioAgent with conviction scoring (Option B: expertise weighting) and risk-averse / risk-neutral profiles
- BacktestEngine with KRX/yfinance fetchers, rolling Sharpe (30-day), and two-profile side-by-side chart
- Institutional reportlab PDF executive summary with Korean font support (AppleGothic)
- Three-mode main menu: `[N]` New analysis, `[L]` Load saved signals, `[C]` Convert MD reports
- Signal JSON persistence for reload without re-analysis
- File naming convention: `{ticker}_{name}_{as-of-date}_{profile}.md` / `_signals.json` / `Exec Sum_{date}.pdf`
- EW Benchmark (orange) overlaid on backtest chart alongside index benchmarks
- README with full system overview, conviction formula, usage examples

---

## [2026-05-02] — Backtest and PDF fixes

- Fixed `NameError: name 'stock_tag' is not defined` in orchestrator finalize
- Added end-date validation: backtest end date must be after analysis as-of date
- Fixed `as_of_date` threading: MD files now store the user-typed analysis date, not the run timestamp
- Added `Data As-Of` field to all MD report headers
- Backtest chart redesigned: all solid lines, legends on every subplot, x-axis anchored to start date, rolling Sharpe warm-up period left blank
- Removed Stop-loss and Take-profit from PDF Portfolio Allocation and Metrics sections

---

## [2026-05-02] — File naming and reportlab PDF

- Output file naming changed to `{ticker}_{corp name}_{as-of date}` for MD/JSON and `Exec Sum_{as-of date}` for PDF
- Renamed all existing files in `reports/` to match new scheme
- Replaced matplotlib-based PDF with institutional reportlab design (navy/gold, Korean font support)
- Page 1: Signal table (BUY/SELL badges, RA/RN columns) + Portfolio allocation cards + Donut pie charts
- Page 2: LLM cross-profile narrative + Portfolio metrics + Backtest chart

---
