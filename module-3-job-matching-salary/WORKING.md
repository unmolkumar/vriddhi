# Module 3: Job Matching & Salary Intelligence — Working Specification

**Branch:** `feat/module-3-job-matching-salary` · **Owner:** Chaitanya Sharma · **API port:** 8003
**Question answered:** *"Which current jobs match me, and what compensation should I reasonably target?"*

Status: complete and integration-ready per context/AGENTS.md §18 (§13 below). Backend only: the UI is built separately on top of this API.

---

## 1. Architecture

```text
 POST /api/v1/jobs/search  {target_role, location | profile.location, module 2 profile or manual skills,
                            typical_experience?, gap_analysis?, market_baseline?, weights?, ...}
        │
        ▼
 job_fetcher.fetch_jobs ─ per city ("Delhi-NCR" → Delhi, Noida, Gurugram):
   fresh SQLite cache (≤ LIVE_CACHE_TTL_HOURS) → Adzuna (5 s) → JSearch (30 s) → stale snapshot (labelled)
   new jobs → module 2 POST /skills/extract (skill ids + maps_to) → saved
   merge cities → drop postings > 45 days → dedupe (title | company | city; JSearch full-text skills win)
        │
        ▼
 employment-type filter → market_profile.build_profile (skill shares) → infer_skills (sparse jobs topped up)
        │
        ▼
 salary.estimate_market: posted salaries → Adzuna histogram → module 1 baseline
        │
        ▼
 matching.match_job (6 weighted components) → ranking (5 factors) → sorted, stable
        │
        ▼
 salary.candidate_value (match + experience) → salary.negotiate (top job) → skill unlocks
        │
        ▼
 JobSearchResponse {jobs[], role_market_profile, market_salary, candidate_value, negotiation,
                    skill_unlocks[], provider_trace[], sources, stale, warnings[]}
```

`POST /api/v1/salary/estimate` and `POST /api/v1/salary/negotiate` run the same pipeline and return the salary part.

Module 3 never imports modules 1 or 2 (AGENTS.md §3/§7). Module 2 is called over REST (`M2_BASE_URL`), and module 1 data arrives as request fields. Storage is a module-local SQLite cache; no MongoDB.

---

## 2. Providers (official APIs only, no scraping)

| Provider | Call | Keys | Timeout | Free quota |
|---|---|---|---|---|
| **Adzuna India** (primary) | `GET api.adzuna.com/v1/api/jobs/in/search/1` (`what`, `where`, `results_per_page=50`, `max_days_old=30`) | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | 5 s | ~250/day |
| **JSearch** (fallback, opt-in enrichment) | `GET jsearch.p.rapidapi.com/search-v2` (`query`, `country=in`, `date_posted=month`) | `RAPIDAPI_KEY` subscribed to JSearch | 30 s | 200/month |
| **Snapshot** | module 3's SQLite of earlier fetches | — | — | — |
| Adzuna histogram (salary) | `GET .../jobs/in/histogram` (`what`, `where`) | Adzuna keys | 5 s | shared |

**Verification:**
- **Adzuna:** search and histogram fields were checked against real responses recorded on 2026-10-05 (`tests/mocks/adzuna_*_sample.json`, tracking ids redacted).
- **JSearch moved to `/search-v2`:** the old `/search` now returns 404 *"Endpoint '/search' does not exist"*. The v2 response (jobs under `data.jobs`, `job_employment_types` codes, currency inside `job_salary_string`) was recorded in `tests/mocks/jsearch_search_v2_sample.json`.
- **JSearch latency:** measured 10–20+ s per call (it runs a Google for Jobs search), hence its own 30 s timeout.

**Quota protection:**
- JSearch is called only when Adzuna fails, or when a search sets `jsearch_enrichment: true`.
- `scripts/prewarm.py` uses no JSearch unless `--with-jsearch` is passed.
- Each failure is reported per city in `provider_trace`: `not_configured`, `not_subscribed`, `timeout`, `error` or `empty`.

---

## 3. Normalised job

