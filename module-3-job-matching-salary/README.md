# Module 3: Job Matching & Salary Intelligence

Finds current Indian job listings for a role and city (Adzuna, JSearch, cached snapshot), matches and ranks them for a candidate, and estimates market and candidate salary ranges with negotiation guidance.

- Spec: [context/MODULE-3-JOB-MATCHING-SALARY.md](../context/MODULE-3-JOB-MATCHING-SALARY.md) · Rules: [context/AGENTS.md](../context/AGENTS.md) · Contracts: [context/INTEGRATION.md](../context/INTEGRATION.md)
- How it works (formulas, worked example, readiness): [WORKING.md](WORKING.md) · JSON Schema: [src/models/schema_m3.json](src/models/schema_m3.json)
- Branch: `feat/module-3-job-matching-salary` · Port: **8003** (module 1: 8001, module 2: 8002)

## Install

Python 3.11, from the repo root:

```bash
pip install -r module-3-job-matching-salary/requirements.txt
```

Settings go in the root `.env` (never committed):

| Variable | Needed for | Default |
|---|---|---|
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Adzuna India (primary) | — |
| `RAPIDAPI_KEY` | JSearch fallback (subscribed to JSearch on RapidAPI; 200 calls/month) | — |
| `LIVE_CACHE_TTL_HOURS` | how long fetched jobs count as fresh | 6 |
| `M2_BASE_URL` | module 2, for job skill extraction | `http://127.0.0.1:8002` |
| `M3_CACHE_PATH` | SQLite cache location | `module-3-job-matching-salary/data/cache/jobs.sqlite` |

No database to set up. Without provider keys, searches use the cached snapshot. Without module 2, skill matching falls back to keywords.

## Run

```bash
cd module-3-job-matching-salary
uvicorn src.api.main:app --port 8003          # Swagger UI: http://localhost:8003/docs
python scripts/prewarm.py --dry-run           # the night before a demo: plan the cache fill
python scripts/prewarm.py                     # fill it; --with-jsearch-salary caches salary estimates (35 JSearch calls),
                                              # --with-jsearch also uses JSearch for jobs (both spend its 200/month quota)
```

Start module 2 (port 8002) as well, so job descriptions get skill ids.

`prewarm.py` always fills Adzuna for all 7 roles × 5 cities. `--roles "A,B"` / `--cities "X,Y"` limit the JSearch calls to those pairs. It prints the most JSearch calls it can make before fetching, and refuses a plan above 20 without `--yes`.

## Demo-day runbook

Commands are PowerShell, from `module-3-job-matching-salary/` with the venv active. JSearch's free plan is 200 calls a month; this plan uses about 41.

**Two nights before:** cache JSearch's salary estimates for every role × city (kept 7 days).
```powershell
python scripts/prewarm.py --with-jsearch-salary --dry-run    # JSearch: at most 35 call(s) ... (0 search + 35 salary ...)
python scripts/prewarm.py --with-jsearch-salary --yes        # 35 JSearch calls, once; Adzuna for all 7x5 as well
```

**Night before:** start module 2, then refresh Adzuna for all 7 × 5 and fetch JSearch full descriptions for the demo pairs only. `--force` is needed because the run two nights before already cached these queries.
```powershell
cd ..\module-2-skill-gap; Start-Process python -ArgumentList "-m","uvicorn","src.api.main:app","--port","8002"; cd ..\module-3-job-matching-salary
python scripts/prewarm.py --roles "Data Scientist,Data Analyst,Backend Developer" --cities "Bengaluru,Pune" --with-jsearch --dry-run
#   JSearch: at most 6 call(s) of the 200/month quota (6 search + 0 salary, over 6 role x city pair(s)).
python scripts/prewarm.py --roles "Data Scientist,Data Analyst,Backend Developer" --cities "Bengaluru,Pune" --with-jsearch --force
```

