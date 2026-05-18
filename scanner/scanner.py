"""
PW Monitor — Prevailing Wage Page Scanner
Version 3.3 | May 2026

Changes from v2.0:
- Gemini AI analysis added for every detected page change
- Non-relevant changes auto-dismissed to archive (never reach review queue)
- Relevant changes include AI summary, impact suggestion, category, confidence level
- Broken URL detection: fetch errors push a pending item to the review queue
- AGGREGATE_SOURCES (NCSL, EPI) added as automated daily scan
- Watch-list updated: CO removed, TN added; all 7 watch-list states have dual URLs
- FL and TX removed from watch-list (no credible legislative path)
- 5-second delay between Gemini calls to prevent rate limit errors (HTTP 429)
- Total monitored sources: 25 (9 active + 14 watch-list + 2 aggregate)
"""

import os
import hashlib
import difflib
import datetime
import json
import time
import urllib.request
import urllib.parse
import urllib.error
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FIREBASE_URL = os.environ["FIREBASE_DATABASE_URL"].rstrip("/")
FIREBASE_SECRET = os.environ["FIREBASE_SECRET"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
)

GEMINI_DELAY_SECONDS = 5  # Prevents HTTP 429 rate limit errors on free tier

JURISDICTIONS = [
    {"id": "CA",        "label": "California (CA)",       "url": "https://www.dir.ca.gov/Public-Works/Prevailing-Wage.html",  "watch_list": False},
    {"id": "NV",        "label": "Nevada (NV)",            "url": "https://labor.nv.gov/Employer/Prevailing_Wage_Information/",  "watch_list": False},
    {"id": "WA",        "label": "Washington (WA)",        "url": "https://lni.wa.gov/licensing-permits/public-works-projects/prevailing-wage",  "watch_list": False},
    {"id": "MA",        "label": "Massachusetts (MA)",     "url": "https://www.mass.gov/prevailing-wages",  "watch_list": False},
    {"id": "MN",        "label": "Minnesota (MN)",         "url": "https://www.dli.mn.gov/business/employment-practices/prevailing-wage",  "watch_list": False},
    {"id": "NJ",        "label": "New Jersey (NJ)",        "url": "https://www.nj.gov/labor/wageandhour/tools-resources/prevailingwage/",  "watch_list": False},
    {"id": "NY",        "label": "New York (NY)",          "url": "https://dol.ny.gov/prevailing-wages",  "watch_list": False},
    {"id": "MI",        "label": "Michigan (MI)",          "url": "https://www.michigan.gov/leo/bureaus-agencies/bers/prevailing-wage",  "watch_list": False},
    {"id": "DENVER_CO", "label": "Denver, CO (Local)",     "url": "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Prevailing-Wage",  "watch_list": False},
]

