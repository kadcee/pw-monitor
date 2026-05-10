"""
PW Monitor - Prevailing Wage Page Scanner

Version 3.2 | May 2026

Upgrades from v3.1:
- Broken URL detection: fetch errors are pushed to the Review Queue as
  a pending item so the team knows a jurisdiction stopped being monitored
- Watch-list states now have two source URLs each (legislature + labor dept)
  giving dual-source coverage per state
- SENDGRID_API_KEY remains required (from v3.1) but alert logic unchanged
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

FIREBASE_URL     = os.environ["FIREBASE_DATABASE_URL"].rstrip("/")
FIREBASE_SECRET  = os.environ["FIREBASE_SECRET"]
GEMINI_API_KEY   = os.environ["GEMINI_API_KEY"]
SENDGRID_API_KEY = os.environ["SENDGRID_API_KEY"]

GEMINI_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/"
    "gemini-2.0-flash:generateContent?key=" + GEMINI_API_KEY
)

SENDGRID_URL    = "https://api.sendgrid.com/v3/mail/send"
ALERT_FROM      = "pw-monitor@honeywell.com"
ALERT_FROM_NAME = "PW Monitor"
ALERT_RECIPIENTS = [
    "Darshana.Bhamburkar@honeywell.com",
    "Tanvera.Shaikh@honeywell.com",
    "UsamaAliQuraishi.Quraishi@honeywell.com",
    "Karen.Corpuz@honeywell.com",
]
ALERT_IMPACT_LEVELS = {"High", "Medium"}

# ---------------------------------------------------------------------------
# Active jurisdictions
# ---------------------------------------------------------------------------

JURISDICTIONS = [
    {"id": "CA",        "label": "California (CA)",   "url": "https://www.dir.ca.gov/Public-Works/Prevailing-Wage.html",                                                                                                                                             "watchList": False},
    {"id": "NV",        "label": "Nevada (NV)",        "url": "https://labor.nv.gov/Employer/Prevailing_Wage_Information/",                                                                                                                                          "watchList": False},
    {"id": "WA",        "label": "Washington (WA)",    "url": "https://lni.wa.gov/licensing-permits/public-works-projects/prevailing-wage",                                                                                                                          "watchList": False},
    {"id": "MA",        "label": "Massachusetts (MA)", "url": "https://www.mass.gov/prevailing-wages",                                                                                                                                                               "watchList": False},
    {"id": "MN",        "label": "Minnesota (MN)",     "url": "https://www.dli.mn.gov/business/employment-practices/prevailing-wage",                                                                                                                                 "watchList": False},
    {"id": "NJ",        "label": "New Jersey (NJ)",    "url": "https://www.nj.gov/labor/wageandhour/tools-resources/prevailingwage/",                                                                                                                                 "watchList": False},
    {"id": "NY",        "label": "New York (NY)",      "url": "https://dol.ny.gov/prevailing-wages",                                                                                                                                                                 "watchList": False},
    {"id": "MI",        "label": "Michigan (MI)",      "url": "https://www.michigan.gov/leo/bureaus-agencies/bers/prevailing-wage",                                                                                                                                   "watchList": False},
    {"id": "DENVER_CO", "label": "Denver, CO (Local)", "url": "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Prevailing-Wage",                                                                 "watchList": False},
]

# ---------------------------------------------------------------------------
# Watch-list jurisdictions — two URLs each (legislature + labor dept)
# ---------------------------------------------------------------------------

WATCH_LIST = [
    {"id": "CO_LEG",   "label": "Colorado (statewide) — Legislature",  "url": "https://leg.colorado.gov/bills",                               "watchList": True},
    {"id": "CO_LABOR", "label": "Colorado (statewide) — CDLE",         "url": "https://cdle.colorado.gov/prevailing-wage",                    "watchList": True},
    {"id": "VA_LEG",   "label": "Virginia — Legislature (LIS)",        "url": "https://lis.virginia.gov",                                     "watchList": True},
    {"id": "VA_LABOR", "label": "Virginia — DOLI Prevailing Wage",     "url": "https://doli.virginia.gov/programs/labor-law/prevailing-wage-law/", "watchList": True},
    {"id": "NC_LEG",   "label": "North Carolina — Legislature",        "url": "https://www.ncleg.gov/legislation",                            "watchList": True},
    {"id": "NC_LABOR", "label": "North Carolina — NC DOL",             "url": "https://www.labor.nc.gov/",                                   "watchList": True},
    {"id": "AZ_LEG",   "label": "Arizona — Legislature",               "url": "https://www.azleg.gov/bills/",                                 "watchList": True},
    {"id": "AZ_LABOR", "label": "Arizona — ICA",                       "url": "https://www.azica.gov/",                                      "watchList": True},
    {"id": "GA_LEG",   "label": "Georgia — Legislature",               "url": "https://www.legis.ga.gov/",                                   "watchList": True},
    {"id": "GA_LABOR", "label": "Georgia — GA DOL",                    "url": "https://dol.georgia.gov/",                                    "watchList": True},
    {"id": "FL_LEG",   "label": "Florida — Legislature (Senate)",      "url": "https://www.flsenate.gov/Laws/Statutes",                       "watchList": True},
    {"id": "FL_LABOR", "label": "Florida — FL DEO",                    "url": "https://floridajobs.org/",                                    "watchList": True},
    {"id": "TX_LEG",   "label": "Texas — Legislature",                 "url": "https://capitol.texas.gov/",                                  "watchList": True},
    {"id": "TX_LABOR", "label": "Texas — TWC",                         "url": "https://www.twc.texas.gov/",                                  "watchList": True},
]

# ---------------------------------------------------------------------------
# HTML text extraction
# ---------------------------------------------------------------------------

class TextExtractor(HTMLParser):
    SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header"}

    def __init__(self):
        super().__init__()
        self._skip  = 0
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
# Broken URL handler
# ---------------------------------------------------------------------------

def push_broken_url(j: dict, error: str, scan_date: str):
    fb_post("changes", {
        "state":         j["label"],
        "title":         f"{j['label']} — Broken URL Detected {scan_date}",
        "category":      "Administrative update",
        "impact":        "Low",
        "type":          "Enacted",
        "summary":       (
            f"The scanner could not fetch this page. Error: {error}. "
            "This jurisdiction is NOT being monitored until the URL is fixed. "
            "Verify the URL is correct, update scanner.py if needed, and dismiss this item."
        ),
        "source":        j["url"],
        "date":          scan_date,
        "effectiveDate": "N/A",
        "status":        "pending",
        "reviewer":      "",
        "notes":         (
            "Broken URL alert. No regulatory data was checked for this jurisdiction today. "
            "Fix the URL in scanner.py and push to GitHub. "
            "Dismiss this item once the URL is corrected."
        ),
        "reviewed":      None,
        "isLocal":       j["id"] == "DENVER_CO",
        "isWatchList":   j["watchList"],
        "autoDetected":  True,
        "brokenUrl":     True,
    })

# ---------------------------------------------------------------------------
# SendGrid email alert
# ---------------------------------------------------------------------------

def send_alert(j: dict, impact: str, category: str, summary: str, scan_date: str):
    watch_tag = " [WATCH-LIST]" if j["watchList"] else ""
    subject   = f"[PW Monitor] {impact} Impact — {j['label']}{watch_tag} — {scan_date}"

    html_body = f"""
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;">
<div style="background:#1F4E79;padding:16px 24px;border-radius:6px 6px 0 0;">
  <span style="color:#fff;font-size:18px;font-weight:bold;">PW Monitor — {impact} Impact Alert</span>
