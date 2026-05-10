"""
PW Monitor - Prevailing Wage Page Scanner
Version 3.0 | May 2026

Upgrades from v2.1:
- Gemini AI analyzes every detected change for prevailing wage relevance
- Active jurisdictions: looks for rate changes, coverage, thresholds, policy guidance
- Watch-list states: looks specifically for new legislation or adoption signals
- Non-relevant changes are auto-dismissed and never hit the Review Queue
- Relevant changes enter `changes/` with AI-generated summary pre-filled
- Watch-list flags marked with isWatchList: true so the app displays them differently
- meta/last_scan written after every run
"""

import os
import hashlib
import difflib
import datetime
import json
import urllib.request
import time
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FIREBASE_URL = os.environ["FIREBASE_DATABASE_URL"].rstrip("/")
FIREBASE_SECRET = os.environ["FIREBASE_SECRET"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent?key=" + GEMINI_API_KEY
)

JURISDICTIONS = [
    {"id": "CA",        "label": "California (CA)",      "url": "https://www.dir.ca.gov/Public-Works/Prevailing-Wage.html",                                                                                                                              "watchList": False},
    {"id": "NV",        "label": "Nevada (NV)",           "url": "https://labor.nv.gov/Employer/Prevailing_Wage_Information/",                                                                                                                            "watchList": False},
    {"id": "WA",        "label": "Washington (WA)",       "url": "https://lni.wa.gov/licensing-permits/public-works-projects/prevailing-wage",                                                                                                            "watchList": False},
    {"id": "MA",        "label": "Massachusetts (MA)",    "url": "https://www.mass.gov/prevailing-wages",                                                                                                                                                 "watchList": False},
    {"id": "MN",        "label": "Minnesota (MN)",        "url": "https://www.dli.mn.gov/business/employment-practices/prevailing-wage",                                                                                                                  "watchList": False},
    {"id": "NJ",        "label": "New Jersey (NJ)",       "url": "https://www.nj.gov/labor/wageandhour/tools-resources/prevailingwage/",                                                                                                                  "watchList": False},
    {"id": "NY",        "label": "New York (NY)",         "url": "https://dol.ny.gov/prevailing-wages",                                                                                                                                                   "watchList": False},
    {"id": "MI",        "label": "Michigan (MI)",         "url": "https://www.michigan.gov/leo/bureaus-agencies/bers/prevailing-wage",                                                                                                                    "watchList": False},
    {"id": "DENVER_CO", "label": "Denver, CO (Local)",    "url": "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Prevailing-Wage",                                                  "watchList": False},
]

WATCH_LIST = [
    {"id": "CO_STATE",  "label": "Colorado (statewide)",  "url": "https://leg.colorado.gov/bills",                                        "watchList": True},
    {"id": "VA",        "label": "Virginia",              "url": "https://doli.virginia.gov/programs/labor-law/prevailing-wage-law/",      "watchList": True},
    {"id": "NC",        "label": "North Carolina",        "url": "https://www.labor.nc.gov/",                                             "watchList": True},
    {"id": "AZ",        "label": "Arizona",               "url": "https://www.azica.gov/",                                                "watchList": True},
    {"id": "GA",        "label": "Georgia",               "url": "https://dol.georgia.gov/",                                              "watchList": True},
    {"id": "FL",        "label": "Florida",               "url": "https://floridajobs.org/",                                              "watchList": True},
    {"id": "TX",        "label": "Texas",                 "url": "https://www.twc.texas.gov/",                                            "watchList": True},
]


# ---------------------------------------------------------------------------
# HTML text extraction
# ---------------------------------------------------------------------------

class TextExtractor(HTMLParser):
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
        headers={"User-Agent": "PW-Monitor-Scanner/3.0 (internal compliance tool)"}
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
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)

    diff = list(difflib.unified_diff(
        old_lines, new_lines,
        fromfile="previous", tofile="current", lineterm=""
    ))

    changed = [l for l in diff if l.startswith(("+", "-", "@@", "---", "+++"))]

    if not changed:
        return ""

    if len(changed) > 100:
        changed = changed[:100]
        changed.append("... (diff truncated at 100 lines)")

    return "\n".join(changed)