WATCH_LIST = [
    {"id": "VA_LEG",    "label": "Virginia (Legislature)",            "url": "https://lis.virginia.gov",                                                                          "watch_list": True},
    {"id": "VA_LABOR",  "label": "Virginia (DOLI)",                   "url": "https://doli.virginia.gov/programs/labor-law/prevailing-wage-law/",                                 "watch_list": True},
    {"id": "NC_LEG",    "label": "North Carolina (Legislature)",      "url": "https://www.ncleg.gov/legislation",                                                                  "watch_list": True},
    {"id": "NC_LABOR",  "label": "North Carolina (NC DOL)",           "url": "https://www.labor.nc.gov/",                                                                          "watch_list": True},
    {"id": "AZ_LEG",    "label": "Arizona (Legislature)",             "url": "https://www.azleg.gov/bills/",                                                                       "watch_list": True},
    {"id": "AZ_LABOR",  "label": "Arizona (ICA)",                     "url": "https://www.azica.gov/",                                                                             "watch_list": True},
    {"id": "WI_LEG",    "label": "Wisconsin (Legislature)",           "url": "https://legis.wisconsin.gov/",                                                                       "watch_list": True},
    {"id": "WI_LABOR",  "label": "Wisconsin (DWD)",                   "url": "https://dwd.wisconsin.gov/",                                                                         "watch_list": True},
    {"id": "WV_LEG",    "label": "West Virginia (Legislature)",       "url": "https://www.wvlegislature.gov/",                                                                     "watch_list": True},
    {"id": "WV_LABOR",  "label": "West Virginia (Labor Dept)",        "url": "https://labor.wv.gov/",                                                                              "watch_list": True},
    {"id": "GA_LEG",    "label": "Georgia (Legislature)",             "url": "https://www.legis.ga.gov/",                                                                          "watch_list": True},
    {"id": "GA_LABOR",  "label": "Georgia (GA DOL)",                  "url": "https://dol.georgia.gov/",                                                                           "watch_list": True},
    {"id": "TN_LEG",    "label": "Tennessee (Legislature)",           "url": "https://wapp.capitol.tn.gov/apps/billsearch/billsearchadvanced.aspx",                               "watch_list": True},
    {"id": "TN_LABOR",  "label": "Tennessee (Labor Dept)",            "url": "https://www.tn.gov/workforce/employees/labor-laws/labor-laws-redirect/wages-breaks/prevailing-wage.html", "watch_list": True},
]

AGGREGATE_SOURCES = [
    {"id": "NCSL",      "label": "NCSL — National Conference of State Legislatures", "url": "https://www.ncsl.org/labor-and-employment/prevailing-wage-laws", "watch_list": False},
    {"id": "EPI",       "label": "EPI — Economic Policy Institute",                  "url": "https://www.epi.org/research/prevailing-wage/",                  "watch_list": False},
]

ALL_SOURCES = JURISDICTIONS + WATCH_LIST + AGGREGATE_SOURCES  # 25 total


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
        headers={"User-Agent": "PW-Monitor-Scanner/3.2 (internal compliance tool)"}
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
        old_lines, new_lines,
        fromfile="previous", tofile="current", lineterm=""
    ))

    changed = [l for l in diff if l.startswith(("+", "-", "@@", "---", "+++"))]

    if not changed:
        return ""

    if len(changed) > 100:
        changed = changed[:100]
        changed.append("... (diff truncated at 100 lines — view source for full comparison)")

    return "\n".join(changed)


def summarize_diff(diff: str) -> str:
    added   = sum(1 for l in diff.splitlines() if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff.splitlines() if l.startswith("-") and not l.startswith("---"))
    parts = []
    if added:
        parts.append(f"{added} line(s) added")
    if removed:
        parts.append(f"{removed} line(s) removed")
    return "; ".join(parts) if parts else "Content changed (see diff)"


# ---------------------------------------------------------------------------
# Gemini AI analysis
# ---------------------------------------------------------------------------

GEMINI_PROMPT_TEMPLATE = """
You are a compliance analyst specializing in US state prevailing wage laws.

A web page monitored for prevailing wage regulatory changes has been updated.
Analyze the diff below and return a JSON object ONLY — no markdown, no explanation.

Source: {label}
URL: {url}
Watch-list state (monitoring for new legislation): {is_watch_list}

Diff:
{diff}

Return this exact JSON structure:
{{
  "relevant": true or false,
  "confidence": "high" or "medium" or "low",
  "summary": "Plain-English description of what changed and why it matters. If not relevant, explain why.",
  "impact_level": "High" or "Medium" or "Low",
  "category": one of ["Rate change", "Coverage expansion", "Coverage contraction", "New state adoption", "Policy guidance", "Apprenticeship / fringe benefit update", "Proposed legislation", "Administrative / technical update"]
}}

Rules:
- relevant = true if the change involves wage rates, coverage rules, thresholds, effective dates, new legislation, repeals, or policy guidance affecting prevailing wage obligations.
- relevant = false if the change is a page redesign, navigation update, broken link fix, or unrelated content change.
- For watch-list states, relevant = true if there is any new bill, adoption signal, ballot initiative, or legislative movement related to prevailing wage.
- impact_level = "High" if it directly affects contract pricing, bid obligations, or legal exposure.
- impact_level = "Medium" if it requires monitoring but has no immediate contract impact (e.g. proposed legislation).
- impact_level = "Low" if it is administrative or procedural with no substantive rate or coverage change.
- confidence = "high" if the diff clearly shows a regulatory change.
- confidence = "medium" if the diff is ambiguous or partial.
- confidence = "low" if the diff is unclear or mostly noise.
"""