</div>
<div style="border:1px solid #ccc;border-top:none;padding:24px;border-radius:0 0 6px 6px;">
  <table style="width:100%;border-collapse:collapse;margin-bottom:16px;">
    <tr><td style="padding:6px 12px;background:#f2f2f2;font-weight:bold;width:140px;">Jurisdiction</td>
        <td style="padding:6px 12px;border:1px solid #ddd;">{j['label']}{watch_tag}</td></tr>
    <tr><td style="padding:6px 12px;background:#f2f2f2;font-weight:bold;">Impact Level</td>
        <td style="padding:6px 12px;border:1px solid #ddd;color:{'#cc0000' if impact == 'High' else '#b36b00'};font-weight:bold;">{impact}</td></tr>
    <tr><td style="padding:6px 12px;background:#f2f2f2;font-weight:bold;">Category</td>
        <td style="padding:6px 12px;border:1px solid #ddd;">{category}</td></tr>
    <tr><td style="padding:6px 12px;background:#f2f2f2;font-weight:bold;">Detected</td>
        <td style="padding:6px 12px;border:1px solid #ddd;">{scan_date}</td></tr>
    <tr><td style="padding:6px 12px;background:#f2f2f2;font-weight:bold;">Source</td>
        <td style="padding:6px 12px;border:1px solid #ddd;"><a href="{j['url']}">{j['url']}</a></td></tr>
  </table>
  <p style="font-weight:bold;margin-bottom:4px;">AI Summary (verify before acting):</p>
  <div style="background:#fffbe6;border-left:4px solid #f0c040;padding:12px 16px;border-radius:4px;margin-bottom:20px;">
    {summary}
  </div>
  <p style="color:#555;font-size:13px;">This item is now in the <strong>Review Queue</strong>.
  Visit the PW Monitor app to review the source, edit the summary, set the final impact level, and approve or dismiss.</p>
  <p style="color:#888;font-size:12px;margin-top:24px;">
    This alert was sent automatically by PW Monitor v3.2. It does not constitute a legal determination.
  </p>
