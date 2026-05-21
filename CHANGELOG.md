# Changelog

All notable changes to AlphaAgents are documented here.  
Format: `[YYYY-MM-DD] — Summary`

---

## [2026-05-21] — Remove legacy "Convert MD Reports" option

- `main.py` + `web/runner.py` — removed the `[C] Convert MD Reports` menu option from both terminal and web UI startup
- JSON signal files are now auto-generated on every analysis run, so manual conversion is no longer needed

---

## [2026-05-21] — Reorganise output into dated + per-stock subdirectories

### New folder structure
```
reports/
  {run_date}/                          ← date the analysis was run
    {ticker}_{company_name}/           ← e.g. 214150_클래시스/
      {ticker}_{company}_{date}_averse.md
      {ticker}_{company}_{date}_neutral.md
      {ticker}_{company}_{date}.json   ← auto-generated signal file
    Exec_Sum_{analysis_date}.pdf       ← multi-stock PDF at run-date level
```

### What changed
- `orchestrator/orchestrator_agent.py`
  - `analyze_stock()` builds `reports/{run_date}/{ticker}_{name}/` directory and saves all MD and JSON files there
  - Signal JSON renamed from `*_signals.json` → `{ticker}_{name}_{date}.json` (no suffix needed)
  - `finalize()` saves PDF inside `reports/{run_date}/` and returns the path
- `main.py` — `_list_signal_files()` searches 2 levels deep (`*/*/*.json`); `_find_md_pairs()` uses recursive glob; MD-to-JSON conversion saves next to source MDs
- `web/runner.py` — signal file glob updated to match new depth; `_standard_backtest` uses return value of `finalize()` for PDF path

---

## [2026-05-21] — Web UI: in-browser file download

- `web/app.py` — added `GET /download?file=reports/...` route; serves files as attachments with path-traversal protection (restricted to `reports/` directory only)
- `web/session.py` — `stock_result()` accepts `signal_file=` path
- `web/runner.py` — globs for signal JSON after each stock analysis, passes path to UI
- `templates/ui.html` — done card shows **Download PDF** button (gold) + a **signals.json** button per stock; note about server restart window

---

## [2026-05-21] — Web UI (Flask + SocketIO)

- `web/app.py` — Flask + SocketIO server on port 5001; `async_mode="threading"` for broad Python version compatibility
- `web/session.py` — `WebSession` bridges the background pipeline thread and the browser using `threading.Event`; `ask()` blocks until the user responds, all other methods emit real-time events
- `web/runner.py` — full pipeline rewrite using `session.ask()` instead of `input()`; supports new analysis, load-signals, standard backtest, and rebalancing modes
- `templates/ui.html` — dark Bloomberg-style chat UI with real-time agent debate cards, stock pool sidebar, step tracker, and SocketIO client
- `orchestrator/orchestrator_agent.py` — `_run_debates()` forwards `progress_cb` to `DebateManager.run()`, completing the full real-time progress callback chain
- `requirements.txt` — added `flask`, `flask-socketio`, `simple-websocket`
- `render.yaml` + `Procfile` — Render deployment config; start command `python3 web/app.py`; build command installs `fonts-nanum` for Linux Korean font support

SocketIO event protocol:
| Direction | Event | Payload |
|---|---|---|
| Server → Client | `s_message` | `{text, msg_type, subtext}` |
| Server → Client | `s_question` | `{text, subtext, input_type, options}` |
| Server → Client | `s_progress` | `{text}` |
| Server → Client | `s_debate_start` | `{ticker, name}` |
| Server → Client | `s_agent_update` | `{agent, status, signal, round}` |
| Server → Client | `s_stock_result` | `{ticker, name, results, signal_file}` |
| Server → Client | `s_done` | `{pdf_path}` |
| Client → Server | `c_start` | — |
| Client → Server | `c_input` | `{value}` |

Run locally:
```bash
python3 web/app.py        # → http://localhost:5001
```

---

## [2026-05-21] — Debug mode + model override

