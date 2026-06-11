# K-AlphaAgents

Korean-equity multi-agent analysis system (inspired by BlackRock's *AlphaAgents* paper).
**Research / paper-trading only — never add live order-execution or money-movement code.**

## Start here: use the knowledge graph, don't scan the whole tree

This repo carries a graphify knowledge graph in `graphify-out/`. Use it to orient and
locate *before* opening source files — you should not need to read the folder to build context.

- **Orient:** read `graphify-out/GRAPH_REPORT.md` (god nodes, communities, architecture).
- **Locate / trace:** `graphify query "<question>"` (e.g. `graphify query "how does the debate flow work"`).
- Then read only the specific files the task actually touches.
- **After changing code, refresh the graph** so it stays accurate: `graphify . --update`.

The graph is a map, not the territory — still read the real code for the lines you edit.

## Run & verify (no test suite)

- **Debug run — no LLM cost, exercises the full pipeline (primary regression check):**
  `DEBUG_MODE=true run`
- CLI: `run`   ·   Web: `python3 web/app.py` (Flask-SocketIO; also the Render start command)
- Env: `ANTHROPIC_API_KEY`, `DART_API_KEY` required; `OPENAI_API_KEY` (fallback), `BOK_API_KEY` (optional).
  Model via `CLAUDE_MODEL` (default `claude-sonnet-4-6`).

## Conventions that bite if ignored

- **pykrx is the authoritative price/index source.** yfinance is *not* for prices or news —
  only optional `.info` valuation ratios in `market_tools`. Do not reintroduce yfinance price fetching.
- **Market class (.KS/.KQ, KOSPI/KOSDAQ) comes from DART `corp_cls`** ("Y"=KOSPI, "K"=KOSDAQ,
  "N"=KONEX) — already fetched upstream. Never add a network probe to detect the market.
- **Stock codes are 6-digit KRX codes** (e.g. `005930`). pykrx takes the raw code; only yfinance needs a suffix.
- **`report/summary_renderer.py` is the source of truth for the PDF.** `summary_renderer_demo.py`
  is a drifted historical prototype — do not sync from it. Brand palette is dark `#0D1117` / `#F0B429`;
  benchmarks are KOSPI/KOSDAQ, not S&P 500.
- **All LLM calls go through `BaseAgent`** (two-tier prompt caching + OpenAI GPT-4o fallback).
- **`reports/` and `graphify-out/` are generated output** (gitignored). On Render's free tier `reports/` is wiped on restart.

## Architecture in one breath

`OrchestratorAgent` is the hub: per stock → 5 analysis agents (fundamental, macro, market,
sentiment, technical — all extend `BaseAgent`) → `DebateManager` (round-robin + majority vote)
→ Portfolio → Backtest → Report. `calibration/` injects each agent's past signal accuracy into the debate.
See `README.md` for the full file tree.