</div>
</body></html>"""

    text_body = (
        f"PW Monitor — {impact} Impact Alert\n"
        f"Jurisdiction: {j['label']}{watch_tag}\n"
        f"Impact Level: {impact}\n"
        f"Category:     {category}\n"
        f"Detected:     {scan_date}\n"
        f"Source:       {j['url']}\n\n"
        f"AI Summary:\n{summary}\n\n"
        f"Visit the PW Monitor Review Queue to approve or dismiss.\n"
        f"This alert does not constitute a legal determination."
    )

    payload = {
        "from":    {"email": ALERT_FROM, "name": ALERT_FROM_NAME},
        "to":      [{"email": r} for r in ALERT_RECIPIENTS],
        "subject": subject,
        "content": [
            {"type": "text/plain", "value": text_body},
            {"type": "text/html",  "value": html_body},
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    req  = urllib.request.Request(
        SENDGRID_URL, data=data, method="POST",
        headers={
            "Content-Type":  "application/json",
            "Authorization": f"Bearer {SENDGRID_API_KEY}",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            print(f"  Alert sent via SendGrid (HTTP {resp.status}).")
    except urllib.error.HTTPError as e:
        print(f"  SendGrid alert failed (HTTP {e.code}): {e.read().decode()}")
    except Exception as e:
        print(f"  SendGrid alert failed: {e}")

# ---------------------------------------------------------------------------
# Diff logic
# ---------------------------------------------------------------------------

def compute_diff(old_text: str, new_text: str) -> str:
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff      = list(difflib.unified_diff(
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

Determine if this change signals any of the following:
- A new prevailing wage bill introduced or advancing in the legislature
- A prevailing wage law being adopted or enacted
- A ballot initiative related to prevailing wage
- Any official government action moving toward prevailing wage coverage

Page diff:
{diff}

Respond in JSON only. No preamble. No markdown fences. Exact format:
{{
  "relevant": true or false,
  "confidence": "high", "medium", or "low",
  "summary": "2-3 sentence plain-English summary.",
  "impact": "High", "Medium", or "Low",
  "category": "Proposed legislation" or "New state adoption" or "Administrative update" or "Not relevant"
}}"""
    else:
        return f"""You are a prevailing wage compliance analyst monitoring US state labor law.

This is an active prevailing wage jurisdiction: {j['label']}.

Determine if this change relates to any of the following:
- Wage rate changes or new rate determinations
- Coverage expansions or contractions
- New statutes, regulations, or policy guidance
- Effective date changes
- Apprenticeship ratio requirements
- Administrative updates with no legal impact

Page diff:
{diff}

Respond in JSON only. No preamble. No markdown fences. Exact format:
{{
  "relevant": true or false,
  "confidence": "high", "medium", or "low",
  "summary": "2-3 sentence plain-English summary.",
  "impact": "High", "Medium", or "Low",
  "category": "Rate change" or "Coverage expansion" or "Coverage contraction" or "Policy guidance" or "Administrative update" or "Proposed legislation" or "Not relevant"
}}"""


