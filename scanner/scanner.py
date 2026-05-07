"""
PW Monitor - Automated Prevailing Wage Scanner
Runs daily at 6:00 AM UTC via GitHub Actions.
Compares page content hashes to detect changes.
Pushes flagged changes to Firebase review queue for human review.
"""

import hashlib
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Firebase config (set in GitHub Secrets)
# ---------------------------------------------------------------------------
FIREBASE_URL    = os.environ["FIREBASE_DATABASE_URL"].rstrip("/")
FIREBASE_SECRET = os.environ["FIREBASE_SECRET"]

# ---------------------------------------------------------------------------
# Active jurisdictions — fully monitored, changes routed to review queue
# ---------------------------------------------------------------------------
ACTIVE_JURISDICTIONS = [
    {
        "id":    "CA",
        "state": "CA",
        "label": "California DIR — Prevailing Wage",
        "url":   "https://www.dir.ca.gov/Public-Works/Prevailing-Wage.html",
    },
    {
        "id":    "NV",
        "state": "NV",
        "label": "Nevada NDOL — Public Works & Prevailing Wages",
        "url":   "https://labor.nv.gov/",
    },
    {
        "id":    "WA",
        "state": "WA",
        "label": "Washington L&I — Prevailing Wage Rates",
        "url":   "https://lni.wa.gov/licensing-permits/public-works-projects/prevailing-wage-rates/",
    },
    {
        "id":    "MA",
        "state": "MA",
        "label": "Massachusetts EOLWD — Prevailing Wage",
        "url":   "https://www.mass.gov/prevailing-wage",
    },
    {
        "id":    "MN",
        "state": "MN",
        "label": "Minnesota DOLI — Prevailing Wage",
        "url":   "https://www.dli.mn.gov/prevailing-wage",
    },
    {
        "id":    "NJ",
        "state": "NJ",
        "label": "New Jersey DOL — Prevailing Wage Rates",
        "url":   "https://www.nj.gov/labor/wageandhour/prevailing-rates/",
    },
    {
        "id":    "NY",
        "state": "NY",
        "label": "New York DOL — Bureau of Public Work",
        "url":   "https://labor.ny.gov/workerprotection/publicwork/PWContents.shtm",
    },
    {
        "id":    "MI",
        "state": "MI",
        "label": "Michigan LEO — Prevailing Wage",
        "url":   "https://www.michigan.gov/leo/bureaus-agencies/ber/wage-and-hour/prevailing-wage",
    },
    {
        "id":    "Denver-CO",
        "state": "CO",
        "label": "Denver Auditor — Prevailing Wage",
        "url":   "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Denver-Labor/Prevailing-Wage",
    },
]

# ---------------------------------------------------------------------------
# Watch-list jurisdictions — no active PW law; monitored for legislative
# activity. Changes flagged for human review and labeled watch-list.
# ---------------------------------------------------------------------------
WATCH_JURISDICTIONS = [
    # Colorado (statewide) — High likelihood; active push to expand beyond
    # the 2021 Quality Apprenticeship Training Act
    {
        "id":    "CO-CDLE",
        "state": "CO",
        "label": "Colorado CDLE — Prevailing Wage Library",
        "url":   "https://cdle.colorado.gov/labor-library-prevailing-wages",
    },
    {
        "id":    "CO-leg",
        "state": "CO",
        "label": "Colorado General Assembly — Bill Search",
        "url":   "https://leg.colorado.gov/bill-search",
    },

    # Virginia — High likelihood; prevailing wage modernization enacted
    # April 2026 (HB 569/SB 518, effective July 1 2026). Monitor for
    # rate publication and further expansion bills.
    {
        "id":    "VA-DOLI",
        "state": "VA",
        "label": "Virginia DOLI — Prevailing Wage",
        "url":   "https://www.doli.virginia.gov/labor-law/prevailing-wage/",
    },
    {
        "id":    "VA-LIS",
        "state": "VA",
        "label": "Virginia Legislative Information System",
        "url":   "https://lis.virginia.gov/",
    },

    # North Carolina — Medium likelihood; HB 412 stalled, advocacy active
    {
        "id":    "NC-leg",
        "state": "NC",
        "label": "North Carolina General Assembly",
        "url":   "https://www.ncleg.gov/Legislation",
    },
    {
        "id":    "NC-DOL",
        "state": "NC",
        "label": "North Carolina DOL",
        "url":   "https://www.nclabor.com/",
    },

    # Arizona — Medium likelihood; ballot initiative activity
    {
        "id":    "AZ-leg",
        "state": "AZ",
        "label": "Arizona Legislature",
        "url":   "https://www.azleg.gov/bills/",
    },
    {
        "id":    "AZ-ICA",
        "state": "AZ",
        "label": "Arizona Industrial Commission",
        "url":   "https://www.azica.gov/",
    },

    # Georgia — Low likelihood; Atlanta exploring local ordinance
    {
        "id":    "GA-leg",
        "state": "GA",
        "label": "Georgia General Assembly",
        "url":   "https://www.legis.ga.gov/",
    },
    {
        "id":    "GA-DOL",
        "state": "GA",
        "label": "Georgia DOL",
        "url":   "https://dol.georgia.gov/",
    },

    # Florida — Low likelihood; law repealed 1979, no active proposals
    {
        "id":    "FL-leg",
        "state": "FL",
        "label": "Florida Legislature",
        "url":   "https://www.myfloridahouse.gov/",
    },

    # Texas — Low likelihood; state preemption limits local action
    {
        "id":    "TX-leg",
        "state": "TX",
        "label": "Texas Legislature",
        "url":   "https://capitol.texas.gov/",
    },
]

