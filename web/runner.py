"""
web/runner.py
=============
Web-adapted pipeline — same logic as main.py but uses
WebSession.ask() instead of input() for all user interactions.
Runs in a background thread per connected client.
"""

import glob
import json
import os
import re
from datetime import datetime

from tools.dart_tools import lookup_company
from orchestrator.orchestrator_agent import OrchestratorAgent
from portfolio.portfolio_agent import compute_conviction

REPORTS_DIR = "reports"


def _list_signal_files():
    # Signal JSONs: reports/signals/{ticker}_{name}/{as_of_date}/*.json
    return sorted(glob.glob(os.path.join(REPORTS_DIR, "signals", "*", "*", "*.json")))


def _list_rebalancing_files():
    # Rebalanced JSONs: reports/backtest/{run_date}/{as_of_date}/rebalance/Rebalanced_*.json
    return sorted(glob.glob(
        os.path.join(REPORTS_DIR, "backtest", "*", "*", "rebalance", "Rebalanced_*.json")
    ))


def run_web_session(session):
    """Entry point — called as a background task by app.py."""
    try:
        _run(session)
    except Exception as e:
        import traceback
        session.message(f"❌ Unexpected error: {e}", msg_type="error",
                        subtext=traceback.format_exc()[:300])
        session.done()


# ── Main flow ─────────────────────────────────────────────────────────────────

def _run(session):
    mode = session.ask(
        "How would you like to proceed?",
        input_type="buttons",
        options=[
            {"label": "📊 New Analysis",         "value": "N"},
            {"label": "📂 Load Saved Signals",    "value": "L"},
            {"label": "⚡ Load & Run Backtest",   "value": "B"},
        ],
    )

    orchestrator = OrchestratorAgent()

    # ── Load & Run Backtest shortcut ──────────────────────────────────────────
    if mode == "B":
        result = _load_signals_flow(session)
        if result is None:
            return
        quarterly_data, sorted_dates = result
        _post_analysis_flow(session, orchestrator, quarterly_data, sorted_dates)
        return

    # ── New analysis ──────────────────────────────────────────────────────────
    if mode == "N":
        all_results, as_of_date = _new_analysis_flow(session, orchestrator)
        quarterly_data = {as_of_date.strftime("%Y-%m-%d"): all_results}
        sorted_dates   = [as_of_date.strftime("%Y-%m-%d")]
    else:  # mode == "L"
        result = _load_signals_flow(session)
        if result is None:
            return
        quarterly_data, sorted_dates = result

    proceed = session.ask(
        "Analysis complete — what would you like to do next?",
        subtext="Signals are already saved and can be reloaded later via 'Load & Run Backtest'",
        input_type="buttons",
        options=[
            {"label": "📈 Run Backtest",  "value": "Y"},
            {"label": "💾 Save & Exit",   "value": "N"},
        ],
    )
    if proceed != "Y":
        session.message(
            "✅ Signals saved.",
            msg_type="success",
            subtext="Reload them any time with 'Load & Run Backtest'.",
        )
        session.done()
        return

    _post_analysis_flow(session, orchestrator, quarterly_data, sorted_dates)


# ── New analysis ──────────────────────────────────────────────────────────────