- `config.py`
  - `DEBUG_MODE = os.getenv("DEBUG_MODE", "false")` — skips all LLM calls when `true`
  - `CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")` — overrideable via env var
- `agents/base_agent.py` — both `call_llm()` and `call_llm_with_cache()` return a stub immediately when `DEBUG_MODE=true`; no API call is made

Usage:
```bash
DEBUG_MODE=true python3 main.py              # zero tokens — test data pipeline only
CLAUDE_MODEL=claude-haiku-4-5 python3 main.py  # real analysis at ~20x lower cost
```

---

## [2026-05-21] — Prompt caching (Tier 2): data blobs cached across debate rounds

### What changed
- `agents/base_agent.py` — added `call_llm_with_cache(cached_data, dynamic_prompt)`
  - Sends the user message as two separate content blocks
  - Block 1: agent's data blob with `cache_control: ephemeral` — cached after Round 0
  - Block 2: task instruction + peer analyses — always fresh, never cached
  - OpenAI fallback combines both blocks into a single message (no caching)
- All 5 agents (`FundamentalAgent`, `SentimentAgent`, `TechnicalAgent`, `MarketAgent`, `MacroAgent`)
  - `analyze()` and `update_position()` now split into `cached = <data>` and `dynamic = <instructions>`
  - Both call `call_llm_with_cache()` instead of `call_llm()`
  - The data string (`fundamental_data`, `sentiment_data`, etc.) is byte-for-byte identical across all rounds → Round 0 writes cache, Rounds 1–3 read it

### Why
- Each agent's own data (DART financials, pykrx metrics, macro indicators, etc.) is re-sent on every debate round even though it never changes between rounds
- Biggest winner: FundamentalAgent — DART financial reports can be 3,000–6,000 tokens, resent up to 8× per stock (4 rounds × 2 profiles)
- Combined with Tier 1 (system prompt caching), the only tokens paid at full price per call are the dynamic parts: company name, peer analyses block, round number

---

## [2026-05-21] — Prompt caching (Tier 1): system prompts + debate instructions

### What changed
- `agents/base_agent.py`
  - `call_llm()` now passes `system` as a list with `cache_control: ephemeral` so Anthropic caches the full system prompt across debate rounds
  - Added `_DEBATE_APPENDIX` — a single block containing both `STEELMAN_INSTRUCTION` and `CHALLENGE_INSTRUCTION`, appended to `self.system_prompt` in `BaseAgent.__init__`
  - `STEELMAN_INSTRUCTION` and `CHALLENGE_INSTRUCTION` are retained as module-level constants for reference but are no longer injected into user messages
- All 5 active agents (`FundamentalAgent`, `SentimentAgent`, `TechnicalAgent`, `MarketAgent`, `MacroAgent`)
  - Removed `{STEELMAN_INSTRUCTION}` from every `analyze()` user prompt
  - Removed `{CHALLENGE_INSTRUCTION}` from every `update_position()` user prompt
  - Import changed from `BaseAgent, STEELMAN_INSTRUCTION, CHALLENGE_INSTRUCTION` → `BaseAgent`

### Why
- System prompt + debate instructions are identical across all debate rounds (Round 0–3) for the same agent and risk profile
- Caching them reduces input token cost by ~90% for those tokens on every call after the first
- Per stock (2 profiles × up to 4 rounds × 5 agents = 40 calls): 1 cache write + 39 cache reads instead of 40 full reads
- Estimated ~80% reduction in system-prompt-related input token spend

---

## [2026-05-20] — Upgrade SentimentAgent: DART disclosures + pykrx investor flow + short selling

### Problem
yfinance news for Korean stocks returns empty results — SentimentAgent had no real data to analyse.

