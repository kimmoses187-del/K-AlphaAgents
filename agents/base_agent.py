import time
import anthropic
import openai
from config import ANTHROPIC_API_KEY, OPENAI_API_KEY, CLAUDE_MODEL, OPENAI_MODEL, DEBUG_MODE

_RETRIABLE = (anthropic.OverloadedError, anthropic.InternalServerError,
              anthropic.APIConnectionError, anthropic.APITimeoutError)
_RETRY_DELAYS = (5, 15, 30)   # seconds between attempts 1→2, 2→3, 3→4


def _safe_print(msg: str) -> None:
    """Print through the DebateGrid lock when active, so cursor math stays correct."""
    try:
        from debate.terminal_display import _active_grid
        if _active_grid is not None:
            _active_grid.print_message(msg)
            return
    except ImportError:
        pass
    print(msg)


def _claude_with_retry(fn):
    """Call fn() (a zero-arg lambda that returns a Claude response), retrying on transient errors."""
    last_err = None
    for attempt, delay in enumerate((_RETRY_DELAYS[0], _RETRY_DELAYS[1], _RETRY_DELAYS[2], None)):
        try:
            return fn()
        except _RETRIABLE as e:
            last_err = e
            if delay is None:
                break
            _safe_print(f"  [Claude] Transient error ({type(e).__name__}), retrying in {delay}s "
                        f"(attempt {attempt + 1}/{len(_RETRY_DELAYS)})...")
            time.sleep(delay)
        except Exception:
            raise   # non-retriable — bubble up immediately
    raise last_err

# ── Shared debate instructions ────────────────────────────────────────────────
# Appended to every agent's system prompt so they are cached along with it.
# Kept as module-level constants for reference; agents no longer inject them
# into user messages.

STEELMAN_INSTRUCTION = """
Before stating your final recommendation, steelman the opposing view:
- Write 2-3 sentences making the strongest possible case for the OPPOSITE signal
- Then explain specifically why your conclusion still holds despite that argument
- This must reflect genuine engagement, not a token dismissal
"""

CHALLENGE_INSTRUCTION = """
When reviewing peer analyses, actively challenge — do not simply acknowledge:
- Identify specific claims in peer analyses that conflict with your data or reasoning
- Explain precisely why those claims are wrong, overstated, or missing important context
- If a peer argument genuinely changes your view, name the exact point that convinced you
- Passive agreement without evidence ("I agree with the fundamental agent") is not acceptable
"""

_DEBATE_APPENDIX = f"""
---
## Debate Conduct

**Initial analysis (Round 0):**
{STEELMAN_INSTRUCTION.strip()}

**Debate rounds (Round 1+):**
{CHALLENGE_INSTRUCTION.strip()}
"""

_claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
_openai = openai.OpenAI(api_key=OPENAI_API_KEY)


class BaseAgent:
    def __init__(self, name: str, system_prompt: str):
        self.name = name
        # Append shared debate instructions so the full system prompt —
        # agent persona + steelman + challenge — is cached in one block.
        self.system_prompt = system_prompt + _DEBATE_APPENDIX

    def call_llm(self, user_message: str, max_tokens: int = 2048) -> str:
        """Call Claude with a cached system prompt; fall back to OpenAI if Claude fails."""
        if DEBUG_MODE:
            return f"[DEBUG STUB — {self.name}]\nNo LLM call made.\nRECOMMENDATION: BUY"
        try:
            resp = _claude_with_retry(lambda: _claude.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=[{
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": user_message}],
            ))
            return resp.content[0].text
        except Exception as claude_err:
            _safe_print(f"  [{self.name}] Claude failed ({type(claude_err).__name__}), falling back to OpenAI...")
            try:
                resp = _openai.chat.completions.create(
                    model=OPENAI_MODEL,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": user_message},
                    ],
                )
                return resp.choices[0].message.content
            except Exception as oai_err:
                raise RuntimeError(
                    f"Both LLMs failed for [{self.name}].\n"
                    f"  Claude: {claude_err}\n  OpenAI: {oai_err}"
                )

    def call_llm_with_cache(self, cached_data: str, dynamic_prompt: str,
                            max_tokens: int = 2048) -> str:
        """
        Tier 2 caching: send the user message as two content blocks.

          Block 1 — cached_data    : the agent's data blob (identical every round) → cached
          Block 2 — dynamic_prompt : task instruction + peer analyses (changes)    → not cached

        The data block is placed first so it forms a stable prefix that Anthropic
        can recognise and serve from cache on Rounds 1-3 after Round 0 writes it.
        Falls back to OpenAI with a combined single message (no caching).
        """
        if DEBUG_MODE:
            return f"[DEBUG STUB — {self.name}]\nNo LLM call made.\nRECOMMENDATION: BUY"
        try:
            resp = _claude_with_retry(lambda: _claude.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens,
                system=[{
                    "type": "text",
                    "text": self.system_prompt,
                    "cache_control": {"type": "ephemeral"},
                }],
                messages=[{"role": "user", "content": [
                    {
                        "type": "text",
                        "text": cached_data,
                        "cache_control": {"type": "ephemeral"},  # ← data cached here
                    },
                    {
                        "type": "text",
                        "text": dynamic_prompt,                   # ← instructions, not cached
                    },
                ]}],
            ))
            return resp.content[0].text
        except Exception as claude_err:
            _safe_print(f"  [{self.name}] Claude failed ({type(claude_err).__name__}), falling back to OpenAI...")
            try:
                resp = _openai.chat.completions.create(
                    model=OPENAI_MODEL,
                    max_tokens=max_tokens,
                    messages=[
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": cached_data + "\n\n" + dynamic_prompt},
                    ],
                )
                return resp.choices[0].message.content
            except Exception as oai_err:
                raise RuntimeError(
                    f"Both LLMs failed for [{self.name}].\n"
                    f"  Claude: {claude_err}\n  OpenAI: {oai_err}"
                )

    def extract_signal(self, text: str, risk_profile: str = "risk-averse") -> str:
        """Extract BUY or SELL from the LLM response.

        Looks specifically for the RECOMMENDATION line the agents are prompted
        to write (e.g. 'RECOMMENDATION: BUY'). This avoids false matches from
        words like 'SELL-off' or 'not a BUY signal' appearing in the body text.

        Falls back to the profile default only if the agent ignored the instruction.
        """
        import re
        match = re.search(r"RECOMMENDATION:\s*(BUY|SELL)", text, re.IGNORECASE)
        if match:
            return match.group(1).upper()
        # Fallback — agent didn't write the expected line
        return "SELL" if risk_profile == "risk-averse" else "BUY"
