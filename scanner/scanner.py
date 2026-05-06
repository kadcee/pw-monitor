"""
PW Monitor — Automated Scanner
Runs via GitHub Actions on a daily schedule.
Checks each jurisdiction's prevailing wage pages for changes.
Pushes new pending items to Firebase when changes are detected.
"""

import hashlib
import json
import os
import time
import urllib.request
import urllib.error
from datetime import date

# ── Firebase config from GitHub Secrets ──
FIREBASE_URL = os.environ["FIREBASE_DATABASE_URL"].rstrip("/")
FIREBASE_SECRET = os.environ["FIREBASE_SECRET"]

# ── Jurisdictions to monitor ──
JURISDICTIONS = [
    {
        "state": "CA",
        "isLocal": False,
        "label": "California DIR",
        "url": "https://www.dir.ca.gov/oprl/pwappwage/MainPage.asp",
        "category": "Rate change",
        "impact": "Medium",
    },
    {
        "state": "NV",
        "isLocal": False,
        "label": "Nevada NDOL",
        "url": "https://labor.nv.gov/Regulations/Prevailing_Wage/Prevailing_Wage/",
        "category": "Rate change",
        "impact": "Medium",
    },
    {
        "state": "WA",
        "isLocal": False,
        "label": "Washington L&I",
        "url": "https://lni.wa.gov/licensing-permits/public-works-projects/prevailing-wage",
        "category": "Administrative update",
        "impact": "Low",
    },
    {
        "state": "MA",
        "isLocal": False,
        "label": "Massachusetts EOLWD",
        "url": "https://www.mass.gov/how-to/determine-the-prevailing-wage-rates-for-a-project",
        "category": "Rate change",
        "impact": "Medium",
    },
    {
        "state": "MN",
        "isLocal": False,
        "label": "Minnesota DOLI",
        "url": "https://www.dli.mn.gov/business/labor-relations/prevailing-wages",
        "category": "Administrative update",
        "impact": "Medium",
    },
    {
        "state": "NJ",
        "isLocal": False,
        "label": "New Jersey DOL",
        "url": "https://www.nj.gov/labor/wagehour/content/prevailing_wage.shtml",
        "category": "Rate change",
        "impact": "Medium",
    },
    {
        "state": "NY",
        "isLocal": False,
        "label": "New York DOL",
        "url": "https://www.labor.ny.gov/workerprotection/laborstandards/wage_hour/wh_prevailing.shtm",
        "category": "Rate change",
        "impact": "Medium",
    },
    {
        "state": "MI",
        "isLocal": False,
        "label": "Michigan LEO",
        "url": "https://www.michigan.gov/leo/bureaus-agencies/ors/prevailing-wage",
        "category": "Administrative update",
        "impact": "Medium",
    },
    {
        "state": "Denver, CO",
        "isLocal": True,
        "label": "Denver Auditor — Prevailing Wage",
        "url": "https://denvergov.org/Government/Agencies-Departments-Offices/Agencies-Departments-Offices-Directory/Auditors-Office/Denver-Labor/Prevailing-Wage",
        "category": "Rate change",
        "impact": "Medium",
    },
    # Watch-list states — legislative tracking pages
    {
        "state": "CO (Watch)",
        "isLocal": False,
        "label": "Colorado Legislature — PW Bills",
        "url": "https://leg.colorado.gov/bills",
        "category": "Proposed legislation",
        "impact": "High",
    },
    {
        "state": "VA (Watch)",
        "isLocal": False,
        "label": "Virginia LIS — PW Bills",
        "url": "https://lis.virginia.gov/",
        "category": "Proposed legislation",
        "impact": "High",
    },
]


def firebase_get(path):
    url = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read().decode())
    except Exception:
        return None


def firebase_put(path, data):
    url = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, method="PUT")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def firebase_post(path, data):
    url = f"{FIREBASE_URL}/{path}.json?auth={FIREBASE_SECRET}"
    payload = json.dumps(data).encode()
    req = urllib.request.Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read().decode())


