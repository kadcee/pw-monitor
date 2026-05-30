"""
PW Monitor — Prevailing Wage Page Scanner
Version 3.7 | May 2026

Changes from v3.6:
- fetch_page now retries without headers if first attempt returns 403,
  fixing sites that block browser-like headers (mass.gov, azica.gov, tn.gov)
- Removed Accept-Encoding header to prevent compressed binary responses
  that urllib cannot decompress, fixing garbled diffs (WA_RATES, NC_LEG)
- EPI sources removed from automated scan; EPI blocks all automated requests.
  EPI is now a manual monthly check. Total sources: 29.
- Gemini delay increased from 5s to 10s and retry logic added on 429 errors
  to reduce rate limit failures
"""

import os
import hashlib
import difflib
import datetime
import json
import time
import urllib.request
import urllib.error
from html.parser import HTMLParser


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

FIREBASE_URL    = os.environ["FIREBASE_DATABASE_URL"].rstrip("/")
FIREBASE_SECRET = os.environ["FIREBASE_SECRET"]
GEMINI_API_KEY  = os.environ["GEMINI_API_KEY"]

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    f"gemini-2.5-flash-lite:generateContent?key={GEMINI_API_KEY}"
)

GEMINI_DELAY_SECONDS = 10
GEMINI_MAX_RETRIES  = 3
GEMINI_RETRY_DELAY  = 30  # seconds to wait after a 429

# Maps jurisdiction ID to the state abbreviation used in the frontend
STATE_MAP = {
    "CA":        "CA",
    "NV_PW":     "NV",
    "NV_HOME":   "NV",
    "WA_POLICY": "WA",
    "WA_RATES":  "WA",
    "MA":        "MA",
    "MN":        "MN",
    "NJ_RATES":  "NJ",
    "NJ_ACT":    "NJ",
    "NY":        "NY",
    "NY_BUREAU": "NY",
    "MI":        "MI",
    "DENVER_CO": "Denver, CO",
    "VA_LEG":    "VA",
    "VA_LABOR":  "VA",
    "NC_LEG":    "NC",
    "NC_LABOR":  "NC",
    "AZ_LEG":    "AZ",
    "AZ_LABOR":  "AZ",
    "WI_LEG":    "WI",
    "WI_LABOR":  "WI",
    "WV_LEG":    "WV",
    "WV_LABOR":  "WV",
    "GA_LEG":    "GA",
    "GA_LABOR":  "GA",
    "TN_LEG":    "TN",
    "TN_LABOR":  "TN",
    "NCSL_LABOR": "NCSL",
    "NCSL_DC":   "NCSL",
}

# IDs where isLocal should be true
LOCAL_IDS = {"DENVER_CO"}

JURISDICTIONS = [
    {"id": "CA",        "label": "California (CA)",                  "url": "https://www.dir.ca.gov/Public-Works/PublicWorks.html",                                                                                                    "watch_list": False},
    {"id": "NV_PW",     "label": "Nevada — Prevailing Wage",         "url": "https://labor.nv.gov/PrevailingWage/Public_Works___Prevailing_Wages/",                                                                                    "watch_list": False},
    {"id": "NV_HOME",   "label": "Nevada — Labor Dept",              "url": "https://labor.nv.gov",                                                                                                                                     "watch_list": False},
    {"id": "WA_POLICY", "label": "Washington — PW Policies",         "url": "https://lni.wa.gov/licensing-permits/public-works-projects/prevailing-wage-policies",                                                                     "watch_list": False},
    {"id": "WA_RATES",  "label": "Washington — PW Rates",            "url": "https://www.lni.wa.gov/licensing-permits/public-works-projects/prevailing-wage-rates/",                                                                   "watch_list": False},
    {"id": "MA",        "label": "Massachusetts (MA)",               "url": "https://www.mass.gov/prevailing-wage-program",                                                                                                             "watch_list": False},
    {"id": "MN",        "label": "Minnesota (MN)",                   "url": "https://dli.mn.gov/prevailing-wage",                                                                                                                       "watch_list": False},
    {"id": "NJ_RATES",  "label": "New Jersey — PW Rates",            "url": "https://www.nj.gov/labor/wageandhour/prevailing-rates/public-works/index.shtml",                                                                         "watch_list": False},
    {"id": "NJ_ACT",    "label": "New Jersey — PW Act",              "url": "https://www.nj.gov/labor/wageandhour/tools-resources/laws/prevailingwageact.shtml",                                                                       "watch_list": False},
    {"id": "NY",        "label": "New York (NY)",                    "url": "https://apps.labor.ny.gov/wpp/publicViewPWChanges.do?method=showIt#",                                                                                                                      "watch_list": False},
    {"id": "NY_BUREAU", "label": "New York — Bureau of Public Work", "url": "https://dol.ny.gov/bureau-public-work-and-prevailing-wage-enforcement",                                                                                   "watch_list": False},
    {"id": "MI",        "label": "Michigan (MI)",                    "url": "https://www.michigan.gov/leo/bureaus-agencies/ber/wage-and-hour/prevailing-wage",                                                                         "watch_list": False},
    {"id": "DENVER_CO", "label": "Denver, CO (Local)",               "url": "https://www.denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Denver-Labor/Prevailing-Wage",   "watch_list": False},
]

