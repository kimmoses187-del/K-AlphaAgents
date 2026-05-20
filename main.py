import glob
import json
import os
import re
import sys
from datetime import datetime

from tools.dart_tools import lookup_company
from orchestrator.orchestrator_agent import OrchestratorAgent

REPORTS_DIR = "reports"


def _ask_date(prompt: str) -> datetime:
    while True:
        raw = input(prompt).strip()
        try:
            return datetime.strptime(raw, "%Y/%m/%d")
        except ValueError:
            print("  Invalid format. Please use YYYY/MM/DD (e.g. 2024/02/01).")


# ── Load-saved-signals flow ───────────────────────────────────────────────────

def _list_signal_files() -> list[str]:
    """Return sorted list of *signals*.json files in the reports folder."""
    return sorted(glob.glob(os.path.join(REPORTS_DIR, "*signals*.json")))


def _load_signals_flow() -> tuple[dict, datetime]:
    """
    Let the user pick saved signal JSON files and reconstruct all_results.
    Returns (all_results, as_of_date) ready to pass straight into finalize().
    """
    files = _list_signal_files()
    if not files:
        print(f"\n  No saved signal files found in '{REPORTS_DIR}/'.")
        print("  Run a new analysis first to generate signal files.")
        sys.exit(1)

    print(f"\n  Saved signal files ({len(files)} found):")
    for i, path in enumerate(files):
        # Quick-peek at the JSON header so we can show useful info
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            label = (f"  [{i+1:>2}] {meta['stock_code']}  "
                     f"{meta['company_name']}  "
                     f"(as_of {meta['as_of_date']})")
        except Exception:
            label = f"  [{i+1:>2}] {os.path.basename(path)}"
        print(label)

    print()
    while True:
        raw = input("  Enter file numbers to load (e.g. 1  or  1,3,4): ").strip()
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip()]
            if not indices:
                raise ValueError
            break
        except ValueError:
            print("  Invalid input — enter comma-separated numbers from the list.")

    all_results = {}
    as_of_date  = None

    for idx in indices:
        if not (0 <= idx < len(files)):
            print(f"  Skipping out-of-range index {idx + 1}.")
            continue
        try:
            with open(files[idx], encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"  Could not read {files[idx]}: {e}. Skipping.")
            continue

        stock_code = data["stock_code"]
        if stock_code in all_results:
            print(f"  {stock_code} already loaded — skipping duplicate.")
            continue

        file_date = datetime.strptime(data["as_of_date"], "%Y-%m-%d")
        if as_of_date is None:
            as_of_date = file_date
        elif as_of_date != file_date:
            print(f"  Warning: {stock_code} has a different as_of_date "
                  f"({data['as_of_date']} vs {as_of_date.strftime('%Y-%m-%d')}). "
                  f"Using the first date.")

        all_results[stock_code] = {
            "company_name":   data["company_name"],
            "corp_info":      data["corp_info"],
            "debate_results": data["debate_results"],
            "report_files":   data["report_files"],
            "data":           {},   # not needed for finalize()
        }
        print(f"  Loaded: {stock_code} — {data['company_name']}  (as_of {data['as_of_date']})")

    if not all_results:
        print("  No signals loaded. Exiting.")
        sys.exit(1)

    return all_results, as_of_date


# ── MD-to-JSON converter ─────────────────────────────────────────────────────

_AGENTS = ["FundamentalAgent", "SentimentAgent", "ValuationAgent",
           "MarketAgent", "MacroAgent"]

_PROFILE_TAG = {"averse": "risk-averse", "neutral": "risk-neutral"}