def _new_analysis_flow(session, orchestrator):
    # Date
    while True:
        raw = session.ask(
            "Enter the analysis date",
            subtext="Data prior to this date will be used  ·  Format: YYYY/MM/DD",
        )
        try:
            as_of_date = datetime.strptime(raw.strip(), "%Y/%m/%d")
            break
        except ValueError:
            session.message("⚠ Invalid format — please use YYYY/MM/DD", msg_type="warning")

    all_results = {}

    while True:
        n = len(all_results) + 1
        raw = session.ask(
            f"Enter Stock #{n} ticker",
            subtext="6-digit KRX code  ·  e.g. 005930 (Samsung), 214150 (클래시스)  ·  Leave blank to finish",
        )
        stock_code = raw.strip()

        if not stock_code:
            if not all_results:
                session.message("⚠ Enter at least one ticker.", msg_type="warning")
                continue
            break

        if stock_code in all_results:
            session.message(f"⚠ {stock_code} already in pool.", msg_type="warning")
            continue

        # Lookup
        session.message(f"🔍 Looking up {stock_code} on OpenDART…", msg_type="loading")
        try:
            corp_info = lookup_company(stock_code)
        except Exception as e:
            session.message(f"❌ Not found: {e}", msg_type="error")
            continue

        name = corp_info["corp_name"]
        session.message(
            f"✓ {name}  ({stock_code})",
            msg_type="success",
            subtext=f"DART confirmed  ·  corp_code: {corp_info.get('corp_code', '')}",
        )

        # Progress callback passed into the orchestrator
        def make_cb(s):
            def cb(event, *args):
                if event == "fetch":
                    s.progress(args[0])
                elif event == "debate_start":
                    s.debate_start(args[0], args[1])
                elif event == "agent_update":
                    agent, status, signal, rnd, prof = args
                    s.agent_update(agent, status, signal, rnd, prof)
            return cb

        result = orchestrator.analyze_stock(
            stock_code, as_of_date, corp_info,
            progress_cb=make_cb(session),
        )
        all_results[stock_code] = result

        # Result card
        debate_results = result["debate_results"]
        card_results = []
        for profile in ("risk-averse", "risk-neutral"):
            dr = debate_results[profile]
            conviction = compute_conviction(dr)
            card_results.append({
                "profile":    profile,
                "signal":     dr["final_signal"],
                "conviction": round(conviction, 3),
                "consensus":  dr["consensus_type"],
                "rounds":     dr["consensus_round"],
            })
        # Find the signals JSON saved by orchestrator (2 levels deep)
        date_tag = as_of_date.strftime("%Y-%m-%d")
        pattern  = os.path.join(REPORTS_DIR, "signals", f"{stock_code}_*", date_tag, f"{stock_code}_*{date_tag}.json")
        matches  = sorted(glob.glob(pattern))
        signal_file = matches[-1] if matches else ""

        session.stock_result(stock_code, name, card_results, signal_file=signal_file)

        # Add more?
        ans = session.ask(
            f"{len(all_results)} stock(s) in pool — add another?",
            input_type="buttons",
            options=[
                {"label": "➕ Add Stock",       "value": "Y"},
                {"label": "✓ Done — Proceed",   "value": "N"},
            ],
        )
        if ans != "Y":
            break

    return all_results, as_of_date


# ── Load saved signals ────────────────────────────────────────────────────────

