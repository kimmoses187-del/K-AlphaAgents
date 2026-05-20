import io
import zipfile
import xml.etree.ElementTree as ET
import requests
from datetime import datetime
from config import DART_API_KEY

DART_BASE = "https://opendart.fss.or.kr/api"

# In-memory cache so we only download the corp code registry once per run
_CORP_CODE_CACHE: dict = {}


def _load_corp_code_registry() -> dict:
    """Download and parse OpenDART's full corp code registry.

    Returns a dict mapping stock_code (6-digit string) → corp_code (8-digit string).
    The registry is downloaded as a ZIP containing CORPCODE.xml.
    """
    global _CORP_CODE_CACHE
    if _CORP_CODE_CACHE:
        return _CORP_CODE_CACHE

    print("  Downloading OpenDART corp code registry...")
    r = requests.get(
        f"{DART_BASE}/corpCode.xml",
        params={"crtfc_key": DART_API_KEY},
        timeout=30,
    )
    r.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        xml_name = next(n for n in zf.namelist() if n.lower().endswith(".xml"))
        xml_bytes = zf.read(xml_name)

    root = ET.fromstring(xml_bytes)
    registry = {}
    for item in root.findall("list"):
        sc = (item.findtext("stock_code") or "").strip()
        cc = (item.findtext("corp_code") or "").strip()
        if sc:
            registry[sc] = cc

    _CORP_CODE_CACHE = registry
    print(f"  Registry loaded: {len(registry):,} listed companies")
    return registry


def lookup_company(stock_code: str) -> dict:
    """Lookup company info by KRX stock code.

    Step 1 — resolve stock_code → corp_code via the registry XML.
    Step 2 — fetch company detail from /api/company.json using corp_code.
    """
    registry = _load_corp_code_registry()
    corp_code = registry.get(stock_code)
    if not corp_code:
        raise ValueError(
            f"Stock code '{stock_code}' not found in OpenDART registry. "
            "Make sure it is a valid 6-digit KRX code."
        )

    r = requests.get(f"{DART_BASE}/company.json", params={
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
    })
    r.raise_for_status()
    data = r.json()
    if data.get("status") != "000":
        raise ValueError(f"DART company lookup failed for corp_code {corp_code}: {data.get('message')}")
    return data


def fetch_financial_statements(corp_code: str, year: int, reprt_code: str = "11011") -> dict:
    """Fetch consolidated financial statements from DART.

    reprt_code: 11011=사업보고서, 11012=반기보고서, 11013=1분기, 11014=3분기
    fs_div: OFS=별도, CFS=연결(consolidated)
    """
    r = requests.get(f"{DART_BASE}/fnlttSinglAcnt.json", params={
        "crtfc_key": DART_API_KEY,
        "corp_code": corp_code,
        "bsns_year": str(year),
        "reprt_code": reprt_code,
        "fs_div": "CFS",
    })
    r.raise_for_status()
    data = r.json()
    # Fall back to separate (OFS) if consolidated not available
    if data.get("status") != "000" or not data.get("list"):
        r2 = requests.get(f"{DART_BASE}/fnlttSinglAcnt.json", params={
            "crtfc_key": DART_API_KEY,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
            "fs_div": "OFS",
        })
        r2.raise_for_status()
        return r2.json()
    return data


def format_financial_data(corp_info: dict, fs_current: dict, fs_prev: dict,
                          current_year: int) -> str:
    """Format DART data into readable text for LLM analysis. (Legacy — kept for compatibility)"""
    lines = []

    lines.append("## Company Information")
    lines.append(f"Name: {corp_info.get('corp_name')}")
    lines.append(f"Stock Code: {corp_info.get('stock_code')}")
    lines.append(f"CEO: {corp_info.get('ceo_nm')}")
    lines.append(f"Main Products/Services: {corp_info.get('prd_nm', 'N/A')}")
    lines.append(f"Founded: {corp_info.get('est_dt', 'N/A')}")
    lines.append(f"Employees: {corp_info.get('enpls_nm', 'N/A')}")
    lines.append(f"Homepage: {corp_info.get('hm_url', 'N/A')}")
    lines.append("")

    lines.append(_format_single_fs(fs_current, f"FY{current_year}"))
    lines.append(_format_single_fs(fs_prev, f"FY{current_year - 1}"))

    return "\n".join(lines)


def _format_single_fs(fs_data: dict, label: str) -> str:
    """Format one DART financial statement response into readable text."""
    if fs_data.get("status") != "000" or not fs_data.get("list"):
        return f"## Financial Statements ({label})\nData not available.\n"

    items = fs_data["list"]
    section = [f"## Financial Statements ({label})"]

    for sj_div, sj_label in [("BS", "Balance Sheet"), ("IS", "Income Statement"), ("CF", "Cash Flow")]:
        group = [i for i in items if i.get("sj_div") == sj_div]
        if not group:
            continue
        section.append(f"\n### {sj_label}")
        for item in group[:20]:
            val      = item.get("thstrm_amount", "N/A")
            prev_val = item.get("frmtrm_amount", "N/A")
            section.append(f"  {item.get('account_nm')}: {val} (prev: {prev_val})")

    return "\n".join(section) + "\n"


def fetch_and_format_reports(corp_info: dict, reports_plan: list,
                             coverage_note: str = "") -> str:
    """
    Fetch multiple DART reports according to a plan from dart_report_planner
    and format them into a single string for the FundamentalAgent.

    Parameters
    ----------
    corp_info      : company info dict from lookup_company()
    reports_plan   : list of {"year", "reprt_code", "label"} from plan_reports()
    coverage_note  : optional pre-built coverage note from build_coverage_note()
    """
    corp_code = corp_info.get("corp_code", "")
    lines = []

    # ── Company header ────────────────────────────────────────────────────
    lines.append("## Company Information")
    lines.append(f"Name: {corp_info.get('corp_name')}")
    lines.append(f"Stock Code: {corp_info.get('stock_code')}")
    lines.append(f"CEO: {corp_info.get('ceo_nm')}")
    lines.append(f"Main Products/Services: {corp_info.get('prd_nm', 'N/A')}")
    lines.append(f"Founded: {corp_info.get('est_dt', 'N/A')}")
    lines.append(f"Employees: {corp_info.get('enpls_nm', 'N/A')}")
    lines.append(f"Homepage: {corp_info.get('hm_url', 'N/A')}")
    lines.append("")

    # ── Coverage note ─────────────────────────────────────────────────────
    if coverage_note:
        lines.append(coverage_note)

    # ── Fetch and format each planned report ──────────────────────────────
    fetched_count = 0
    for spec in reports_plan:
        fs_data = fetch_financial_statements(
            corp_code, spec["year"], spec["reprt_code"]
        )
        lines.append(_format_single_fs(fs_data, spec["label"]))
        lines.append("")
        if fs_data.get("status") == "000" and fs_data.get("list"):
            fetched_count += 1

    if fetched_count == 0:
        lines.append(
            "⚠️  No DART financial data was returned for any planned report. "
            "The company may not file structured financials via OpenDART, "
            "or the corp_code mapping may be incorrect. "
            "Base your analysis on publicly known information and flag the data gap."
        )

    return "\n".join(lines)
