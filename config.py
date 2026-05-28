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

# ── Debug mode ────────────────────────────────────────────────────────────────
# Set DEBUG_MODE=true to skip all LLM calls and return stub responses.
# Use this to test the data pipeline end-to-end without spending tokens.
#
#   Normal run:  python3 main.py
#   Debug run:   DEBUG_MODE=true python3 main.py
#   Cheap run:   CLAUDE_MODEL=claude-haiku-4-5 python3 main.py
DEBUG_MODE = os.getenv("DEBUG_MODE", "false").lower() == "true"