Every source maps to the spec's shape: `job_id, title, company, description, location, employment_type, experience_min/max, skills, salary_min/max, currency INR, posted_at, source, source_url`. It also gets `last_observed_at`, `salary_is_predicted`, `publisher` (the underlying site for JSearch), `work_mode`, `description_quality`, `skills_source`, `skill_parents`, `skills_inferred`/`inferred_skills`, and `stale`/`age_hours`.

**Data rules:**
- **Salaries** are INR per year. JSearch monthly pay is × 12; non-INR and hourly figures are dropped. Anything outside ₹50k–₹2 crore is treated as noise.
- **Adzuna-predicted salaries** (`salary_is_predicted: true`) are never shown as the employer's figure or used in estimates.
- **Missing fields** (company, location, salary, dates, skills) stay `null`/empty rather than being guessed.
- **Expiry:** JSearch listings past their expiry and any posting older than 45 days are dropped with a warning.
- **Cities** normalise to the names modules 1 and 2 use (Bengaluru, Gurugram, Mumbai…); `Delhi-NCR` expands to three cities.
- **Dedupe** uses `title | company | city`, ignoring legal and generic suffixes (Pvt Ltd, Private Limited, Inc, India, Technologies, Solutions, Services). A live search returned "Honeywell" and "Honeywell Technologies" for the same role. The kept copy prefers a real posted salary.

---

## 4. Cache, snapshot and freshness

- A (role, city) query younger than `LIVE_CACHE_TTL_HOURS` (default 6) is served from SQLite with no network call.
- When it's older, providers are tried again. If all fail, the old jobs come back as a snapshot with `stale: true`, their `age_hours`, a warning and slightly lower recency in ranking. Stale data is never presented as fresh.
- `scripts/prewarm.py` fills the cache for 7 roles × 5 cities: at most 49 Adzuna calls, and no JSearch unless `--with-jsearch` is passed.

---

## 5. Job skills: extraction, full text, inference

1. **Extraction.** Module 2's `/skills/extract` gives skill ids and their `maps_to` parents for each job's title + description. If module 2 is down: `skills_source: "unavailable"`, a warning, keyword matching for those jobs, and a retry on the next search.
2. **Full text.** Adzuna truncates descriptions to ~500 characters. When the same job also came from JSearch with a full description, its skills replace the Adzuna ones (`skills_source: "jsearch_full"`).
3. **Role market profile.** `share(skill)` = jobs mentioning the skill ÷ jobs with extracted skills, for the role and city. It is returned as `role_market_profile` (useful to module 2 and integration).
4. **Inference.** A job with fewer than `MIN_JOB_SKILLS = 3` of its own skills is topped up to `INFER_FILL_TO = 5` from profile skills with share ≥ `MIN_PROFILE_SHARE = 0.15`. It is marked `skills_inferred: true` with `inferred_skills` listed. Inferred skills count at `INFERRED_SKILL_WEIGHT = 0.5`.

In the live Bengaluru run, 28 of 41 jobs had extracted skills, and 16 were topped up by inference.

---

## 6. Matching (`engines/matching.py`)

### 6.1 Components (each in [0, 1])

| Component | Weight | Rule |
|---|---|---|
| **skills** | 0.40 | Σ w_s · credit_s / Σ w_s over the job's skills. w = 1, or 0.5 if inferred. credit = candidate level → 0.6 (L1), 0.8 (L2), 1.0 (L3+), × 0.5 if module 2 set `needs_verification`. A candidate skill whose `maps_to` is the job skill counts (PostgreSQL covers `sql`). If the candidate only has the job skill's parent, credit is 0.5 × that skill's credit. Jobs with no skills: share of the candidate's top 8 skills found in the job text (`skill_method: "keywords"`). |
| **experience** | 0.20 | Band from the posting (`3-5 years`; `4+` read as 4–7), else the request's `typical_experience` (module 1), else the title (senior 4–8, lead 6–12, …), else neutral 0.7. Recorded as `experience_source: posting / request / title_heuristic / unknown`. Below the band: years ÷ min (the spec's 2 years for "3+" = 0.67). Inside: 1. Above: 1 − (years − max)/10, floor 0.5. |
| **education** | 0.10 | No degree mentioned in the job: 1. Candidate degree ≥ the one mentioned: 1. Lower degree: 0.6. None: 0.4. |
| **location** | 0.10 | Remote job or a preferred/home city: 1. Same region (Noida for a Gurugram candidate): 0.8. Unknown: 0.7. Other city: 0.3. |
| **seniority** | 0.10 | Title level (intern 0 … principal 5, else from the band) vs. candidate level from years; 1 − 0.3 × levels apart. |
| **preference** | 0.10 | Mean over the requested work modes, employment types and minimum salary (unknown job attribute 0.7). With no preferences: 1. |

