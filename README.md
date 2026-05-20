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
        │   │    1. Fetch data  (DART + yfinance)       │
        │   │    2. Run 5-agent debate × 2 profiles     │
        │   │       (parallel)                          │
        │   │    3. Save  .md reports + _signals.json   │
        │   │                                           │
        │   │  Once all stocks analysed:                │
        │   │    4. PortfolioAgent → weights            │
        │   │    5. BacktestEngine → charts             │
        │   │    6. SummaryRenderer → PDF               │
        │   └───────────────────────────────────────────┘
        │
        ├── [L] Load Saved Signals   (skip analysis → choose backtest mode)
        │
        └── [C] Convert MD Reports   (convert existing .md files to _signals.json)
        │
        After [N] or [L] completes, the system asks:
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
| **FundamentalAgent** | OpenDART (사업보고서 / 분기보고서) | Revenue trends, margins, cash flow quality, debt, governance |
| **SentimentAgent** | yfinance news feed | News sentiment, analyst rating changes, insider signals |
| **ValuationAgent** | yfinance price & volume (3-month window) | Price momentum, annualised return & volatility, volume confirmation |
| **MarketAgent** | yfinance sector info + Korean peer tickers | Industry cycle, competitive positioning, peer valuation vs KOSPI |
| **MacroAgent** | KRW/USD · KOSPI · KOSDAQ · S&P 500 · NASDAQ · US 10Y · Oil · Gold | Currency impact, interest rate environment, Korea vs global capital flows |

Each agent is independently role-prompted with the chosen risk profile and produces a standalone **BUY / SELL** recommendation before entering the debate phase.

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

Both risk profiles run **simultaneously** via `ThreadPoolExecutor` — data is fetched once and shared, halving wall-clock time.

---

## Conviction Scoring

Conviction is computed using **Option B — Agent Expertise Weighting**:

```
conviction = (weighted_vote × 0.6) + (round_score × 0.4)

weighted_vote : sum of agent weights for agents agreeing with final signal
round_score   : 1.0 at round 0 (instant consensus), decays to 0.0 at round 3
```

| Agent | Weight | Rationale |
|---|---|---|
| FundamentalAgent | 0.30 | Hardest quantitative data |
| ValuationAgent | 0.25 | Direct price-signal evidence |
| MacroAgent | 0.20 | Structural macro context |
| MarketAgent | 0.15 | Industry positioning |
| SentimentAgent | 0.10 | Softest / most noisy signal |

---

## Portfolio Construction

After all stocks are analysed, the **PortfolioAgent** constructs two separate portfolios:

| Parameter | Risk-Averse | Risk-Neutral |
|---|---|---|
| Equity allocation | 60% | 80% |
| Bond allocation (KODEX 국고채3년 · 114260) | 40% | 20% |
| Stop-loss | −5% | −10% |
| Take-profit | +10% | +20% |

- Stocks with a **SELL** signal receive **0% weight**
- All **BUY** stocks qualify for equity allocation regardless of conviction score
- Equity weight is distributed **conviction-proportionally** across qualifying stocks
- Remaining weight goes to the Korean 3Y Government Bond ETF

---

## Backtesting

`BacktestEngine` fetches KRX prices via `pykrx` and computes:

- **Cumulative Return** — portfolio vs. two benchmarks
- **Rolling Sharpe Ratio** — 30-trading-day window (x-axis anchored to start date; warm-up period left blank)

**Benchmarks overlaid on every chart:**
1. **EW Benchmark** — equal-weight of all analysed stocks regardless of signal (orange)
2. **KOSPI** — `^KS11` fetched via yfinance (green)
3. **KOSDAQ** — `^KQ11` fetched via yfinance (purple)

Backtesting is skipped automatically if no stocks qualify for equity allocation in either profile.

---

## Output Files

All outputs are saved to `reports/` with a consistent naming scheme:

| File | Naming | Contents |
|---|---|---|
| MD report (per stock × profile) | `{ticker}_{name}_{as-of}_{averse\|neutral}.md` | Full agent analyses, debate log, signal summary |
| Signal JSON (per stock) | `{ticker}_{name}_{as-of}_signals.json` | Structured signals for reloading without re-analysis |
| Executive Summary PDF | `Exec Sum_{as-of}.pdf` | 2-page institutional PDF (see below) |

### Executive Summary PDF

Built with **reportlab** — institutional navy/gold design, Korean font support.

| Page | Sections |
|---|---|
| Page 1 | §1 Stock Signals & Conviction table · §2 Portfolio Allocation cards + donut pie charts |
| Page 2 | §3 Cross-Profile Narrative (Claude-written) · §4 Portfolio Metrics at a Glance · §5 Backtest Results chart |

---

## Project Structure

