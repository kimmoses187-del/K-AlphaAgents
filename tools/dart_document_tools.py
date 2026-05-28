"""
tools/dart_document_tools.py
============================
Gap 1 — DART Full Document Text for FundamentalAgent.

Fetches the full-text content of DART annual / interim reports and extracts
the high-signal narrative sections:
  • 사업의 내용    (Business description / products / strategy)
  • 위험요소       (Risk factors)
  • 재무제표에 관한 사항 (Notes to financial statements)
  • 경영진의 논의와 분석  (MD&A / management commentary)

These sections are NOT available through the structured financial API
(fnlttSinglAcnt.json) — only through the full document XML/HTML.

Public API
----------
from tools.dart_document_tools import fetch_document_narrative

text = fetch_document_narrative(
    corp_code   = "00126380",
    reports_plan = [...],      # from dart_report_planner.plan_reports()
    max_tokens   = 8000,       # hard cap to avoid bloating the cache block
)
# Returns a formatted markdown string, or "" on failure.
"""

from __future__ import annotations

import io
import re
import zipfile
import logging
from typing import List, Dict, Optional

import requests
from bs4 import BeautifulSoup

from config import DART_API_KEY

log = logging.getLogger(__name__)

DART_BASE = "https://opendart.fss.or.kr/api"

# ── Section keywords to extract (priority order) ──────────────────────────────
# Each tuple: (section_label_for_output, list_of_korean_header_keywords)
_TARGET_SECTIONS = [
    ("Business Overview",   ["사업의 내용", "사업의개요", "영업의 내용", "주요 사업"]),
    ("Risk Factors",        ["위험요소", "위험 요소", "투자위험"]),
    ("MD&A",                ["경영진의 논의", "경영진 토의", "재무상태 및 영업실적", "영업실적"]),
    ("Key Financial Notes", ["재무제표에 관한 사항", "연결재무제표 주석", "재무제표 주석"]),
    ("Forward Outlook",     ["향후 전망", "사업 전망", "시장 전망"]),
]

# Characters → approximate tokens (conservative)
_CHARS_PER_TOKEN = 3.5

# Hard cap: max chars to inject per annual report (prevents cache bloat)
# ~8,000 tokens × 3.5 chars ≈ 28,000 chars — then we trim proportionally
_MAX_CHARS_PER_REPORT = 28_000


# ── DART filing list → rcpNo ──────────────────────────────────────────────────

def _get_filing_rcpno(corp_code: str, year: int, reprt_code: str) -> Optional[str]:
    """
    Look up the most recent receipt number (rcpNo) for a specific filing.
    Returns None if not found or on error.
    """
    try:
        r = requests.get(
            f"{DART_BASE}/list.json",
            params={
                "crtfc_key":  DART_API_KEY,
                "corp_code":  corp_code,
                "bgn_de":     f"{year}0101",
                "end_de":     f"{year}1231",
                "pblntf_ty":  "A",          # 사업보고서 type
                "page_count": 10,
            },
            timeout=20,
        )
        r.raise_for_status()
        body = r.json()
        if body.get("status") != "000":
            return None
        # Match the requested reprt_code by report name pattern
        reprt_labels = {
            "11011": ["사업보고서"],
            "11012": ["반기보고서"],
            "11013": ["1분기보고서", "분기보고서"],
            "11014": ["3분기보고서"],
        }
        target_names = reprt_labels.get(reprt_code, [])
        for item in body.get("list", []):
            nm = item.get("report_nm", "")
            if any(t in nm for t in target_names):
                return item.get("rcept_no")
    except Exception as exc:
        log.debug("DART filing list error: %s", exc)
    return None


# ── Download and parse document ZIP ──────────────────────────────────────────

def _fetch_document_html(rcpno: str) -> Optional[str]:
    """
    Download the DART document ZIP for `rcpno` and return the largest
    HTML/HTM file's text content.  Returns None on failure.
    """
    try:
        r = requests.get(
            f"{DART_BASE}/document.xml",
            params={"crtfc_key": DART_API_KEY, "rcept_no": rcpno},
            timeout=60,
            stream=True,
        )
        r.raise_for_status()
        content = r.content

        # The response may be a ZIP or raw XML/HTML
        if content[:2] == b"PK":
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                # Pick the largest HTML-like file (main body document)
                html_files = sorted(
                    [n for n in zf.namelist()
                     if n.lower().endswith((".html", ".htm", ".xml"))
                     and not n.lower().startswith("__mac")],
                    key=lambda n: zf.getinfo(n).file_size,
                    reverse=True,
                )
                if not html_files:
                    return None
                raw = zf.read(html_files[0])
        else:
            raw = content

        # Decode — DART documents are usually UTF-8 or EUC-KR
        for enc in ("utf-8", "euc-kr", "cp949", "utf-8-sig"):
            try:
                return raw.decode(enc)
            except (UnicodeDecodeError, LookupError):
                continue
        return raw.decode("utf-8", errors="replace")

    except Exception as exc:
        log.debug("DART document fetch error (rcpno=%s): %s", rcpno, exc)
        return None