overall = Σ weight × component. The weights are `MatchWeights` and can be overridden per request (normalised to sum to 1). `match_score` = round(overall × 100).

### 6.2 Classification
Strong ≥ 0.80 · Good ≥ 0.65 · Partial ≥ 0.45 · Weak below (`CLASSIFICATION`).

### 6.3 Worked example (test `test_worked_example_backend_developer`)
The spec's example: the candidate has Python, FastAPI, SQL and Redis at level 3 and Docker at level 2, with 2 years' experience, based in Bengaluru, with a B.Tech. The job is a Backend Developer role in Bengaluru requiring Python, FastAPI, SQL and Docker, with 3–6 years.

| Component | Score | Why |
|---|---|---|
| skills | (1 + 1 + 1 + 0.8) / 4 = **0.95** | Docker at level 2 earns 0.8 |
| experience | 2 / 3 = **0.6667** | below the 3-year minimum |
| education | **1.0** | no degree mentioned |
| location | **1.0** | Bengaluru |
| seniority | **1.0** | untitled job, band min 3 → level 2; 2 years → level 2 |
| preference | **1.0** | none given |

overall = 0.4 × 0.95 + 0.2 × 0.6667 + 0.1 × (1 + 1 + 1 + 1) = 0.38 + 0.1333 + 0.4 = **0.9133** → 91%, **Strong**.

---

## 7. Ranking (`engines/ranking.py`)

rank_score = **0.55** × match + **0.15** × location + **0.10** × experience + **0.10** × salary attractiveness + **0.10** × recency.

- **Salary attractiveness** = 0.5 + (posted midpoint − market median) / (2 × median), clamped to [0, 1]. It is 0.5 (neutral) when there's no posted salary, the salary is Adzuna-predicted, or there's no market median. Salary is a tenth of the score and can't outrank a much better match (tested).
- **Recency:** 1.0 for postings up to 3 days old, falling linearly to 0.2 at 45 days; 0.5 if unknown; × 0.8 for stale snapshot jobs.
- **Order:** rank_score, then match, then newest, then job_id. It is stable, and every job returns its `rank_components`.

---

## 8. Salary intelligence (`engines/salary.py`)

### 8.1 Market range
1. **Posted salaries** of the fetched jobs (the midpoint of each range). Excluded, with counts in `excluded`:
   - Adzuna-predicted figures (`predicted`);
   - ranges with max/min > `MAX_RANGE_RATIO = 4` (`wide_range`), such as the real "₹4L–₹25L" listing;
   - Tukey outliers beyond 1.5 × IQR (`outlier`).

   With ≥ `MIN_POSTED_SAMPLE = 5` left: estimated_min/median/max = P25/P50/P75. Confidence = min(0.85, 0.45 + 0.03 × n), minus 0.1 if the IQR is wider than the median.
2. **Adzuna histogram** for role + city (bucket midpoints weighted by vacancies). Needs ≥ 20 vacancies; P25/P50/P75; confidence 0.5. It is Adzuna's aggregate, so it may include its own predicted salaries.
3. **Module 1 baseline** (`market_baseline.median_salary_inr_lpa`): median ± 25%, confidence 0.3.
4. **Otherwise:** no estimate (`null` fields, confidence 0) and a note listing what was tried.

Figures are rounded half-up to ₹10,000, and display text reads "Estimated market range: 14.3–17.8 LPA". Every estimate carries the note *"Estimated, market-based range … indicative, not a guarantee or an offer"* (AGENTS.md §13).

### 8.2 Candidate market value
adjustment = 1 + 0.20 × (match − 0.70) + 0.08 × experience position, clamped to [0.8, 1.2]. The experience position runs from −1 to +1 across the role band. The match used is the mean of the top 5 ranked jobs, or the specific job's match for negotiation.