**Morning of:** keep the night's jobs fresh through the demo, start both services and check them.
```powershell
$env:LIVE_CACHE_TTL_HOURS = "36"          # set in this window before starting module 3 (or put it in .env)
uvicorn src.api.main:app --port 8003      # in another window: module 2 on 8002 as above
Invoke-RestMethod http://127.0.0.1:8002/api/v1/health                 # module 2: status ok
Invoke-RestMethod http://127.0.0.1:8003/api/v1/health                 # module 3: status ok, providers adzuna/jsearch true, cache jobs > 0, salary_estimates >= 35
$r = Invoke-RestMethod http://127.0.0.1:8003/api/v1/jobs/search -Method Post -ContentType application/json `
     -Body '{"target_role":"Data Scientist","location":"Bengaluru","skills":["python","sql","machine learning"],"experience_years":4}'
$r.stale; $r.data_age; $r.sources; $r.market_salary.display    # False, "fetched 9 h ago", adzuna (+ jsearch), a range
$r.market_salary.source_check    # module 1 vs JSearch: both numbers, the gap, and which one is primary
```
`stale` must be `False`, and `data_age` should match last night's run. If `stale` is `True`, Adzuna couldn't be reached: check the network, then re-run the night-before command.

**If the Wi-Fi dies:**
- Pre-warmed role × city pairs searched within `LIVE_CACHE_TTL_HOURS` are served from the cache, so nothing changes (`stale: false`). Salary ranges come from the cached JSearch estimates.
- Older pairs come back from the last snapshot. The response shows `"stale": true` and `"sources": ["snapshot"]`. Each job carries `stale: true` and `age_hours`. `provider_trace` shows `adzuna: error` and `snapshot: used, "14.2 h old"`. `warnings` says *"Live providers unavailable for Bengaluru; showing jobs last fetched 14.2 hours ago."*
- A pair that was never fetched returns no jobs, with the warning *"No live or cached jobs available for …"*. Stick to the pre-warmed pairs.
- What to say: "We're offline, so these are real listings from last night's fetch, labelled with their age. Live, the same call goes to Adzuna and JSearch; the snapshot exists so the product degrades instead of breaking."

## Test

```bash
pytest module-3-job-matching-salary/tests/ -v      # 190 tests (165 v1 + 25 v2); live tests skip without keys / modules 1-2
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/jobs/search` | Ranked current jobs with match breakdowns, market profile, salary, negotiation and skill unlocks |
| POST | `/api/v1/salary/estimate` | Market range for the role and city, and the candidate's estimated range |
| POST | `/api/v1/salary/negotiate` | Target and reasonable minimum for a job (`job_id` from a search) or an offer (`posted_salary_min/max`) |
| GET | `/api/v1/health` | Configured providers, module 2 URL, cache size |
| POST | `/api/v2/jobs/search` | Any occupation: relevant listings matched by module 2, ranked, with unlocks ([v2 below](#v2-any-occupation-post-apiv2jobssearch)) |

Errors: `{"error": {"code": "...", "message": "..."}}`: 422 `INVALID_REQUEST`, 404 `JOB_NOT_FOUND`, 500 `INTERNAL_ERROR`.

### Example: search

Request (the spec's minimal input):

```bash
curl -X POST http://localhost:8003/api/v1/jobs/search -H "Content-Type: application/json" -d '{
  "target_role": "Backend Developer",
  "location": "Bengaluru",
  "skills": ["python", "sql", "docker"],
  "experience_years": 2
}'
```

With module 2's profile instead of `skills`, pass `"profile": <module 2 UserProfile>`. Typed `skills` are resolved through module 2 ("Postgres" becomes `postgresql`). Optional inputs:

| Field | From | Effect |
|---|---|---|
| `gap_analysis` | module 2 | drives which skills the unlocks look at |
| `typical_experience` `{min, max}` | module 1 | experience band for jobs that don't state one |
| `market_salary_percentiles` | module 1 `POST /api/v1/career/analyze`, passed as-is | salary source after posted salaries: the candidate's on-site experience tier (× city ratio when that city has ≥ 50 points), or `remote_inr_lpa` when `work_mode` is `["remote"]`. Checked against JSearch; if they differ by more than 25%, the larger India sample wins (`PREFER_LARGER_INDIA_SAMPLE`) |
| `market_baseline` | module 1 `regional_breakdown.india` | last salary fallback |
| `jsearch_salary: true` | — | fetch JSearch's salary estimate if not cached (spends JSearch quota; cached 7 days) |
| `jsearch_enrichment: true` | — | also query JSearch for full job descriptions (spends JSearch quota) |

Response, abridged from a real search (Data Scientist, Bengaluru, module 2 profile from the fixture resume with 7.5 years, module 1's 3.4–7.4 band, `jsearch_salary: true`):

```json
{
  "target_role": "Data Scientist", "location": "Bengaluru", "total_found": 41, "total_available": 1022,
  "jobs": [
    {"rank": 1, "title": "Data Scientist", "company": "Wesco", "match_score": 82, "classification": "Strong",
     "matched_skills": ["data_analysis", "statistics"], "missing_skills": ["big_data"],
     "skills_confidence": "extracted", "skills_note": null, "rank_score": 0.8491},
    {"rank": 2, "title": "Senior Data Scientist", "company": "Epsilon", "match_score": 82, "classification": "Strong",
     "matched_skills": ["machine_learning", "statistics", "python"], "missing_skills": [],
     "skills_confidence": "inferred",
     "skills_note": "Skills inferred from similar Data Scientist jobs in Bengaluru; the listing's text was too short.",
     "rank_score": 0.8438}
  ],
  "market_salary": {"estimated_min": 1400000, "estimated_median": 2100000, "estimated_max": 2800000, "confidence": 0.7,
                    "sample_size": 466, "sources_used": ["jsearch_salary_estimate"],
                    "display": "Estimated market range: 14-28 LPA",
                    "note": "Estimated, market-based range ... From JSearch's salary estimate (Glassdoor data, 466 salaries)."},
  "candidate_value": {"display": "Your estimated range: 14.4-21.5 LPA", "market_position": 0.25,
                      "reasons": ["Strong skill and profile match (82%)", "Relevant experience (7.5 years, typical 7-9)", "..."]},
  "negotiation": {"posted_salary": null, "market_range": "14-28 LPA", "candidate_range": "14.4-21.5 LPA",
                  "recommended_target": "18 LPA", "reasonable_minimum": "14.5 LPA", "confidence": 0.54},
  "skill_unlocks": [
    {"skill": "llm", "jobs_unlocked": 4,
     "message": "Learning Large Language Models would move 4 more Data Scientist jobs in Bengaluru to a Good or Strong match."},
    {"skill": "rag", "jobs_unlocked": 2,
     "message": "Learning RAG would move 2 more Data Scientist jobs in Bengaluru to a Good or Strong match."}],
  "provider_trace": [{"provider": "adzuna", "city": "Bengaluru", "status": "ok", "jobs": 50}],
  "warnings": ["Merged 9 duplicate listing(s).", "Some listings had too few skills (descriptions are truncated); ..."]
}
```

### Example: negotiate an offer

```bash
curl -X POST http://localhost:8003/api/v1/salary/negotiate -H "Content-Type: application/json" -d '{
  "target_role": "Data Scientist", "location": "Bengaluru", "skills": ["python", "sql", "machine_learning"],
  "experience_years": 4, "posted_salary_min": 1000000, "posted_salary_max": 1200000
}'
```

The response has `negotiation` (`posted_salary`, `market_range`, `candidate_range`, `recommended_target`, `reasonable_minimum`, `confidence`, `reasons`), plus `market_salary`, `candidate_value` and `match_score`. All figures are estimates, not guarantees.

## v2: any occupation (`POST /api/v2/jobs/search`)

v2 searches, matches and ranks listings for **any** occupation. Module 1 resolves the role and judges each listing's title, and module 2's `POST /api/v2/skills/match_texts` matches the whole page against the user's evidence in one call. v1 above is unchanged.

**Needs:**
- module 1 at `M1_BASE_URL` (default `http://127.0.0.1:8001`);
- module 2 at `M2_BASE_URL` (default `http://127.0.0.1:8002`, with `match_texts`);
- Adzuna keys.

