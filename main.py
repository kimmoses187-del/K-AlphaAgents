import glob
import json
import os
import re
import sys
from datetime import datetime

from tools.dart_tools import lookup_company
from orchestrator.orchestrator_agent import OrchestratorAgent
from calibration import load_or_generate_calibration
from calibration.pipeline import get_existing_signal_dates

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
    Structure: reports/signals/{ticker}_{name}/{as_of_date}/{file}.json
    """
    return sorted(glob.glob(os.path.join(REPORTS_DIR, "signals", "*", "*", "*.json")))


def _pick_files_interactive(files: list[str], metas: list) -> list[int]:
    """
    Two-level folder browser — company → date.
      Level 0 — company list  (ENTER open · D done · ESC cancel)
      Level 1 — date list     (SPACE toggle · A all · ENTER back · ESC back)
    ENTER at date level goes BACK to company list (not exit).
    D at company level finalises all selections and exits.
    Returns list of selected 0-based file indices.
    Falls back to plain numbered input if curses is unavailable.
    """
    import curses

    def _path_parts(path):
        """Return (ticker_folder, as_of_date) from a signals/ path."""
        p = path.replace("\\", "/").split("/")
        # reports/signals/{ticker}_{name}/{as_of_date}/{file}.json
        return (p[2] if len(p) > 2 else "?",   # ticker folder
                p[3] if len(p) > 3 else "?")    # as_of_date

    # Build:  ticker_folder → [(file_index, as_of_date, meta), ...]
    ticker_map: dict[str, list] = {}
    for i, (path, meta) in enumerate(zip(files, metas)):
        tkr, aod = _path_parts(path)
        ticker_map.setdefault(tkr, []).append((i, aod, meta))
    sorted_tickers = sorted(ticker_map.keys())

    def _curses_browser(stdscr):
        curses.curs_set(0)
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(1, curses.COLOR_BLACK,  curses.COLOR_YELLOW)  # cursor
        curses.init_pair(2, curses.COLOR_GREEN,  -1)                   # selected ☑
        curses.init_pair(3, curses.COLOR_CYAN,   -1)                   # company name

        level      = 0          # 0 = company list, 1 = date list
        cur_ticker = None
        selected   = set()      # file indices chosen by user
        cursor     = 0

        def _total_sel():
            return len(selected)

        def _draw_company_level(h, w):
            n_total = _total_sel()
            done_hint = f" · D confirm ({n_total} selected)" if n_total else ""
            hdr = f"  📁 signals/   ↑↓ navigate · ENTER open · ESC cancel{done_hint}"
            stdscr.addstr(0, 0, hdr[:w-1], curses.A_BOLD)
            stdscr.addstr(1, 0, "  " + "─" * min(72, w-3))
            for i, tkr in enumerate(sorted_tickers):
                row = i + 2
                if row >= h - 2:
                    break
                entries = ticker_map[tkr]
                n_dates = len(entries)
                n_sel   = sum(1 for e in entries if e[0] in selected)
                sel_tag = f"  ✓ {n_sel}/{n_dates}" if n_sel else ""
                m0      = entries[0][2]
                display = m0.get("company_name", tkr) if m0 else tkr
                line    = f"  📁  {display}  ({n_dates} date{'s' if n_dates != 1 else ''}){sel_tag}"
                attr    = (curses.color_pair(1) | curses.A_BOLD
                           if i == cursor else curses.color_pair(3))
                stdscr.addstr(row, 0, line[:w-1], attr)
                if n_sel and i != cursor:
                    # Green tick for companies that have selections
                    stdscr.addstr(row, 2, "📁", curses.color_pair(2))
            foot = "  ENTER open company · D confirm all · ESC cancel"
            stdscr.addstr(h - 1, 0, foot[:w-1], curses.A_DIM)

        def _draw_date_level(h, w):
            entries = ticker_map[cur_ticker]
            m0      = entries[0][2]
            company = m0.get("company_name", cur_ticker) if m0 else cur_ticker
            hdr     = (f"  📁 {company}   "
                       "SPACE select · A all · ENTER back · ESC back")
            stdscr.addstr(0, 0, hdr[:w-1], curses.A_BOLD)
            stdscr.addstr(1, 0, "  " + "─" * min(72, w-3))
            for i, (idx, aod, meta) in enumerate(entries):
                row  = i + 2
                if row >= h - 2:
                    break
                mark  = "☑" if idx in selected else "☐"
                label = f"  {mark}  {aod}"
                attr  = (curses.color_pair(1) | curses.A_BOLD
                         if i == cursor else curses.A_NORMAL)
                stdscr.addstr(row, 0, label[:w-1], attr)
                if idx in selected and i != cursor:
                    stdscr.addstr(row, 2, "☑", curses.color_pair(2) | curses.A_BOLD)
            n_sel = sum(1 for e in entries if e[0] in selected)
            foot  = f"  {n_sel} selected here — ENTER / ← to go back to company list"
            stdscr.addstr(h - 1, 0, foot[:w-1], curses.A_DIM)

        while True:
            stdscr.erase()
            h, w = stdscr.getmaxyx()

            if level == 0:
                _draw_company_level(h, w)
                n = len(sorted_tickers)
            else:
                _draw_date_level(h, w)
                n = len(ticker_map[cur_ticker])

            stdscr.refresh()
            key = stdscr.getch()

            if key in (curses.KEY_UP, ord('k')):
                cursor = (cursor - 1) % max(1, n)

            elif key in (curses.KEY_DOWN, ord('j')):
                cursor = (cursor + 1) % max(1, n)

            elif level == 0 and key in (10, 13, curses.KEY_ENTER, curses.KEY_RIGHT):
                # ENTER at company level → open date list
                cur_ticker = sorted_tickers[cursor]
                level      = 1
                cursor     = 0

            elif level == 0 and key in (ord('d'), ord('D')):
                # D at company level → done, return all selected
                if selected:
                    return sorted(selected)

            elif level == 0 and key == 27:
                return []

            elif level == 1:
                if key == ord(' '):
                    idx = ticker_map[cur_ticker][cursor][0]
                    selected.discard(idx) if idx in selected else selected.add(idx)

                elif key in (ord('a'), ord('A')):
                    tkr_idxs = {e[0] for e in ticker_map[cur_ticker]}
                    if tkr_idxs.issubset(selected):
                        selected -= tkr_idxs
                    else:
                        selected |= tkr_idxs

                elif key in (10, 13, curses.KEY_ENTER,
                             curses.KEY_LEFT, curses.KEY_BACKSPACE, 27):
                    # Any "back" key at date level → return to company list
                    level  = 0
                    cursor = sorted_tickers.index(cur_ticker)

    try:
        return curses.wrapper(_curses_browser)
    except Exception:
        # ── Fallback: plain numbered input ───────────────────────────────────
        print(f"\n  Saved signal files ({len(files)} found):")
        for i, (path, m) in enumerate(zip(files, metas)):
            tkr, aod = _path_parts(path)
            if m:
                print(f"  [{i+1:>2}] {m['stock_code']}  {m.get('company_name','')}  as_of:{aod}")
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


def _load_signals_flow() -> tuple[dict, list]:
    """
    Let the user pick saved signal JSON files.

    Returns
    -------
    quarterly_data : {as_of_date_str: {stock_code: result_dict}}
                     One entry per unique as-of date found in the selection.
    sorted_dates   : list[str] — as_of_date strings in ascending order.

    Single date selected  → standard load (one quarter, static portfolio).
    Multiple dates selected → pre-computed quarterly backtest is offered.
    """
    files = _list_signal_files()
    if not files:
        print(f"\n  No saved signal files found in '{REPORTS_DIR}/signals/'.")
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

    # Group by as_of_date so multiple quarters are preserved
    quarterly_data: dict[str, dict] = {}   # {date_str: {stock_code: result}}

    for idx in indices:
        if not (0 <= idx < len(files)):
            continue
        data = metas[idx]
        if data is None:
            print(f"  Could not read {files[idx]}. Skipping.")
            continue

        date_str   = data["as_of_date"]
        stock_code = data["stock_code"]

        if date_str not in quarterly_data:
            quarterly_data[date_str] = {}

        if stock_code in quarterly_data[date_str]:
            continue   # same stock + same date already loaded

        quarterly_data[date_str][stock_code] = {
            "company_name":   data["company_name"],
            "corp_info":      data["corp_info"],
            "debate_results": data["debate_results"],
            "report_files":   data["report_files"],
            "data":           {},
        }
        print(f"  Loaded: {stock_code} — {data['company_name']}  (as_of {date_str})")

    if not quarterly_data:
        print("  No signals loaded. Exiting.")
        sys.exit(1)

    sorted_dates = sorted(quarterly_data.keys())

    if len(sorted_dates) == 1:
        n = len(quarterly_data[sorted_dates[0]])
        print(f"\n  {n} stock(s) loaded  (as_of {sorted_dates[0]})")
    else:
        print(f"\n  {len(sorted_dates)} quarter(s) of signals loaded:")
        for d in sorted_dates:
            print(f"    {d}: {len(quarterly_data[d])} stock(s)")

    return quarterly_data, sorted_dates


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
    """Full stock-pool loop: ask date, confirm pool, then analyze with calibration."""
    as_of_date = _ask_date(
        "\n  Enter analysis date (YYYY/MM/DD)"
        " — all stocks will be analysed using data prior to this date: "
    )

    # ── Phase 1: collect stock pool ───────────────────────────────────────────
    corp_infos: dict[str, dict] = {}   # stock_code → corp_info

    while True:
        n = len(corp_infos) + 1
        print(f"\n{'─'*60}")
        print(f"  Stock #{n}  ({len(corp_infos)} in pool so far)")
        print(f"{'─'*60}")

        stock_code = input("  Enter stock ticker (e.g. 005930): ").strip()
        if not stock_code:
            if not corp_infos:
                print("  No stocks entered. Exiting.")
                sys.exit(1)
            print("  Empty input — ending stock entry.")
            break

        if stock_code in corp_infos:
            print(f"  {stock_code} is already in the pool. Skipping.")
            continue

        print("  Looking up company on OpenDART...")
        try:
            corp_info = lookup_company(stock_code)
        except Exception as e:
            print(f"  Error: {e}. Skipping.")
            continue
        print(f"  Confirmed: {corp_info['corp_name']}  ({stock_code})")
        corp_infos[stock_code] = corp_info

        more = input(
            f"\n  Add another stock to the pool? (Y/N)"
            f"  [{len(corp_infos)} stock(s) confirmed]: "
        ).strip().upper()
        if more != "Y":
            break

    # ── Phase 2: load or generate calibration context ─────────────────────────
    stock_codes = list(corp_infos.keys())
    calibration_context: dict = {}

    prior_dates = get_existing_signal_dates(stock_codes, REPORTS_DIR)
    as_of_str   = as_of_date.strftime("%Y-%m-%d")
    # Only consider prior dates (before current as-of date)
    prior_dates = [d for d in prior_dates if d < as_of_str]

    if prior_dates:
        # Include current as-of date as the holding-period end for the last prior quarter
        all_dates = prior_dates + [as_of_str]
        print(f"\n  📊 Loading calibration from {len(prior_dates)} prior quarter(s): "
              f"{', '.join(prior_dates)}")
        try:
            calibration_context = load_or_generate_calibration(
                stock_codes=stock_codes,
                signal_dates=all_dates,
                reports_dir=REPORTS_DIR,
            )
            if calibration_context:
                agents_with_cal = list(calibration_context.keys())
                print(f"  ✓ Calibration context ready for: {', '.join(agents_with_cal)}")
            else:
                print("  ⚪ No calibration context produced (holding period may not be complete).")
        except Exception as e:
            print(f"  ⚠️  Calibration generation failed ({e}) — proceeding without it.")
            calibration_context = {}
    else:
        print("\n  ⚪ No prior signals found — running without calibration context (cold start).")

    # ── Phase 3: run analysis with calibration injected ───────────────────────
    all_results: dict = {}
    for stock_code, corp_info in corp_infos.items():
        result = orchestrator.analyze_stock(
            stock_code, as_of_date, corp_info,
            calibration_context=calibration_context,
        )
        all_results[stock_code] = result

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

    # ── Establish the run directory for backtest output files ────────────
    date_tag = as_of_date.strftime("%Y-%m-%d")
    run_date = datetime.now().strftime("%Y-%m-%d")
    run_dir  = os.path.join(REPORTS_DIR, "backtest", run_date, date_tag)

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
    rebal_dir = os.path.join(run_dir, "rebalance")
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
        quarterly_data, sorted_dates = _load_signals_flow()
        _run_backtest_menu(orchestrator, quarterly_data, sorted_dates)
        return

    # ── Load signals ──────────────────────────────────────────────────────────
    if choice == "L":
        quarterly_data, sorted_dates = _load_signals_flow()

    # ── New analysis ──────────────────────────────────────────────────────────
    else:
        all_results, as_of_date = _new_analysis_flow(orchestrator)
        print(f"\n  {len(all_results)} stock(s) analysed.")
        # Wrap into quarterly_data format for unified backtest menu
        quarterly_data = {as_of_date.strftime("%Y-%m-%d"): all_results}
        sorted_dates   = [as_of_date.strftime("%Y-%m-%d")]

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

    _run_backtest_menu(orchestrator, quarterly_data, sorted_dates)


def _run_precomputed_rebalancing(quarterly_data: dict, sorted_dates: list) -> None:
    """
    Run a rebalancing backtest using pre-loaded quarterly signals.
    No LLM calls — portfolio weights are computed directly from saved debate_results.

    quarterly_data : {as_of_date_str: {stock_code: result_dict}}
    sorted_dates   : ascending list of as_of_date strings
    """
    from portfolio.portfolio_agent import construct_portfolio
    from backtest.runner import run_rebalanced_backtest
    from report.summary_renderer import build_pdf
    import anthropic as _ant
    from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

    PROFILES = ("risk-averse", "risk-neutral")

    print(f"\n{'='*60}")
    print(f"  PRE-COMPUTED QUARTERLY BACKTEST")
    print(f"  {len(sorted_dates)} quarter(s): {' → '.join(sorted_dates)}")
    print(f"{'='*60}")

    weight_schedule: dict[str, list] = {p: [] for p in PROFILES}
    quarterly_log   = []
    all_stock_codes: set  = set()
    company_names:   dict = {}

    # Build weight schedule from saved signals — one entry per quarter
    for q_num, date_str in enumerate(sorted_dates, 1):
        q_results = quarterly_data[date_str]
        q_date    = datetime.strptime(date_str, "%Y-%m-%d")

        stock_debate = {code: r["debate_results"] for code, r in q_results.items()}
        portfolios   = construct_portfolio(stock_debate)

        for code, r in q_results.items():
            all_stock_codes.add(code)
            company_names[code] = r["company_name"]

        for profile in PROFILES:
            weight_schedule[profile].append((q_date, dict(portfolios[profile]["weights"])))
            po      = portfolios[profile]
            n_held  = sum(1 for a in po["stock_allocations"].values() if a["weight"] > 0)
            print(f"  Q{q_num} {date_str} [{profile.upper():<14}] {n_held} stock(s) selected")

        quarterly_log.append({
            "quarter":    q_num,
            "start":      q_date,
            "end":        None,   # filled below
            "results":    q_results,
            "portfolios": portfolios,
        })

    # Fill quarter end dates
    for i in range(len(quarterly_log) - 1):
        quarterly_log[i]["end"] = quarterly_log[i + 1]["start"]

    # Ask backtest end date
    last_q_date = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
    while True:
        end_date = _ask_date(
            f"  Enter backtest end date (YYYY/MM/DD)"
            f" [must be after {sorted_dates[-1]}]: "
        )
        if end_date > last_q_date:
            quarterly_log[-1]["end"] = end_date
            break
        print(f"  End date must be after {sorted_dates[-1]}. Try again.")

    start_date  = datetime.strptime(sorted_dates[0], "%Y-%m-%d")
    stock_codes = sorted(all_stock_codes)

    # Guard: check if any profile has at least one positive weight across all quarters
    any_invested = any(
        v > 0
        for sched in weight_schedule.values()
        for _, w in sched
        for v in w.values()
    )
    if not any_invested:
        print("\n  ⚠️  All quarters produced SELL signals for all stocks — "
              "no equity positions to backtest. Aborting pre-computed rebalancing.")
        return

    print(f"\n  Running backtest: "
          f"{start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}...")
    backtest_results = run_rebalanced_backtest(
        weight_schedules=weight_schedule,
        start_date=start_date,
        end_date=end_date,
        all_stock_codes=stock_codes,
    )

    # Check that at least one risk profile engine was produced
    has_results = any(backtest_results.get(p) is not None
                      for p in ("risk-averse", "risk-neutral"))
    if not has_results:
        print("\n  ⚠️  No backtest results produced (all profiles had all-SELL signals). "
              "Nothing to save.")
        return

    # LLM narrative (best-effort)
    date_tag = start_date.strftime("%Y-%m-%d")
    q_summary = "\n".join(
        f"  Q{q['quarter']} ({q['start'].strftime('%Y-%m-%d')}): "
        + ", ".join(
            f"{code} → {q['portfolios']['risk-neutral']['stock_allocations'][code]['signal']}"
            for code in stock_codes if code in q["portfolios"]["risk-neutral"]["stock_allocations"]
        )
        for q in quarterly_log
    )
    try:
        _cl       = _ant.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp      = _cl.messages.create(
            model=CLAUDE_MODEL, max_tokens=500,
            messages=[{"role": "user", "content":
                f"Write a 4–5 sentence executive summary for a rebalanced Korean equity portfolio.\n"
                f"Stocks: {', '.join(f'{c} ({n})' for c, n in company_names.items())}\n"
                f"Quarterly signal history:\n{q_summary}\n"
                f"Cover: how signals evolved, which stocks were held vs rotated, "
                f"recommended posture, one key risk."}]
        )
        narrative = resp.content[0].text.strip()
    except Exception:
        narrative = (f"Pre-computed quarterly portfolio across {len(quarterly_log)} quarter(s). "
                     "LLM narrative unavailable.")

    # Save PDF + JSON
    run_date  = datetime.now().strftime("%Y-%m-%d")
    rebal_dir = os.path.join(REPORTS_DIR, "backtest", run_date, date_tag, "rebalance")
    os.makedirs(rebal_dir, exist_ok=True)
    pdf_path  = os.path.join(rebal_dir, f"Exec_Sum_Rebalanced_{date_tag}.pdf")

    build_pdf(
        pdf_path=pdf_path,
        company_names=company_names,
        portfolios=quarterly_log[-1]["portfolios"],
        narrative=narrative,
        as_of_date=end_date,
        backtest_results=backtest_results,
        quarterly_log=quarterly_log,
    )

    _save_rebalancing_json(
        start_date=start_date, end_date=end_date,
        stock_codes=stock_codes, company_names=company_names,
        weight_schedule=weight_schedule, quarterly_log=quarterly_log,
        save_dir=rebal_dir,
    )

    print(f"\n{'='*60}")
    print(f"  QUARTERLY BACKTEST COMPLETE")
    print(f"  {len(quarterly_log)} quarter(s) — no LLM re-runs")
    print(f"  PDF → {pdf_path}")
    print(f"{'='*60}\n")


def _run_backtest_menu(orchestrator, quarterly_data: dict, sorted_dates: list) -> None:
    """
    Choose and run a backtest mode.

    quarterly_data : {as_of_date_str: {stock_code: result}}
    sorted_dates   : ascending list of date strings
    """
    q1_date    = datetime.strptime(sorted_dates[0], "%Y-%m-%d")
    q1_results = quarterly_data[sorted_dates[0]]

    print("\n" + "─"*60)

    if len(sorted_dates) > 1:
        # Multiple quarters pre-loaded — offer dedicated mode
        print(f"  {len(sorted_dates)} quarter(s) of signals loaded"
              f" ({' / '.join(sorted_dates)})")
        print()
        print("  [P] Pre-computed quarterly backtest  — use all loaded quarters, no LLM")
        print("  [S] Standard backtest                — Q1 signals only, static portfolio")
        print("  [R] Quarterly rebalancing            — Q1 loaded, Q2+ re-run LLM")
        while True:
            choice = input("\n  Choice (P / S / R): ").strip().upper()
            if choice in ("P", "S", "R"):
                break
            print("  Please enter P, S, or R.")
        print("─"*60)

        if choice == "P":
            _run_precomputed_rebalancing(quarterly_data, sorted_dates)
        elif choice == "S":
            orchestrator.finalize(q1_results, q1_date)
        else:
            _run_rebalancing(orchestrator, q1_results, q1_date)

    else:
        # Single quarter — original two-option menu
        while True:
            rebalance = input(
                "  Would you like to rebalance quarterly? (Y/N): "
            ).strip().upper()
            if rebalance in ("Y", "N"):
                break
            print("  Please enter Y or N.")
        print("─"*60)

        if rebalance == "Y":
            _run_rebalancing(orchestrator, q1_results, q1_date)
        else:
            orchestrator.finalize(q1_results, q1_date)


if __name__ == "__main__":
    main()
