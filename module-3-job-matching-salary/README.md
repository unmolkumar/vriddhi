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

## Test

```bash
pytest module-3-job-matching-salary/tests/ -v      # 138 tests; the two live provider tests skip without keys
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/jobs/search` | Ranked current jobs with match breakdowns, market profile, salary, negotiation and skill unlocks |
| POST | `/api/v1/salary/estimate` | Market range for the role and city, and the candidate's estimated range |
| POST | `/api/v1/salary/negotiate` | Target and reasonable minimum for a job (`job_id` from a search) or an offer (`posted_salary_min/max`) |
| GET | `/api/v1/health` | Configured providers, module 2 URL, cache size |

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
| `market_salary_percentiles` `{p25, p50, p75, sample_size, experience_band}` | module 1 | primary salary source after posted salaries |
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