# ---------------------------------------------------------------------------
# Aggregate national sources — checked by scanner but reviewed manually.
# These catch new state adoptions before official labor dept pages update.
# ---------------------------------------------------------------------------
AGGREGATE_SOURCES = [
    {
        "id":    "NCSL-PW",
        "state": "NATIONAL",
        "label": "NCSL — Prevailing Wage Laws by State",
        "url":   "https://www.ncsl.org/labor-and-employment/prevailing-wage-laws",
    },
    {
        "id":    "EPI-PW",
        "state": "NATIONAL",
        "label": "Economic Policy Institute — Prevailing Wage Research",
        "url":   "https://www.epi.org/research/prevailing-wage/",
    },
]

ALL_SOURCES = ACTIVE_JURISDICTIONS + WATCH_JURISDICTIONS + AGGREGATE_SOURCES


# ---------------------------------------------------------------------------
# Firebase helpers
# ---------------------------------------------------------------------------

def fb_get(path: str):
    url = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def fb_put(path: str, data):
    url     = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    payload = json.dumps(data).encode()
    req     = urllib.request.Request(url, data=payload, method="PUT",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


def fb_push(path: str, data):
    url     = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    payload = json.dumps(data).encode()
    req     = urllib.request.Request(url, data=payload, method="POST",
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read())


# ---------------------------------------------------------------------------
# Core scanner
# ---------------------------------------------------------------------------

def fetch_hash(url: str) -> str | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "PWMonitor/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
        return hashlib.sha256(content).hexdigest()
    except Exception as e:
        print(f"  ERROR fetching {url}: {e}")
        return None


def scan_source(source: dict) -> dict:
    sid   = source["id"]
    label = source["label"]
    url   = source["url"]

    print(f"Scanning [{sid}] {label}")
    new_hash = fetch_hash(url)

    result = {
        "id":     sid,
        "label":  label,
        "status": "error",
        "url":    url,
    }

    if new_hash is None:
        result["status"] = "fetch_error"
        return result

    stored = fb_get(f"hashes/{sid}")

    if stored is None:
        # First run — establish baseline, do not create a queue item
        fb_put(f"hashes/{sid}", {"hash": new_hash, "first_seen": _now()})
        print(f"  Baseline stored for {sid}")
        result["status"] = "baseline"
        return result

    if stored.get("hash") == new_hash:
        print(f"  No change — {sid}")
        result["status"] = "unchanged"
        return result

    # Page changed — update stored hash and push to review queue
    fb_put(f"hashes/{sid}", {"hash": new_hash, "updated": _now()})

    is_watch   = source in WATCH_JURISDICTIONS
    is_agg     = source in AGGREGATE_SOURCES
    queue_item = {
        "state":          source.get("state", ""),
        "title":          f"{label} — Page Updated {_today()}",
        "summary":        (
            f"Automated scan detected a change on the {label} prevailing wage page. "
            f"Review the source link and summarize what changed before approving. "
            f"Detected: {_today()}."
        ),
        "source_url":     url,
        "category":       "Rate change",
        "status":         "pending",
        "impact_level":   "Medium",
        "effective_date": "Review required",
        "notes":          "Auto-flagged by scanner. Edit this summary before approving.",
        "watch_list":     is_watch,
        "aggregate":      is_agg,
        "detected_at":    _now(),
        "reviewed":       False,
    }
    fb_push("review_queue", queue_item)
    print(f"  CHANGE DETECTED — queued for review: {sid}")
    result["status"] = "changed"
    return result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print(f"PW Monitor scanner starting — {_now()}")
    results = {"date": _today(), "sources": {}}

    for source in ALL_SOURCES:
        outcome = scan_source(source)
        results["sources"][source["id"]] = outcome["status"]

    # Write scan log to Firebase
    fb_push("scan_logs", results)
    print(f"\nScan complete. Results: {json.dumps(results['sources'], indent=2)}")


if __name__ == "__main__":
    main()
