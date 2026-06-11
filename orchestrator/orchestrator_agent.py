import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Optional

import anthropic

from config import (ANTHROPIC_API_KEY, CLAUDE_MODEL, ALL_PROFILES,
                    profile_tag, profile_label)
from tools.dart_tools import fetch_and_format_reports
from tools.dart_report_planner import plan_reports, build_coverage_note, describe_plan
from tools.dart_document_tools import fetch_document_narrative          # Gap 1
from tools.valuation_tools import build_valuation_context               # Gap 2
from tools.sentiment_tools import fetch_sentiment_data
from tools.pykrx_tools import (fetch_ohlcv, fetch_index_ohlcv,
                                KOSPI_INDEX, KOSDAQ_INDEX)
from tools.metrics_tools import calculate_price_metrics, format_metrics_for_llm
from tools.market_tools import (get_company_sector_info, get_peer_comparison,
                                format_market_data_for_llm)
from tools.macro_tools import fetch_macro_indicators, format_macro_data_for_llm
from tools.naver_tools import fetch_analyst_consensus                    # Gap 8
from debate.debate_manager import DebateManager
from portfolio.portfolio_agent import construct_portfolio, compute_conviction
from report.report_generator import generate_report
from report.summary_renderer import build_pdf
from report.exporters import export_portfolio_xlsx, export_reports_docx  # Gap 7
from backtest.runner import run_backtest

REPORTS_DIR = "reports"
PROFILES    = ALL_PROFILES   # default set; a run may use a subset (see analyze_stock)

_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _safe_filename(name: str) -> str:
    """
    Make a string safe for use in filenames.
    - Strips Korean/Japanese corporate suffixes like (주), (주식회사), (유), Inc., Ltd.
    - Removes characters invalid in filenames.
    - Collapses whitespace to underscores.
    """
    # Remove common corporate suffixes
    name = re.sub(r"\(주\)|\(주식회사\)|\(유\)", "", name)
    name = re.sub(r"(?i)\b(inc|ltd|co|corp|llc)\.?\b", "", name)
    # Keep Korean/CJK chars, word chars, spaces, hyphens
    name = re.sub(r"[^\w가-힣぀-ヿ一-鿿\s\-]", "", name)
    name = re.sub(r"[\s_]+", "_", name.strip()).strip("_")
    return name or "unknown"