def _load_signals_flow(session):
    files = _list_signal_files()
    if not files:
        session.message("❌ No saved signal files found in reports/.", msg_type="error")
        session.done()
        return None

    # ── Build run_date → [(file_index, meta), ...] map ────────────────────────
    def _parts(path):
        p = path.replace("\\", "/").split("/")
        # reports/signals/{ticker}_{name}/{as_of_date}/{file}.json
        return (p[2] if len(p) > 2 else "?",   # ticker folder
                p[3] if len(p) > 3 else "?")    # as_of_date

    metas = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                metas.append(json.load(f))
        except Exception:
            metas.append(None)

    # Build: ticker_folder → [(file_index, as_of_date, meta), ...]
    ticker_map: dict[str, list] = {}
    for i, (path, meta) in enumerate(zip(files, metas)):
        tkr, aod = _parts(path)
        ticker_map.setdefault(tkr, []).append((i, aod, meta))
    sorted_tickers = sorted(ticker_map.keys())

    # ── Step 1 + loop: pick companies and their dates ────────────────────────
    # The user picks one company at a time, selects dates, then decides to add more.
    local_indices = []   # accumulated (file_idx, aod, meta) tuples across all companies
    loaded_tickers: set = set()

    while True:
        # Build company options excluding already-loaded tickers
        company_opts = []
        for tkr in sorted_tickers:
            entries = ticker_map[tkr]
            m0      = entries[0][2]
            company = m0.get("company_name", tkr) if m0 else tkr
            n_dates = len(entries)
            already = "✓ loaded" if tkr in loaded_tickers else ""
            company_opts.append({
                "label": f"📁 {company}  ({n_dates} date{'s' if n_dates != 1 else ''})  {already}".strip(),
                "value": tkr,
            })

        n_loaded = len(loaded_tickers)
        prompt   = ("Select a company" if n_loaded == 0
                    else f"Add another company?  ({n_loaded} loaded so far)")
        opts_to_show = company_opts if len(company_opts) <= 4 else company_opts

        if len(company_opts) <= 4:
            chosen_tkr = session.ask(
                prompt,
                subtext="Pick a company then choose which quarter date(s) to load",
                input_type="buttons",
                options=company_opts + ([{"label": "✅ Done — proceed to backtest", "value": "__done__"}]
                                        if n_loaded > 0 else []),
            )
        else:
            raw = session.ask(
                prompt,
                subtext="Select one company, then click Confirm  ·  Select __done__ when finished",
                input_type="checkboxes",
                options=company_opts + ([{"label": "✅ Done — proceed to backtest", "value": "__done__"}]
                                        if n_loaded > 0 else []),
            )
            chosen_tkr = raw.split(",")[0].strip()

        if chosen_tkr == "__done__":
            break

        if chosen_tkr not in ticker_map:
            session.message("❌ Invalid selection.", msg_type="error")
            continue

        # ── Step 2: pick date(s) for the chosen company ───────────────────────
        entries   = ticker_map[chosen_tkr]
        m0        = entries[0][2]
        company   = m0.get("company_name", chosen_tkr) if m0 else chosen_tkr
        date_opts = []
        for i, (file_idx, aod, meta) in enumerate(entries):
            date_opts.append({"label": f"as_of: {aod}", "value": str(i)})

        while True:
            raw = session.ask(
                f"📁 {company} — select quarter date(s)",
                subtext="Select one date for standard backtest · multiple for quarterly backtest",
                input_type="checkboxes",
                options=date_opts,
            )
            try:
                chosen_local = [int(x.strip()) for x in raw.split(",") if x.strip()]
                if not chosen_local:
                    raise ValueError
                break
            except ValueError:
                session.message("⚠ Please select at least one date.", msg_type="warning")

        local_indices.extend([(entries[i][0], entries[i][1], entries[i][2])
                               for i in chosen_local if 0 <= i < len(entries)])
        loaded_tickers.add(chosen_tkr)

        # If only one company in the system, skip the "add more" loop
        if len(sorted_tickers) == 1:
            break

    # ── Load selected files grouped by as_of_date ────────────────────────────
    quarterly_data: dict[str, dict] = {}
    for file_idx, aod, meta in local_indices:
        if meta is None:
            continue
        stock_code = meta["stock_code"]
        date_str   = meta["as_of_date"]
        if date_str not in quarterly_data:
            quarterly_data[date_str] = {}
        if stock_code not in quarterly_data[date_str]:
            quarterly_data[date_str][stock_code] = {
                "company_name":   meta["company_name"],
                "corp_info":      meta["corp_info"],
                "debate_results": meta["debate_results"],
                "report_files":   meta["report_files"],
                "data":           {},
            }
            session.message(f"✓ Loaded {stock_code} — {meta['company_name']}  (as_of {date_str})",
                            msg_type="success")

    if not quarterly_data:
        session.message("❌ No valid signals loaded.", msg_type="error")
        session.done()
        return None

    sorted_dates = sorted(quarterly_data.keys())
    if len(sorted_dates) > 1:
        session.message(
            f"📅 {len(sorted_dates)} quarter(s) loaded: {' → '.join(sorted_dates)}",
            msg_type="info",
        )

    return quarterly_data, sorted_dates


