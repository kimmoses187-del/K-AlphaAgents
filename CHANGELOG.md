# Changelog

All notable changes to AlphaAgents are documented here.  
Format: `[YYYY-MM-DD] — Summary`

---

## [2026-05-22] — Render deployment fixed: bundled Korean font + Werkzeug flag

### Problems

Two separate issues were preventing the Render deployment from going live:

**1. Build failure — `apt-get install -y fonts-nanum` (exit code 100)**

`render.yaml` used `apt-get` to install `fonts-nanum` (NanumGothic) before running `pip`. Render's Python runtime build process runs without root access, so `apt-get` failed immediately with:

```
E: Could not open lock file /var/lib/dpkg/lock-frontend - open (13: Permission denied)
```

Additionally, the Render dashboard had this build command saved as a service-level override that took precedence over `render.yaml`.

**2. Runtime crash — Werkzeug production guard (exit code 1)**

After the build issue was resolved, `flask-socketio 5.6` raised:

```
RuntimeError: The Werkzeug web server is not designed to run in production.
Pass allow_unsafe_werkzeug=True to the run() method to disable this error.
```

### Fixes

**Font — bundle `NanumGothic.ttf` in the repository**

- Downloaded `NanumGothic.ttf` (2 MB) from Google Fonts and committed it to `fonts/NanumGothic.ttf`
- Updated `_find_font()` in `report/summary_renderer.py` to check the bundled path first for both `_KOREAN_FONT` and `_UNICODE_FONT`:
  ```python
  os.path.join(_HERE, "fonts", "NanumGothic.ttf"),   # bundled — works everywhere
  "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",  # Linux fallback
  "/System/Library/Fonts/Supplemental/AppleGothic.ttf" # macOS fallback
  ```
- Removed `apt-get install -y fonts-nanum &&` from `render.yaml` buildCommand
- Fixed the build command in the Render dashboard (Settings → Build) to match: `pip install -r requirements.txt`

**Werkzeug — add `allow_unsafe_werkzeug=True`**

Updated `web/app.py` line 89:
```python
# Before
socketio.run(app, host="0.0.0.0", port=port, debug=False)
# After
socketio.run(app, host="0.0.0.0", port=port, debug=False,
             allow_unsafe_werkzeug=True)
```

### Result

Service is live at **https://alpha-agents-su4l.onrender.com**

### Files changed

- `fonts/NanumGothic.ttf` — added (2 MB, bundled Korean font)
- `report/summary_renderer.py` — `_find_font()` checks bundled path first
- `render.yaml` — buildCommand simplified to `pip install -r requirements.txt`
- `web/app.py` — `allow_unsafe_werkzeug=True` added to `socketio.run()`

---

## [2026-05-21] — New report folder structure: run date → as-of date → stock → profile

### What changed

The `reports/` directory now follows a four-level hierarchy:

```
reports/{run_date}/{as_of_date}/{ticker}_{name}/{neutral|averse}/
```

Previously outputs were saved as:
```
reports/{run_date}/{ticker}_{name}/{ticker}_{name}_{as-of}_averse.md
reports/{run_date}/{ticker}_{name}/{ticker}_{name}_{as-of}_neutral.md
reports/{run_date}/Exec_Sum_{as-of}.pdf
```

They are now saved as:
```
reports/{run_date}/{as_of_date}/{ticker}_{name}/neutral/{ticker}_{name}_{as-of}_neutral.md
reports/{run_date}/{as_of_date}/{ticker}_{name}/averse/{ticker}_{name}_{as-of}_averse.md
reports/{run_date}/{as_of_date}/{ticker}_{name}/{ticker}_{name}_{as-of}.json
reports/{run_date}/{as_of_date}/backtest/buy_and_hold/Exec_Sum_{as-of}.pdf
```

For rebalancing runs, quarterly analyses and outputs go under:
```
reports/{run_date}/{as_of_date}/backtest/rebalance/Q{n}/{ticker}_{name}/neutral/
reports/{run_date}/{as_of_date}/backtest/rebalance/Q{n}/{ticker}_{name}/averse/
reports/{run_date}/{as_of_date}/backtest/rebalance/Rebalanced_{as-of}.json
reports/{run_date}/{as_of_date}/backtest/rebalance/Exec_Sum_Rebalanced_{as-of}.pdf
```

Q1 analysis (initial debate) lives at the top `{as_of_date}/{ticker}_{name}/` level and is not duplicated into a Q1 folder.

### Why

- **Run date vs as-of date** were conflated in the old structure. The run date is when you executed the pipeline; the as-of date is the data cutoff. Separating them makes it easy to re-run analysis on the same historical date on a different day without overwriting results.
- **Profile subfolders** (`neutral/`, `averse/`) make it immediately clear which MD file belongs to which investor profile without reading the filename suffix.
- **`backtest/` subfolder** cleanly separates analysis outputs (per-stock MDs and JSONs) from portfolio/backtest outputs (PDFs, Rebalanced JSON), keeping each stock folder focused on signal data only.
- **Rebalancing quarters under `backtest/rebalance/Q{n}/`** give each quarterly re-analysis its own space while keeping all quarters under the same rebalancing run directory.

### Files changed

- `orchestrator/orchestrator_agent.py` — `analyze_stock()` creates `neutral/` and `averse/` subdirs; accepts optional `output_dir` for rebalancing quarters. `finalize()` writes PDF to `backtest/buy_and_hold/`.
- `rebalance/rebalance_engine.py` — imports `_safe_filename`; threads `run_dir` through `run()` → `_run_quarter_analysis()`; builds per-stock `output_dir` for Q2+.
- `main.py` — glob updated to 4-level depth for signal file discovery; `_save_rebalancing_json()` accepts `save_dir`; `_run_rebalancing()` computes `run_dir` once and routes both PDF and JSON into `backtest/rebalance/`.

