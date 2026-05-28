# K-AlphaAgents 🤖📈
### LLM Multi-Agent System for Korean Equity Research

A partial replication of **BlackRock's AlphaAgents** (Zhao et al., 2025) adapted for Korean equities (KOSPI/KOSDAQ).  
**Five specialized AI agents** collaborate, debate, and reach a consensus **BUY / SELL** recommendation — simultaneously under both **Risk-Averse** and **Risk-Neutral** investor profiles — then automatically construct a portfolio and backtest it.

---

## System Overview

```
python3 main.py
        │
        ├── [N] New Analysis
        │       │
        │       ├── Enter as-of date  (YYYY/MM/DD)
        │       └── Enter stock pool  (one or more tickers)
        │               │
        │               ▼
        │   ┌───────────────────────────────────────────┐
        │   │           OrchestratorAgent               │
        │   │                                           │
        │   │  For each stock:                          │
        │   │    1. Fetch data  (DART + pykrx)          │
        │   │    2. Run 5-agent debate × 2 profiles     │
        │   │       (parallel)                          │
        │   │    3. Save  .md reports + .json signals   │
        │   │       → reports/signals/{ticker}_{name}/  │
        │   │                                           │
        │   │  Once all stocks analysed:                │
        │   │    4. PortfolioAgent → weights            │
        │   │    5. BacktestEngine → charts             │
        │   │    6. SummaryRenderer → PDF               │
        │   └───────────────────────────────────────────┘
        │
        └── [L] Load Saved Signals   (skip analysis → choose backtest mode)

        After [N] or [L] completes, the system first asks:
        │
        ├── [💾 Save & Exit]         signals already saved as JSON — exit now and reload later
        │
        └── [📈 Run Backtest]        proceed to portfolio construction & backtesting
                │
                ├── [S] Standard Backtest    (static portfolio, single as-of date)
                │       Benchmarks: EW buy-and-hold · KOSPI · KOSDAQ
                │
                └── [R] Rebalancing          (quarterly LLM rebalance + event-triggered re-weighting)
                        │             Benchmarks: EW buy-and-hold · KOSPI · KOSDAQ
                        │
                        ▼
            ┌─────────────────────────────────────────────┐
            │  RebalanceEngine                            │
            │                                             │
            │  Q1: reuses [N]/[L] analysis (no LLM redo) │
            │  Q2+: full 5-agent debate with fresh data   │
            │                                             │
            │  Each quarter:                              │
            │    1. Construct portfolio → base weights     │
            │    2. Monitor daily prices (no LLM):        │
            │         • Price drop > 8% from entry        │
            │         • 20d annualised vol > 40%          │
            │         • Price below 20d MA × 3 days       │
            │       → Re-weight via momentum scores       │
            │                                             │
            │  After all quarters:                        │
            │    3. Time-varying backtest                  │
            │    4. Executive Summary PDF                  │
            └─────────────────────────────────────────────┘
```

---

## The Five Agents

| Agent | Data Source | Analytical Lens |
|---|---|---|
| **FundamentalAgent** | OpenDART — stage-aware: 3 annual FYs + all interim reports (initial) / 1 annual + 1 interim (rebalancing) · **DART full filing documents** (MD&A, risk factors, business description) · **DCF + comps valuation** · **Naver analyst consensus** (target price, implied upside) | Revenue trends, margins, cash flow quality, debt, governance, intrinsic value vs market price |
| **SentimentAgent** | DART disclosure list · pykrx investor net flow · pykrx short selling | Corporate events, foreign/institutional accumulation vs distribution, bearish positioning |
| **TechnicalAgent** | pykrx price history — current quarter + prior quarter + KOSPI/KOSDAQ index | MA20/MA60, RSI, Bollinger Bands, relative performance vs benchmarks, QoQ delta |
| **MarketAgent** | DART KSIC sector (primary) · **pykrx dynamic peer detection** (live sector classifications) · pykrx benchmark returns · yfinance ratios (optional) | Industry cycle, competitive positioning, KOSPI/KOSDAQ benchmark comparison |
| **MacroAgent** | KRW/USD · KOSPI · KOSDAQ · S&P 500 · NASDAQ · US 10Y · Oil · Gold · **BoK ECOS** (base rate, CPI, industrial production, CD rate) | Currency impact, interest rate environment, Korea vs global capital flows, domestic monetary policy |