def call_gemini(label: str, url: str, is_watch_list: bool, diff: str) -> dict:
    """
    Sends the diff to Gemini 2.0 Flash for analysis.
    Returns a dict with keys: relevant, confidence, summary, impact_level, category.
    Returns None if the API call fails.
    """
    prompt = GEMINI_PROMPT_TEMPLATE.format(
        label=label,
        url=url,
        is_watch_list=str(is_watch_list),
        diff=diff[:8000]  # Cap diff to avoid token limits
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 500,
            "responseMimeType": "application/json"
        }
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        GEMINI_URL, data=data, method="POST",
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
        # Strip any accidental markdown fences
        clean = raw_text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
        return json.loads(clean)
    except urllib.error.HTTPError as e:
        print(f"    Gemini HTTP error: {e.code} {e.reason}")
        return None
    except Exception as e:
        print(f"    Gemini error: {e}")
        return None


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------

def scan_source(source: dict, scan_date: str, gemini_call_count: list) -> dict:
    """
    Scans one source URL. Returns a result dict.
    gemini_call_count is a mutable list used to track calls across sources
    so the 5-second delay is applied correctly.
    """
    sid   = source["id"]
    url   = source["url"]
    label = source["label"]
    is_watch_list = source.get("watch_list", False)

    result = {"id": sid, "label": label, "url": url, "status": "ok", "diff": ""}

    # ── Fetch page ──────────────────────────────────────────────────────────
    try:
        html     = fetch_page(url)
        new_text = extract_text(html)
        new_hash = compute_hash(new_text)
    except Exception as e:
        result["status"] = "fetch_error"
        result["error"]  = str(e)

        # Push broken URL item to review queue so team knows monitoring is down
        queue_item = {
            "jurisdiction_id":    sid,
            "jurisdiction_label": label,
            "url":                url,
            "detected_at":        scan_date,
            "status":             "pending",
            "source":             "auto_scanner",
            "category":           "Administrative / technical update",
            "impact_level":       "Low",
            "title":              f"{label} — Source URL Unreachable ({scan_date})",
            "summary":            (
                f"The scanner could not fetch this source URL on {scan_date}. "
                f"Error: {str(e)}. Visit the URL manually to check if the page has moved. "
                f"If it has moved, update scanner.py with the new URL and dismiss this item."
            ),
            "diff_summary":       "Fetch error — source not monitored",
            "diff":               "",
            "gemini_relevant":    None,
            "gemini_confidence":  None,
            "gemini_summary":     "Not analyzed — fetch failed.",
            "gemini_impact":      None,
            "gemini_category":    None,
            "internal_notes":     "Auto-flagged by scanner: fetch error. Verify URL and update scanner.py if the page has moved.",
            "reviewer":           "",
            "reviewed_at":        "",
            "decision":           "",
            "decision_notes":     ""
        }
        fb_post("review_queue", queue_item)
        print(f"    -> fetch_error: pushed broken URL item to review queue")
        return result

    # ── Load baseline ────────────────────────────────────────────────────────
    baseline = fb_get(f"baselines/{sid}") or {}
    old_hash = baseline.get("hash", "")
    old_text = baseline.get("text", "")

    # First run — store baseline only
    if not old_hash:
        fb_put(f"baselines/{sid}", {
            "hash":      new_hash,
            "text":      new_text,
            "stored_at": scan_date
        })
        result["status"] = "baseline_stored"
        return result

    # No change
    if new_hash == old_hash:
        result["status"] = "no_change"
        return result

    # ── Change detected ──────────────────────────────────────────────────────
    diff         = compute_diff(old_text, new_text)
    diff_summary = summarize_diff(diff)

    # Apply delay before Gemini call (except on the very first call)
    if gemini_call_count[0] > 0:
        time.sleep(GEMINI_DELAY_SECONDS)
    gemini_call_count[0] += 1

    print(f"    -> change detected ({diff_summary}). Calling Gemini...")
    gemini = call_gemini(label, url, is_watch_list, diff)

    # ── Handle Gemini failure ────────────────────────────────────────────────
    if gemini is None:
        # Gemini failed — route to review queue with low confidence flag
        queue_item = {
            "jurisdiction_id":    sid,
            "jurisdiction_label": label,
            "url":                url,
            "detected_at":        scan_date,
            "status":             "pending",
            "source":             "auto_scanner",
            "category":           "Rate change",
            "impact_level":       "Medium",
            "title":              f"{label} — Page Updated {scan_date}",
            "summary":            (
                f"Automated scan detected a change on the {label} page. "
                f"Gemini AI analysis failed (check API key or rate limit). "
                f"Review the source link manually before approving."
            ),
            "diff_summary":       diff_summary,
            "diff":               diff,
            "gemini_relevant":    None,
            "gemini_confidence":  "low",
            "gemini_summary":     "Gemini analysis failed (HTTP Error 429 or API error). Review manually.",
            "gemini_impact":      None,
            "gemini_category":    None,
            "internal_notes":     "Gemini analysis failed. Treat confidence as LOW. Visit source link and review manually.",
            "reviewer":           "",
            "reviewed_at":        "",
            "decision":           "",
            "decision_notes":     ""
        }
        fb_post("review_queue", queue_item)
        result["status"]      = "change_detected_gemini_failed"
        result["diff_summary"] = diff_summary
        print(f"    -> Gemini failed. Item pushed to review queue with low confidence.")

    elif not gemini.get("relevant", False):
        # ── Non-relevant: auto-dismiss to archive ────────────────────────────
        archive_item = {
            "jurisdiction_id":    sid,
            "jurisdiction_label": label,
            "url":                url,
            "detected_at":        scan_date,
            "status":             "dismissed",
            "source":             "auto_scanner",
            "category":           gemini.get("category", "Administrative / technical update"),
            "impact_level":       gemini.get("impact_level", "Low"),
            "title":              f"{label} — Non-Relevant Change {scan_date}",
            "summary":            gemini.get("summary", "Auto-dismissed as non-relevant."),
            "diff_summary":       diff_summary,
            "diff":               diff,
            "gemini_relevant":    False,
            "gemini_confidence":  gemini.get("confidence", "low"),
            "gemini_summary":     gemini.get("summary", ""),
            "gemini_impact":      gemini.get("impact_level", "Low"),
            "gemini_category":    gemini.get("category", ""),
            "internal_notes":     "Auto-dismissed by Gemini AI as non-relevant (page redesign, nav update, or unrelated content change).",
            "reviewer":           "auto_scanner",
            "reviewed_at":        scan_date,
            "decision":           "dismissed",
            "decision_notes":     f"Auto-dismissed. Gemini confidence: {gemini.get('confidence', 'unknown')}."
        }
        fb_post("archive", archive_item)
        result["status"]      = "auto_dismissed"
        result["diff_summary"] = diff_summary
        print(f"    -> Non-relevant (confidence: {gemini.get('confidence')}). Auto-dismissed to archive.")

    else:
        # ── Relevant: push to review queue ───────────────────────────────────
        queue_item = {
            "jurisdiction_id":    sid,
            "jurisdiction_label": label,
            "url":                url,
            "detected_at":        scan_date,
            "status":             "pending",
            "source":             "auto_scanner",
            "category":           gemini.get("category", "Rate change"),
            "impact_level":       gemini.get("impact_level", "Medium"),
            "title":              f"{label} — Page Updated {scan_date}",
            "summary":            gemini.get("summary", "Review the source link and summarize what changed before approving."),
            "diff_summary":       diff_summary,
            "diff":               diff,
            "gemini_relevant":    True,
            "gemini_confidence":  gemini.get("confidence", "medium"),
            "gemini_summary":     gemini.get("summary", ""),
            "gemini_impact":      gemini.get("impact_level", "Medium"),
            "gemini_category":    gemini.get("category", ""),
            "internal_notes":     f"Auto-flagged by scanner. Gemini confidence: {gemini.get('confidence', 'unknown')}. Edit summary before approving.",
            "reviewer":           "",
            "reviewed_at":        "",
            "decision":           "",
            "decision_notes":     ""
        }
        fb_post("review_queue", queue_item)
        result["status"]      = "change_detected"
        result["diff_summary"] = diff_summary
        print(f"    -> Relevant (confidence: {gemini.get('confidence')}, impact: {gemini.get('impact_level')}). Pushed to review queue.")

    # ── Update baseline ──────────────────────────────────────────────────────
    fb_put(f"baselines/{sid}", {
        "hash":      new_hash,
        "text":      new_text,
        "stored_at": scan_date
    })

    result["diff"] = diff
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    scan_date = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    log = {"date": scan_date, "results": []}
    gemini_call_count = [0]  # Mutable so scan_source can increment it

    print(f"PW Monitor Scanner v3.3 — {scan_date}")
    print(f"Scanning {len(ALL_SOURCES)} sources...\n")

    for source in ALL_SOURCES:
        print(f"  [{source['id']}] {source['url']}")
        result = scan_source(source, scan_date, gemini_call_count)
        log["results"].append({
            "id":           result["id"],
            "status":       result["status"],
            "diff_summary": result.get("diff_summary", ""),
            "error":        result.get("error", "")
        })

    # Write scan log to Firebase
    fb_post("scan_logs", log)

    # Print summary
    changed       = [r for r in log["results"] if r["status"] == "change_detected"]
    dismissed     = [r for r in log["results"] if r["status"] == "auto_dismissed"]
    gemini_failed = [r for r in log["results"] if r["status"] == "change_detected_gemini_failed"]
    errors        = [r for r in log["results"] if r["status"] == "fetch_error"]
    baselines     = [r for r in log["results"] if r["status"] == "baseline_stored"]
    no_change     = [r for r in log["results"] if r["status"] == "no_change"]

    print(f"\n{'='*60}")
    print(f"Scan complete — {scan_date}")
    print(f"{'='*60}")
    print(f"  Sources scanned:      {len(ALL_SOURCES)}")
    print(f"  No change:            {len(no_change)}")
    print(f"  Baselines stored:     {len(baselines)}")
    print(f"  Changes (relevant):   {len(changed)}")
    print(f"  Auto-dismissed:       {len(dismissed)}")
    print(f"  Gemini failed:        {len(gemini_failed)}")
    print(f"  Fetch errors:         {len(errors)}")
    print(f"  Gemini calls made:    {gemini_call_count[0]}")

    if changed:
        print("\nRelevant changes pushed to review queue:")
        for r in changed:
            print(f"  {r['id']}: {r['diff_summary']}")

    if gemini_failed:
        print("\nGemini failed (pushed to queue with low confidence — review manually):")
        for r in gemini_failed:
            print(f"  {r['id']}")

    if errors:
        print("\nFetch errors (broken URL items pushed to review queue):")
        for r in errors:
            print(f"  {r['id']}: {r['error']}")

    print(f"\nScan log written to Firebase under scan_logs.")


if __name__ == "__main__":
    main()