# ---------------------------------------------------------------------------
# Gemini AI analysis
# ---------------------------------------------------------------------------

def build_prompt(j: dict, diff: str) -> str:
    if j["watchList"]:
        return f"""You are a prevailing wage compliance analyst monitoring US state labor law.

This is a watch-list state: {j['label']}. This state does NOT currently have a prevailing wage law.
You are reviewing a change detected on their labor department or legislature page.

Determine if this change signals any of the following:
- A new prevailing wage bill introduced or advancing in the legislature
- A prevailing wage law being adopted or enacted
- A ballot initiative related to prevailing wage
- Any official government action moving toward prevailing wage coverage

Page diff (lines added start with +, lines removed start with -):

{diff}

Respond in JSON only. No preamble. No markdown fences. Exact format:
{{
  "relevant": true or false,
  "confidence": "high", "medium", or "low",
  "summary": "2-3 sentence plain-English summary of what changed and why it matters. If not relevant, explain why.",
  "impact": "High", "Medium", or "Low",
  "category": "Proposed legislation" or "New state adoption" or "Administrative update" or "Not relevant"
}}"""
    else:
        return f"""You are a prevailing wage compliance analyst monitoring US state labor law.

This is an active prevailing wage jurisdiction: {j['label']}.
You are reviewing a change detected on their official prevailing wage page.

Determine if this change relates to any of the following:
- Wage rate changes or new rate determinations
- Coverage expansions or contractions (project types, thresholds)
- New statutes, regulations, or policy guidance
- Effective date changes
- Apprenticeship ratio or utilization requirements
- Administrative updates with no legal impact (page redesigns, nav changes, formatting)

Page diff (lines added start with +, lines removed start with -):

{diff}

Respond in JSON only. No preamble. No markdown fences. Exact format:
{{
  "relevant": true or false,
  "confidence": "high", "medium", or "low",
  "summary": "2-3 sentence plain-English summary of what changed and why it matters. If not relevant, explain why.",
  "impact": "High", "Medium", or "Low",
  "category": "Rate change" or "Coverage expansion" or "Coverage contraction" or "Policy guidance" or "Administrative update" or "Proposed legislation" or "Not relevant"
}}"""