WATCH_LIST = [
    {"id": "VA_LEG",   "label": "Virginia (Legislature)",          "url": "https://lis.virginia.gov",                                                                           "watch_list": True},
    {"id": "VA_LABOR", "label": "Virginia (DOLI)",                 "url": "https://doli.virginia.gov/programs/labor-law/prevailing-wage-law/",                                  "watch_list": True},
    {"id": "NC_LEG",   "label": "North Carolina (Legislature)",    "url": "https://www.ncleg.gov/legislation",                                                                   "watch_list": True},
    {"id": "NC_LABOR", "label": "North Carolina (NC DOL)",         "url": "https://www.labor.nc.gov/",                                                                           "watch_list": True},
    {"id": "AZ_LEG",   "label": "Arizona (Legislature)",           "url": "https://www.azleg.gov/bills/",                                                                        "watch_list": True},
    {"id": "AZ_LABOR", "label": "Arizona (ICA)",                   "url": "https://www.azica.gov/",                                                                              "watch_list": True},
    {"id": "WI_LEG",   "label": "Wisconsin (Legislature)",         "url": "https://legis.wisconsin.gov/",                                                                        "watch_list": True},
    {"id": "WI_LABOR", "label": "Wisconsin (DWD)",                 "url": "https://dwd.wisconsin.gov/",                                                                          "watch_list": True},
    {"id": "WV_LEG",   "label": "West Virginia (Legislature)",     "url": "https://www.wvlegislature.gov/",                                                                      "watch_list": True},
    {"id": "WV_LABOR", "label": "West Virginia (Labor Dept)",      "url": "https://labor.wv.gov/",                                                                               "watch_list": True},
    {"id": "GA_LEG",   "label": "Georgia (Legislature)",           "url": "https://www.legis.ga.gov/",                                                                           "watch_list": True},
    {"id": "GA_LABOR", "label": "Georgia (GA DOL)",                "url": "https://dol.georgia.gov/",                                                                            "watch_list": True},
    {"id": "TN_LEG",   "label": "Tennessee (Legislature)",         "url": "https://wapp.capitol.tn.gov/apps/billsearch/billsearchadvanced.aspx",                                "watch_list": True},
    {"id": "TN_LABOR", "label": "Tennessee (Labor Dept)",          "url": "https://www.tn.gov/workforce/employees/labor-laws/labor-laws-redirect/wages-breaks/prevailing-wage.html", "watch_list": True},
]

AGGREGATE_SOURCES = [
    {"id": "NCSL_LABOR", "label": "NCSL — Labor and Employment",       "url": "https://www.ncsl.org/labor-and-employment",                   "watch_list": False},
    {"id": "NCSL_DC",    "label": "NCSL — In DC (Federal Legislation)","url": "https://www.ncsl.org/in-dc",                                  "watch_list": False},
]

