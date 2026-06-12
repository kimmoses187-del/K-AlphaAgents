# Changelog

All notable changes to AlphaAgents are documented here.  
Format: `[YYYY-MM-DD] — Summary`

---

## [2026-06-12] — Selectable Debate Agents (odd count)

**What:** The user now chooses which of the five analysis agents (Fundamental / Sentiment / Technical / Market / Macro) debate each stock. The picker enforces an **odd count (1, 3, or 5)** so the binary BUY/SELL majority vote always has a strict majority — an even pick is rejected with a prompt to re-choose. Default remains all five.

**Why:** Not every stock needs all five lenses, and running fewer agents cuts LLM debate cost proportionally. Odd-only keeps the vote unambiguous without relying on the SELL-default tie-break.

**How — design:** Same derive-from-data approach as selectable profiles. The selection is threaded only at the entry edge (`analyze_stock(agents=…) → _run_debates → DebateManager`); everything downstream reads the agent set from each debate's results. Conviction weighting re-normalises over the agents that actually ran, so a 3-agent unanimous vote still scores 1.0 (the full set sums to 1.0 → unchanged; a subset sums to <1.0 → scaled up).

**How — changes:**
- `config.py` — `ALL_AGENTS` + `AGENT_LABELS` / `agent_label` helper.
- `debate/debate_manager.py` — refactored the five hard-wired agent calls into a registry-driven loop (`_AGENT_REGISTRY`: name → class + data-key); `DebateManager(agents=…)` runs only the selected set, in canonical order.
- `portfolio/portfolio_agent.py` — `compute_conviction` re-normalises `AGENT_WEIGHTS` over the agents present in the result.
- `orchestrator/orchestrator_agent.py` — `analyze_stock` / `_run_debates` accept `agents`, passed to `DebateManager` and `DebateGrid`.
- `main.py` / `web/runner.py` — agent pickers with odd-count validation (re-ask on even).
- `debate/terminal_display.py` — `DebateGrid(agents=…)` renders only selected agent rows; `templates/ui.html` builds agent rows lazily.
- `rebalance/rebalance_engine.py` — derives the agent set from prior results and threads it into Q2+ re-analysis.

**Impact:** Fewer agents → proportionally lower debate cost. Default (all five) is unchanged. Verified via `DEBUG_MODE=true` runs (3-agent single-profile through backtest/PDF/XLSX/DOCX; 5-agent both-profile regression) and unit tests for conviction re-normalisation and the odd-count guard.

**Known limitation:** Data fetch still pulls all five data blobs even when fewer agents are selected (the saving is in the LLM debate calls, not the data APIs). De-selected agents' data fetch could be skipped later, but the sources are partly shared (pykrx prices feed technical + market + sentiment).

---

## [2026-06-11] — Selectable Risk Profiles

**What:** The user now chooses which risk profile(s) to analyse at the start of a New Analysis — **Risk-Averse only**, **Risk-Neutral only**, or **Both** (default). A single-profile run produces one debate per stock, one portfolio, one backtest, and a single-column PDF / XLSX / DOCX. Previously every run always computed both profiles side by side.

**Why:** Running both profiles doubles LLM cost and clutters the output when an investor only cares about one risk stance. Making the set selectable lets a user get a focused, single-portfolio analysis at half the debate cost.

**How — design:** The selection is captured once at the entry point and then *derived from the data* everywhere downstream, rather than threaded as an argument through every layer. `analyze_stock(profiles=…)` runs only the chosen debates, so each stock's `debate_results` carries only the selected profiles; from there `portfolios.keys()` is the single source of truth for the portfolio, backtest, report, exports, and rebalancing stages. Saved single-profile signals therefore stay single-profile through every reload/backtest/rebalance path with no extra plumbing. See decision note `selectable-risk-profiles-derive-from-data`.