### Existing files

All previously saved reports were migrated to the new structure in-place.

---

## [2026-05-21] — Conviction scoring: connectedness-based agent weights

### Problem with the original weights

The original weights were ordered by *data hardness* (how quantitative or audited the data was):

| Agent | Old weight | Rationale |
|---|---|---|
| FundamentalAgent | 0.30 | Hardest quantitative data |
| TechnicalAgent | 0.25 | Direct price-signal evidence |
| MacroAgent | 0.20 | Structural macro context |
| MarketAgent | 0.15 | Industry positioning |
| SentimentAgent | 0.10 | Softest / most noisy signal |

This ordering made sense when agents had varying data quality. But all five agents now have substantial, structured data inputs — DART financials, pykrx metrics, disclosure feeds, macro indicators — so treating SentimentAgent's data (investor flow + short selling + DART disclosures) as worth only a third of FundamentalAgent's no longer reflects reality.

### Why connectedness is a better criterion

The more meaningful difference between agents is **how directly their data connects to the specific company being analysed**:

- **Direct agents** draw on data that is specific to the firm: financial statements (Fundamental), corporate events and investor flows (Sentiment), and the stock's own price/volume history (Technical). If the company performs well or poorly, this data reflects it immediately.
- **Indirect agents** draw on contextual data that surrounds the company but is not company-specific: sector cycle and peer comparisons (Market), macro environment and capital flows (Macro). This data matters, but it is one layer removed from the firm itself.

Weighting by connectedness — rather than data hardness — preserves differentiation while being more defensible: it reflects how much of each agent's signal is about *this company* versus *the world around it*.

### New weights (`portfolio/portfolio_agent.py`)

| Group | Agents | Each weight | Group total |
|---|---|---|---|
| Direct | FundamentalAgent · SentimentAgent · TechnicalAgent | **0.2167** | 65% |
| Indirect | MacroAgent · MarketAgent | **0.1750** | 35% |

- Each direct agent individually outweighs each indirect agent (0.2167 > 0.1750)
- Within each group, agents carry equal weight — no further subjective ordering
- Weights are normalised at runtime so they sum to exactly 1.0, avoiding floating-point drift

---

## [2026-05-21] — Risk profile overhaul: prompt framework + signal extraction

### 1. `extract_signal()` — fixed for both profiles (`agents/base_agent.py`)

**Problem:**
The old implementation scanned every line of the LLM response from the bottom up, returning the first line that contained the word BUY or SELL *anywhere* in the text. This caused false matches from normal English phrases in the body of the analysis:
- `"risk of a SELL-off"` → incorrectly returned SELL
- `"not a BUY signal at this level"` → incorrectly returned BUY
- `"avoid the urge to SELL prematurely"` → incorrectly returned SELL

Since every agent is explicitly prompted to end with `RECOMMENDATION: BUY` or `RECOMMENDATION: SELL`, the scanner was regularly picking up the wrong sentence before reaching that line, producing arbitrary signals disconnected from the agent's actual conclusion.

**Fix:**
```python
import re
match = re.search(r"RECOMMENDATION:\s*(BUY|SELL)", text, re.IGNORECASE)
if match:
    return match.group(1).upper()
return "SELL" if risk_profile == "risk-averse" else "BUY"
```
The function now targets only the `RECOMMENDATION:` line the agents are instructed to write. The profile-default fallback only triggers if the agent failed to write that line at all.

---

### 2. Risk profile prompt framework — all 5 agents (`agents/*.py`)

**Problem:**
The old prompts conflated *risk-averse* with *permanently bearish*. Every agent's risk-averse prompt contained an explicit fallback instruction such as:
- `"When in doubt, SELL"` (FundamentalAgent)
- `"Mixed or uncertain picture → lean SELL"` (SentimentAgent)
- `"When signals are mixed or ambiguous → default to SELL"` (TechnicalAgent)
- `"When industry outlook is uncertain → lean SELL"` (MarketAgent)
- `"In macro uncertainty, lean SELL"` (MacroAgent)

In practice, real-world data is almost always mixed — some indicators positive, some negative. This meant every agent's "when in doubt" fallback fired on nearly every stock, producing unanimous SELL regardless of actual company quality. The prompts were also telling agents to *look for* negative signals rather than *weigh* them differently, causing information bias at the observation stage.

**Why this was wrong:**
Risk-averse and risk-neutral are defined by how an investor *weights* risk versus return in their final judgment — not by what they look for in the data. A risk-averse investor still reads all available information; they simply give more weight to potential losses than potential gains when making their decision.

**Fix — two-step structure applied to all 5 agents × 2 profiles:**

```
Step 1 — Read all data objectively:
  Both profiles analyse the full dataset without filtering or dismissing
  any signal. Observation is identical and profile-agnostic.

Step 2 — Apply the profile lens to the final judgment:
  Risk-averse:  when risks and returns are of similar magnitude, risk wins → lean SELL
  Risk-neutral: when risks and returns are of similar magnitude, return wins → lean BUY
```

This means:
- Risk-averse agents can and will recommend BUY — when the return case clearly outweighs the risk
- Risk-neutral agents can and will recommend SELL — when risks clearly dominate
- The profiles produce a genuine spectrum rather than a fixed binary outcome
- No information is filtered at the observation stage; the difference is entirely in the final weighting

**Files changed:** `agents/fundamental_agent.py`, `agents/sentiment_agent.py`, `agents/technical_agent.py`, `agents/market_agent.py`, `agents/macro_agent.py`

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