def _parse_md(path: str) -> dict:
    """
    Parse a K-AlphaAgents markdown report and return a structured dict:
      {
        "company_name": str,
        "stock_code":   str,
        "analysis_date": datetime,
        "profile":      "risk-averse" | "risk-neutral",
        "final_signal": "BUY" | "SELL",
        "consensus_type": "unanimous" | "majority",
        "consensus_round": int,
        "initial_signals": {agent: signal, ...},
        "final_signals":   {agent: signal, ...},
      }
    Raises ValueError if key fields cannot be parsed.
    """
    with open(path, encoding="utf-8") as f:
        text = f.read()

    def _first(pattern, flags=0):
        m = re.search(pattern, text, flags)
        if not m:
            raise ValueError(f"Pattern not found in {path}: {pattern!r}")
        return m.group(1).strip()

    company_name   = _first(r"\|\s*\*\*Company\*\*\s*\|\s*(.+?)\s*\|")
    stock_code     = _first(r"\|\s*\*\*Stock Code\*\*\s*\|\s*(\d+)\s*\|")
    date_str       = _first(r"\|\s*\*\*Analysis Date\*\*\s*\|\s*(.+?)\s*\|")
    profile_raw    = _first(r"\|\s*\*\*Risk Profile\*\*\s*\|\s*(.+?)\s*\|")
    final_signal   = _first(r"##\s*Final Recommendation:\s*\*\*(\w+)\*\*")
    consensus_raw  = _first(r"Consensus type:\s*\*\*(\w+)\*\*")
    rounds_str     = _first(r"Reached after:\s*\*\*(\d+)\*\*")

    # Normalise analysis date (actual run timestamp — kept for reference)
    try:
        analysis_date = datetime.strptime(date_str, "%Y-%m-%d %H:%M")
    except ValueError:
        analysis_date = datetime.strptime(date_str[:10], "%Y-%m-%d")

    # User-supplied data as-of date (present in newer MD files via "Data As-Of" row)
    m_aod = re.search(r"\|\s*\*\*Data As-Of\*\*\s*\|\s*(.+?)\s*\|", text)
    data_as_of: datetime | None = None
    if m_aod:
        raw_aod = m_aod.group(1).strip()
        if raw_aod and raw_aod != "N/A":
            try:
                data_as_of = datetime.strptime(raw_aod, "%Y-%m-%d")
            except ValueError:
                pass

    profile = "risk-averse" if "averse" in profile_raw.lower() else "risk-neutral"
    consensus_type = consensus_raw.lower()          # "unanimous" / "majority"
    consensus_round = int(rounds_str)

    # Agent signals table
    # Header line: | Agent | Initial Signal | Final Signal | Changed? |
    # Data lines:  | FundamentalAgent | SELL | SELL | No |
    table_block = re.search(
        r"\|\s*Agent\s*\|.*?Initial Signal.*?\|.*?\n((?:\|.+\|\n?)+)",
        text, re.IGNORECASE
    )
    if not table_block:
        raise ValueError(f"Agent signals table not found in {path}")

    initial_signals: dict[str, str] = {}
    final_signals:   dict[str, str] = {}
    for row in table_block.group(1).splitlines():
        cols = [c.strip() for c in row.split("|") if c.strip()]
        if len(cols) < 3:
            continue
        agent, initial, final = cols[0], cols[1].upper(), cols[2].upper()
        if agent in _AGENTS:
            initial_signals[agent] = initial
            final_signals[agent]   = final

    return {
        "company_name":    company_name,
        "stock_code":      stock_code,
        "analysis_date":   analysis_date,   # actual run timestamp
        "data_as_of":      data_as_of,      # user-typed date (None if old file)
        "profile":         profile,
        "final_signal":    final_signal.upper(),
        "consensus_type":  consensus_type,
        "consensus_round": consensus_round,
        "initial_signals": initial_signals,
        "final_signals":   final_signals,
    }


