import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OPENAI_API_KEY    = os.getenv("OPENAI_API_KEY")
DART_API_KEY      = os.getenv("DART_API_KEY")
BOK_API_KEY       = os.getenv("BOK_API_KEY")       # Bank of Korea ECOS API (optional)

CLAUDE_MODEL      = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
OPENAI_MODEL      = "gpt-4o"
MAX_DEBATE_ROUNDS = 3

# ── Risk profiles ─────────────────────────────────────────────────────────────
# The full set of risk profiles the agents can run. A single analysis may run
# any non-empty subset of these (user-selectable at the entry points). Every
# downstream consumer (portfolio, backtest, report) derives the active profiles
# from the data it receives — never from this constant directly — so a one-profile
# run produces one analysis, one portfolio, one column of output.
ALL_PROFILES   = ("risk-averse", "risk-neutral")
PROFILE_LABELS = {"risk-averse": "Risk-Averse", "risk-neutral": "Risk-Neutral"}
PROFILE_TAGS   = {"risk-averse": "averse",       "risk-neutral": "neutral"}
PROFILE_SHORT  = {"risk-averse": "RA",           "risk-neutral": "RN"}


def profile_label(profile: str) -> str:
    """Human-facing label, e.g. 'risk-averse' → 'Risk-Averse'."""
    return PROFILE_LABELS.get(profile, profile.replace("risk-", "Risk-").title())


def profile_tag(profile: str) -> str:
    """Short filename tag, e.g. 'risk-averse' → 'averse'."""
    return PROFILE_TAGS.get(profile, profile.replace("risk-", ""))


def profile_short(profile: str) -> str:
    """Two-letter column tag, e.g. 'risk-averse' → 'RA'."""
    return PROFILE_SHORT.get(profile, profile.replace("risk-", "")[:2].upper())

# ── Debate agents ─────────────────────────────────────────────────────────────
# The five analysis agents that debate each stock. A run may use any ODD-sized
# subset (1/3/5) so the majority vote always resolves without a tie-break.
# Downstream code derives the active agents from each debate's results — never
# from this constant — so conviction weighting re-normalises over whatever set ran.
ALL_AGENTS   = ("FundamentalAgent", "SentimentAgent", "TechnicalAgent",
                "MarketAgent", "MacroAgent")
AGENT_LABELS = {
    "FundamentalAgent": "Fundamental",
    "SentimentAgent":   "Sentiment",
    "TechnicalAgent":   "Technical",
    "MarketAgent":      "Market",
    "MacroAgent":       "Macro",
}


def agent_label(agent: str) -> str:
    """Human-facing label, e.g. 'FundamentalAgent' → 'Fundamental'."""
    return AGENT_LABELS.get(agent, agent.replace("Agent", ""))

# ── Debug mode ────────────────────────────────────────────────────────────────
# Set DEBUG_MODE=true to skip all LLM calls and return stub responses.
# Use this to test the data pipeline end-to-end without spending tokens.
#
#   Normal run:  python3 main.py
#   Debug run:   DEBUG_MODE=true python3 main.py
#   Cheap run:   CLAUDE_MODEL=claude-haiku-4-5 python3 main.py
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