# ── Post-analysis: backtest mode ──────────────────────────────────────────────

def _post_analysis_flow(session, orchestrator, quarterly_data: dict, sorted_dates: list):
    q1_date    = datetime.strptime(sorted_dates[0], "%Y-%m-%d")
    q1_results = quarterly_data[sorted_dates[0]]

    multi_quarter = len(sorted_dates) > 1

    options = []
    if multi_quarter:
        options.append({
            "label": f"⚡ Pre-computed Quarterly ({len(sorted_dates)} quarters, no LLM)",
            "value": "P",
        })
    options += [
        {"label": "📊 Standard Backtest",    "value": "S"},
        {"label": "🔄 Quarterly Rebalancing (LLM)", "value": "R"},
    ]

    subtext = (
        f"Pre-computed uses your {len(sorted_dates)} loaded quarters directly  ·  "
        "Rebalancing re-runs LLM for Q2+"
        if multi_quarter else
        "Standard = static portfolio  ·  Rebalancing = quarterly LLM re-analysis"
    )

    mode = session.ask(
        "Choose backtest mode",
        subtext=subtext,
        input_type="buttons",
        options=options,
    )

    if mode == "P":
        _precomputed_rebalancing_flow(session, quarterly_data, sorted_dates)
    elif mode == "S":
        _standard_backtest(session, orchestrator, q1_results, q1_date)
    elif mode == "R":
        _rebalancing_flow(session, orchestrator, q1_results, q1_date)
    else:
        session.message("❌ Unknown mode.", msg_type="error")
        session.done()


def _precomputed_rebalancing_flow(session, quarterly_data: dict, sorted_dates: list):
    """Run a rebalancing backtest from pre-loaded quarterly signals — no LLM calls."""
    from portfolio.portfolio_agent import construct_portfolio
    from backtest.runner import run_rebalanced_backtest

    PROFILES = ("risk-averse", "risk-neutral")

    session.message(
        f"⚡ Pre-computed quarterly backtest — {len(sorted_dates)} quarter(s)",
        msg_type="loading",
        subtext=" · ".join(sorted_dates),
    )

    weight_schedule: dict = {p: [] for p in PROFILES}
    quarterly_log   = []
    all_stock_codes: set  = set()
    company_names:   dict = {}

    try:
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

            quarterly_log.append({
                "quarter":    q_num,
                "start":      q_date,
                "end":        None,
                "results":    q_results,
                "portfolios": portfolios,
            })
            session.progress(f"Q{q_num} ({date_str}) portfolio computed from saved signals")

        # Fill end dates
        for i in range(len(quarterly_log) - 1):
            quarterly_log[i]["end"] = quarterly_log[i + 1]["start"]

    except Exception as e:
        session.message(f"❌ Error building weight schedule: {e}", msg_type="error")
        session.done()
        return

    # Ask end date
    last_q_date = datetime.strptime(sorted_dates[-1], "%Y-%m-%d")
    while True:
        raw = session.ask(
            "Enter backtest end date",
            subtext=f"Must be after {sorted_dates[-1]}  ·  Format: YYYY/MM/DD",
        )
        try:
            end_date = datetime.strptime(raw.strip(), "%Y/%m/%d")
            if end_date > last_q_date:
                quarterly_log[-1]["end"] = end_date
                break
            session.message(f"⚠ Must be after {sorted_dates[-1]}.", msg_type="warning")
        except ValueError:
            session.message("⚠ Invalid format — use YYYY/MM/DD.", msg_type="warning")

    start_date  = datetime.strptime(sorted_dates[0], "%Y-%m-%d")
    stock_codes = sorted(all_stock_codes)

    session.message(
        "📈 Running pre-computed quarterly backtest…",
        msg_type="loading",
        subtext=f"{start_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}",
    )

    try:
        run_rebalanced_backtest(
            weight_schedules=weight_schedule,
            start_date=start_date,
            end_date=end_date,
            all_stock_codes=stock_codes,
        )

        date_tag = start_date.strftime("%Y-%m-%d")
        run_date = __import__('datetime').datetime.now().strftime("%Y-%m-%d")
        pdf = os.path.join(REPORTS_DIR, "backtest", run_date, date_tag,
                           "rebalance", f"Exec_Sum_Rebalanced_{date_tag}.pdf")

        session.message("✅ Pre-computed quarterly backtest complete", msg_type="success",
                        subtext=pdf)
        session.done(pdf_path=pdf)
    except Exception as e:
        session.message(f"❌ Error: {e}", msg_type="error")
        session.done()