**How — changes:**
- `config.py` — new `ALL_PROFILES` constant + `profile_label` / `profile_tag` / `profile_short` helpers (single source of truth for profile metadata).
- Entry points — `main.py` `_ask_profiles()` prompt and `web/runner.py` button picker; both pass `profiles` into `analyze_stock`.
- `orchestrator/orchestrator_agent.py` — `analyze_stock` / `_run_debates` accept `profiles`; `finalize` and `_llm_narrative` derive the active set from `portfolios.keys()` (single-profile gets a solo narrative, not a cross-profile synthesis).
- `portfolio/portfolio_agent.py` — `construct_portfolio` builds only the profiles present in its input.
- `backtest/runner.py` + `backtest/engine.py` — backtest loops over present profiles; new 2×N `plot_profiles()` renders one or two columns; `plot_two_profiles()` retained as a thin wrapper.
- `report/summary_renderer.py` — signal table, profile cards, metrics table, donut pies, and rebalance-history tables all rebuilt dynamically per profile.
- `report/exporters.py`, `rebalance/rebalance_engine.py` — derive profiles from data.
- UI — `templates/ui.html` builds debate-grid columns lazily; terminal `DebateGrid(profiles)` shows only selected columns.

**Impact:** Single-profile runs roughly halve debate LLM cost. Default behaviour (Both) is unchanged. Verified via `DEBUG_MODE=true` runs (single + dual, through to backtest/PDF/XLSX/DOCX) and synthetic single/dual-profile render tests.

**Known limitation:** The legacy CLI MD-pair converter (`_convert_md_to_signals_flow`) still assumes both profiles, but it is an orphan helper not wired into the N/L/B menu; all live load flows use the JSON signals and handle single-profile saves. No `risk-seeking` profile exists yet — the agents only carry averse/neutral prompts.

---

## [2026-05-28] — Data Enrichment & Output Enhancements

Nine targeted improvements were shipped in one batch. Token cost impact is shown per item.

---

### Gap 1 — DART Full Filing Documents (`tools/dart_document_tools.py`)

**What:** FundamentalAgent now receives the full narrative text of DART filings — MD&A, risk factors, business description, financial highlights, and outlook — in addition to the structured financial statement numbers it already had.

**Why:** Quantitative ratios tell you *what* happened; narrative disclosures tell you *why* and *what management expects next*. Without the narrative, the agent was working with numbers stripped of context — unable to detect one-off charges, product-line pivots, regulatory risks, or management guidance.

**How:**
- New `tools/dart_document_tools.py` with `fetch_document_narrative(corp_code, reports_plan, max_tokens=8000) → str`
- Calls DART `/api/list.json` to get filing reception numbers, then downloads each report as a ZIP (`/api/document.xml?rcpNo=...`)
- Unzips in-memory, parses HTML with BeautifulSoup, strips boilerplate, extracts target sections by Korean keyword headers (사업의 내용, 위험요소, MD&A, 재무제표에 관한 사항, 향후 전망)
- Caps at 28,000 characters (~8,000 tokens) across all reports combined; fails silently with `""` on any error
- Called in `orchestrator/_fetch_data()` and injected into FundamentalAgent's context block

**Potential impact:** +~4,000–8,000 input tokens per stock (~$0.012–$0.024 per stock) from the narrative text; cached from Round 1 onward so cost per debate round is negligible. Expected improvement in FundamentalAgent's ability to identify qualitative risks and forward-looking signals.

---

### Gap 2 — DCF + Peer Comps Valuation (`tools/valuation_tools.py`)

**What:** FundamentalAgent now receives a structured valuation context including a 5-year DCF with Bear/Base/Bull scenarios and a peer P/E / P/B comparison table.

**Why:** Without an intrinsic value anchor, the agent was forming price judgments with no reference point beyond trend direction. The DCF provides a quantitative floor/ceiling; the comps table shows relative market pricing vs sector peers.

**How:**
- New `tools/valuation_tools.py` with `build_valuation_context(fs_years, peers, ticker_str, company_name) → str`
- `_build_dcf_summary()`: parses DART `fnlttSinglAcnt` response dicts for revenue, operating income, and FCF; computes 3-year revenue CAGR; projects 5 years at half-CAGR (clamped 0–30%); discounts at WACC 10%, terminal growth 2%; Bear ×0.80 / Bull ×1.25 sensitivity
- `_build_comps_summary()`: builds peer P/E and P/B table from MarketAgent peer data already fetched by orchestrator — no extra API call
- Confidence rating (HIGH / MEDIUM / LOW) based on margin stability and CAGR sign
- Called in `orchestrator/_fetch_data()` and appended to FundamentalAgent's data blob

