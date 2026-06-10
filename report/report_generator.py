from datetime import datetime


def generate_report(debate_result: dict, corp_info: dict,
                    metrics: dict, ticker_str: str,
                    risk_profile: str = "risk-averse",
                    portfolio: dict | None = None,
                    as_of_date: datetime | None = None) -> str:
    """Produce a structured Markdown report from debate results."""

    company_name   = debate_result["company_name"]
    final_signal   = debate_result["final_signal"]
    consensus_type = debate_result["consensus_type"]
    consensus_rnd  = debate_result["consensus_round"]
    debate_log     = debate_result["debate_log"]

    signal_label = "BUY" if final_signal == "BUY" else "SELL"

    initial_results = debate_log[0]["results"]
    final_results   = debate_log[-1]["results"]

    lines = []

    # ── Header ────────────────────────────────────────────────────────────
    lines += [
        "# K-AlphaAgents — Korean Equity Analysis Report",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Company** | {company_name} |",
        f"| **Stock Code** | {corp_info.get('stock_code', 'N/A')} |",
        f"| **Ticker** | {ticker_str} |",
        f"| **CEO** | {corp_info.get('ceo_nm', 'N/A')} |",
        f"| **Analysis Date** | {datetime.now().strftime('%Y-%m-%d %H:%M')} |",
        f"| **Data As-Of** | {as_of_date.strftime('%Y-%m-%d') if as_of_date else 'N/A'} |",
        f"| **Risk Profile** | {risk_profile.title()} |",
        "",
    ]

    # ── Final Verdict ─────────────────────────────────────────────────────
    lines += [
        "---",
        f"## Final Recommendation: **{signal_label}**",
        "",
        f"- Consensus type: **{consensus_type.title()}**",
        f"- Reached after: **{consensus_rnd}** debate round(s)",
        "",
    ]

    # ── Agent Signal Summary Table ────────────────────────────────────────
    lines += [
        "---",
        "## Agent Signals Summary",
        "",
        "| Agent | Initial Signal | Final Signal | Changed? |",
        "|-------|:--------------:|:------------:|:--------:|",
    ]
    for init_r, fin_r in zip(initial_results, final_results):
        changed = "Yes" if init_r["signal"] != fin_r["signal"] else "No"
        lines.append(
            f"| {init_r['agent']} | {init_r['signal']} | {fin_r['signal']} | {changed} |"
        )
    lines.append("")

    # ── Key Valuation Metrics ─────────────────────────────────────────────
    if metrics:
        lines += [
            "---",
            "## Key Technical Metrics (3-Month Window)",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Current Price | {metrics.get('current_price', 'N/A'):,} KRW |",
            f"| Period Return | {metrics.get('period_return_pct', 'N/A')}% |",
            f"| Annualised Return | {metrics.get('annualized_return', 0) * 100:.2f}% |",
            f"| Annualised Volatility | {metrics.get('annualized_volatility', 0) * 100:.2f}% |",
            f"| Avg Daily Volume | {metrics.get('avg_daily_volume', 0):,.0f} shares |",
            f"| 3M High | {metrics.get('price_high', 'N/A'):,} KRW |",
            f"| 3M Low | {metrics.get('price_low', 'N/A'):,} KRW |",
            *(
                [f"| RSI (14-day) | {metrics.get('rsi'):.1f} |"]
                if "rsi" in metrics else []
            ),
            *(
                [f"| 20-day MA | {metrics.get('ma20', 'N/A'):,} KRW  ({metrics.get('pct_vs_ma20', 0):+.1f}%) |"]
                if "ma20" in metrics else []
            ),
            *(
                [f"| Alpha vs KOSPI | {metrics.get('alpha_vs_kospi', 0):+.2f}% |"]
                if "alpha_vs_kospi" in metrics else []
            ),
            *(
                [f"| Return QoQ Δ | {metrics.get('return_qoq_delta', 0):+.2f}% |"]
                if "return_qoq_delta" in metrics else []
            ),
            "",
        ]

    # ── Portfolio Allocation ──────────────────────────────────────────────
    if portfolio:
        action = "Position taken" if portfolio.get("position_taken") else "No position — stock not recommended"
        lines += [
            "---",
            "## Portfolio Allocation",
            "",
            f"| Field | Value |",
            f"|-------|-------|",
            f"| **Signal** | {portfolio.get('signal', 'N/A')} |",
            f"| **Convergence Score** | {portfolio.get('conviction', 'N/A')} |",
            f"| **Portfolio Weight** | {portfolio.get('weight', 0)*100:.1f}% |",
            f"| **Action** | {action} |",
            "",
        ]

    # ── Detailed Agent Analyses (Initial Round) ───────────────────────────
    lines += ["---", "## Detailed Agent Analyses (Initial Round)", ""]
    for r in initial_results:
        lines += [
            f"### {r['agent']}  —  Signal: **{r['signal']}**",
            "",
            r["analysis"],
            "",
        ]

    # ── Debate Log ────────────────────────────────────────────────────────
    if len(debate_log) > 1:
        lines += ["---", "## Debate Log", ""]
        for entry in debate_log[1:]:
            lines += [f"### {entry['label']}", ""]
            for r in entry["results"]:
                lines += [
                    f"**{r['agent']}**  →  {r['signal']}",
                    "",
                    r["analysis"],
                    "",
                ]

    # ── Footer ────────────────────────────────────────────────────────────
    lines += [
        "---",
        "*Generated by K-AlphaAgents — LLM Multi-Agent Equity Research System*  ",
        "*Inspired by: Zhao et al. (2025), BlackRock K-AlphaAgents*",
        "",
        "> **Disclaimer:** This report is generated by AI agents for research purposes only.",
        "> It does not constitute financial advice. Always conduct your own due diligence.",
    ]

    return "\n".join(lines)