def analyze_with_gemini(j: dict, diff: str) -> dict:
    prompt = build_prompt(j, diff)
    payload = json.dumps({
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512}
    }).encode("utf-8")

    req = urllib.request.Request(
        GEMINI_URL,
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
        raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

        # Strip markdown fences if present
        if raw_text.startswith("```"):
            raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
        raw_text = raw_text.strip()

        return json.loads(raw_text)

    except Exception as e:
        print(f"    Gemini error: {e}")
        # Fallback: flag for human review
        return {
            "relevant": True,
            "confidence": "low",
            "summary": f"Gemini analysis failed ({e}). Page change detected. Review source manually.",
            "impact": "Medium",
            "category": "Administrative update"
        }


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------

def scan_jurisdiction(j: dict, scan_date: str) -> dict:
    jid = j["id"]
    url = j["url"]
    result = {"id": jid, "label": j["label"], "url": url, "status": "ok"}

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

    # First run — store baseline only
    if not old_hash:
        fb_put(f"baselines/{jid}", {
            "hash": new_hash,
            "text": new_text,
            "stored_at": scan_date
        })
        result["status"] = "baseline_stored"
        return result

    # No change
    if new_hash == old_hash:
        result["status"] = "no_change"
        return result

    # Change detected
    diff = compute_diff(old_text, new_text)
    if not diff:
        result["status"] = "no_change"
        return result

    print(f"    Change detected. Analyzing with Gemini...")
    analysis = analyze_with_gemini(j, diff)

    relevant   = analysis.get("relevant", True)
    summary    = analysis.get("summary", "Page change detected. Review source manually.")
    impact     = analysis.get("impact", "Medium")
    category   = analysis.get("category", "Administrative update")
    confidence = analysis.get("confidence", "low")

    if not relevant:
        # Auto-dismiss
        fb_post("changes", {
            "state": j["label"],
            "title": f"{j['label']} — Non-PW Page Update {scan_date}",
            "category": "Administrative update",
            "impact": "Low",
            "type": "Enacted",
            "summary": summary,
            "source": url,
            "date": scan_date,
            "effectiveDate": "N/A",
            "status": "dismissed",
            "reviewer": "Auto-scanner (Gemini)",
            "notes": f"Auto-dismissed: not prevailing-wage-relevant (Gemini confidence: {confidence}).",
            "reviewed": scan_date,
            "isLocal": jid == "DENVER_CO",
            "isWatchList": j["watchList"],
            "autoDetected": True,
        })
        # Update baseline so we do not re-flag the same change tomorrow
        fb_put(f"baselines/{jid}", {
            "hash": new_hash,
            "text": new_text,
            "stored_at": scan_date
        })
        result["status"] = "auto_dismissed"
        result["summary"] = summary
        return result

    # Relevant — push to Review Queue
    fb_post("changes", {
        "state": j["label"],
        "title": f"{j['label']} — {category} Detected {scan_date}",
        "category": category,
        "impact": impact,
        "type": "Enacted",
        "summary": summary,
        "source": url,
        "date": scan_date,
        "effectiveDate": "Pending",
        "status": "pending",
        "reviewer": "",
        "notes": f"Auto-flagged by scanner. Gemini confidence: {confidence}. Confirm impact and edit summary before approving.",
        "reviewed": None,
        "isLocal": jid == "DENVER_CO",
        "isWatchList": j["watchList"],
        "autoDetected": True,
        "diff": diff,
    })

    # Update baseline
    fb_put(f"baselines/{jid}", {
        "hash": new_hash,
        "text": new_text,
        "stored_at": scan_date
    })

    result["status"] = "change_detected"
    result["summary"] = summary
    result["impact"] = impact
    result["category"] = category
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    scan_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    all_jurisdictions = JURISDICTIONS + WATCH_LIST
    log = {"date": scan_date, "results": []}

    print(f"PW Monitor Scanner v3.0 — {scan_date}")
    print(f"Scanning {len(all_jurisdictions)} jurisdictions with Gemini AI analysis...\n")

    for j in all_jurisdictions:
        watch_tag = " [WATCH]" if j["watchList"] else ""
        print(f"  {j['id']}{watch_tag}: {j['url']}")
        result = scan_jurisdiction(j, scan_date)
        time.sleep(5)
        log["results"].append({
            "id": result["id"],
            "status": result["status"],
            "summary": result.get("summary", ""),
            "error": result.get("error", "")
        })
        status_line = f"    -> {result['status']}"
        if result.get("summary"):
            status_line += f": {result['summary'][:80]}"
        print(status_line)

    # Write scan log
    fb_post("scan_logs", log)

    # Update meta/last_scan for the app footer
    changes_found = sum(1 for r in log["results"] if r["status"] == "change_detected")
    fb_put("meta/last_scan", {
        "date": scan_date,
        "changes_found": changes_found
    })

    print(f"\nScan complete. Log and meta/last_scan written to Firebase.")

    changed   = [r for r in log["results"] if r["status"] == "change_detected"]
    dismissed = [r for r in log["results"] if r["status"] == "auto_dismissed"]
    errors    = [r for r in log["results"] if r["status"] == "fetch_error"]

    print(f"\nSummary: {len(changed)} flagged for review, {len(dismissed)} auto-dismissed, {len(errors)} error(s).")

    if changed:
        print("Flagged for review:")
        for r in changed:
            print(f"  {r['id']}: {r.get('summary','')[:80]}")
    if dismissed:
        print("Auto-dismissed (not PW-relevant):")
        for r in dismissed:
            print(f"  {r['id']}: {r.get('summary','')[:80]}")
    if errors:
        print("Errors:")
        for r in errors:
            print(f"  {r['id']}: {r['error']}")


if __name__ == "__main__":
    main()