**Potential impact:** +~400–800 input tokens per stock (valuation summary is compact). Reuses DART data already in memory — zero extra API calls. Expected improvement in conviction accuracy for fairly-valued vs overvalued stocks.

---

### Gap 4 — Dynamic Peer Detection (`tools/market_tools.py`)

**What:** MarketAgent's peer list is now built dynamically from live pykrx sector classifications instead of a static hardcoded dictionary.

**Why:** The hardcoded `KOREAN_SECTOR_PEERS` table only covered major sectors and had to be manually updated. New IPOs, sector reclassifications, and niche KOSDAQ companies were frequently missing or mismatched peers.

**How:**
- Added `_get_dynamic_peers(stock_code, sector, induty_code, exchange, max_peers=5) → list[str]` to `market_tools.py`
- Strategy 1: calls `pykrx.get_market_sector_classifications(date, market)` → filters rows whose sector label contains the target sector string → sorts by market cap (descending) → returns top 5 codes excluding the target stock itself
- Strategy 2: falls back to `KOREAN_SECTOR_PEERS` if the live call fails or returns no matches
- Updated `get_peer_comparison()` signature to accept `induty_code` and `exchange` params; orchestrator now passes both
- Peer names resolved via `krx.get_market_ticker_name()` (no yfinance round-trip needed)