Without module 1 it searches the raw phrase with no relevance check. Without module 2 it falls back to keyword matching. Either way the response says so in `module_1_available` / `module_2_available` and `warnings`. Design, thresholds and validation: [WORKING.md §15](WORKING.md).

```bash
curl -X POST http://localhost:8003/api/v2/jobs/search -H "Content-Type: application/json" -d '{
  "target_role": "electrician", "location": "Delhi NCR", "experience_years": 9,
  "free_text": "ITI Electrician, 9 years. Wire new flats: read the drawing, lay conduit, pull cables, fix DB and MCBs, earthing, fault finding with multimeter."
}'
```

Response (trimmed: one job, live data, 7 October 2026):

```json
{"resolution": {"soc_code": "47-2111.00", "title": "Electricians", "confidence": 1.0, "low_confidence": false},
 "queries": ["electrician"], "cities": ["Delhi", "Noida", "Gurugram"],
 "relevance_summary": {"listings": 117, "on_target": 1, "adjacent": 20, "off_target_dropped": 96, "on_target_share": 0.0085},
 "thresholds": {"Strong": 0.29, "Good": 0.12, "Partial": 0.06},
 "jobs": [{"rank": 1, "title": "Electrician - Noida", "company": "Hunarstreet Technologies Pvt Ltd", "location": "Noida",
           "relevance": {"label": "on_target", "reason": "Title contains 'electrician'."},
           "match": {"method": "m2_match_texts", "match_score": 0.3233, "classification": "Strong", "job_text_score": 0.3519,
                     "occupation_score": 0.2804, "match_confidence": "ok",
                     "missing": [{"requirement": "Handle troubleshooting of programmable logic controller",
                                  "requirement_id": "job:…", "item_type": "task", "status": "missing", "score_gain_if_met": 0.0667}]},
           "experience": {"band": [1.0, 4.0], "source": "job_zone", "candidate_years": 9.0, "fit": 0.5},
           "rank_score": 0.5349,
           "rank_components": {"match": 0.3233, "relevance": 1.0, "experience": 0.5, "location": 1.0, "recency": 0.5713}}],
 "unlocks": [],
 "module_1_available": true, "module_2_available": true, "warnings": []}
```