### New file
- `tools/sentiment_tools.py` — three reliable Korean-market sentiment sources:
  - **D — DART disclosures** (`fetch_dart_disclosures`): corporate events over the quarter window via `/api/list.json`; categorises each filing (Litigation, Dilution Risk, Buyback, M&A, Insider Ownership Change, Fair Disclosure, Material Event); capped at 20 entries to limit token usage
  - **E — pykrx investor flow** (`fetch_investor_flow`): cumulative 3-month net buying in KRW for Foreign, Institutional, and Retail investors via `get_market_net_purchases_of_equities_by_investor()`; includes interpretation (accumulation vs distribution signal)
  - **F — pykrx short selling** (`fetch_short_selling`): average / max / recent 5-day short ratio and trend (Rising/Falling/Stable) via `get_market_short_selling_volume_by_date()`; threshold labels (>5% high, 2.5–5% moderate, <2.5% low)
  - `format_sentiment_data_for_llm()` — combines all three into a single structured string with 3 labelled sections
  - `fetch_sentiment_data()` — master function called by orchestrator

### Updated files
- `agents/sentiment_agent.py` — system prompts rewritten to cover the three new data sources; both risk profiles now explicitly reference D/E/F sections and signal interpretation guidelines
- `orchestrator/orchestrator_agent.py` — replaced `fetch_news()` + `format_news_for_llm()` with `fetch_sentiment_data()`; yfinance retained only for `get_yfinance_ticker()` (exchange suffix for market tools)

---

## [2026-05-20] — Upgrade MarketAgent data: DART sector, pykrx peers, KOSDAQ benchmark

### A — Peer returns via pykrx (replaces yfinance)
- `get_peer_comparison()` now uses `krx.get_market_ohlcv_by_date()` for 3-month peer returns — more reliable than yfinance for KRX stocks
- yfinance retained only for peer names and P/E/P/B ratios (optional, graceful fallback to ticker code)
- `KOREAN_SECTOR_PEERS` expanded with KOSDAQ peers in Healthcare and other sectors

### B — KOSDAQ benchmark alongside KOSPI
- `format_market_data_for_llm()` now displays both KOSPI and KOSDAQ 3M returns
- Primary benchmark auto-selected from DART `corp_cls`: KOSPI stocks compare to KOSPI (★), KOSDAQ stocks compare to KOSDAQ (★)
- Benchmark returns reused from pykrx fetch already performed for TechnicalAgent — no duplicate API call

### D — DART sector detection replaces yfinance `.info`
- New `KSIC_TO_SECTOR` mapping (33 entries, covers C10–C33 manufacturing sub-divisions + major divisions A–S)
- `ksic_to_sector()` maps DART `induty_code` to sector: tries 3-char prefix first (`"C26"` → Technology), then 1-char fallback (`"C"`)
- `get_company_sector_info()` uses DART `corp_info` as primary: `induty_code` → sector, `corp_cls` → exchange label
- yfinance `.info` optionally enriches valuation ratios and business description (all fields `None` if unavailable)
- Orchestrator passes `corp_info` (not `ticker_obj`) to sector function; `get_kospi_return()` import removed

---

## [2026-05-20] — Upgrade ValuationAgent → TechnicalAgent with full indicator suite

### New files
- `tools/pykrx_tools.py` — KRX price fetching via pykrx (replaces yfinance for stock prices)
  - `fetch_ohlcv(stock_code, as_of_date, months, offset_months)` — current or prior-quarter window
  - `fetch_index_ohlcv(index_code, ...)` — KOSPI (1001) and KOSDAQ (2001) index data
- `agents/technical_agent.py` — replaces `agents/valuation_agent.py`
  - System prompt updated to explicitly cover MA, RSI, Bollinger Bands, relative performance, QoQ delta
  - Agent name changed to `"TechnicalAgent"`

### Updated files
- `tools/metrics_tools.py` — extended from 8 basic metrics to a full technical analysis dataset:
  - **Moving Averages**: 20d MA, 60d MA, % vs MA, consecutive days closing below MA20
  - **RSI**: 14-day Wilder's RSI with overbought (>70) / oversold (<30) zone labels
  - **Bollinger Bands**: 20d ±2σ; %B position (0 = lower band, 1 = upper band); normalised band width
  - **Relative Performance**: stock alpha vs KOSPI and KOSDAQ over the identical 3-month window
  - **QoQ Delta**: period return change and annualised volatility change vs the prior quarter
  - `format_metrics_for_llm()` now outputs 4 labelled sections for the agent
