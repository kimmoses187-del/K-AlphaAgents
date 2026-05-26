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
    """
    Return sorted list of signal JSON files.
    Structure: reports/{run_date}/{as_of_date}/{ticker_name}/{file}.json
    (3 levels deep under reports/ — depth 4 total).
    Rebalanced_*.json and Q-folder JSONs sit deeper (5–6 levels) so are
    automatically excluded by this glob.
    """
    return sorted(glob.glob(os.path.join(REPORTS_DIR, "*", "*", "*", "*.json")))


def _pick_files_interactive(files: list[str], metas: list) -> list[int]:
    """
    Arrow-key + Space cursor picker. Returns list of selected 0-based indices.
    Falls back to numbered input if curses is unavailable.
    """
    import curses

    def _curses_picker(stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK,  curses.COLOR_YELLOW)  # cursor row
        curses.init_pair(2, curses.COLOR_GREEN,  -1)                   # selected mark
        curses.init_pair(3, curses.COLOR_YELLOW, -1)                   # ticker highlight

        selected = set()
        cursor   = 0
        n        = len(files)

        def _label(i):
            m = metas[i]
            if m:
                return f"{m['stock_code']:<8} {m['company_name']:<22} {m['as_of_date']}"
            return os.path.basename(files[i])

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            header = "  Select signal files   ↑↓ navigate · SPACE toggle · A all · ENTER confirm"
            stdscr.addstr(0, 0, header[:w-1], curses.A_BOLD)
            stdscr.addstr(1, 0, "  " + "─" * min(70, w-3))

            for i in range(n):
                row = i + 2
                if row >= h - 2:
                    break
                mark  = "☑" if i in selected else "☐"
                label = _label(i)
                line  = f"  {mark}  {label}"[:w-1]
                attr  = curses.color_pair(1) | curses.A_BOLD if i == cursor else curses.A_NORMAL
                stdscr.addstr(row, 0, line, attr)
                if i in selected and i != cursor:
                    # Re-colour the checkmark green
                    stdscr.addstr(row, 2, "☑", curses.color_pair(2) | curses.A_BOLD)

            footer = f"  {len(selected)} selected — ENTER to confirm"
            stdscr.addstr(h - 1, 0, footer[:w-1], curses.A_DIM)
            stdscr.refresh()

            key = stdscr.getch()
            if key in (curses.KEY_UP, ord('k')):
                cursor = (cursor - 1) % n
            elif key in (curses.KEY_DOWN, ord('j')):
                cursor = (cursor + 1) % n
            elif key == ord(' '):
                if cursor in selected:
                    selected.discard(cursor)
                else:
                    selected.add(cursor)
            elif key in (ord('a'), ord('A')):
                if len(selected) == n:
                    selected.clear()
                else:
                    selected = set(range(n))
            elif key in (10, 13, curses.KEY_ENTER):   # Enter
                if selected:
                    return sorted(selected)
            elif key == 27:                            # Esc — cancel
                return []

    try:
        return curses.wrapper(_curses_picker)
    except Exception:
        # Fallback: plain numbered input
        print(f"\n  Saved signal files ({len(files)} found):")
        for i, m in enumerate(metas):
            if m:
                print(f"  [{i+1:>2}] {m['stock_code']}  {m['company_name']}  (as_of {m['as_of_date']})")
            else:
                print(f"  [{i+1:>2}] {os.path.basename(files[i])}")
        print()
        while True:
            raw = input("  Enter file numbers to load (e.g. 1  or  1,3,4): ").strip()
            try:
                indices = [int(x.strip()) - 1 for x in raw.split(",") if x.strip()]
                if indices:
                    return indices
            except ValueError:
                pass
            print("  Invalid input — enter comma-separated numbers from the list.")


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

    # Load metadata for all files upfront
    metas = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                metas.append(json.load(f))
        except Exception:
            metas.append(None)

    indices = _pick_files_interactive(files, metas)
    if not indices:
        print("  No files selected. Exiting.")
        sys.exit(1)

    all_results = {}
    as_of_date  = None

    for idx in indices:
        if not (0 <= idx < len(files)):
            continue
        data = metas[idx]
        if data is None:
            print(f"  Could not read {files[idx]}. Skipping.")
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
            "data":           {},
        }
        print(f"  Loaded: {stock_code} — {data['company_name']}  (as_of {data['as_of_date']})")

    if not all_results:
        print("  No signals loaded. Exiting.")
        sys.exit(1)

    return all_results, as_of_date


# ── MD-to-JSON converter ─────────────────────────────────────────────────────