For a beginner (an electrician's helper) the same search returns `unlocks` such as *"Learning Circuit Troubleshooting would move 6 more electrician jobs in Delhi NCR to a good match."*

Request options:
- `soc_code` instead of `target_role`;
- `skills` / `profile` (module 2's `UserProfile`) instead of, or with, `free_text`;
- `preferred_locations`;
- `max_jobs` (default 50);
- `use_jsearch` (default true: JSearch only when Adzuna returns nothing for a city);
- `include_dropped` (list the off-target listings with reasons).

Contract: [src/models/schema_m3_v2.json](src/models/schema_m3_v2.json).

## Layout

```text
module-3-job-matching-salary/
├── scripts/prewarm.py             fill the cache for 7 roles x 5 cities (Adzuna only by default)
├── src/
│   ├── api/                       FastAPI app (port 8003)
│   ├── engines/                   job_fetcher, job_store, locations, m2_client, market_profile,
│   │                              matching, ranking, salary, search
│   ├── models/                    schemas.py (Pydantic) + schema_m3.json
│   └── providers/                 adzuna, jsearch, common normalisation
└── tests/                         mocks/ (recorded Adzuna and JSearch v2 responses), test_*.py
```

Module 3 never imports module 1 or module 2: module 2 is called over REST, and module 1 data arrives as request fields.