```
alpha_agents/
│
├── main.py                        # Entry point — [N] New / [L] Load / [C] Convert
├── config.py                      # API keys & model settings
├── requirements.txt
│
├── agents/
│   ├── base_agent.py              # Claude (primary) + OpenAI (fallback) LLM wrapper
│   ├── fundamental_agent.py       # OpenDART financial disclosure analysis
│   ├── sentiment_agent.py         # News sentiment analysis
│   ├── valuation_agent.py         # Price / volume / volatility analysis
│   ├── market_agent.py            # Industry cycle, competitive landscape, peers
│   └── macro_agent.py             # KRW/USD, rates, KOSPI vs global indices
│
├── tools/
│   ├── dart_tools.py              # OpenDART: corp registry + financial statements
│   ├── yfinance_tools.py          # Price history & news (anchored to as-of date)
│   ├── metrics_tools.py           # Annualised return, volatility (paper formulas)
│   ├── market_tools.py            # Sector info, KOSPI benchmark, peer tickers
│   └── macro_tools.py             # KRW/USD, US yields, global indices, commodities
│
├── debate/
│   └── debate_manager.py          # 5-agent round-robin debate + majority vote
│
├── orchestrator/
│   └── orchestrator_agent.py      # Pipeline director: fetch → debate → portfolio
│                                  #   → backtest → PDF
│
├── portfolio/
│   └── portfolio_agent.py         # Conviction scoring + risk-profile allocation
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
│   ├── summary_renderer.py        # Executive Summary PDF (reportlab)
│   └── summary_renderer_demo.py   # Standalone demo with mock data
│
└── reports/                       # Auto-created on first run
    ├── 214150_클래시스_2025-06-01_averse.md
    ├── 214150_클래시스_2025-06-01_neutral.md
    ├── 214150_클래시스_2025-06-01_signals.json
    └── Exec Sum_2025-06-01.pdf
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
```

| Key | Where to obtain |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com](https://console.anthropic.com) → API Keys |
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) → API Keys |
| `DART_API_KEY` | [opendart.fss.or.kr](https://opendart.fss.or.kr) → 인증키 신청/관리 |

---

## Usage

```bash
python3 main.py
```

### [N] New Analysis

```
  [N] New analysis        — fetch data, run agents, save signals
  [L] Load saved signals  — skip analysis, go straight to portfolio & backtest
  [C] Convert MD reports  — convert existing .md reports to signal JSON files

  Choice (N / L / C): N

  Enter analysis date (YYYY/MM/DD) — all stocks will be analysed using data prior to this date: 2025/06/01

  Stock #1
  Enter stock ticker (e.g. 005930): 214150
  → Looking up company on OpenDART...
  → Confirmed: (주)클래시스  (214150)
  → [1/2] Fetching data...
  → [2/2] Running debates (both profiles in parallel)...
  → [RISK-AVERSE  ] BUY   conviction=0.920  (unanimous, 0 round(s))
  → [RISK-NEUTRAL ] BUY   conviction=0.880  (unanimous, 0 round(s))

  Add another stock to the pool? (Y/N): Y
  ...

  Automatically starting backtest...
  Enter backtest end date (YYYY/MM/DD) [must be after 2025-06-01]: 2026/01/01

  Generating executive summary PDF...
  [PDF] Saved → reports/Exec Sum_2025-06-01.pdf
```

### [L] Load Saved Signals

Skip the full analysis and go straight to portfolio construction and backtesting using previously saved `_signals.json` files:

```
  Choice (N / L / C): L

  Saved signal files (5 found):
  [ 1] 086900  (주)메디톡스  (as_of 2025-06-01)
  [ 2] 145020  휴젤(주)      (as_of 2025-06-01)
  ...

  Enter file numbers to load (e.g. 1  or  1,3,4): 1,2,3,4,5
```

### [C] Convert MD Reports

If you have existing `.md` reports from before the signal JSON feature was added, convert them without re-running the analysis:

```
  Choice (N / L / C): C

  Found 5 convertible MD report pair(s):
  [ 1] 086900  (주)메디톡스  (data as-of: 2025-06-01)
  ...

  Convert all? (A) or enter numbers (e.g. 1,3): A
  → Converted: 086900 → reports/086900_메디톡스_2025-06-01_signals.json
```

### Example tickers

| Ticker | Company | Sector |
|---|---|---|
| `005930` | 삼성전자 (Samsung Electronics) | Technology |
| `000660` | SK하이닉스 (SK Hynix) | Technology |
| `035420` | NAVER | Communication Services |
| `068270` | 셀트리온 (Celltrion) | Healthcare |
| `086900` | 메디톡스 | Healthcare |
| `145020` | 휴젤 | Healthcare |
| `214150` | 클래시스 | Healthcare |

---

## LLM Architecture

| Role | Model | Provider |
|---|---|---|
| Primary (all agents + narrative) | `claude-sonnet-4-6` | Anthropic |
| Fallback (auto, per agent) | `gpt-4o` | OpenAI |

- If a Claude API call fails for any agent, the system transparently retries with GPT-4o
- Each agent operates independently — no shared memory or state within a round
- Debate context is passed as explicit text, preserving full transparency

---

## Macro Indicators Tracked

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

---

## Key Formulas

**Annualised Cumulative Return**

$$R_{\text{annualized}} = \left(1 + R_{\text{cumulative}}\right)^{\frac{252}{n}} - 1$$

**Annualised Volatility**

$$\sigma_{\text{annualized}} = \sigma_{\text{daily}} \times \sqrt{252}$$

**Conviction Score**

$$\text{conviction} = \left(\sum_{i \in \text{agree}} w_i\right) \times 0.6 + \left(1 - \frac{r}{R_{\max}}\right) \times 0.4$$

where $w_i$ = agent weight, $r$ = rounds taken, $R_{\max}$ = 3.

---

## Limitations

- **News coverage:** yfinance news is sparse for smaller KOSPI/KOSDAQ stocks. The SentimentAgent may receive limited data for mid/small-cap names.
- **Financial data lag:** OpenDART financials reflect the most recently filed annual report — typically the prior completed fiscal year.
- **KRX login warning:** pykrx prints a login warning on startup — this is cosmetic and does not affect data fetching.
- **Peer mapping:** Sector peer tickers are predefined for major Korean sectors. Niche or cross-sector companies may lack ideal comparisons.
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

*K-AlphaAgents — Built with Claude (Anthropic) · OpenAI · OpenDART · pykrx · yfinance · reportlab*