def _build_debate_result(parsed: dict) -> dict:
    """
    Reconstruct the debate_result dict expected by portfolio_agent / finalize()
    from the fields extracted by _parse_md().
    """
    def _results_list(signal_map: dict) -> list:
        return [{"agent": a, "signal": signal_map.get(a, "SELL"), "analysis": ""}
                for a in _AGENTS]

    debate_log = [
        {"round": 0, "label": "Independent Analysis",
         "results": _results_list(parsed["initial_signals"])},
    ]
    if parsed["consensus_round"] > 0:
        debate_log.append({
            "round": parsed["consensus_round"],
            "label": f"Debate Round {parsed['consensus_round']}",
            "results": _results_list(parsed["final_signals"]),
        })

    return {
        "company_name":    parsed["company_name"],
        "final_signal":    parsed["final_signal"],
        "consensus_type":  parsed["consensus_type"],
        "consensus_round": parsed["consensus_round"],
        "debate_log":      debate_log,
    }


def _find_md_pairs() -> list[dict]:
    """
    Scan REPORTS_DIR for *_averse_*.md + matching *_neutral_*.md pairs.
    Returns a list of dicts: {stock_code, timestamp, averse_path, neutral_path}.
    """
    averse_files = glob.glob(os.path.join(REPORTS_DIR, "*_averse*.md"))
    pairs = []
    seen = set()
    for averse in sorted(averse_files):
        base = os.path.basename(averse)
        # New format: 086900_메디톡스_2025-06-01_averse.md
        # Old format: 086900_averse_20260501_1106.md
        m_new = re.match(r"^(\d+)_(.+)_(\d{4}-\d{2}-\d{2})_averse\.md$", base)
        m_old = re.match(r"^(\d+)_averse_(\d{8}_\d{4})\.md$", base)

        if m_new:
            stock_code = m_new.group(1)
            corp_name  = m_new.group(2)
            date_part  = m_new.group(3)
            neutral    = os.path.join(
                REPORTS_DIR, f"{stock_code}_{corp_name}_{date_part}_neutral.md"
            )
            key = f"{stock_code}_{date_part}"
        elif m_old:
            stock_code = m_old.group(1)
            ts         = m_old.group(2)
            corp_name  = ""
            date_part  = ts
            neutral    = os.path.join(REPORTS_DIR, f"{stock_code}_neutral_{ts}.md")
            key = f"{stock_code}_{ts}"
        else:
            continue

        if key in seen or not os.path.exists(neutral):
            continue
        seen.add(key)
        pairs.append({
            "stock_code":   stock_code,
            "timestamp":    date_part,
            "averse_path":  averse,
            "neutral_path": neutral,
        })
    return pairs