**Potential impact:** No token cost change (peer context is already part of MarketAgent's data blob). Qualitative improvement: peers are now current and market-cap ranked rather than arbitrary. Reduces risk of comparing a healthcare company to the wrong sector.

---

### Gap 5 — BoK ECOS Macro Data + Dynamic Risk-Free Rate (`tools/macro_tools.py` · `backtest/engine.py` · `config.py`)

**What:** MacroAgent now includes four live BoK ECOS indicators (base rate, CPI YoY, industrial production index, 91-day CD rate). The 91-day CD rate feeds the Sharpe ratio calculation in BacktestEngine, replacing the previously hardcoded 3.5%.

**Why:** The prior MacroAgent context was entirely yfinance-sourced (USD/KRW, global indices, commodities) — it had no direct data on Korean monetary policy, domestic inflation, or credit market rates. The hardcoded 3.5% Sharpe denominator was stale and not tied to actual market conditions.

**How:**
- Added `BOK_API_KEY = os.getenv("BOK_API_KEY")` to `config.py`
- `_bok_fetch(stat_code, cycle, item_code, start_ym, end_ym)` in `macro_tools.py`: calls `ecos.bok.or.kr/api/StatisticSearch/{key}/json/kr/1/5/...` and returns the value list
- `fetch_bok_indicators(as_of_date, months) → dict`: fetches all 4 series; extracts the last available data point per series
- `get_risk_free_rate(as_of_date) → float`: fetches 91-day CD rate; falls back to 0.035 if `BOK_API_KEY` absent or API fails
- `fetch_macro_indicators()`: merges yfinance + BoK results; tags each entry with its source
- `format_macro_data_for_llm()`: adds a separate "BoK Monetary Policy & Domestic Macro" section when BoK data is available
- `BacktestEngine.__init__()`: if `risk_free_rate=None`, calls `get_risk_free_rate(start_date)` at engine construction; falls back to 0.035

**Potential impact:** +~200–400 input tokens per stock (BoK section in macro context). Requires `BOK_API_KEY` (free, instant from ecos.bok.or.kr). Sharpe ratios now reflect current Korean credit market rates rather than a fixed assumption.

---

### Gap 6 — Calibration Visualization (`calibration/visualizer.py` · `calibration/builder.py`)

**What:** After each calibration build, two PNG charts are saved alongside `calibration.json`: a per-agent accuracy bar chart and a per-agent signal return chart.

**Why:** `calibration.json` contains rich per-agent accuracy data but it is JSON — not human-readable at a glance. The charts let you immediately see which agents are performing above/below random baseline and whether BUY signals are actually generating positive returns.

**How:**
- New `calibration/visualizer.py` with `generate_calibration_charts(calibration_data, output_dir) → List[str]`
- Chart 1 `agent_accuracy.png`: horizontal bar chart; bars colored green (≥65%), gold (50–65%), red (<50%); dashed reference lines at 50% (random baseline) and 65% (target threshold); dark theme matching brand colors
- Chart 2 `signal_outcomes.png`: grouped bars — avg return on BUY (green) vs avg return on SELL (red) per agent; labeled with return values
- `calibration/builder.py` wraps the calibration dict in a `_CalibrationDataWrapper` with a `per_agent_summary` attribute and calls `generate_calibration_charts()` after saving JSON; logs chart paths; continues gracefully if generation fails
- Both PNGs are saved to the same directory as `calibration.json`

**Potential impact:** No token cost change. Pure observability improvement — calibration data is now actionable without parsing JSON manually.

---

### Gap 7 — Excel + Word Export (`report/exporters.py`)

**What:** After each analysis run, two additional output files are generated: a styled `.xlsx` portfolio summary and a `.docx` bundle of all per-stock reports.

**Why:** PDF is the primary deliverable but is read-only. Analysts need the portfolio data in Excel to build their own models, and the full per-stock narrative in Word for annotation and editing. Without these formats, raw data was locked inside the PDF and individual MD files.

**How:**
- New `report/exporters.py` with two public functions:
  - `export_portfolio_xlsx(portfolios, backtest_results, company_names, as_of_date, output_dir) → Optional[str]`: 3-sheet XLSX using openpyxl; Sheet 1 "Portfolio Summary" — signal, conviction, weight per stock per profile with BUY=green / SELL=red cell fill and navy/gold header row; Sheet 2 "Backtest Summary"; Sheet 3 "Signal Details"
  - `export_reports_docx(report_md_paths, company_names, as_of_date, output_dir) → Optional[str]`: iterates all per-stock MD files; converts Markdown (headings, bullets, table rows) to Word paragraphs using python-docx; bundles into single file
- Both called in `orchestrator/finalize()` after PDF generation; paths logged to console
- Filenames: `portfolio_{YYYY-MM-DD}.xlsx`, `report_{YYYY-MM-DD}.docx`

**Potential impact:** No token cost change (pure post-processing). Adds two new output files per run. Requires `openpyxl>=3.1.0` and `python-docx>=1.1.0` (both added to `requirements.txt`).

---

### Gap 8 — Naver Finance Analyst Consensus (`tools/naver_tools.py`)

**What:** FundamentalAgent now receives the analyst consensus target price, analyst count, and implied upside/downside from Naver Finance.

**Why:** Market consensus target prices represent the aggregated view of sell-side analysts with access to management guidance and proprietary models. Knowing whether the current price is 30% below or 10% above consensus is a material input for any fundamental call — but this data was absent from the system.

**How:**
- New `tools/naver_tools.py` with `fetch_analyst_consensus(stock_code) → str`
- Scrapes `finance.naver.com/item/main.nhn?code={code}` with EUC-KR encoding set explicitly (`r.encoding = "euc-kr"`)
- Extracts 목표주가 via regex `r"목표주가[^\d]*([0-9,]+)"` and analyst count; falls back to the consensus sub-page `/item/coinfo.nhn?code={code}&target=total`
- Computes implied upside/downside from current price and maps to interpretation: >20% = bullish, 5–20% = cautiously constructive, −5% to +5% = neutral, <−5% = downside risk
- Returns `""` on any failure (never raises); called in `orchestrator/_fetch_data()` and appended to FundamentalAgent's context

**Potential impact:** +~100–200 input tokens per stock (consensus block is short). No API key required. Expected to improve FundamentalAgent's price anchoring — currently it forms valuation judgments without knowing where the market consensus sits.

---

### Gap 11 — PDF Brand Refresh + Calibration Chart Page (`report/summary_renderer.py`)

**What:** The Executive Summary PDF now uses consistent brand colors throughout (navy `#0D1117`, gold `#F0B429`, BUY green `#2EA043`, SELL red `#F85149`). If calibration charts exist, a third page is appended showing agent accuracy and signal return charts side by side.

**Why:** The previous PDF mixed multiple blues and greens that were not aligned with the system's visual identity. BUY/SELL badges used inconsistent colors across the file. The calibration charts, once generated (Gap 6), had no path into the PDF deliverable — they sat in the `calibration/` folder unused.

**How:**
- Updated brand constants in `summary_renderer.py`: `C_NAVY="#0D1117"`, `C_GOLD="#F0B429"`, `C_BLUE_MID="#2EA043"` (BUY), `C_RED_MID="#F85149"` (SELL)
- `_badge_bg()` now returns `colors.HexColor("#2EA043")` for BUY and `colors.HexColor("#F85149")` for SELL
- Section titles use gold `#F0B429` throughout
- `build_pdf()` signature: added `calibration_charts_dir: Optional[str] = None`
- If `agent_accuracy.png` or `signal_outcomes.png` exist in that directory, a third page is appended with the two charts placed side by side (half-width each) and a caption
- `orchestrator/finalize()` passes the `calibration/` directory path to `build_pdf()` so charts are automatically included when available

**Potential impact:** No token cost change. Visual improvement only. Calibration insight is now surfaced inside the primary PDF deliverable without requiring the user to separately open the PNG files.

---

### Dependencies added (`requirements.txt`)

| Package | Version | Required by |
|---|---|---|
| `beautifulsoup4` | ≥4.12.0 | DART document parsing (Gap 1) |
| `lxml` | ≥5.0.0 | BeautifulSoup HTML parser (Gap 1) |
| `openpyxl` | ≥3.1.0 | Excel export (Gap 7) |
| `python-docx` | ≥1.1.0 | Word export (Gap 7) |

---

### Files changed (summary)

**New files:** `tools/dart_document_tools.py` · `tools/valuation_tools.py` · `tools/naver_tools.py` · `calibration/visualizer.py` · `report/exporters.py`

**Modified files:** `tools/market_tools.py` · `tools/macro_tools.py` · `backtest/engine.py` · `calibration/builder.py` · `report/summary_renderer.py` · `orchestrator/orchestrator_agent.py` · `config.py` · `requirements.txt` · `.env` · `.env.example`

---

## [2026-05-26] — Performance Calibration Agent

### New `reports/calibration/` subtree

```
reports/calibration/
└── {signal_as_of_date}/
    └── calibration.json
```

`calibration.json` schema:
```json
{
  "signal_as_of_date":  "2025-06-01",
  "holding_period_end": "2025-09-01",
  "generated_at":       "2026-05-26",
  "stocks_covered":     ["086900", "145020", "214150", "214450", "290650"],
  "actual_returns":     {"086900": -31.09, ...},
  "per_agent": {
    "TechnicalAgent":   {"records": [...], "formatted_context": "..."},
    "FundamentalAgent": {"records": [...], "formatted_context": "..."},
    "SentimentAgent":   {"records": [...], "formatted_context": "..."},
    "MarketAgent":      {"records": [...], "formatted_context": "..."},
    "MacroAgent":       {"records": [...], "formatted_context": "..."}
  }
}
```

### How it works

Each agent receives only its own domain's historical data — not a generic summary:
- **TechnicalAgent** sees its own past RSI, MA20, signal, and actual outcome per stock per quarter
- **FundamentalAgent** sees its own margin / earnings / debt calls and outcomes
- **SentimentAgent** sees its own DART flag calls and outcomes
- **MarketAgent** sees its own competitive positioning calls and outcomes
- **MacroAgent** sees its own macro indicator calls and outcomes

No LLM is used to generate the calibration — it is pure data engineering (parsing saved signal JSONs + fetching actual price returns). Each agent interprets its own track record through its own reasoning framework.

### Load / generate rules

| Condition | Behaviour |
|---|---|
| `calibration/{date}/calibration.json` exists AND `stocks_covered` matches current pool | Load directly — no regeneration, no API call |
| File missing OR stock pool changed | Generate fresh → save → use |
| First run (no prior signal history) | Skip calibration — agents run as normal |
| Holding period end date is in the future | Skip that quarter — outcome not yet known |

### Files changed
`calibration/__init__.py` · `calibration/returns.py` · `calibration/extractor.py` · `calibration/builder.py` · `calibration/formatter.py` · `orchestrator/orchestrator_agent.py` · `main.py` · `web/runner.py` · `README.md`

---

## [2026-05-26] — Company-first reports/ restructure + hierarchical file picker

### New `reports/` folder layout

All outputs are now split into two clean subtrees:

```
reports/
├── signals/
│   └── {ticker}_{name}/          ← browse by company
│       └── {as_of_date}/         ← Q1, Q2, Q3 all land here
│           ├── averse/ *.md
│           ├── neutral/ *.md
│           └── *.json
└── backtest/
    └── {run_date}/
        └── {as_of_date}/
            ├── buy_and_hold/ Exec_Sum_*.pdf
            └── rebalance/ Rebalanced_*.json  Exec_Sum_Rebalanced_*.pdf
```

**Why:** Q2/Q3 signals were buried under `backtest/rebalance/Q2/{ticker}/` — 8 levels deep, invisible to the file picker. Now every quarterly signal (Q1, Q2, Q3…) is written to `signals/{ticker}/{as_of_date}/`, making all quarters immediately discoverable by company. Backtest PDFs and JSON weight schedules are kept strictly separate in `backtest/`.

**Files changed:** `orchestrator/orchestrator_agent.py`, `rebalance/rebalance_engine.py`, `main.py`, `web/runner.py`

**Migration:** `scripts/migrate_reports.py` was run to move all existing files to the new structure. Run it again on any older clone: `python3 scripts/migrate_reports.py`

### Hierarchical file picker (terminal + web)

**Terminal (curses):**
- Level 0: browse companies (`signals/{ticker}/` folders) — ENTER to open
- Level 1: select which as-of date(s) for that company — SPACE toggle, A all, ENTER confirm, ← back
- The company name (from meta) is shown, not the raw folder name

**Web:**
- Step 1: pick a company (buttons ≤4, checkboxes >4)
- Step 2: pick which date(s) for that company (checkboxes)

Previously both pickers showed a flat list of all files, making runs with the same ticker on different dates indistinguishable.

---

## [2026-05-22] — Save & Exit breakpoint + duplicate agent output fixes

### Save & Exit breakpoint (`web/runner.py`)

After every analysis session (new analysis or loaded signals), the web UI now presents a pause point before the backtest:

```
Analysis complete — what would you like to do next?
Signals are already saved and can be reloaded later via 'Load Saved Signals'

  [ 📈 Run Backtest ]     [ 💾 Save & Exit ]
```

Choosing **Save & Exit** ends the session cleanly. The signal JSON files are already written to disk by the orchestrator during analysis, so no data is lost. The user can return any time, select **Load Saved Signals**, pick the saved file, and proceed straight to backtest mode. Previously the only way to stop was to complete the entire backtest flow.

### Duplicate agent output fixes (`debate/debate_manager.py`)

Two separate issues caused agent names to appear twice in output:

**1. "Analyzing…" + result pair in terminal mode**  
The `_cb` helper printed both the intermediate `"analyzing…"` status and the final `"done"` / `"round"` result for each agent. The intermediate state is only useful for live in-place UIs (DebateGrid or web). In plain terminal output it cannot overwrite the previous line, so each agent appeared twice.  
Fix: skip printing when `status in ("analyzing", "analyzing…")`.

**2. Parallel profiles both printing in web mode**  
Both the risk-averse and risk-neutral `DebateManager` instances run in parallel threads. In web mode `DebateGrid` is `None`, so both threads fell through to `print()` — every agent appeared twice (once per profile) interleaved on stdout.  
Fix: only print when `progress_cb` is also absent (`elif not progress_cb and status not in (...)`). In web mode the SocketIO callback handles all display; in terminal mode the grid handles it.

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
