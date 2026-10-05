# Module 3: Job Matching & Salary Intelligence

Finds current Indian job listings for a role and city (Adzuna, JSearch, cached snapshot), normalises them into one job shape, and (step 2) matches and ranks them for a candidate and estimates salary ranges and negotiation targets.

- Spec: [context/MODULE-3-JOB-MATCHING-SALARY.md](../context/MODULE-3-JOB-MATCHING-SALARY.md) · Rules: [context/AGENTS.md](../context/AGENTS.md) · Contracts: [context/INTEGRATION.md](../context/INTEGRATION.md)
- How it works: [WORKING.md](WORKING.md)
- Branch: `feat/module-3-job-matching-salary` · Port: **8003** (module 1: 8001, module 2: 8002)
- Status: step 1 (live job data) done; matching, salary and `POST /api/v1/jobs/search` come in step 2.

## Install

Python 3.11, from the repo root:

```bash
pip install -r module-3-job-matching-salary/requirements.txt
```

Keys go in the root `.env` (never committed):

| Variable | Needed for | Default |
|---|---|---|
| `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | Adzuna India (primary) | — |
| `RAPIDAPI_KEY` | JSearch fallback (the key must be subscribed to JSearch on RapidAPI) | — |
| `LIVE_CACHE_TTL_HOURS` | how long fetched jobs count as fresh | 6 |
| `M2_BASE_URL` | module 2, for job skill extraction | `http://127.0.0.1:8002` |
| `M3_CACHE_PATH` | SQLite cache location | `module-3-job-matching-salary/data/cache/jobs.sqlite` |

No database to set up. Without any provider key, searches fall back to the cached snapshot.

## Run

```bash
cd module-3-job-matching-salary
uvicorn src.api.main:app --port 8003        # Swagger UI: http://localhost:8003/docs
python scripts/prewarm.py --dry-run          # before a demo: plan the cache fill (drop --dry-run to run it)
```

Start module 2 (port 8002) as well, so job descriptions get skill ids.

## Test

```bash
pytest module-3-job-matching-salary/tests/ -v
```

Offline tests use recorded Adzuna responses and never touch the network. One live test per provider runs when its key is set.

## Endpoints (step 1)

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/v1/health` | Status, which provider keys are configured, module 2 URL, cache size |

```json
{"status": "ok", "module": "module-3-job-matching-salary", "version": "0.1.0",
 "providers": {"adzuna": true, "jsearch": true}, "m2_base_url": "http://127.0.0.1:8002",
 "cache": {"queries": 1, "jobs": 42}}
```

Errors use `{"error": {"code": "...", "message": "..."}}`.

## A normalised job

From a live Adzuna search (data scientist, Bengaluru) with module 2 running:

```json
{
  "job_id": "adzuna:5898184067",
  "title": "Data Scientist",
  "company": "VY SYSTEMS PRIVATE LIMITED",
  "description": "Support with design and build to prove out agentic AI solution flow ...",
  "description_quality": "ok",
  "location": "Bengaluru",
  "location_raw": "Bangalore, Karnataka",
  "employment_type": "full_time",
  "work_mode": "unknown",
  "experience_min": null,
  "experience_max": null,
  "skills": ["ai", "langchain", "langgraph", "llm", "rag"],
  "skills_source": "m2",
  "salary_min": 400000,
  "salary_max": 2500000,
  "currency": "INR",
  "salary_is_predicted": false,
  "posted_at": "2026-09-25T16:18:18Z",
  "source": "adzuna",
  "publisher": null,
  "source_url": "https://www.adzuna.in/details/5898184067?utm_medium=api&utm_source=...",
  "last_observed_at": "2026-10-05T11:13:10Z",
  "stale": false,
  "age_hours": null
}
```

## Layout

```text
module-3-job-matching-salary/
├── scripts/prewarm.py             fill the cache for 7 roles x 5 cities
├── src/
│   ├── api/                       FastAPI app (port 8003)
│   ├── engines/                   job_fetcher (chain), job_store (SQLite), locations, m2_client
│   ├── models/schemas.py          Job, JobSearchRequest, module 2 profile adapter, module 1 baseline
│   └── providers/                 adzuna, jsearch, common normalisation helpers
└── tests/                         mocks/ (recorded Adzuna, constructed JSearch), test_*.py
```

Module 3 never imports module 1 or module 2: module 2 is called over REST, and module 1 data arrives as request fields.