def _convert_md_to_signals_flow() -> None:
    """
    Find MD report pairs, let the user pick which to convert, then
    write *_signals_*.json files ready for [L] to load.
    """
    pairs = _find_md_pairs()
    if not pairs:
        print(f"\n  No MD report pairs found in '{REPORTS_DIR}/'.")
        print("  Expected files like: <code>_averse_<ts>.md  +  <code>_neutral_<ts>.md")
        return

    print(f"\n  Found {len(pairs)} convertible MD report pair(s):\n")
    # Pre-parse all pairs so we can show data_as_of in the list
    parsed_pairs = []
    for p in pairs:
        try:
            pav  = _parse_md(p["averse_path"])
            pneu = _parse_md(p["neutral_path"])
            parsed_pairs.append((p, pav, pneu))
            aod_str = (pav["data_as_of"].strftime("%Y-%m-%d")
                       if pav["data_as_of"] else "unknown — will ask")
            label = (f"  [{len(parsed_pairs):>2}] {p['stock_code']}  {pav['company_name']}"
                     f"  (data as-of: {aod_str})")
        except Exception:
            parsed_pairs.append((p, None, None))
            label = f"  [{len(parsed_pairs):>2}] {p['stock_code']}  (could not parse)"
        print(label)

    print()
    while True:
        raw = input("  Convert all? (A) or enter numbers (e.g. 1,3): ").strip().upper()
        if raw == "A":
            indices = list(range(len(parsed_pairs)))
            break
        try:
            indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip()]
            if not indices:
                raise ValueError
            break
        except ValueError:
            print("  Invalid input.")

    # If any selected file is missing data_as_of, ask once upfront
    needs_date = any(
        parsed_pairs[i][1] is not None and parsed_pairs[i][1]["data_as_of"] is None
        for i in indices if 0 <= i < len(parsed_pairs)
    )
    fallback_as_of: datetime | None = None
    if needs_date:
        print("\n  Some MD files don't have the original analysis date stored.")
        print("  This is the date you typed at the start of the analysis run")
        print("  (e.g. 2025/06/01 means 'use data prior to June 1 2025').")
        fallback_as_of = _ask_date("  Enter that date (YYYY/MM/DD): ")

    converted = 0
    for idx in indices:
        if not (0 <= idx < len(parsed_pairs)):
            print(f"  Skipping out-of-range index {idx + 1}.")
            continue
        p, parsed_av, parsed_neu = parsed_pairs[idx]
        if parsed_av is None or parsed_neu is None:
            print(f"  Could not parse {p['stock_code']}. Skipping.")
            continue

        as_of_date   = parsed_av["data_as_of"] or fallback_as_of
        company_name = parsed_av["company_name"]
        safe_name    = re.sub(r"\(주\)|\(주식회사\)|\(유\)", "", company_name)
        safe_name    = re.sub(r"[^\w가-힣぀-ヿ一-鿿\s\-]", "", safe_name)
        safe_name    = re.sub(r"[\s_]+", "_", safe_name.strip()).strip("_") or p["stock_code"]
        date_tag     = as_of_date.strftime("%Y-%m-%d")

        debate_results = {
            "risk-averse":  _build_debate_result(parsed_av),
            "risk-neutral": _build_debate_result(parsed_neu),
        }
        corp_info = {
            "corp_name":  company_name,
            "corp_code":  "",            # not needed by finalize()
        }

        signals_path = os.path.join(
            REPORTS_DIR,
            f"{p['stock_code']}_{safe_name}_{date_tag}_signals.json"
        )
        payload = {
            "stock_code":     p["stock_code"],
            "company_name":   company_name,
            "as_of_date":     as_of_date.strftime("%Y-%m-%d"),
            "corp_info":      corp_info,
            "debate_results": debate_results,
            "report_files": {
                "risk-averse":  p["averse_path"],
                "risk-neutral": p["neutral_path"],
            },
        }
        with open(signals_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

        print(f"  Converted: {p['stock_code']} ({company_name})  →  {signals_path}")
        converted += 1

    print(f"\n  Done — {converted} signal file(s) created.")
    if converted:
        print("  You can now run [L] to load them without re-analysing.\n")


# ── New-analysis flow ─────────────────────────────────────────────────────────

def _new_analysis_flow(orchestrator: OrchestratorAgent) -> tuple[dict, datetime]:
    """Full stock-pool loop: ask date, analyze stocks one by one."""
    as_of_date = _ask_date(
        "\n  Enter analysis date (YYYY/MM/DD)"
        " — all stocks will be analysed using data prior to this date: "
    )

    all_results = {}

    while True:
        n = len(all_results) + 1
        print(f"\n{'─'*60}")
        print(f"  Stock #{n}  ({len(all_results)} in pool so far)")
        print(f"{'─'*60}")

        stock_code = input("  Enter stock ticker (e.g. 005930): ").strip()
        if not stock_code:
            if not all_results:
                print("  No stocks entered. Exiting.")
                sys.exit(1)
            print("  Empty input — ending stock entry.")
            break

        if stock_code in all_results:
            print(f"  {stock_code} is already in the pool. Skipping.")
            continue

        print("  Looking up company on OpenDART...")
        try:
            corp_info = lookup_company(stock_code)
        except Exception as e:
            print(f"  Error: {e}. Skipping.")
            continue
        print(f"  Confirmed: {corp_info['corp_name']}  ({stock_code})")

        result = orchestrator.analyze_stock(stock_code, as_of_date, corp_info)
        all_results[stock_code] = result

        more = input(
            f"\n  Add another stock to the pool? (Y/N)"
            f"  [{len(all_results)} stock(s) analysed]: "
        ).strip().upper()
        if more != "Y":
            break

    return all_results, as_of_date


# ── Rebalancing JSON save / load ──────────────────────────────────────────────

def _save_rebalancing_json(
    start_date: datetime,
    end_date: datetime,
    stock_codes: list,
    company_names: dict,
    weight_schedule: dict,
    quarterly_log: list,
) -> str:
    """
    Persist rebalancing results so the user can reload and re-run the
    backtest without repeating the LLM analysis.

    Saves to: reports/Rebalanced_{start_date}.json
    """
    def _ser_portfolio(po: dict) -> dict:
        return {
            "equity_weight":    po["equity_weight"],
            "bond_weight":      po["bond_weight"],
            "position_taken":   po["position_taken"],
            "weights":          dict(po["weights"]),
            "stock_allocations": {
                code: {
                    "signal":     a["signal"],
                    "conviction": a["conviction"],
                    "weight":     a["weight"],
                }
                for code, a in po["stock_allocations"].items()
            },
        }

    ql_serial = [
        {
            "quarter":    q["quarter"],
            "start":      q["start"].strftime("%Y-%m-%d"),
            "end":        q["end"].strftime("%Y-%m-%d"),
            "portfolios": {
                p: _ser_portfolio(q["portfolios"][p])
                for p in ("risk-averse", "risk-neutral")
            },
        }
        for q in quarterly_log
    ]

    ws_serial = {
        profile: [
            [dt.strftime("%Y-%m-%d") if isinstance(dt, datetime) else str(dt)[:10], w]
            for dt, w in entries
        ]
        for profile, entries in weight_schedule.items()
    }

    payload = {
        "start_date":     start_date.strftime("%Y-%m-%d"),
        "end_date":       end_date.strftime("%Y-%m-%d"),
        "stock_codes":    stock_codes,
        "company_names":  company_names,
        "quarterly_log":  ql_serial,
        "weight_schedule": ws_serial,
    }

    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = os.path.join(
        REPORTS_DIR,
        f"Rebalanced_{start_date.strftime('%Y-%m-%d')}.json"
    )
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  Rebalancing results saved → {path}")
    return path


def _list_rebalancing_files() -> list:
    """Return sorted list of Rebalanced_*.json files in reports/."""
    return sorted(glob.glob(os.path.join(REPORTS_DIR, "Rebalanced_*.json")))


def _load_rebalancing_json(path: str) -> dict:
    """Deserialise a saved rebalancing JSON back to Python objects."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    data["start_date"] = datetime.strptime(data["start_date"], "%Y-%m-%d")
    data["end_date"]   = datetime.strptime(data["end_date"],   "%Y-%m-%d")

    for q in data["quarterly_log"]:
        q["start"]   = datetime.strptime(q["start"], "%Y-%m-%d")
        q["end"]     = datetime.strptime(q["end"],   "%Y-%m-%d")
        q["results"] = {}   # not stored — signals already in per-stock JSON files

    data["weight_schedule"] = {
        profile: [
            (datetime.strptime(dt_str, "%Y-%m-%d"), weights)
            for dt_str, weights in entries
        ]
        for profile, entries in data["weight_schedule"].items()
    }

    return data


def _load_rebalancing_backtest_flow(orchestrator: "OrchestratorAgent") -> None:
    """
    [B] Load saved rebalancing results and proceed straight to backtest + PDF.

    No LLM calls — uses the saved weight schedule and quarterly log.
    The user may extend the backtest end date beyond the original rebalancing period.
    """
    from backtest.runner import run_rebalanced_backtest
    from report.summary_renderer import build_pdf
    import anthropic as _ant
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    files = _list_rebalancing_files()
    if not files:
        print("\n  No saved rebalancing files found in reports/.")
        return

    print(f"\n  Saved rebalancing results ({len(files)} found):")
    for i, path in enumerate(files):
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            print(f"  [{i+1:>2}]  {meta['start_date']} → {meta['end_date']}"
                  f"  |  {len(meta['quarterly_log'])} quarters"
                  f"  |  {', '.join(meta['stock_codes'])}")
        except Exception:
            print(f"  [{i+1:>2}]  {os.path.basename(path)}  (unreadable)")

    if len(files) == 1:
        selected = files[0]
        print(f"\n  Auto-selected: {os.path.basename(selected)}")
    else:
        while True:
            raw = input(f"\n  Select file (1–{len(files)}): ").strip()
            if raw.isdigit() and 1 <= int(raw) <= len(files):
                selected = files[int(raw) - 1]
                break
            print(f"  Please enter a number between 1 and {len(files)}.")

    data          = _load_rebalancing_json(selected)
    start_date    = data["start_date"]
    saved_end     = data["end_date"]
    stock_codes   = data["stock_codes"]
    company_names = data["company_names"]
    quarterly_log = data["quarterly_log"]
    weight_schedule = data["weight_schedule"]

    print(f"\n  Loaded: {len(quarterly_log)} quarters  "
          f"({start_date.strftime('%Y-%m-%d')} → {saved_end.strftime('%Y-%m-%d')})")
    print(f"  Stocks: {', '.join(stock_codes)}")

    # ── Optionally extend the backtest end date ───────────────────────────
    print(f"\n  Saved end date: {saved_end.strftime('%Y-%m-%d')}")
    extend = input(
        "  Use saved end date? (Y) or enter a new end date? (N) [Y]: "
    ).strip().upper()

    if extend == "N":
        while True:
            end_date = _ask_date(
                f"  New backtest end date (YYYY/MM/DD)"
                f" [must be after {start_date.strftime('%Y-%m-%d')}]: "
            )
            if end_date > start_date:
                break
            print(f"  Must be after {start_date.strftime('%Y-%m-%d')}.")
    else:
        end_date = saved_end

    # ── Run time-varying backtest ─────────────────────────────────────────
    print(f"\n  Running rebalanced backtest "
          f"({start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')})...")
    backtest_results = run_rebalanced_backtest(
        weight_schedules=weight_schedule,
        start_date=start_date,
        end_date=end_date,
        all_stock_codes=stock_codes,
    )

    # ── LLM narrative ─────────────────────────────────────────────────────
    q_summary = "\n".join(
        f"  Q{q['quarter']} ({q['start'].strftime('%Y-%m-%d')}): "
        + ", ".join(
            f"{code} → "
            f"{q['portfolios']['risk-neutral']['stock_allocations'][code]['signal']}"
            for code in stock_codes
            if code in q['portfolios']['risk-neutral']['stock_allocations']
        )
        for q in quarterly_log
    )
    narrative_prompt = (
        f"You are writing a concise executive summary for a rebalanced portfolio report.\n"
        f"Stocks: {', '.join(f'{c} ({n})' for c, n in company_names.items())}\n"
        f"Rebalancing history:\n{q_summary}\n\n"
        f"Write 4–5 sentences covering: (1) how signals evolved across quarters, "
        f"(2) which stocks were consistently held vs rotated out, "
        f"(3) the recommended posture going forward, "
        f"(4) one key risk to watch."
    )
    try:
        _cl       = _ant.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp      = _cl.messages.create(
            model=CLAUDE_MODEL, max_tokens=500,
            messages=[{"role": "user", "content": narrative_prompt}]
        )
        narrative = resp.content[0].text.strip()
    except Exception:
        narrative = (
            f"Rebalanced portfolio across {len(quarterly_log)} quarter(s). "
            "LLM narrative unavailable — check API key."
        )

    # ── PDF ───────────────────────────────────────────────────────────────
    os.makedirs(REPORTS_DIR, exist_ok=True)
    date_tag = start_date.strftime("%Y-%m-%d")
    pdf_path = os.path.join(REPORTS_DIR, f"Exec Sum_Rebalanced_{date_tag}.pdf")

    build_pdf(
        pdf_path=pdf_path,
        company_names=company_names,
        portfolios=quarterly_log[-1]["portfolios"],
        narrative=narrative,
        as_of_date=end_date,
        backtest_results=backtest_results,
        quarterly_log=quarterly_log,
    )

    print(f"\n{'='*60}")
    print(f"  BACKTEST COMPLETE  (from saved rebalancing)")
    print(f"  PDF → {pdf_path}")
    print(f"{'='*60}\n")


# ── Rebalancing flow ──────────────────────────────────────────────────────────

def _run_rebalancing(
    orchestrator: OrchestratorAgent,
    all_results: dict,
    as_of_date: datetime,
) -> None:
    """
    Rebalancing path — called after [N] or [L] analysis completes.

    Reuses the already-computed Q1 results so no LLM calls are wasted.
    Subsequent quarters re-run the full 5-agent debate with fresh data.

    Benchmarks: EW buy-and-hold + KOSPI + KOSDAQ
    (same indices as standard backtest; portfolio line is time-varying)
    """
    from rebalance.rebalance_engine import RebalanceEngine
    from backtest.runner import run_rebalanced_backtest
    from report.summary_renderer import build_pdf
    import anthropic as _ant
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    stock_codes   = list(all_results.keys())
    corp_infos    = {code: r["corp_info"] for code, r in all_results.items()}
    company_names = {code: r["company_name"] for code, r in all_results.items()}

    print("\n  Rebalancing from "
          f"{as_of_date.strftime('%Y-%m-%d')} (Q1 analysis already done).")

    while True:
        end_date = _ask_date(
            f"  Enter backtest end date (YYYY/MM/DD)"
            f" [must be after {as_of_date.strftime('%Y-%m-%d')}]: "
        )
        if end_date > as_of_date:
            break
        print(f"  End date must be after {as_of_date.strftime('%Y-%m-%d')}. Try again.")

    use_events = input(
        "\n  Enable intra-quarter event-triggered re-weighting? (Y/N) [Y]: "
    ).strip().upper()
    use_events = use_events != "N"

    # ── Run rebalancing engine (Q1 results reused, Q2+ re-analysed) ──────
    engine = RebalanceEngine(orchestrator)
    weight_schedule, quarterly_log = engine.run(
        stock_codes=stock_codes,
        corp_infos=corp_infos,
        start_date=as_of_date,
        end_date=end_date,
        use_event_triggers=use_events,
        initial_results=all_results,       # skip Q1 LLM re-run
    )

    # ── Time-varying backtest ─────────────────────────────────────────────
    print(f"\n  Running rebalanced backtest "
          f"({as_of_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')})...")
    backtest_results = run_rebalanced_backtest(
        weight_schedules=weight_schedule,
        start_date=as_of_date,
        end_date=end_date,
        all_stock_codes=stock_codes,
    )

    # ── LLM narrative ─────────────────────────────────────────────────────
    q_summary = "\n".join(
        f"  Q{q['quarter']} ({q['start'].strftime('%Y-%m-%d')}): "
        + ", ".join(
            f"{code} → "
            f"{q['portfolios']['risk-neutral']['stock_allocations'][code]['signal']}"
            for code in stock_codes
        )
        for q in quarterly_log
    )
    narrative_prompt = (
        f"You are writing a concise executive summary for a rebalanced portfolio report.\n"
        f"Stocks: {', '.join(f'{c} ({n})' for c, n in company_names.items())}\n"
        f"Rebalancing history:\n{q_summary}\n\n"
        f"Write 4–5 sentences covering: (1) how signals evolved across quarters, "
        f"(2) which stocks were consistently held vs rotated out, "
        f"(3) the impact of event-triggered re-weighting, "
        f"(4) the recommended posture going forward, "
        f"(5) one key risk to watch."
    )
    try:
        _cl        = _ant.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp       = _cl.messages.create(
            model=CLAUDE_MODEL, max_tokens=500,
            messages=[{"role": "user", "content": narrative_prompt}]
        )
        narrative = resp.content[0].text.strip()
    except Exception:
        narrative = (
            f"Rebalanced portfolio across {len(quarterly_log)} quarter(s). "
            "LLM narrative unavailable — check API key."
        )

    # ── PDF ───────────────────────────────────────────────────────────────
    os.makedirs(REPORTS_DIR, exist_ok=True)
    date_tag = as_of_date.strftime("%Y-%m-%d")
    pdf_path = os.path.join(REPORTS_DIR, f"Exec Sum_Rebalanced_{date_tag}.pdf")

    build_pdf(
        pdf_path=pdf_path,
        company_names=company_names,
        portfolios=quarterly_log[-1]["portfolios"],
        narrative=narrative,
        as_of_date=end_date,
        backtest_results=backtest_results,
        quarterly_log=quarterly_log,
    )

    # ── Save rebalancing results for future reload ────────────────────────
    _save_rebalancing_json(
        start_date=as_of_date,
        end_date=end_date,
        stock_codes=stock_codes,
        company_names=company_names,
        weight_schedule=weight_schedule,
        quarterly_log=quarterly_log,
    )

    print(f"\n{'='*60}")
    print(f"  REBALANCING COMPLETE")
    print(f"  {len(quarterly_log)} quarter(s)  |  "
          f"{sum(len(v) for v in weight_schedule.values())} total weight events")
    print(f"  PDF → {pdf_path}")
    print(f"  Tip: Use [B] next time to reload this run and re-run the backtest.")
    print(f"{'='*60}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "="*60)
    print("  K-AlphaAgents — Korean Equity Analysis")
    print("="*60)
    print("\n  [N] New analysis        — fetch data, run agents, save signals")
    print("  [L] Load saved signals  — skip analysis, go straight to portfolio & backtest")
    print("  [C] Convert MD reports  — convert existing .md reports to signal JSON files")

    while True:
        choice = input("\n  Choice (N / L / C): ").strip().upper()
        if choice in ("N", "L", "C"):
            break
        print("  Please enter N, L, or C.")

    if choice == "C":
        _convert_md_to_signals_flow()
        return

    orchestrator = OrchestratorAgent()

    if choice == "L":
        all_results, as_of_date = _load_signals_flow()
        print(f"\n  {len(all_results)} stock(s) loaded.")
    else:
        all_results, as_of_date = _new_analysis_flow(orchestrator)
        print(f"\n  {len(all_results)} stock(s) analysed.")

    # ── Ask how to proceed ────────────────────────────────────────────────
    rebal_files  = _list_rebalancing_files()
    valid_modes  = {"S", "R"}

    print("\n" + "─"*60)
    print("  How would you like to proceed?")
    print("  [S] Standard backtest   — static portfolio, single as-of date")
    print("      Benchmarks: EW buy-and-hold · KOSPI · KOSDAQ")
    print("  [R] Rebalancing         — quarterly LLM rebalance + event-triggered re-weighting")
    print("      Benchmarks: EW buy-and-hold · KOSPI · KOSDAQ")

    if rebal_files:
        valid_modes.add("B")
        print(f"  [B] Load saved rebalancing  — use existing rebalancing JSON → backtest")
        print(f"      ({len(rebal_files)} saved run(s) found in reports/)")

    print("─"*60)

    prompt = f"\n  Choice ({' / '.join(sorted(valid_modes))}): "
    while True:
        mode = input(prompt).strip().upper()
        if mode in valid_modes:
            break
        print(f"  Please enter one of: {', '.join(sorted(valid_modes))}.")

    if mode == "R":
        _run_rebalancing(orchestrator, all_results, as_of_date)
    elif mode == "B":
        _load_rebalancing_backtest_flow(orchestrator)
    else:
        orchestrator.finalize(all_results, as_of_date)


if __name__ == "__main__":
    main()
