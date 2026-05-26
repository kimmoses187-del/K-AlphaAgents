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
    # Signal JSONs live 4 levels deep: reports/{run_date}/{as_of_date}/{ticker_name}/*.json
    return sorted(glob.glob(os.path.join(REPORTS_DIR, "*", "*", "*", "*.json")))


def _list_rebalancing_files():
    return sorted(glob.glob(os.path.join(REPORTS_DIR, "Rebalanced_*.json")))


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
        all_results, as_of_date = result
        _post_analysis_flow(session, orchestrator, all_results, as_of_date)
        return

    # ── New analysis ──────────────────────────────────────────────────────────
    if mode == "N":
        all_results, as_of_date = _new_analysis_flow(session, orchestrator)
    else:  # mode == "L"
        result = _load_signals_flow(session)
        if result is None:
            return
        all_results, as_of_date = result

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

    _post_analysis_flow(session, orchestrator, all_results, as_of_date)


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
        pattern  = os.path.join(REPORTS_DIR, "*", "*", f"{stock_code}_*", f"{stock_code}_*{date_tag}.json")
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

    options = []
    metas   = []
    for path in files:
        try:
            with open(path, encoding="utf-8") as f:
                meta = json.load(f)
            label = f"{meta['stock_code']} · {meta['company_name']} · {meta['as_of_date']}"
        except Exception:
            meta  = None
            label = os.path.basename(path)
        options.append({"label": label, "value": str(len(options))})
        metas.append(meta)

    # Use interactive checkbox picker — user selects files and clicks Confirm
    while True:
        raw = session.ask(
            f"{len(files)} saved signal file(s) found — select files to load",
            subtext="Click to select, then click Confirm",
            input_type="checkboxes",
            options=options,
        )
        try:
            indices = [int(x.strip()) for x in raw.split(",") if x.strip()]
            if not indices:
                raise ValueError
            break
        except ValueError:
            session.message("⚠ Please select at least one file.", msg_type="warning")

    all_results = {}
    as_of_date  = None
    for idx in indices:
        if not (0 <= idx < len(metas)) or metas[idx] is None:
            continue
        meta       = metas[idx]
        stock_code = meta["stock_code"]
        file_date  = datetime.strptime(meta["as_of_date"], "%Y-%m-%d")
        if as_of_date is None:
            as_of_date = file_date
        all_results[stock_code] = {
            "company_name":   meta["company_name"],
            "corp_info":      meta["corp_info"],
            "debate_results": meta["debate_results"],
            "report_files":   meta["report_files"],
            "data":           {},
        }
        session.message(f"✓ Loaded {stock_code} — {meta['company_name']}", msg_type="success")

    if not all_results:
        session.message("❌ No valid signals loaded.", msg_type="error")
        session.done()
        return None

    return all_results, as_of_date


# ── Post-analysis: backtest mode ──────────────────────────────────────────────

def _post_analysis_flow(session, orchestrator, all_results, as_of_date):
    rebal_files = _list_rebalancing_files()
    options = [
        {"label": "📊 Standard Backtest",      "value": "S"},
        {"label": "🔄 Quarterly Rebalancing",   "value": "R"},
    ]
    if rebal_files:
        options.append({
            "label": f"📂 Load Saved Rebalancing ({len(rebal_files)} found)",
            "value": "B",
        })

    mode = session.ask(
        "Choose backtest mode",
        subtext="Standard = static portfolio  ·  Rebalancing = quarterly LLM re-analysis",
        input_type="buttons",
        options=options,
    )

    if mode == "S":
        _standard_backtest(session, orchestrator, all_results, as_of_date)
    elif mode == "R":
        _rebalancing_flow(session, orchestrator, all_results, as_of_date)
    else:
        session.message("📂 Load saved rebalancing — use terminal for now.", msg_type="info")
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
        pdf = f"reports/Exec Sum_Rebalanced_{as_of_date.strftime('%Y-%m-%d')}.pdf"
        session.message("✅ Rebalancing complete — PDF generated", msg_type="success", subtext=pdf)
        session.done(pdf_path=pdf)
    except Exception as e:
        session.message(f"❌ Error: {e}", msg_type="error")
        session.done()
