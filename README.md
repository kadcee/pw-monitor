# PW Monitor — Prevailing Wage Intelligence System

An internal compliance tool that monitors prevailing wage laws and policies daily across 9 active jurisdictions. It automatically detects page changes, uses Gemini AI to analyze whether a change is relevant, and routes confirmed changes to a human review queue.

---

## What It Does

- Scans 22 monitored URLs daily via GitHub Actions
- Uses Gemini 2.5 Flash-Lite to analyze detected changes for prevailing wage relevance
- Routes relevant changes to a human review queue
- Auto-dismisses non-relevant changes (page redesigns, navigation updates) with AI rationale recorded
- Sends a weekly digest every Monday summarizing approved changes
- Sends immediate notifications for High impact items on the day of approval
- Maintains a permanent, searchable archive of all approved and dismissed items

---

## Active Jurisdictions (9)

| State / Jurisdiction | Agency |
|---|---|
| California (CA) | DIR — Division of Labor Standards Enforcement |
| Nevada (NV) | Nevada Department of Labor |
| Washington (WA) | L&I — Labor and Industries |
| Massachusetts (MA) | EOLWD |
| Minnesota (MN) | DOLI — Dept of Labor and Industry |
| New Jersey (NJ) | NJDOL — Wage and Hour |
| New York (NY) | NYDOL |
| Michigan (MI) | LARA / LEO |
| Denver, CO (Local) | Denver Auditor — Prevailing Wage Administrator |

---

## Watch-List States (2)

States with repealed prevailing wage laws and active reinstatement bills. Each is scanned at one URL.

| State | Reason |
|---|---|
| Wisconsin (WI) | Repealed 2017. Active reinstatement bills surface regularly. Monitored via DWD. |
| West Virginia (WV) | Repealed 2016. Reinstatement bills surface regularly. |

---

## Aggregate National Sources (3)

Daily automated scan covering all 50 states and federal prevailing wage activity.

| Source | URL |
|---|---|
| NCSL — Labor and Employment | https://www.ncsl.org/labor-and-employment |
| NCSL — In DC (Federal Legislation) | https://www.ncsl.org/in-dc |
| ABC — Prevailing Wage | https://www.abc.org/News-Media/News-Releases |

---

## Infrastructure

| Component | Service |
|---|---|
| Frontend | GitHub Pages (static HTML) |
| Database | Firebase Realtime Database — Spark plan |
| Automated scanner | GitHub Actions — daily Python script at 6:00 AM UTC |
| AI analysis | Gemini 2.5 Flash-Lite (free tier) |
| Authentication | Firebase Authentication — Google Sign-In |

**App URL:** https://kadcee.github.io/pw-monitor

---

## GitHub Secrets Required

The following secrets must be configured in the repository Settings > Secrets and variables > Actions:

| Secret | Description |
|---|---|
| `FIREBASE_DATABASE_URL` | Full Firebase Realtime Database URL (ends in .firebaseio.com) |
| `FIREBASE_SECRET` | Firebase legacy database secret from Project Settings |
| `GEMINI_API_KEY` | Google Gemini API key (free tier at aistudio.google.com) |

---

## Scanner

`scanner.py` runs daily via GitHub Actions. It visits all 22 monitored URLs, extracts visible text, computes a content hash, and compares it to the previously stored baseline in Firebase. If the page changed, it sends the diff to Gemini AI for analysis.

**Current version:** v4.0 (June 2026)

**Scan schedule:** Daily at 6:00 AM UTC (10:00 PM PT / 1:00 AM ET)

**Manual trigger:** Go to Actions tab → PW Monitor Daily Scan → Run Workflow

> **Important:** GitHub automatically disables scheduled workflows after 60 days of repository inactivity. Push a commit at least every 55 days to keep the scanner running. A recurring calendar reminder is maintained for this purpose.

---

## Accepted Risks

| Risk | Status | Notes |
|---|---|---|
| Public GitHub repository | Accepted | Required for GitHub Pages on free personal account. No credentials in the repository. All secrets in encrypted GitHub Secrets. |
| Personal Google account for Firebase | Accepted | Required for Firebase Authentication on free tier. IT adoption will formalize credential management. |
| Free tier infrastructure | Accepted | Total cost is zero. IT adoption will address long-term continuity. |
| Sites that block automated access | Accepted | Some government sites block automated cloud requests. legis.wisconsin.gov is a confirmed example — WI_LABOR is used instead. |

---

## Ownership

**Built and maintained by:** K. Corpuz, Prevailing Wage Supervisor, HBS Americas — Honeywell Building Automation

**Phase 1 daily queue owner:** D. Bhamburkar

**Credential backup:** Firebase config, Firebase secret, Gemini API key, and app URL are stored on NetApp for team access if K. Corpuz is unavailable.

**Long-term plan:** Formal adoption by Honeywell IT on Honeywell-managed infrastructure.

---

## Documentation

Full documentation is maintained separately and available from K. Corpuz:

- **SOP v2.22** — Standard Operating Procedure
- **FAQ v2.7** — Frequently Asked Questions
- **Digest Instructions** — Step-by-step daily queue review and digest distribution guide for D. Bhamburkar

---

*Internal Use Only | Honeywell Building Automation | Contact K. Corpuz for access or questions*