_AGENTS = ["FundamentalAgent", "SentimentAgent", "TechnicalAgent",
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
    averse_files = glob.glob(os.path.join(REPORTS_DIR, "**", "*_averse.md"), recursive=True)
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

        # Save JSON next to the MD files (same directory)
        md_dir       = os.path.dirname(p["averse_path"])
        signals_path = os.path.join(md_dir, f"{p['stock_code']}_{safe_name}_{date_tag}.json")
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
    save_dir: str = None,
) -> str:
    """
    Persist rebalancing results so the user can reload and re-run the
    backtest without repeating the LLM analysis.

    Saves to: {save_dir}/Rebalanced_{start_date}.json
    Falls back to reports/ root if save_dir is not provided.
    """
    def _ser_portfolio(po: dict) -> dict:
        return {
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

    dest = save_dir or REPORTS_DIR
    os.makedirs(dest, exist_ok=True)
    path = os.path.join(dest, f"Rebalanced_{start_date.strftime('%Y-%m-%d')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"  Rebalancing results saved → {path}")
    return path


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

    # ── Establish the run directory for all output files ──────────────────
    date_tag = as_of_date.strftime("%Y-%m-%d")
    run_date = datetime.now().strftime("%Y-%m-%d")
    run_dir  = os.path.join(REPORTS_DIR, run_date, date_tag)

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
        run_dir=run_dir,                   # Q2+ reports go under run_dir/backtest/rebalance/
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
    rebal_dir = os.path.join(run_dir, "backtest", "rebalance")
    os.makedirs(rebal_dir, exist_ok=True)
    pdf_path = os.path.join(rebal_dir, f"Exec_Sum_Rebalanced_{date_tag}.pdf")

    build_pdf(
        pdf_path=pdf_path,
        company_names=company_names,
        portfolios=quarterly_log[-1]["portfolios"],
        narrative=narrative,
        as_of_date=end_date,
        backtest_results=backtest_results,
        quarterly_log=quarterly_log,
    )

    # ── Save rebalancing results ──────────────────────────────────────────
    _save_rebalancing_json(
        start_date=as_of_date,
        end_date=end_date,
        stock_codes=stock_codes,
        company_names=company_names,
        weight_schedule=weight_schedule,
        quarterly_log=quarterly_log,
        save_dir=rebal_dir,
    )

    print(f"\n{'='*60}")
    print(f"  REBALANCING COMPLETE")
    print(f"  {len(quarterly_log)} quarter(s)  |  "
          f"{sum(len(v) for v in weight_schedule.values())} total weight events")
    print(f"  PDF → {pdf_path}")
    print(f"{'='*60}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    print("\n" + "="*60)
    print("  K-AlphaAgents — Korean Equity Analysis")
    print("="*60)
    print("\n  [N] New analysis         — fetch data, run agents, save signals")
    print("  [L] Load saved signals   — load signals, then choose save or backtest")
    print("  [B] Load & run backtest  — load signals and go straight to backtest")

    while True:
        choice = input("\n  Choice (N / L / B): ").strip().upper()
        if choice in ("N", "L", "B"):
            break
        print("  Please enter N, L, or B.")

    orchestrator = OrchestratorAgent()

    # ── Load & Backtest shortcut ──────────────────────────────────────────────
    if choice == "B":
        all_results, as_of_date = _load_signals_flow()
        print(f"\n  {len(all_results)} stock(s) loaded.")
        _run_backtest_menu(orchestrator, all_results, as_of_date)
        return

    # ── Load signals ──────────────────────────────────────────────────────────
    if choice == "L":
        all_results, as_of_date = _load_signals_flow()
        print(f"\n  {len(all_results)} stock(s) loaded.")

    # ── New analysis ──────────────────────────────────────────────────────────
    else:
        all_results, as_of_date = _new_analysis_flow(orchestrator)
        print(f"\n  {len(all_results)} stock(s) analysed.")

    # ── Save & Exit breakpoint ────────────────────────────────────────────────
    print("\n" + "─"*60)
    print("  Signals saved to reports/.")
    print("  [R] Run backtest now")
    print("  [S] Save & exit  (reload later with L or B)")
    while True:
        nxt = input("\n  Choice (R / S): ").strip().upper()
        if nxt in ("R", "S"):
            break
        print("  Please enter R or S.")
    print("─"*60)

    if nxt == "S":
        print("\n  Signals saved. Reload any time with [L] or [B].")
        return

    _run_backtest_menu(orchestrator, all_results, as_of_date)


def _run_backtest_menu(orchestrator, all_results, as_of_date) -> None:
    """Ask standard vs rebalancing backtest and run it."""
    print("\n" + "─"*60)
    while True:
        rebalance = input(
            "  Would you like to rebalance quarterly? (Y/N): "
        ).strip().upper()
        if rebalance in ("Y", "N"):
            break
        print("  Please enter Y or N.")
    print("─"*60)

    if rebalance == "Y":
        _run_rebalancing(orchestrator, all_results, as_of_date)
    else:
        orchestrator.finalize(all_results, as_of_date)


if __name__ == "__main__":
    main()