# ── Section extraction ─────────────────────────────────────────────────────────

def _clean_text(raw: str) -> str:
    """Strip HTML tags, collapse whitespace, remove noise."""
    soup = BeautifulSoup(raw, "lxml")
    # Remove script / style
    for tag in soup(["script", "style", "head"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    # Collapse runs of blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse runs of spaces
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text.strip()


def _extract_sections(full_text: str) -> List[tuple]:
    """
    Scan `full_text` for target section headers and extract each section's
    content up to the next major header.

    Returns a list of (label, content) tuples in found order.
    """
    lines = full_text.split("\n")
    found: List[tuple] = []
    i = 0
    current_label: Optional[str] = None
    current_buf: List[str] = []
    max_section_lines = 400  # ~2,000 words per section at most

    while i < len(lines):
        line = lines[i].strip()

        # Check if this line matches any target section header
        matched_label = None
        for label, keywords in _TARGET_SECTIONS:
            if any(kw in line for kw in keywords) and len(line) < 60:
                matched_label = label
                break

        if matched_label:
            # Save previous section
            if current_label and current_buf:
                found.append((current_label, "\n".join(current_buf).strip()))
            current_label = matched_label
            current_buf = []
        elif current_label:
            # Accumulate content — stop at line count limit
            if len(current_buf) < max_section_lines:
                current_buf.append(lines[i])
            else:
                # Section too long — truncate here
                found.append((current_label, "\n".join(current_buf).strip()))
                current_label = None
                current_buf = []

        i += 1

    # Flush last section
    if current_label and current_buf:
        found.append((current_label, "\n".join(current_buf).strip()))

    return found


# ── Format for LLM injection ──────────────────────────────────────────────────

def _format_narrative(sections: List[tuple], label: str,
                      max_chars: int = _MAX_CHARS_PER_REPORT) -> str:
    """
    Format extracted sections into a structured markdown block.
    Trims proportionally if total chars exceed max_chars.
    """
    if not sections:
        return ""

    lines = [f"### DART Full Document — {label}", ""]
    total_chars = 0
    per_section_limit = max_chars // max(len(sections), 1)

    for sec_label, content in sections:
        trimmed = content[:per_section_limit]
        if len(content) > per_section_limit:
            trimmed += "\n[... section truncated to fit token budget ...]"
        lines.append(f"**{sec_label}**")
        lines.append(trimmed)
        lines.append("")
        total_chars += len(trimmed)

    lines.append("---")
    return "\n".join(lines)


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_document_narrative(
    corp_code: str,
    reports_plan: List[Dict],
    max_tokens: int = 8_000,
) -> str:
    """
    For each report in `reports_plan`, fetch the full DART document and
    extract narrative sections.  Returns a combined formatted markdown block.

    Parameters
    ----------
    corp_code    : 8-digit DART corp code
    reports_plan : list of {"year", "reprt_code", "label"} from plan_reports()
    max_tokens   : hard cap on total injected tokens (approx; 1 token ≈ 3.5 chars)

    Returns
    -------
    str — formatted narrative block, or "" if all fetches fail.
    """
    if not DART_API_KEY:
        return ""

    max_chars_total = int(max_tokens * _CHARS_PER_TOKEN)
    per_report_chars = max_chars_total // max(len(reports_plan), 1)

    all_blocks: List[str] = []

    for spec in reports_plan:
        year       = spec["year"]
        reprt_code = spec["reprt_code"]
        label      = spec["label"]

        log.debug("DART document: fetching %s (year=%s, code=%s)", label, year, reprt_code)

        rcpno = _get_filing_rcpno(corp_code, year, reprt_code)
        if not rcpno:
            log.debug("  No rcpNo found — skipping %s", label)
            continue

        html = _fetch_document_html(rcpno)
        if not html:
            log.debug("  Document download failed — skipping %s", label)
            continue

        full_text = _clean_text(html)
        sections  = _extract_sections(full_text)

        if not sections:
            log.debug("  No target sections found in %s", label)
            continue

        block = _format_narrative(sections, label, max_chars=per_report_chars)
        if block:
            all_blocks.append(block)
            log.debug("  Extracted %d section(s) from %s (~%d chars)",
                      len(sections), label, len(block))

    if not all_blocks:
        return ""

    header = (
        "## DART Full Document Narratives\n"
        "*(Extracted from official filings: business overview, risk factors, MD&A)*\n\n"
    )
    return header + "\n\n".join(all_blocks)