Each agent is independently role-prompted with the chosen risk profile and produces a standalone **BUY / SELL** recommendation before entering the debate phase.

---

## Risk Profile Framework

Each agent operates under one of two investor profiles, applied with a **two-step structure**:

**Step 1 — Objective data reading:**  
The agent reads and analyses all available data without filtering — every positive and negative signal is noted before any conclusion is formed.

**Step 2 — Risk-weighted judgment:**  
The agent applies the lens of its assigned profile when forming its final recommendation:

| Profile | Tie-break rule | Interpretation |
|---|---|---|
| **Risk-Averse** | Risk wins | When risks and returns are of similar magnitude, the downside carries more weight → lean SELL |
| **Risk-Neutral** | Return wins | When risks and returns are of similar magnitude, the upside carries more weight → lean BUY |

This two-step approach ensures agents always consider the full picture before applying their profile weighting — avoiding the trap of selectively reading only bearish (or only bullish) signals.

Both profiles run **simultaneously** via `ThreadPoolExecutor` — data is fetched once and shared.

---

## Debate Mechanism

```
Round 0 — Independent Analysis  (Steelman enforced)
  All 5 agents analyse in isolation → each issues BUY or SELL
  Each agent must argue the strongest opposing case before concluding
  If all 5 agree → TERMINATE  (unanimous, 0 debate rounds)

Rounds 1–3 — Structured Debate  (Active challenge enforced)
  Each agent reads all 4 peers' analyses
  Must cite specific conflicting claims and explain why they are wrong
  Explicitly states: MAINTAINING or CHANGING position, and why
  After each round: if all 5 agree → TERMINATE  (unanimous)

After Round 3 — Majority Vote
  3-of-5 wins  (5-0, 4-1, or 3-2 — no tie possible)
```

---

## Conviction Scoring

Conviction is computed using a weighted vote combined with a round-speed bonus:

```
conviction = (weighted_vote × 0.6) + (round_score × 0.4)

weighted_vote : sum of agent weights for agents agreeing with the final signal
round_score   : 1.0 at round 0 (instant consensus), decays to 0.0 at round 3
```

Agents are weighted by how directly their data connects to the firm being analysed. All five agents now have substantial data inputs, so the original data-hardness ordering (Fundamental > Technical > Macro > Market > Sentiment) no longer reflects meaningful differences in data quality. Instead, weights are set by **data connectedness**:

