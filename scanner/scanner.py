"""
PW Monitor - Prevailing Wage Page Scanner
Version 2.0 | May 2026

Upgrades from v1.0:
- Stores full extracted page text in Firebase (not just a hash)
- Computes a text diff on each run
- Includes the diff in the Review Queue item so reviewers see exactly what changed
- Hash is still computed and stored as a quick-change sentinel
"""

import os
import hashlib
import difflib
import datetime
import json
import urllib.request
import urllib.parse
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FIREBASE_URL = os.environ["FIREBASE_DATABASE_URL"].rstrip("/")
FIREBASE_SECRET = os.environ["FIREBASE_SECRET"]

JURISDICTIONS = [
    {"id": "CA",         "label": "California (CA)",          "url": "https://www.dir.ca.gov/Public-Works/Prevailing-Wage.html"},
    {"id": "NV",         "label": "Nevada (NV)",               "url": "https://labor.nv.gov/Employer/Prevailing_Wage_Information/"},
    {"id": "WA",         "label": "Washington (WA)",           "url": "https://lni.wa.gov/licensing-permits/public-works-projects/prevailing-wage"},
    {"id": "MA",         "label": "Massachusetts (MA)",        "url": "https://www.mass.gov/prevailing-wages"},
    {"id": "MN",         "label": "Minnesota (MN)",            "url": "https://www.dli.mn.gov/business/employment-practices/prevailing-wage"},
    {"id": "NJ",         "label": "New Jersey (NJ)",           "url": "https://www.nj.gov/labor/wageandhour/tools-resources/prevailingwage/"},
    {"id": "NY",         "label": "New York (NY)",             "url": "https://dol.ny.gov/prevailing-wages"},
    {"id": "MI",         "label": "Michigan (MI)",             "url": "https://www.michigan.gov/leo/bureaus-agencies/bers/prevailing-wage"},
    {"id": "DENVER_CO",  "label": "Denver, CO (Local)",        "url": "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Prevailing-Wage"},
]

AGGREGATE_SOURCES = [
    # National Conference of State Legislatures — tracks prevailing wage law status across all 50 states.
    # Catches new state adoptions, repeals, or major amendments before official labor department pages update.
    {"id": "NCSL",       "label": "NCSL — Prevailing Wage Laws (National)", "url": "https://www.ncsl.org/labor-and-employment/prevailing-wage-laws"},
    # Economic Policy Institute — tracks state-level prevailing wage policy research and legislation nationally.
    # Catches new bills, coverage changes, and policy shifts not yet reflected on state agency pages.
    {"id": "EPI",        "label": "EPI — Prevailing Wage Research (National)", "url": "https://www.epi.org/research/prevailing-wage/"},
]

WATCH_LIST = [
    {"id": "VA_LEG",     "label": "Virginia (Legislature)",    "url": "https://lis.virginia.gov"},
    {"id": "VA_LABOR",   "label": "Virginia (DOLI)",           "url": "https://doli.virginia.gov/programs/labor-law/prevailing-wage-law/"},
    {"id": "NC_LEG",     "label": "North Carolina (Legislature)", "url": "https://www.ncleg.gov/legislation"},
    {"id": "NC_LABOR",   "label": "North Carolina (NC DOL)",   "url": "https://www.labor.nc.gov/"},
    {"id": "AZ_LEG",     "label": "Arizona (Legislature)",     "url": "https://www.azleg.gov/bills/"},
    {"id": "AZ_LABOR",   "label": "Arizona (ICA)",             "url": "https://www.azica.gov/"},
    {"id": "WI_LEG",     "label": "Wisconsin (Legislature)",   "url": "https://legis.wisconsin.gov/"},
    {"id": "WI_LABOR",   "label": "Wisconsin (DWD)",           "url": "https://dwd.wisconsin.gov/"},
    {"id": "WV_LEG",     "label": "West Virginia (Legislature)", "url": "https://www.wvlegislature.gov/"},
    {"id": "WV_LABOR",   "label": "West Virginia (Labor Dept)", "url": "https://labor.wv.gov/"},
    {"id": "GA_LEG",     "label": "Georgia (Legislature)",     "url": "https://www.legis.ga.gov/"},
    {"id": "GA_LABOR",   "label": "Georgia (GA DOL)",          "url": "https://dol.georgia.gov/"},
]


# ---------------------------------------------------------------------------
# HTML text extraction
# ---------------------------------------------------------------------------

class TextExtractor(HTMLParser):
    """Extracts visible text from HTML, skipping scripts, styles, and nav."""

    SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header"}

    def __init__(self):
        super().__init__()
        self._skip = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._skip == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)

    def get_text(self):
        return "\n".join(self.chunks)