def fetch_page(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; PWMonitor/1.0; "
            "internal compliance tool; +https://github.com)"
        )
    }
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.read().decode("utf-8", errors="ignore")
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code} for {url}")
        return None
    except Exception as e:
        print(f"  Error fetching {url}: {e}")
        return None


def page_hash(content):
    # Strip common dynamic elements before hashing
    # (timestamps, session tokens, ad slots)
    import re
    content = re.sub(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", "", content)
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL)
    content = re.sub(r"<script[^>]*>.*?</script>", "", content, flags=re.DOTALL)
    content = re.sub(r"<style[^>]*>.*?</style>", "", content, flags=re.DOTALL)
    content = " ".join(content.split())
    return hashlib.sha256(content.encode()).hexdigest()


def safe_key(url):
    return hashlib.md5(url.encode()).hexdigest()


def main():
    today = date.today().isoformat()
    scan_log = {"scanned_at": today, "results": []}
    changes_found = 0

    print(f"PW Monitor Scanner — {today}")
    print(f"Monitoring {len(JURISDICTIONS)} jurisdictions\n")

    for jur in JURISDICTIONS:
        url = jur["url"]
        state = jur["state"]
        print(f"Checking {state}: {url}")

        content = fetch_page(url)
        if content is None:
            print(f"  Skipped (fetch failed)\n")
            scan_log["results"].append({"state": state, "status": "fetch_failed"})
            time.sleep(2)
            continue

        current_hash = page_hash(content)
        hash_key = safe_key(url)

        stored = firebase_get(f"scan_hashes/{hash_key}")
        stored_hash = stored.get("hash") if stored else None

        if stored_hash is None:
            # First time seeing this page — store hash, don't flag
            firebase_put(f"scan_hashes/{hash_key}", {
                "url": url,
                "state": state,
                "hash": current_hash,
                "first_seen": today,
                "last_checked": today,
            })
            print(f"  First scan — baseline stored\n")
            scan_log["results"].append({"state": state, "status": "baseline"})

        elif stored_hash != current_hash:
            # Page changed — push a pending item to review queue
            print(f"  CHANGE DETECTED")
            new_item = {
                "state": state,
                "isLocal": jur["isLocal"],
                "title": f"{jur['label']} — Page Updated {today}",
                "category": jur["category"],
                "impact": jur["impact"],
                "type": "Enacted",
                "summary": (
                    f"Automated scan detected a change on the {jur['label']} prevailing wage page. "
                    f"Review the source link and summarize what changed before approving. "
                    f"Detected: {today}."
                ),
                "source": url,
                "effectiveDate": "Review required",
                "notes": "Auto-flagged by scanner. Edit this summary before approving.",
                "date": today,
                "status": "pending",
                "reviewer": "",
                "reviewed": None,
                "addedBy": "scanner",
                "autoDetected": True,
            }
            firebase_post("changes", new_item)

            # Update stored hash
            firebase_put(f"scan_hashes/{hash_key}", {
                "url": url,
                "state": state,
                "hash": current_hash,
                "first_seen": stored.get("first_seen", today),
                "last_checked": today,
                "last_change": today,
            })

            changes_found += 1
            scan_log["results"].append({"state": state, "status": "changed"})
            print(f"  Pushed to review queue\n")

        else:
            # No change
            firebase_put(f"scan_hashes/{hash_key}", {
                **stored,
                "last_checked": today,
            })
            print(f"  No change\n")
            scan_log["results"].append({"state": state, "status": "no_change"})

        time.sleep(3)  # Be polite to government servers

    # Write scan log to Firebase
    firebase_post("scan_logs", scan_log)

    # Update last_scan timestamp on root
    firebase_put("meta/last_scan", {"date": today, "changes_found": changes_found})

    print(f"\nScan complete. {changes_found} change(s) pushed to review queue.")


if __name__ == "__main__":
    main()