- **Direct agents** use data that is specific to the company (financial statements, corporate disclosures, investor flows, the stock's own price history).
- **Indirect agents** use contextual data that surrounds the company (sector dynamics, macro environment) but is not company-specific.

| Group | Agents | Each weight | Group total |
|---|---|---|---|
| **Direct** (company-specific data) | FundamentalAgent · SentimentAgent · TechnicalAgent | 0.2167 | 65% |
| **Indirect** (sector / macro context) | MacroAgent · MarketAgent | 0.1750 | 35% |

Each direct agent individually outweighs each indirect agent (0.2167 > 0.1750). Weights are normalised to sum to exactly 1.0.

---

## Portfolio Construction

After all stocks are analysed, the **PortfolioAgent** constructs two separate portfolios — one per risk profile:

- **SELL** stocks receive 0% weight and are excluded from the portfolio
- **BUY** stocks are included regardless of conviction score
- Weight is distributed **conviction-proportionally** across all BUY stocks, summing to 100%
- Both profiles are signal-only — no fixed equity/bond split or stop-loss rules

---

## Backtesting

`BacktestEngine` fetches KRX prices via `pykrx` and computes:

- **Cumulative Return** — portfolio vs. two benchmarks
- **Rolling Sharpe Ratio** — 30-trading-day window (x-axis anchored to start date; warm-up period left blank)

**Benchmarks overlaid on every chart:**
1. **EW Benchmark** — equal-weight of all analysed stocks regardless of signal (orange)
2. **KOSPI** — fetched via pykrx (green)
3. **KOSDAQ** — fetched via pykrx (purple)

Backtesting is skipped automatically if no stocks receive a BUY signal in either profile.

---

## Recent Enhancements (2026-05-28)

Nine data-enrichment and output improvements were shipped in one batch. Summary of what was added:

| # | Enhancement | Files |
|---|---|---|
| 1 | **DART full filing documents** — MD&A, risk factors, business narrative, outlook; ~8 K tokens per stock | `tools/dart_document_tools.py` |
| 2 | **DCF + comps valuation** — 5-year DCF (WACC 10%, terminal 2%) with Bear/Base/Bull; peer P/E and P/B table; reuses already-fetched DART data | `tools/valuation_tools.py` |
| 4 | **Dynamic peer detection** — live pykrx sector classifications replace the hardcoded peer list | `tools/market_tools.py` |
| 5 | **BoK ECOS macro data + dynamic risk-free rate** — base rate, CPI, industrial production, 91-day CD rate; CD rate feeds Sharpe calculation in BacktestEngine | `tools/macro_tools.py` · `backtest/engine.py` · `config.py` |
| 6 | **Calibration charts** — `agent_accuracy.png` (bar chart with baseline and target lines) + `signal_outcomes.png` (avg return on BUY vs SELL); dark-themed, saved alongside `calibration.json` | `calibration/visualizer.py` · `calibration/builder.py` |
| 7 | **Excel + Word export** — `portfolio_{date}.xlsx` (3-sheet portfolio summary + backtest + signal detail) and `report_{date}.docx` (all MD reports bundled) | `report/exporters.py` |
| 8 | **Naver Finance analyst consensus** — scrapes target price and analyst count; computes implied upside/downside; appended to FundamentalAgent context | `tools/naver_tools.py` |
| 11 | **PDF brand refresh + calibration chart page** — brand colors updated to navy/gold/BUY-green/SELL-red; if calibration charts exist a third page is appended to the executive summary PDF | `report/summary_renderer.py` |

---

## Output Files

`reports/` is split into three clean subtrees — **signals** (all LLM outputs, always reloadable), **backtest** (PDFs and result files), and **calibration** (per-agent signal accuracy history):

```
reports/
├── signals/
│   └── {ticker}_{name}/               ← one folder per company (Q1, Q2, Q3 all here)
│       └── {as_of_date}/              ← data cutoff date — one per quarter analysed
│           ├── averse/
│           │   └── {ticker}_{name}_{as-of}_averse.md
│           ├── neutral/
│           │   └── {ticker}_{name}_{as-of}_neutral.md
│           └── {ticker}_{name}_{as-of}.json   ← signals for both profiles
│
├── backtest/
│   └── {run_date}/                    ← date the backtest was executed
│       └── {as_of_date}/
│           ├── buy_and_hold/
│           │   └── Exec_Sum_{as-of}.pdf         ← Executive Summary PDF
│           └── rebalance/                        ← only present if rebalancing was run
│               ├── Rebalanced_{as-of}.json       ← full weight schedule + quarterly log
│               └── Exec_Sum_Rebalanced_{as-of}.pdf
│
└── calibration/
    └── {signal_as_of_date}/           ← the quarter whose signals are being calibrated
        ├── calibration.json           ← per-agent signal accuracy history (auto-loaded on next run)
        ├── agent_accuracy.png         ← bar chart: per-agent accuracy vs 50% baseline / 65% target
        └── signal_outcomes.png        ← avg return on BUY vs SELL per agent
```

**Q1, Q2, Q3 signals all land in `signals/{ticker}/{as_of_date}/`** — there is no separate quarterly subfolder.  
Each quarterly as-of date (e.g. `2025-06-01`, `2025-09-01`, `2025-12-01`) gets its own date folder under the company.

| File | Contents |
|---|---|
| `signals/…/*_neutral.md` | Full agent analyses + debate log — Risk-Neutral profile |
| `signals/…/*_averse.md` | Full agent analyses + debate log — Risk-Averse profile |
| `signals/…/*.json` | Structured signals for both profiles — auto-created after every run |
| `backtest/…/Exec_Sum_*.pdf` | 2–3 page institutional PDF (buy-and-hold backtest); page 3 = calibration charts if available |
| `backtest/…/Exec_Sum_Rebalanced_*.pdf` | 2–3 page institutional PDF (rebalancing backtest) |
| `backtest/…/Rebalanced_*.json` | Saved weight schedule + quarterly log for future reload |
| `backtest/…/portfolio_{date}.xlsx` | 3-sheet Excel: Portfolio Summary · Backtest Summary · Signal Details |
| `backtest/…/report_{date}.docx` | All per-stock MD reports bundled into a single Word document |
| `calibration/…/calibration.json` | Per-agent signal accuracy history — auto-loaded at the start of the next analysis |
| `calibration/…/agent_accuracy.png` | Bar chart: per-agent BUY/SELL accuracy vs 50% baseline and 65% target |
| `calibration/…/signal_outcomes.png` | Grouped bar chart: avg return on BUY signals vs avg return on SELL signals |

Signal JSON files are created automatically after every `[N] New Analysis` run — no manual conversion step is required.

### Calibration Files

`calibration/{signal_as_of_date}/calibration.json` is generated automatically the next time a new analysis is run after a holding period has ended. It records what each of the five agents predicted for the previous quarter, what actually happened, and pre-formats that history per agent for injection into the next debate.

**Load rule:** at the start of every new analysis, the system checks for existing calibration files covering the same stock pool. If found, they are loaded directly (no regeneration, no extra API call). If missing or the stock pool has changed, they are generated fresh and saved for future runs.

**Cold start:** on the very first run there is no calibration history — the system proceeds without it and behavior is identical to today. Calibration activates automatically from the second quarter onwards.

### Executive Summary PDF

Built with **reportlab** — institutional navy (`#0D1117`) / gold (`#F0B429`) design, BUY badges in green (`#2EA043`), SELL badges in red (`#F85149`), Korean font support.

| Page | Sections |
|---|---|
| Page 1 | §1 Stock Signals & Conviction table · §2 Portfolio Allocation cards + donut pie charts |
| Page 2 | §3 Cross-Profile Narrative (Claude-written) · §4 Portfolio Metrics at a Glance · §5 Backtest Results chart |
| Page 3 *(optional)* | Calibration Charts — agent accuracy bars + signal return outcomes; only appended if `calibration/` charts exist |

---

## Project Structure

```
alpha_agents/
│
├── main.py                        # CLI entry point — [N] New / [L] Load
├── config.py                      # API keys, model settings, DEBUG_MODE
├── requirements.txt
├── render.yaml                    # Render deployment config
│
├── web/                           # Web UI (Flask + SocketIO)
│   ├── app.py                     # Flask server
│   ├── runner.py                  # Web-side pipeline runner
│   └── session.py                 # Per-session state management
│
├── templates/
│   └── ui.html                    # Dark-theme chat interface
│
├── agents/
│   ├── base_agent.py              # Claude (cached) + OpenAI (fallback) LLM wrapper
│   ├── fundamental_agent.py       # OpenDART financial disclosure analysis
│   ├── sentiment_agent.py         # DART disclosures + investor flow + short selling
│   ├── technical_agent.py         # Price action, MA/RSI/Bollinger, relative performance
│   ├── market_agent.py            # Industry cycle, competitive landscape, peers
│   └── macro_agent.py             # KRW/USD, rates, KOSPI vs global indices
│
├── tools/
│   ├── dart_tools.py              # OpenDART: corp registry + financial statements
│   ├── dart_document_tools.py     # DART full filing documents: MD&A, risk factors, outlook
│   ├── dart_report_planner.py     # Stage-aware DART report planning (initial vs rebalancing)
│   ├── pykrx_tools.py             # KRX price & index data via pykrx
│   ├── sentiment_tools.py         # DART disclosures + pykrx investor flow + short selling
│   ├── metrics_tools.py           # MA, RSI, Bollinger Bands, relative perf, QoQ delta
│   ├── market_tools.py            # KSIC sector mapping, dynamic peer detection, benchmark returns
│   ├── macro_tools.py             # KRW/USD, US yields, global indices, commodities, BoK ECOS
│   ├── valuation_tools.py         # DCF (5-yr, WACC 10%, terminal 2%) + peer P/E/P/B comps
│   ├── naver_tools.py             # Naver Finance: analyst consensus target price + implied upside
│   └── yfinance_tools.py          # Ticker lookup + optional ratio enrichment
│
├── debate/
│   └── debate_manager.py          # 5-agent round-robin debate + majority vote
│
├── orchestrator/
│   └── orchestrator_agent.py      # Pipeline director: fetch → debate → portfolio
│                                  #   → backtest → PDF
│
├── portfolio/
│   └── portfolio_agent.py         # Conviction scoring + signal-based allocation
│
├── backtest/
│   ├── engine.py                  # KRX data fetcher, metrics, BacktestEngine,
│   │                              #   plot_two_profiles(), run_with_schedule()
│   └── runner.py                  # Runs both profiles + EW/KOSPI/KOSDAQ benchmarks
│
├── rebalance/
│   ├── rebalance_engine.py        # Quarterly LLM rebalance + intra-quarter monitoring
│   ├── event_monitor.py           # Trigger detection (price drop / vol spike / MA flip)
│   └── weight_adjuster.py         # Momentum-based re-weighting (no LLM)
│
├── report/
│   ├── report_generator.py        # Per-stock Markdown report generator
│   ├── summary_renderer.py        # Executive Summary PDF (reportlab) — brand colors + calibration page
│   ├── exporters.py               # Excel (.xlsx) + Word (.docx) export
│   └── summary_renderer_demo.py   # Standalone demo with mock data
│
└── reports/                       # Auto-created on first run
    └── {run_date}/                ← date the analysis was run
        └── {as_of_date}/          ← data cutoff date
            ├── {ticker_A}_{name_A}/
            │   ├── neutral/
            │   │   └── {ticker_A}_{name_A}_{as-of}_neutral.md
            │   ├── averse/
            │   │   └── {ticker_A}_{name_A}_{as-of}_averse.md
            │   └── {ticker_A}_{name_A}_{as-of}.json
            ├── {ticker_B}_{name_B}/
            │   └── ...
            └── backtest/
                ├── buy_and_hold/
                │   └── Exec_Sum_{as-of}.pdf
                └── rebalance/          ← if rebalancing was chosen
                    ├── Q2/ · Q3/ ...
                    ├── Rebalanced_{as-of}.json
                    └── Exec_Sum_Rebalanced_{as-of}.pdf
```

---

## Setup

### 1. Clone the repository
```bash
git clone https://github.com/your-username/alpha-agents.git
cd alpha-agents
```

### 2. Install dependencies
```bash
pip3 install -r requirements.txt
```

### 3. Configure API keys

```bash
cp .env.example .env
```

```env
ANTHROPIC_API_KEY=your_anthropic_key
OPENAI_API_KEY=your_openai_key
DART_API_KEY=your_opendart_key
BOK_API_KEY=your_bok_ecos_key      # optional — ecos.bok.or.kr/api (free, instant)
```

| Key | Where to obtain | Required |
|---|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys | ✅ |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) → API Keys | ✅ |
| `DART_API_KEY` | [opendart.fss.or.kr](https://opendart.fss.or.kr) → 인증키 신청/관리 | ✅ |
| `BOK_API_KEY` | [ecos.bok.or.kr](https://ecos.bok.or.kr) → Open API → 인증키 신청 (free) | optional |

`BOK_API_KEY` unlocks BoK ECOS macro indicators (base rate, CPI, industrial production, 91-day CD rate). Without it MacroAgent falls back to yfinance-only data and BacktestEngine uses a fixed 3.5% risk-free rate.

---

## Usage

```bash
python3 main.py
```

#### [N] New Analysis

```
  [N] New analysis        — fetch data, run agents, save signals
  [L] Load saved signals  — skip analysis, go straight to portfolio & backtest

  Choice (N / L): N

  Enter analysis date (YYYY/MM/DD) — all stocks will be analysed using data prior to this date: 2025/06/01

  Stock #1
  Enter stock ticker (e.g. 005930): 005930
  → Looking up company on OpenDART...
  → Confirmed: 삼성전자(주)  (005930)
  → [1/2] Fetching data...
  → [2/2] Running debates (both profiles in parallel)...
  → [RISK-AVERSE  ] BUY   conviction=0.920  (unanimous, 0 round(s))
  → [RISK-NEUTRAL ] BUY   conviction=0.960  (unanimous, 0 round(s))

  Add another stock to the pool? (Y/N): Y
  ...

  Analysis complete — proceed to backtest?
  [📈 Run Backtest]   [💾 Save & Exit]

  # Choosing Save & Exit ends the session here.
  # Signals are already on disk — reload them later with [L] Load Saved Signals.

  # Choosing Run Backtest continues:
  Enter backtest end date (YYYY/MM/DD) [must be after 2025-06-01]: 2026/01/01

  Generating executive summary PDF...
  [PDF] Saved → reports/2026-05-21/Exec_Sum_2025-06-01.pdf
```

#### [L] Load Saved Signals

Skip the full analysis and go straight to portfolio construction and backtesting using previously saved `.json` signal files:

```
  Choice (N / L): L

  Saved signal files (5 found):
  [ 1] 005930  삼성전자(주)    (as_of 2025-06-01)
  [ 2] 000660  SK하이닉스(주)  (as_of 2025-06-01)
  ...

  Enter file numbers to load (e.g. 1  or  1,3,4): 1,2,3,4,5
```

---

## Example tickers

Top 10 KOSPI companies by market capitalisation:

| Ticker | Company | Sector |
|---|---|---|
| `005930` | 삼성전자 (Samsung Electronics) | Technology |
| `000660` | SK하이닉스 (SK Hynix) | Technology |
| `373220` | LG에너지솔루션 (LG Energy Solution) | Battery / Energy |
| `207940` | 삼성바이오로직스 (Samsung Biologics) | Pharmaceuticals |
| `005380` | 현대차 (Hyundai Motor) | Automotive |
| `000270` | 기아 (Kia) | Automotive |
| `105560` | KB금융 (KB Financial Group) | Financial |
| `055550` | 신한지주 (Shinhan Financial Group) | Financial |
| `068270` | 셀트리온 (Celltrion) | Healthcare |
| `035420` | NAVER | Communication Services |

---

## LLM Architecture

| Role | Model | Provider |
|---|---|---|
| Primary (all agents + narrative) | `claude-sonnet-4-6` | Anthropic |
| Fallback (auto, per agent) | `gpt-4o` | OpenAI |

- If a Claude API call fails for any agent, the system transparently retries with GPT-4o
- Each agent operates independently — no shared memory or state within a round
- Debate context is passed as explicit text, preserving full transparency

### Prompt Caching

All LLM calls use Anthropic's **prompt caching** to reduce token costs:

| Cached content | Savings |
|---|---|
| **Tier 1** — Agent system prompt + debate instructions (steelman / challenge) | ~90% cheaper from Round 1 onwards |
| **Tier 2** — Agent's data blob (DART financials, pykrx metrics, etc.) | ~90% cheaper from Round 1 onwards |

Per stock (5 agents × 2 profiles × up to 4 rounds = 40 calls): only Round 0 pays full price for system prompts and data. Rounds 1–3 hit cache for both.

### Run Modes

```bash
# Full quality — for actual investment decisions
python3 main.py

# Pipeline testing — zero token cost, stub BUY responses
DEBUG_MODE=true python3 main.py

# Real analysis at ~20x lower cost — for development/debugging
CLAUDE_MODEL=claude-haiku-4-5 python3 main.py
```

---

## Render Deployment

The web UI is hosted on Render at **https://alpha-agents-su4l.onrender.com**.

### How it works

- **Runtime:** Python 3 web service (`python3 web/app.py`)
- **Build command:** `pip install -r requirements.txt` (no system-level installs needed)
- **Korean font:** `fonts/NanumGothic.ttf` is bundled in the repository — `report/summary_renderer.py` checks this path first, so no `apt-get` is required during build
- **SocketIO server:** runs with `allow_unsafe_werkzeug=True` (Render free tier uses Werkzeug, not gunicorn/eventlet)
- **Free tier:** the instance spins down after 15 min of inactivity; first request after spin-down may take ~50 s to respond
- **Persistent storage:** the free plan does not mount a persistent disk, so `reports/` is ephemeral across restarts. Upgrade to the Starter plan and uncomment the `disk:` block in `render.yaml` to keep generated PDFs and signal JSONs across deploys

### Environment variables

Set these in the Render dashboard (Settings → Environment):

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | ✅ | Claude API key |
| `OPENAI_API_KEY` | ✅ | OpenAI fallback key |
| `DART_API_KEY` | ✅ | OpenDART API key |
| `BOK_API_KEY` | optional | BoK ECOS API key — enables live Korean macro indicators |
| `CLAUDE_MODEL` | optional | Override the default model (e.g. `claude-haiku-4-5`) |
| `DEBUG_MODE` | optional | Set `true` to run without LLM calls (stub responses) |

### Re-deploying

Push to `main` — auto-deploy is enabled. To force a clean rebuild:

```
Render dashboard → alpha-agents → Manual Deploy → Clear build cache & deploy
```

---

## Data Sources

| Agent | Primary | Fallback |
|---|---|---|
| FundamentalAgent | OpenDART `/fnlttSinglAcnt.json` · DART full filing documents (ZIP→HTML→text) · Naver Finance analyst consensus | — |
| SentimentAgent | DART `/list.json` · pykrx investor flow · pykrx short selling | — |
| TechnicalAgent | pykrx `get_market_ohlcv_by_date()` | — |
| MarketAgent | DART `corp_info` (KSIC sector) · pykrx dynamic sector peers (`get_market_sector_classifications`) · pykrx peer returns | yfinance (P/E, P/B ratios) |
| MacroAgent | yfinance (KRW/USD, indices, commodities) · BoK ECOS (base rate, CPI, industrial production, CD rate) | yfinance-only if `BOK_API_KEY` absent |
| Valuation (fed to FundamentalAgent) | DART financial statements (reused, no extra call) → DCF + peer P/E/P/B | Confidence downgraded to LOW if data incomplete |

---

## Macro Indicators Tracked

**yfinance (always available):**

| Indicator | Ticker | Relevance |
|---|---|---|
| USD/KRW | `KRW=X` | Weaker KRW boosts export revenues |
| KOSPI | `^KS11` | Korean large-cap benchmark |
| KOSDAQ | `^KQ11` | Korean tech/growth benchmark |
| S&P 500 | `^GSPC` | Global risk appetite |
| NASDAQ | `^IXIC` | Tech-sector correlation |
| US 10Y Treasury | `^TNX` | EM capital flow pressure |
| Gold | `GC=F` | Safe-haven demand |
| Crude Oil (WTI) | `CL=F` | Input cost / geopolitical proxy |

**BoK ECOS (requires `BOK_API_KEY` — free):**

| Indicator | Series | Relevance |
|---|---|---|
| Base Rate | 722Y001 | Monetary policy stance; discount rate for DCF |
| CPI (YoY) | 901Y009 | Inflation environment |
| Industrial Production Index | 403Y003 | Domestic economic activity |
| 91-day CD Rate | 817Y002 | Risk-free rate proxy for Sharpe calculation |

---

## Limitations

- **KRX login warning:** pykrx prints a login warning on startup — this is cosmetic and does not affect data fetching. Public market data works without credentials.
- **Financial data lag:** OpenDART financials reflect the most recently filed report based on Korea's actual filing calendar. For dates before key deadlines (Mar 31 / May 15 / Aug 14 / Nov 14), earlier reports are used.
- **Dynamic peer detection:** pykrx `get_market_sector_classifications()` uses KRX sector labels which may differ from DART KSIC codes. If the live lookup fails, the system falls back to the hardcoded `KOREAN_SECTOR_PEERS` table.
- **DART full documents:** Annual and semi-annual reports are available as downloadable ZIPs. Not all filings contain every section (e.g., outlook or MD&A). Missing sections are skipped gracefully and the document context is left empty rather than raising an error.
- **Naver Finance scraping:** Target prices are scraped from the public Naver Finance page and may be absent for thinly covered stocks. Scraping fails silently — FundamentalAgent proceeds without consensus data.
- **DCF valuation:** The DCF model uses simplified assumptions (WACC 10%, terminal growth 2%, 5-year window). It is intended as a relative anchor, not a precise fair-value estimate.
- **BoK ECOS lag:** BoK series are published with a 1–2 month lag. The most recent available data point is used; the series date is shown in the macro context.
- **yfinance ratios:** P/E and P/B ratios from yfinance are optional enrichment — many Korean stocks return N/A. Core analysis does not depend on them.
- **LLM outputs:** Despite the multi-agent debate mechanism (which demonstrably reduces hallucination — Du et al., 2023), all outputs should be treated as research assistance, not financial advice.

---

## References

> Zhao, T., Lyu, J., Jones, S., Garber, H., Pasquali, S., & Mehta, D. (2025).  
> *AlphaAgents: Large Language Model based Multi-Agents for Equity Portfolio Constructions.*  
> BlackRock, Inc. arXiv:2508.11152

> Du, Y., Li, S., Torralba, A., Tenenbaum, J. B., & Mordatch, I. (2023).  
> *Improving factuality and reasoning in language models through multiagent debate.*  
> arXiv:2305.14325

---

## Disclaimer

> This system is built for **academic and research purposes only.**  
> It does not constitute financial advice. Past signals generated by this system do not guarantee future performance. Always conduct your own due diligence before making any investment decisions.

---

*K-AlphaAgents — Built with Claude (Anthropic) · OpenAI · OpenDART · pykrx · reportlab*