def extract_text(html: str) -> str:
    parser = TextExtractor()
    parser.feed(html)
    return parser.get_text()


def fetch_page(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "PW-Monitor-Scanner/2.0 (internal compliance tool)"}
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


def compute_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Firebase helpers
# ---------------------------------------------------------------------------

def fb_get(path: str):
    url = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read())


def fb_put(path: str, data):
    url = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="PUT",
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def fb_post(path: str, data):
    url = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    payload = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=payload, method="POST",
                                  headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------

def compute_diff(old_text: str, new_text: str) -> str:
    """
    Returns a unified-style diff of meaningful lines only.
    Limits output to 100 lines to keep Firebase payloads manageable.
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile="previous",
        tofile="current",
        lineterm=""
    ))

    changed = [l for l in diff if l.startswith(("+", "-", "@@", "---", "+++"))]

    if not changed:
        return ""

    if len(changed) > 100:
        changed = changed[:100]
        changed.append("... (diff truncated at 100 lines — view source for full comparison)")

    return "\n".join(changed)


def summarize_diff(diff: str) -> str:
    added = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    parts = []
    if added:
        parts.append(f"{added} line(s) added")
    if removed:
        parts.append(f"{removed} line(s) removed")
    return "; ".join(parts) if parts else "Content changed (see diff)"


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------

def scan_jurisdiction(j: dict, scan_date: str) -> dict:
    jid = j["id"]
    url = j["url"]
    result = {"id": jid, "label": j["label"], "url": url, "status": "ok", "diff": ""}

    try:
        html = fetch_page(url)
        new_text = extract_text(html)
        new_hash = compute_hash(new_text)
    except Exception as e:
        result["status"] = "fetch_error"
        result["error"] = str(e)
        return result

    baseline = fb_get(f"baselines/{jid}") or {}
    old_hash = baseline.get("hash", "")
    old_text = baseline.get("text", "")

    if not old_hash:
        fb_put(f"baselines/{jid}", {
            "hash": new_hash,
            "text": new_text,
            "stored_at": scan_date
        })
        result["status"] = "baseline_stored"
        return result

    if new_hash == old_hash:
        result["status"] = "no_change"
        return result

    diff = compute_diff(old_text, new_text)
    diff_summary = summarize_diff(diff)

    queue_item = {
        "jurisdiction_id": jid,
        "jurisdiction_label": j["label"],
        "url": url,
        "detected_at": scan_date,
        "status": "pending",
        "source": "auto_scanner",
        "category": "Rate change",
        "impact_level": "Medium",
        "title": f"{j['label']} — Page Updated {scan_date}",
        "summary": (
            f"Automated scan detected a change on the {j['label']} prevailing wage page. "
            f"Review the source link and summarize what changed before approving. "
            f"Detected: {scan_date}."
        ),
        "diff_summary": diff_summary,
        "diff": diff,
        "internal_notes": "Auto-flagged by scanner. Edit this summary before approving.",
        "reviewer": "",
        "reviewed_at": "",
        "decision": "",
        "decision_notes": ""
    }
    fb_post("review_queue", queue_item)

    fb_put(f"baselines/{jid}", {
        "hash": new_hash,
        "text": new_text,
        "stored_at": scan_date
    })

    result["status"] = "change_detected"
    result["diff_summary"] = diff_summary
    result["diff"] = diff
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    scan_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    all_jurisdictions = JURISDICTIONS + WATCH_LIST + AGGREGATE_SOURCES
    log = {"date": scan_date, "results": []}

    print(f"PW Monitor Scanner v2.1 — {scan_date}")
    print(f"Scanning {len(all_jurisdictions)} jurisdictions...\n")

    for j in all_jurisdictions:
        print(f"  {j['id']}: {j['url']}")
        result = scan_jurisdiction(j, scan_date)
        log["results"].append({
            "id": result["id"],
            "status": result["status"],
            "diff_summary": result.get("diff_summary", ""),
            "error": result.get("error", "")
        })
        print(f"    -> {result['status']}" + (f": {result.get('diff_summary','')}" if result.get("diff_summary") else ""))

    fb_post("scan_logs", log)

    changed = [r for r in log["results"] if r["status"] == "change_detected"]
    fb_put("meta/last_scan", {
        "date": scan_date,
        "changes_found": len(changed)
    })

    print(f"\nScan complete. Log written to Firebase.")

    errors = [r for r in log["results"] if r["status"] == "fetch_error"]
    print(f"\nSummary: {len(changed)} change(s) detected, {len(errors)} error(s).")
    if changed:
        print("Changed:")
        for r in changed:
            print(f"  {r['id']}: {r['diff_summary']}")
    if errors:
        print("Errors:")
        for r in errors:
            print(f"  {r['id']}: {r['error']}")


if __name__ == "__main__":
    main()