class OrchestratorAgent:
    """
    Director of the K-AlphaAgents pipeline.

    Two-phase API
    -------------
    1. analyze_stock(stock_code, as_of_date, corp_info)
         Fetch data + run 5-agent debate for one stock.
         Saves a per-stock markdown report.
         Returns the analysis result dict (stored by caller).

    2. finalize(all_results, as_of_date)
         Called once after all stocks are analyzed.
         Constructs the multi-stock portfolio, auto-runs the backtest,
         and generates the executive summary as a PDF.
    """

    # ── Phase 1: single-stock analysis ───────────────────────────────────────

    def analyze_stock(self, stock_code: str, as_of_date: datetime,
                      corp_info: dict, stage: str = "initial",
                      progress_cb=None, output_dir: Optional[str] = None,
                      calibration_context: dict = None,
                      profiles=None) -> dict:
        """
        Fetch data and run the 5-agent debate for one stock.
        Saves per-profile markdown reports.
        Returns a result dict to be stored by the caller.

        progress_cb(event, *args) — optional callback for web UI progress.
        output_dir : if provided, save files here instead of the default path.
                     Used by RebalanceEngine to place Q2+ reports under
                     backtest/rebalance/Q{n}/{ticker}_{name}/.
        profiles   : which risk profiles to run (subset of ALL_PROFILES).
                     Defaults to all profiles; a single-element tuple yields a
                     single-profile analysis.
        """
        profiles     = tuple(profiles) if profiles else ALL_PROFILES
        company_name = corp_info["corp_name"]

        print(f"\n{'─'*60}")
        print(f"  Analyzing {company_name} ({stock_code})")
        print(f"{'─'*60}")

        if progress_cb:
            progress_cb("debate_start", stock_code, company_name)

        data           = self._fetch_data(stock_code, as_of_date, corp_info,
                                          stage=stage, progress_cb=progress_cb)
        debate_results = self._run_debates(company_name, data,
                                           progress_cb=progress_cb,
                                           calibration_context=calibration_context,
                                           profiles=profiles)

        # ── Output paths ──────────────────────────────────────────────────────
        # Structure: reports/signals/{ticker}_{name}/{as_of_date}/
        #            └── averse/{base}_averse.md
        #            └── neutral/{base}_neutral.md
        #            └── {base}.json  (combined, both profiles)
        safe_name = _safe_filename(company_name)
        date_tag  = as_of_date.strftime("%Y-%m-%d")
        base_name = f"{stock_code}_{safe_name}_{date_tag}"

        if output_dir is not None:
            stock_dir = output_dir
        else:
            stock_dir = os.path.join(
                REPORTS_DIR, "signals", f"{stock_code}_{safe_name}", date_tag
            )
        os.makedirs(stock_dir, exist_ok=True)

        # Per-stock markdown reports — each profile in its own subfolder
        report_files = {}
        for profile in profiles:
            report_md = generate_report(
                debate_results[profile], corp_info,
                data["metrics"], data["ticker_str"], profile,
                portfolio=None,
                as_of_date=as_of_date,
            )
            tag         = profile_tag(profile)
            profile_dir = os.path.join(stock_dir, tag)
            os.makedirs(profile_dir, exist_ok=True)
            filename    = os.path.join(profile_dir, f"{base_name}_{tag}.md")
            with open(filename, "w", encoding="utf-8") as f:
                f.write(report_md)
            report_files[profile] = filename

        # Per-stock signal printout
        print(f"\n  ── Signals for {company_name} ({stock_code}) ──")
        for profile in profiles:
            dr         = debate_results[profile]
            conviction = compute_conviction(dr)
            print(f"  [{profile.upper():<14}] {dr['final_signal']:<4}  "
                  f"convergence={conviction:.3f}  "
                  f"({dr['consensus_type']}, {dr['consensus_round']} round(s))")
        for profile, path in report_files.items():
            print(f"  Report [{profile}]: {path}")

        # Combined JSON at ticker folder level (contains both profiles)
        signals_path = os.path.join(stock_dir, f"{base_name}.json")
        with open(signals_path, "w", encoding="utf-8") as f:
            json.dump({
                "stock_code":     stock_code,
                "company_name":   company_name,
                "as_of_date":     as_of_date.strftime("%Y-%m-%d"),
                "corp_info":      corp_info,
                "debate_results": debate_results,
                "report_files":   report_files,
            }, f, ensure_ascii=False, indent=2)
        print(f"  Signals saved: {signals_path}")

        return {
            "corp_info":      corp_info,
            "company_name":   company_name,
            "debate_results": debate_results,   # {"risk-averse": ..., "risk-neutral": ...}
            "data":           data,
            "report_files":   report_files,
        }

    # ── Phase 2: portfolio + backtest + PDF ───────────────────────────────────

    def finalize(self, all_results: dict, as_of_date: datetime,
                 end_date_override: datetime = None,
                 progress_cb=None) -> None:
        """
        Construct the multi-stock portfolio, auto-run the backtest,
        and produce a PDF executive summary.

        Parameters
        ----------
        all_results       : {stock_code: analyze_stock() return dict}
        as_of_date        : shared analysis date (= backtest start date)
        end_date_override : if provided, skip the input() prompt for end date
        progress_cb       : optional callable for web UI progress updates
        """
        company_names = {code: r["company_name"] for code, r in all_results.items()}

        # Build {stock_code: {profile: debate_result}} for portfolio agent
        stock_debate_results = {
            code: result["debate_results"]
            for code, result in all_results.items()
        }

        # ── Multi-stock portfolio construction ────────────────────────────
        portfolios = construct_portfolio(stock_debate_results)
        profiles   = list(portfolios.keys())   # the profiles actually analysed

        print(f"\n{'='*60}")
        print(f"  PORTFOLIO SUMMARY")
        print(f"{'='*60}")
        for profile in profiles:
            po = portfolios[profile]
            n_buy = sum(1 for a in po["stock_allocations"].values() if a["weight"] > 0)
            print(f"\n  [{profile.upper()}]  {n_buy} stock(s) selected")
            for code, alloc in po["stock_allocations"].items():
                name   = company_names[code]
                status = f"weight={alloc['weight']*100:.1f}%" if alloc["weight"] > 0 \
                         else "excluded (SELL)"
                print(f"    {code} ({name:<15}): {alloc['signal']:<4}  "
                      f"convergence={alloc['conviction']:.3f}  {status}")

        # ── Skip backtest if no equity positions in any analysed profile ──
        any_equity = any(portfolios[p]["position_taken"] for p in profiles)

        date_tag  = as_of_date.strftime("%Y-%m-%d")
        stock_tag = "_".join(all_results.keys())
        run_date  = datetime.now().strftime("%Y-%m-%d")
        bh_dir    = os.path.join(REPORTS_DIR, "backtest", run_date, date_tag, "buy_and_hold")
        os.makedirs(bh_dir, exist_ok=True)
        pdf_path  = os.path.join(bh_dir, f"Exec_Sum_{date_tag}.pdf")

        if not any_equity:
            scope = "either profile" if len(profiles) > 1 else "the selected profile"
            print(f"\n  No stocks were recommended for purchase by {scope}.")
            print("  Backtesting skipped — capital fully preserved in bond allocation.")
            backtest_results = None
        else:
            # ── Backtest end date — from override (web) or input() (terminal) ─
            if end_date_override is not None:
                end_date = end_date_override
            else:
                print(f"\n  Automatically starting backtest...")
                while True:
                    raw = input(
                        f"  Enter backtest end date (YYYY/MM/DD)"
                        f"  [must be after {as_of_date.strftime('%Y-%m-%d')}]: "
                    ).strip()
                    try:
                        end_date = datetime.strptime(raw, "%Y/%m/%d")
                    except ValueError:
                        print("  Invalid format. Please use YYYY/MM/DD.")
                        continue
                    if end_date <= as_of_date:
                        print(f"  End date must be after the analysis date "
                              f"({as_of_date.strftime('%Y-%m-%d')}). Try again.")
                        continue
                    break

            print(f"\n  Running backtest: "
                  f"{as_of_date.strftime('%Y-%m-%d')} → {end_date.strftime('%Y-%m-%d')}")

            backtest_results = run_backtest(
                portfolios=portfolios,
                as_of_date=as_of_date,
                end_date=end_date,
                company_name=", ".join(company_names.values()),
                stock_code=stock_tag,
                all_stock_codes=list(all_results.keys()),
            )

        # ── Generate PDF ──────────────────────────────────────────────────
        print("\n  Generating executive summary PDF...")
        narrative = self._llm_narrative(company_names, portfolios, stock_debate_results)

        # Gap 11: find the most recent calibration charts dir to embed in PDF
        from calibration.pipeline import get_existing_signal_dates
        import glob as _glob
        _cal_charts_dir = None
        try:
            _existing_dates = get_existing_signal_dates(
                list(all_results.keys()), REPORTS_DIR
            )
            if _existing_dates:
                _latest_cal = _existing_dates[-1]
                _candidate  = os.path.join(REPORTS_DIR, "calibration", _latest_cal)
                if os.path.isfile(os.path.join(_candidate, "agent_accuracy.png")):
                    _cal_charts_dir = _candidate
        except Exception:
            pass

        build_pdf(
            pdf_path=pdf_path,
            company_names=company_names,
            portfolios=portfolios,
            narrative=narrative,
            as_of_date=as_of_date,
            backtest_results=backtest_results,
            calibration_charts_dir=_cal_charts_dir,
        )

        # ── Gap 7: Excel + Word export ────────────────────────────────────
        report_md_paths = {
            code: result["report_files"]
            for code, result in all_results.items()
        }
        xlsx_path = export_portfolio_xlsx(
            portfolios       = portfolios,
            backtest_results = backtest_results,
            company_names    = company_names,
            as_of_date       = as_of_date,
            output_dir       = bh_dir,
        )
        docx_path = export_reports_docx(
            report_md_paths = report_md_paths,
            company_names   = company_names,
            as_of_date      = as_of_date,
            output_dir      = bh_dir,
        )

        # ── Final console summary ─────────────────────────────────────────
        print(f"\n{'='*60}")
        print(f"  OUTPUT FILES")
        print(f"{'='*60}")
        for code, result in all_results.items():
            for profile, path in result["report_files"].items():
                print(f"  {path}")
        print(f"  PDF  → {pdf_path}")
        if xlsx_path:
            print(f"  XLSX → {xlsx_path}")
        if docx_path:
            print(f"  DOCX → {docx_path}")
        print(f"{'='*60}\n")
        return pdf_path

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch_data(self, stock_code: str, as_of_date: datetime,
                    corp_info: dict, stage: str = "initial",
                    progress_cb=None) -> dict:
        def _log(msg):
            print(msg)
            if progress_cb:
                progress_cb("fetch", msg.strip())

        _log("\n  [1/2] Fetching data...")
        company_name = corp_info["corp_name"]

        # ── DART: dynamic report planning ─────────────────────────────────
        reports_plan  = plan_reports(as_of_date, stage=stage)
        cov_note      = build_coverage_note(reports_plan, as_of_date)
        _log(f"    {describe_plan(reports_plan, as_of_date, stage)}")
        fundamental_data = fetch_and_format_reports(corp_info, reports_plan, cov_note)

        # Gap 1: Append DART full-document narratives (MD&A, risk factors, etc.)
        corp_code_for_docs = corp_info.get("corp_code", "")
        if corp_code_for_docs:
            _log("    dart-docs: fetching full document narratives...")
            doc_narrative = fetch_document_narrative(
                corp_code   = corp_code_for_docs,
                reports_plan = reports_plan,
                max_tokens  = 8_000,
            )
            if doc_narrative:
                fundamental_data = fundamental_data + "\n\n" + doc_narrative
                _log(f"    dart-docs: {len(doc_narrative)//4:,} tokens of narrative added")

        # ── pykrx: price history (current + previous quarter + benchmarks) ─
        _log("    pykrx: price history (current + prev quarter + KOSPI/KOSDAQ)...")
        price_history  = fetch_ohlcv(stock_code, as_of_date, months=3, offset_months=0)
        prev_quarter   = fetch_ohlcv(stock_code, as_of_date, months=3, offset_months=3)
        kospi_history  = fetch_index_ohlcv(KOSPI_INDEX,  as_of_date, months=3)
        kosdaq_history = fetch_index_ohlcv(KOSDAQ_INDEX, as_of_date, months=3)
        metrics = calculate_price_metrics(
            price_history,
            prev_quarter   = prev_quarter   if not prev_quarter.empty   else None,
            kospi_history  = kospi_history  if not kospi_history.empty  else None,
            kosdaq_history = kosdaq_history if not kosdaq_history.empty else None,
        )
        technical_data = format_metrics_for_llm(metrics, stock_code)

        # ── yfinance ticker string for optional .info ratio enrichment ──────
        # Market suffix (.KS/.KQ) comes from DART corp_cls ("Y"=KOSPI, "K"=KOSDAQ),
        # already in hand — no per-analysis network probe needed.
        _suffix = {"Y": ".KS", "K": ".KQ"}.get(corp_info.get("corp_cls", ""), ".KS")
        ticker_str = f"{stock_code}{_suffix}"

        # ── Sentiment: DART disclosures + pykrx investor flow + short selling ─
        corp_code = corp_info.get("corp_code", "")
        _log("    sentiment: DART disclosures + investor flow + short selling...")
        sentiment_data = fetch_sentiment_data(
            corp_code=corp_code,
            stock_code=stock_code,
            company_name=company_name,
            as_of_date=as_of_date,
            months=3,
        )

        # ── Market: sector from DART + peer returns via pykrx ────────────────
        _log("    sector / peers / analyst consensus / macro...")

        # Compute benchmark returns from already-fetched pykrx history (no extra call)
        def _period_ret(hist):
            if hist is None or hist.empty:
                return None
            c = hist["Close"].dropna()
            return round((float(c.iloc[-1]) / float(c.iloc[0]) - 1) * 100, 2) if len(c) >= 2 else None

        kospi_return  = _period_ret(kospi_history)
        kosdaq_return = _period_ret(kosdaq_history)

        # Sector detection: DART corp_info (primary) + yfinance ratios (optional)
        sector_info = get_company_sector_info(corp_info, ticker_str)

        # Gap 4: dynamic peers — pass induty_code + exchange for pykrx sector lookup
        peers = get_peer_comparison(
            stock_code   = stock_code,
            sector       = sector_info.get("sector", ""),
            as_of_date   = as_of_date,
            induty_code  = sector_info.get("induty_code", ""),
            exchange     = sector_info.get("exchange", "KOSPI"),
        )

        market_data = format_market_data_for_llm(
            sector_info, kospi_return, kosdaq_return, peers, company_name
        )

        # Gap 8: Naver Finance analyst consensus appended to market_data
        analyst_block = fetch_analyst_consensus(stock_code)
        if analyst_block:
            market_data = market_data + "\n\n" + analyst_block

        macro_indicators = fetch_macro_indicators(as_of_date)
        macro_data       = format_macro_data_for_llm(macro_indicators,
                                                      sector_info.get("sector", "Unknown"))

        # Gap 2: Valuation context (DCF + comps) appended to fundamental_data
        # Re-use the already-fetched FS data from DART (no extra API calls)
        try:
            from tools.dart_tools import fetch_financial_statements
            corp_code_v = corp_info.get("corp_code", "")
            if corp_code_v:
                # Fetch up to 3 annual report year data for DCF trend
                _current_year  = as_of_date.year - (0 if as_of_date.month > 3 else 1)
                _fs_years_raw  = []
                for yr_offset in range(3):
                    yr = _current_year - yr_offset
                    fs = fetch_financial_statements(corp_code_v, yr, "11011")
                    if fs.get("status") == "000" and fs.get("list"):
                        _fs_years_raw.append(fs)
                # Reverse to oldest → newest
                _fs_years_raw.reverse()

                if _fs_years_raw:
                    val_block = build_valuation_context(
                        fs_years     = _fs_years_raw,
                        peers        = peers,
                        ticker_str   = ticker_str,
                        company_name = company_name,
                    )
                    if val_block:
                        fundamental_data = fundamental_data + "\n\n" + val_block
        except Exception as _ve:
            _log(f"    [valuation] skipped: {_ve}")

        n_days = len(price_history) if not price_history.empty else 0
        print(f"    {stock_code} | {n_days} days | "
              f"{len(peers)} peers | {len(macro_indicators)} macro indicators")

        return {
            "fundamental_data": fundamental_data,
            "sentiment_data":   sentiment_data,
            "technical_data":   technical_data,
            "market_data":      market_data,
            "macro_data":       macro_data,
            "metrics":          metrics,
            "ticker_str":       ticker_str,
        }

    def _run_debates(self, company_name: str, data: dict,
                     progress_cb=None, calibration_context: dict = None,
                     profiles=None) -> dict:
        from debate.terminal_display import DebateGrid

        profiles = tuple(profiles) if profiles else ALL_PROFILES
        plural   = "profiles in parallel" if len(profiles) > 1 else "profile"
        print(f"\n  [2/2] Running debates ({len(profiles)} {plural})...")

        # Build the shared in-place grid (terminal only; skipped if web callback active)
        grid = None
        if not progress_cb:
            grid = DebateGrid(profiles)
            grid.init()

        def _debate(profile: str) -> tuple:
            manager = DebateManager(risk_profile=profile)
            result  = manager.run(
                company_name=company_name,
                fundamental_data=data["fundamental_data"],
                sentiment_data=data["sentiment_data"],
                technical_data=data["technical_data"],
                market_data=data["market_data"],
                macro_data=data["macro_data"],
                progress_cb=progress_cb,
                display=grid,
                calibration_context=calibration_context,
            )
            return profile, result

        results = {}
        try:
            with ThreadPoolExecutor(max_workers=max(len(profiles), 1)) as pool:
                futures = {pool.submit(_debate, p): p for p in profiles}
                for future in as_completed(futures):
                    profile, result = future.result()
                    results[profile] = result
        finally:
            if grid is not None:
                grid.close()
        return results

    def _llm_narrative(self, company_names: dict, portfolios: dict,
                       stock_debate_results: dict) -> str:
        profiles  = list(portfolios.keys())
        multi     = len(profiles) > 1

        def _n_buy(po):
            return sum(1 for a in po["stock_allocations"].values() if a["weight"] > 0)

        stock_lines = []
        for code, name in company_names.items():
            for profile in profiles:
                dr  = stock_debate_results[code][profile]
                po  = portfolios[profile]["stock_allocations"][code]
                stock_lines.append(
                    f"  {code} ({name}) [{profile}]: "
                    f"{dr['final_signal']} | convergence={po['conviction']:.3f} | "
                    f"weight={po['weight']*100:.1f}% | "
                    f"{dr['consensus_type']} after {dr['consensus_round']} round(s)"
                )

        portfolio_lines = "\n".join(
            f"{profile_label(p)} portfolio: {_n_buy(portfolios[p])} BUY stock(s) selected — "
            f"position taken: {'Yes' if portfolios[p]['position_taken'] else 'No'}"
            for p in profiles
        )

        if multi:
            instructions = (
                "Write a 4–5 sentence professional cross-profile synthesis in plain prose "
                "(no bullet points, no markdown). Cover: (1) which stocks have strong / weak "
                "signals and why, (2) where the profiles agree or diverge, (3) the "
                "convergence-driven weight differences, (4) the recommended action for each "
                "investor type, (5) one key risk to monitor across the pool."
            )
        else:
            instructions = (
                f"Write a 4–5 sentence professional summary in plain prose (no bullet points, "
                f"no markdown) for a {profile_label(profiles[0])} investor. Cover: (1) which "
                "stocks have strong / weak signals and why, (2) how convergence drove the "
                "portfolio weights, (3) the recommended action, (4) one key risk to monitor "
                "across the pool."
            )

        prompt = f"""You are writing a concise executive summary for a professional multi-stock equity research report.

Stocks analysed: {', '.join(f"{c} ({n})" for c, n in company_names.items())}

Per-stock results:
{chr(10).join(stock_lines)}

{portfolio_lines}

{instructions}
"""
        try:
            resp = _claude.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.content[0].text.strip()
        except Exception:
            summary = " — ".join(
                f"{profile_label(p)}: {_n_buy(portfolios[p])} BUY stock(s)" for p in profiles
            )
            return f"{summary}. LLM narrative unavailable — check API key."