- `debate/debate_manager.py` — `ValuationAgent` → `TechnicalAgent`; parameter `valuation_data` → `technical_data`
- `orchestrator/orchestrator_agent.py` — `_fetch_data()` fetches current quarter, previous quarter, KOSPI, and KOSDAQ via pykrx; yfinance retained only for SentimentAgent news with graceful fallback
- `report/report_generator.py` — "Key Valuation Metrics" → "Key Technical Metrics"; added RSI, MA20, alpha vs KOSPI, and return QoQ Δ rows to metric table

---

## [2026-05-20] — Dynamic DART report planning for FundamentalAgent

- New `tools/dart_report_planner.py` module:
  - `plan_reports(as_of_date, stage)` determines which DART reports to fetch based on Korea's actual filing calendar (annual deadline: Mar 31, Q1: May 15, H1: Aug 14, Q3: Nov 14)
  - `stage="initial"` fetches 3 annual FYs + all available interim reports (full picture)
  - `stage="rebalancing"` fetches 1 annual FY + single most recent interim report (delta focus)
  - `build_coverage_note()` generates an LLM-readable list of fetched vs. missing periods
- `tools/dart_tools.py` — added `fetch_and_format_reports()` to fetch multiple reports per plan and format into one string; extracted `_format_single_fs()` as a shared helper; added clear warning when DART returns no data
- `orchestrator/orchestrator_agent.py` — `analyze_stock()` and `_fetch_data()` now accept `stage` parameter and use the planner instead of hardcoded `year - 1` logic
- `rebalance/rebalance_engine.py` — `_run_quarter_analysis()` auto-derives stage from `q_num` (Q1 → "initial", Q2+ → "rebalancing")

---

## [2026-05-20] — Fix: auto-select rebalancing file when only one exists

- When only one `Rebalanced_*.json` file exists in `reports/`, `main.py` now skips the file-selection prompt and loads it automatically
- Removes confusing "select file" interaction when there is no real choice to make

---

## [2026-05-20] — Add [B] mode: load saved rebalancing → backtest

- `main.py` — post-analysis menu now includes `[B] Load saved rebalancing` option when any `Rebalanced_*.json` files exist
- `_save_rebalancing_json()` serialises weight schedule + quarterly log to `reports/Rebalanced_{start_date}.json` after every [R] run
- `_load_rebalancing_json()` deserialises JSON back to Python, reconstructing `[(datetime, weights_dict), ...]` for the weight schedule
- `_load_rebalancing_backtest_flow()` loads the JSON, optionally extends the end date, runs the rebalanced backtest, and generates a PDF — zero LLM calls
- `results` field excluded from serialised quarterly log (too large; per-stock signals already persisted in individual JSON files)

---

## [2026-05-20] — Show all quarterly portfolios in rebalanced PDF executive summary

- `report/summary_renderer.py` — added `_build_rebalance_history()` which renders a table of all quarterly portfolio snapshots (Q1 start, Q2 rebalance, Q3 rebalance …) with BUY cells in blue and SELL/excluded cells in red
- `build_pdf()` now accepts an optional `quarterly_log` parameter; when provided, the PDF gains a §1 Rebalancing History section and section numbers shift accordingly
- Previously the PDF only showed the final quarter's portfolio, hiding the rebalancing decisions that drove returns

---

## [2026-05-20] — Refactor: rebalancing choice moved to post-analysis prompt

- Replaced top-level `[R] Rebalancing` main menu option with a post-analysis `[S] Standard backtest / [R] Rebalancing` prompt shown after stock analysis completes
- Both paths share the same debate results, avoiding any duplicate LLM analysis
- Q1 rebalancing re-uses pre-computed results from the `[N]`/`[L]` analysis via `initial_results` parameter passed to `RebalanceEngine.run()`
- Benchmark selection adapts to the chosen path: standard backtest shows KOSPI/KOSDAQ vs EW; rebalanced backtest shows the same benchmarks against the time-varying rebalanced portfolio

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