ALL_SOURCES = JURISDICTIONS + WATCH_LIST + AGGREGATE_SOURCES  # 29 total (EPI moved to manual monthly check)


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
    """Fetch a page with browser-like headers. If the server returns 403,
    retry without custom headers — some government sites block browser UA strings."""
    browser_headers = {
        "User-Agent":      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Connection":      "keep-alive",
    }
    for headers in (browser_headers, {}):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=20) as resp:
                raw = resp.read()
                encoding = resp.headers.get_content_charset() or "utf-8"
                return raw.decode(encoding, errors="replace")
        except urllib.error.HTTPError as e:
            if e.code == 403 and headers:
                print(f"    -> 403 with headers, retrying without headers...")
                continue
            raise
    raise urllib.error.HTTPError(url, 403, "403 with and without headers", {}, None)


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
    url     = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    payload = json.dumps(data).encode("utf-8")
    req     = urllib.request.Request(url, data=payload, method="PUT",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def fb_post(path: str, data):
    url     = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    payload = json.dumps(data).encode("utf-8")
    req     = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------

def compute_diff(old_text: str, new_text: str) -> str:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff      = list(difflib.unified_diff(
        old_lines, new_lines, fromfile="previous", tofile="current", lineterm=""
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
    parts   = []
    if added:   parts.append(f"{added} line(s) added")
    if removed: parts.append(f"{removed} line(s) removed")
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
- relevant = true if the change involves wage rates, coverage rules, thresholds, effective dates, new legislation, repeals, policy guidance, Davis-Bacon Act changes, Service Contract Act changes, or federal prevailing wage activity affecting state obligations.
- relevant = false if the change is a page redesign, navigation update, broken link fix, or unrelated content change.
- For watch-list states, relevant = true if there is any new bill, adoption signal, ballot initiative, or legislative movement related to prevailing wage.
- For aggregate national sources, relevant = true if there is any new prevailing wage legislation, federal wage law change, or state adoption activity.
- impact_level = "High" if it directly affects contract pricing, bid obligations, or legal exposure.
- impact_level = "Medium" if it requires monitoring but has no immediate contract impact.
- impact_level = "Low" if it is administrative or procedural with no substantive rate or coverage change.
- confidence = "high" if the diff clearly shows a regulatory change.
- confidence = "medium" if the diff is ambiguous or partial.
- confidence = "low" if the diff is unclear or mostly noise.
"""


def call_gemini(label: str, url: str, is_watch_list: bool, diff: str) -> dict:
    prompt = GEMINI_PROMPT_TEMPLATE.format(
        label=label, url=url,
        is_watch_list=str(is_watch_list),
        diff=diff[:8000]
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
    req  = urllib.request.Request(
        GEMINI_URL, data=data, method="POST",
        headers={"Content-Type": "application/json"}
    )
    for attempt in range(1, GEMINI_MAX_RETRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
            raw_text = result["candidates"][0]["content"]["parts"][0]["text"]
            clean    = raw_text.strip().lstrip("```json").lstrip("```").rstrip("```").strip()
            return json.loads(clean)
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < GEMINI_MAX_RETRIES:
                print(f"    Gemini 429 rate limit (attempt {attempt}/{GEMINI_MAX_RETRIES}). Waiting {GEMINI_RETRY_DELAY}s...")
                time.sleep(GEMINI_RETRY_DELAY)
                continue
            print(f"    Gemini HTTP error: {e.code} {e.reason}")
            return None
        except Exception as e:
            print(f"    Gemini error: {e}")
            return None
    return None


# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------

def scan_source(source: dict, scan_date: str, gemini_call_count: list) -> dict:
    sid           = source["id"]
    url           = source["url"]
    label         = source["label"]
    is_watch_list = source.get("watch_list", False)
    state         = STATE_MAP.get(sid, sid)
    is_local      = sid in LOCAL_IDS
    result        = {"id": sid, "label": label, "url": url, "status": "ok", "diff": ""}

    try:
        html     = fetch_page(url)
        new_text = extract_text(html)
        new_hash = compute_hash(new_text)
    except Exception as e:
        result["status"] = "fetch_error"
        result["error"]  = str(e)
        # Write broken URL item to 'changes' node so frontend queue shows it
        change_item = {
            "state":         state,
            "title":         f"{label} — Source URL Unreachable ({scan_date})",
            "category":      "Administrative update",
            "impact":        "Low",
            "type":          "Enacted",
            "summary":       f"The scanner could not fetch this source URL on {scan_date}. Error: {str(e)}. Visit the URL manually to check if the page has moved. If it has moved, update scanner.py with the new URL and dismiss this item.",
            "source":        url,
            "date":          scan_date,
            "effectiveDate": "N/A",
            "status":        "pending",
            "reviewer":      "",
            "notes":         "Auto-flagged by scanner: fetch error. Verify URL and update scanner.py if page has moved.",
            "reviewed":      None,
            "isLocal":       is_local,
            "isFederal":     False,
            "autoDetected":  True,
        }
        fb_post("changes", change_item)
        print(f"    -> fetch_error: pushed broken URL item to changes (queue)")
        return result

    baseline = fb_get(f"baselines/{sid}") or {}
    old_hash = baseline.get("hash", "")
    old_text = baseline.get("text", "")

    if not old_hash:
        fb_put(f"baselines/{sid}", {"hash": new_hash, "text": new_text, "stored_at": scan_date})
        result["status"] = "baseline_stored"
        return result

    if new_hash == old_hash:
        result["status"] = "no_change"
        return result

    diff         = compute_diff(old_text, new_text)
    diff_summary = summarize_diff(diff)

    if gemini_call_count[0] > 0:
        time.sleep(GEMINI_DELAY_SECONDS)
    gemini_call_count[0] += 1

    print(f"    -> change detected ({diff_summary}). Calling Gemini...")
    gemini = call_gemini(label, url, is_watch_list, diff)

    if gemini is None:
        # Gemini failed — push to queue for manual review
        change_item = {
            "state":         state,
            "title":         f"{label} — Page Updated {scan_date}",
            "category":      "Rate change",
            "impact":        "Medium",
            "type":          "Enacted",
            "summary":       f"Automated scan detected a change on {label}. Gemini AI analysis failed (HTTP 429 or API error). Review the source link manually before approving.",
            "source":        url,
            "date":          scan_date,
            "effectiveDate": "Review required",
            "status":        "pending",
            "reviewer":      "",
            "notes":         "Gemini analysis failed. Treat confidence as LOW. Visit source link and review manually.",
            "reviewed":      None,
            "isLocal":       is_local,
            "isFederal":     False,
            "autoDetected":  True,
        }
        fb_post("changes", change_item)
        result["status"] = "change_detected_gemini_failed"
        result["diff_summary"] = diff_summary
        print(f"    -> Gemini failed. Item pushed to changes (queue) with low confidence.")

    elif not gemini.get("relevant", False):
        # Non-relevant — write to 'changes' as dismissed so archive shows it
        change_item = {
            "state":         state,
            "title":         f"{label} — Non-Relevant Change {scan_date}",
            "category":      gemini.get("category", "Administrative update"),
            "impact":        gemini.get("impact_level", "Low"),
            "type":          "Enacted",
            "summary":       gemini.get("summary", "Auto-dismissed as non-relevant."),
            "source":        url,
            "date":          scan_date,
            "effectiveDate": "N/A",
            "status":        "dismissed",
            "reviewer":      "auto_scanner",
            "notes":         f"Auto-dismissed by Gemini AI. Confidence: {gemini.get('confidence', 'unknown')}.",
            "reviewed":      scan_date,
            "isLocal":       is_local,
            "isFederal":     False,
            "autoDetected":  True,
        }
        fb_post("changes", change_item)
        result["status"] = "auto_dismissed"
        result["diff_summary"] = diff_summary
        print(f"    -> Non-relevant (confidence: {gemini.get('confidence')}). Auto-dismissed to changes (archive).")

    else:
        # Relevant — push to queue for human review
        change_item = {
            "state":         state,
            "title":         f"{label} — Page Updated {scan_date}",
            "category":      gemini.get("category", "Rate change"),
            "impact":        gemini.get("impact_level", "Medium"),
            "type":          "Enacted",
            "summary":       gemini.get("summary", "Review the source link and summarize what changed before approving."),
            "source":        url,
            "date":          scan_date,
            "effectiveDate": "Review required",
            "status":        "pending",
            "reviewer":      "",
            "notes":         f"Auto-flagged by scanner. Gemini confidence: {gemini.get('confidence', 'unknown')}. Edit summary before approving.",
            "reviewed":      None,
            "isLocal":       is_local,
            "isFederal":     False,
            "autoDetected":  True,
        }
        fb_post("changes", change_item)
        result["status"] = "change_detected"
        result["diff_summary"] = diff_summary
        print(f"    -> Relevant (confidence: {gemini.get('confidence')}, impact: {gemini.get('impact_level')}). Pushed to changes (queue).")

    fb_put(f"baselines/{sid}", {"hash": new_hash, "text": new_text, "stored_at": scan_date})
    result["diff"] = diff
    return result


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    scan_date         = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    log               = {"date": scan_date, "results": []}
    gemini_call_count = [0]

    print(f"PW Monitor Scanner v3.7 — {scan_date}")
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

    fb_post("scan_logs", log)

    changes_found = sum(1 for r in log["results"] if r["status"] in ("change_detected", "change_detected_gemini_failed"))
    fb_put("meta/last_scan", {
        "date":            scan_date,
        "changes_found":   changes_found,
        "sources_scanned": len(ALL_SOURCES),
        "errors":          sum(1 for r in log["results"] if r["status"] == "fetch_error"),
        "auto_dismissed":  sum(1 for r in log["results"] if r["status"] == "auto_dismissed"),
    })

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
        print("\nGemini failed (review manually):")
        for r in gemini_failed:
            print(f"  {r['id']}")
    if errors:
        print("\nFetch errors (broken URL items pushed to review queue):")
        for r in errors:
            print(f"  {r['id']}: {r['error']}")

    print(f"\nScan log written to Firebase under scan_logs.")


if __name__ == "__main__":
    main()