def analyze_with_gemini(j: dict, diff: str) -> dict:
    prompt  = build_prompt(j, diff)
    payload = json.dumps({
        "contents":         [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 512}
    }).encode("utf-8")

    req = urllib.request.Request(
        GEMINI_URL, data=payload, method="POST",
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data     = json.loads(resp.read())
            raw_text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.split("```")[1]
            if raw_text.startswith("json"):
                raw_text = raw_text[4:]
            return json.loads(raw_text.strip())
    except Exception as e:
        print(f"  Gemini error: {e}")
        return {
            "relevant":   True,
            "confidence": "low",
            "summary":    f"Gemini analysis failed ({e}). Page change detected. Review source manually.",
            "impact":     "Medium",
            "category":   "Administrative update"
        }

# ---------------------------------------------------------------------------
# Core scan logic
# ---------------------------------------------------------------------------

def scan_jurisdiction(j: dict, scan_date: str) -> dict:
    jid    = j["id"]
    url    = j["url"]
    result = {"id": jid, "label": j["label"], "url": url, "status": "ok"}

    try:
        html     = fetch_page(url)
        new_text = extract_text(html)
        new_hash = compute_hash(new_text)
    except Exception as e:
        error_msg = str(e)
        print(f"  Fetch error: {error_msg}")
        push_broken_url(j, error_msg, scan_date)
        result["status"] = "fetch_error"
        result["error"]  = error_msg
        return result

    baseline = fb_get(f"baselines/{jid}") or {}
    old_hash = baseline.get("hash", "")
    old_text = baseline.get("text", "")

    if not old_hash:
        fb_put(f"baselines/{jid}", {"hash": new_hash, "text": new_text, "stored_at": scan_date})
        result["status"] = "baseline_stored"
        return result

    if new_hash == old_hash:
        result["status"] = "no_change"
        return result

    diff = compute_diff(old_text, new_text)
    if not diff:
        result["status"] = "no_change"
        return result

    print(f"  Change detected. Analyzing with Gemini...")
    analysis   = analyze_with_gemini(j, diff)
    relevant   = analysis.get("relevant", True)
    summary    = analysis.get("summary",  "Page change detected. Review source manually.")
    impact     = analysis.get("impact",   "Medium")
    category   = analysis.get("category", "Administrative update")
    confidence = analysis.get("confidence", "low")

    if not relevant:
        fb_post("changes", {
            "state": j["label"], "title": f"{j['label']} — Non-PW Page Update {scan_date}",
            "category": "Administrative update", "impact": "Low", "type": "Enacted",
            "summary": summary, "source": url, "date": scan_date, "effectiveDate": "N/A",
            "status": "dismissed", "reviewer": "Auto-scanner (Gemini)",
            "notes": f"Auto-dismissed: not prevailing-wage-relevant (Gemini confidence: {confidence}).",
            "reviewed": scan_date, "isLocal": jid == "DENVER_CO",
            "isWatchList": j["watchList"], "autoDetected": True,
        })
        fb_put(f"baselines/{jid}", {"hash": new_hash, "text": new_text, "stored_at": scan_date})
        result["status"]  = "auto_dismissed"
        result["summary"] = summary
        return result

    fb_post("changes", {
        "state": j["label"], "title": f"{j['label']} — {category} Detected {scan_date}",
        "category": category, "impact": impact, "type": "Enacted",
        "summary": summary, "source": url, "date": scan_date, "effectiveDate": "Pending",
        "status": "pending", "reviewer": "",
        "notes": f"Auto-flagged by scanner. Gemini confidence: {confidence}. Confirm impact and edit summary before approving.",
        "reviewed": None, "isLocal": jid == "DENVER_CO",
        "isWatchList": j["watchList"], "autoDetected": True, "diff": diff,
    })

    fb_put(f"baselines/{jid}", {"hash": new_hash, "text": new_text, "stored_at": scan_date})

    if impact in ALERT_IMPACT_LEVELS:
        print(f"  Sending {impact} impact alert to team...")
        send_alert(j, impact, category, summary, scan_date)

    result["status"]   = "change_detected"
    result["summary"]  = summary
    result["impact"]   = impact
    result["category"] = category
    return result

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    scan_date         = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    all_jurisdictions = JURISDICTIONS + WATCH_LIST
    log               = {"date": scan_date, "results": []}

    print(f"PW Monitor Scanner v3.2 — {scan_date}")
    print(f"Scanning {len(all_jurisdictions)} sources ({len(JURISDICTIONS)} active, {len(WATCH_LIST)} watch-list)...\n")

    for j in all_jurisdictions:
        watch_tag = " [WATCH]" if j["watchList"] else ""
        print(f"  {j['id']}{watch_tag}: {j['url']}")

        result = scan_jurisdiction(j, scan_date)
        log["results"].append({
            "id":      result["id"],
            "status":  result["status"],
            "summary": result.get("summary", ""),
            "error":   result.get("error",   "")
        })

        status_line = f"  -> {result['status']}"
        if result.get("summary"):
            status_line += f": {result['summary'][:80]}"
        print(status_line)

        time.sleep(5)

    fb_post("scan_logs", log)
    changes_found = sum(1 for r in log["results"] if r["status"] == "change_detected")
    fb_put("meta/last_scan", {"date": scan_date, "changes_found": changes_found})

    print(f"\nScan complete. Log and meta/last_scan written to Firebase.")

    changed   = [r for r in log["results"] if r["status"] == "change_detected"]
    dismissed = [r for r in log["results"] if r["status"] == "auto_dismissed"]
    errors    = [r for r in log["results"] if r["status"] == "fetch_error"]

    print(f"\nSummary: {len(changed)} flagged for review, {len(dismissed)} auto-dismissed, {len(errors)} broken URL(s).")

    if changed:
        print("Flagged for review:")
        for r in changed:
            print(f"  {r['id']}: {r.get('summary','')[:80]}")
    if dismissed:
        print("Auto-dismissed (not PW-relevant):")
        for r in dismissed:
            print(f"  {r['id']}: {r.get('summary','')[:80]}")
    if errors:
        print("Broken URLs (pushed to Review Queue):")
        for r in errors:
            print(f"  {r['id']}: {r['error']}")


if __name__ == "__main__":
    main()