The candidate range is [(min + median)/2, (median + max)/2] × adjustment. The reasons cover match strength, experience against the band, and the location and data source.

### 8.3 Negotiation (spec shape)
| Situation | Recommended target | Reasonable minimum |
|---|---|---|
| Posted max ≥ candidate max | candidate max | max(posted min, candidate min) |
| Posted max between candidate mid and max | posted max | max(posted min, candidate min) |
| Posted below candidate mid | candidate mid | max(posted max, candidate min) |
| No posted salary (or only an Adzuna prediction) | candidate mid | candidate min; confidence × 0.85 |

Figures are rounded to 0.5 LPA and returned as text ("16.5 LPA") and INR, with confidence, reasons and the same "not a guarantee" note.

---

## 9. "Unlocks N jobs"

For up to 3 skills the candidate lacks, taken from module 2's `gap_analysis.critical_missing` and `learning_priorities` first, else the role market profile: count the fetched jobs that are Partial or Weak now but would be Good or Strong if the candidate had the skill at level 3. That gives *"Learning AI would move 4 more Data Scientist jobs in Bengaluru to a Good or Strong match."*, sorted by jobs unlocked, with example job ids.

---

## 10. Output dictionary (`JobSearchResponse`)

| Field | Meaning |
|---|---|
| `jobs[]` | Ranked `JobResult`: job fields, `salary {min, max, currency, is_predicted}`, `match_score` (%), `classification`, `match` (components, weights, `skill_method`), `matched_skills`, `missing_skills` (the job's own first, inferred last), `inferred_skills`, `skills_source`, `experience_required`, `experience_source`, `rank`, `rank_score`, `rank_components` |
| `role_market_profile` | `jobs_analysed` and `top_skills[] {skill, share, jobs}` for the role and city |
| `market_salary` | §8.1: `estimated_min/median/max`, `confidence`, `sample_size`, `sources_used`, `excluded`, `display`, `note` |
| `candidate_value` | §8.2: range, `adjustment`, `confidence`, `reasons` |
| `negotiation` | §8.3 for the top-ranked job |
| `skill_unlocks[]` | §9 |
| `provider_trace[]` | per city: provider, status, detail, jobs |
| `total_found` / `total_available` | jobs considered after dedupe and filters / the provider's total count |
| `sources`, `stale`, `warnings` | where jobs came from, whether any are a snapshot, and plain-language notes |

Full JSON Schema: `src/models/schema_m3.json` (regenerate with `python -m src.models.schemas`; a test fails on drift).

---

## 11. API

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/v1/jobs/search` | `JobSearchRequest` | `JobSearchResponse` |
| POST | `/api/v1/salary/estimate` | `JobSearchRequest` fields | market range, candidate value, warnings |
| POST | `/api/v1/salary/negotiate` | search fields + `job_id` (from a search) **or** `posted_salary_min/max` | negotiation, market, candidate value, match score |
| GET | `/api/v1/health` | — | configured providers, module 2 URL, cache size |

Errors: `{"error": {"code", "message"}}`. Invalid input is 422 `INVALID_REQUEST` (e.g. no location anywhere, empty role, max < min experience, all-zero weights); an unknown `job_id` is 404 `JOB_NOT_FOUND`; unexpected errors are 500 `INTERNAL_ERROR`.

---

## 12. Integration contracts

**Inputs**
- `profile`: module 2's `UserProfile`, unchanged (`skills[].name/level/evidence/maps_to/needs_verification`, `experience_years`, `education`, `location`, `preferred_locations`; extra fields ignored). Standalone alternative: `skills` + `experience_years` + `location` (typed skills become id-style slugs at level 2).
- `gap_analysis`: module 2's `match_score`, `verdict`, `learning_priorities`, `critical_missing`. It drives the unlock candidates.
- `typical_experience`: module 1's per-role band (its export now has one per role; e.g. Data Scientist 3.4–7.4).
- `market_baseline`: module 1's `regional_breakdown.india` (`median_salary_inr_lpa`, …). It's the last salary fallback.

**Dependency:** module 2's `POST /api/v1/skills/extract`, on `feat/module-2-skill-gap`, isn't on `main` yet. Until it is, module 3 runs with `skills_source: "unavailable"` and keyword matching.

**Outputs:** `JobSearchResponse` and the salary/negotiation responses above. `role_market_profile` is the live demand signal module 2 or the integration layer can show next to module 1's forecast.

---

## 13. Integration readiness (AGENTS.md §18)

| # | Gate | Status | Evidence |
|---|---|---|---|
| 1 | Self-contained execution | ✅ | Own FastAPI service: `cd module-3-job-matching-salary && uvicorn src.api.main:app --port 8003`. Module-local SQLite, no database to provision; runs without provider keys (snapshot) and without module 2 (keyword matching) |
| 2 | Contract compliance | ✅ | Spec job shape and `/api/v1/jobs/search` input/output; accepts module 2's profile and module 1's baseline and band as plain data; integration error shape; `src/models/schema_m3.json` (drift-tested) |
| 3 | 100% passing tests | ✅ | `pytest module-3-job-matching-salary/tests/ -v` → **113 passed**, including both live provider tests (Adzuna, JSearch). Offline tests fail on any real network call |
| 4 | Error handling | ✅ | Provider timeout, HTTP error, bad JSON, missing key and unsubscribed key → next provider, then snapshot; module 2 down → keyword matching; no salary data → null estimate with a note; invalid input → 422; unknown job → 404; unexpected → 500 without a stack trace |
| 5 | Zero cross-module imports | ✅ | `src/` imports only `src.*` and third-party packages; module 2 via HTTP, module 1 via request fields |
| 6 | Documentation | ✅ | `README.md` (install, keys, run, test, payloads); this file (formulas, worked example, contracts) |
| 7 | Clean git history | ✅ | Conventional Commits with `(module-3)` scope on `feat/module-3-job-matching-salary`, one change per commit |

---

## 14. Tests

```bash
pytest module-3-job-matching-salary/tests/ -v
```

**113 passed** (~15 s, or ~4 s without the two live calls).

| File | Tests | Covers |
|---|---|---|
| `test_providers.py` | 27 | Adzuna recorded normalisation, predicted flag, missing fields, params, errors, histogram; JSearch v2 recorded and constructed normalisation, currency from the salary string, headers, `not_subscribed`; parsing helpers; dedupe key incl. company suffixes |
| `test_fetcher.py` | 18 | fallback chain, cache hit/expiry/force, stale snapshot, nothing available, Delhi-NCR, dedupe, old postings, module 2 down then back, JSearch kept out, opt-in enrichment, full-text skills, maps_to kept |
| `test_locations_api.py` | 15 | city aliases, expansion, provider names, module 2 / manual / baseline inputs, health |
| `test_matching.py` | 25 | worked example, every component, evidence and parents, inferred weight, keyword fallback, experience sources, weights, classification, market profile and inference |
| `test_salary_ranking.py` | 12 | the 4–25 LPA exclusion, predicted salaries, outliers, posted / histogram / baseline / none, candidate value, negotiation (low, generous, none, predicted posted salary), ranking order |
| `test_search_api.py` | 14 | search end to end, unlocks-N-jobs, unlocks from the market profile, employment filter, module 2 down, salary estimate, negotiate (by job, by offer, no salary, unknown job), invalid requests, readable names, schema drift, OpenAPI paths |
| `test_live_providers.py` | 2 | one real call each to Adzuna and JSearch (skipped without keys) |

**Known limitations**
- Adzuna truncates descriptions to ~500 characters, so many jobs need inferred skills (inferred skills are flagged and count half).
- Few Indian listings post a salary (1 usable in 41 in the live Bengaluru run), so estimates often come from Adzuna's histogram, which is wide (5–25 LPA) and may include Adzuna's own predictions. Confidence reflects this (0.5).
- JSearch is slow (10–20+ s) and has 200 calls a month, so it is kept to fallback and opt-in enrichment.
- Manual (non-module-2) skills are slugged, not alias-resolved ("Postgres" won't match `postgresql`); send module 2's profile for exact ids.