def _ask_end_date(session, as_of_date):
    while True:
        raw = session.ask(
            "Enter backtest end date",
            subtext=f"Must be after {as_of_date.strftime('%Y-%m-%d')}  ·  Format: YYYY/MM/DD",
        )
        try:
            end_date = datetime.strptime(raw.strip(), "%Y/%m/%d")
            if end_date > as_of_date:
                return end_date
            session.message(f"⚠ Must be after {as_of_date.strftime('%Y-%m-%d')}.", msg_type="warning")
        except ValueError:
            session.message("⚠ Invalid format — use YYYY/MM/DD.", msg_type="warning")


def _standard_backtest(session, orchestrator, all_results, as_of_date):
    end_date = _ask_end_date(session, as_of_date)
    session.message(
        f"📈 Running backtest…",
        msg_type="loading",
        subtext=f"{as_of_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}",
    )
    try:
        pdf = orchestrator.finalize(all_results, as_of_date,
                                    end_date_override=end_date,
                                    progress_cb=session.progress)
        session.message("✅ Complete — PDF generated", msg_type="success", subtext=pdf or "")
        session.done(pdf_path=pdf or "")
    except Exception as e:
        session.message(f"❌ Error: {e}", msg_type="error")
        session.done()


def _rebalancing_flow(session, orchestrator, all_results, as_of_date):
    end_date = _ask_end_date(session, as_of_date)
    ans = session.ask(
        "Enable intra-quarter event-triggered re-weighting?",
        input_type="buttons",
        options=[
            {"label": "✓ Yes — monitor price triggers", "value": "Y"},
            {"label": "✗ No — quarterly LLM only",      "value": "N"},
        ],
    )
    use_events = ans == "Y"
    session.message("🔄 Running quarterly rebalancing…", msg_type="loading",
                    subtext="Q1 uses pre-computed results — Q2+ will re-run agents")
    try:
        from rebalance.rebalance_engine import RebalanceEngine
        from backtest.runner import run_rebalanced_backtest

        stock_codes   = list(all_results.keys())
        corp_infos    = {code: r["corp_info"]    for code, r in all_results.items()}
        company_names = {code: r["company_name"] for code, r in all_results.items()}

        engine = RebalanceEngine(orchestrator)
        weight_schedule, quarterly_log = engine.run(
            stock_codes=stock_codes, corp_infos=corp_infos,
            start_date=as_of_date, end_date=end_date,
            use_event_triggers=use_events, initial_results=all_results,
        )
        run_rebalanced_backtest(
            weight_schedules=weight_schedule,
            start_date=as_of_date, end_date=end_date,
            all_stock_codes=stock_codes,
        )
        date_tag = as_of_date.strftime('%Y-%m-%d')
        run_date = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
        pdf = os.path.join(REPORTS_DIR, "backtest", run_date, date_tag,
                           "rebalance", f"Exec_Sum_Rebalanced_{date_tag}.pdf")
        session.message("✅ Rebalancing complete — PDF generated", msg_type="success", subtext=pdf)
        session.done(pdf_path=pdf)
    except Exception as e:
        session.message(f"❌ Error: {e}", msg_type="error")
        session.done()
