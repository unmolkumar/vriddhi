# Module 3: Job Matching & Salary Intelligence — Working Specification

**Branch:** `feat/module-3-job-matching-salary` · **Owner:** Chaitanya Sharma · **API port:** 8003
**Question answered:** *"Which current jobs match me, and what compensation should I reasonably target?"*

> **Status: step 1 of 2 done.** Live job retrieval is built and tested: providers, normalisation, cache, snapshot fallback, city handling, dedupe, module 2 skill extraction and pre-warm (§1–§7). Matching, ranking, salary intelligence, negotiation and `POST /api/v1/jobs/search` are step 2 (§9). Backend only: the UI is built separately on top of this API.

---

## 1. Architecture

```text
 Search: target_role + location (+ module 2 profile, optional module 1 baseline)
        │
        ▼
 engines/locations.expand_cities        "Delhi-NCR" → Delhi, Noida, Gurugram
        │
        ▼  per city
 engines/job_store (SQLite, module-local)
   fresh (≤ LIVE_CACHE_TTL_HOURS) ──► cache hit, no network
   otherwise ──► providers in order, 5 s timeout each
                   1. providers/adzuna   (Adzuna India, primary)
                   2. providers/jsearch  (JSearch on RapidAPI, fallback)
                 first provider with jobs wins → normalise → module 2 skills → save
   all providers fail ──► last saved jobs as a snapshot, labelled stale + age_hours
        │
        ▼
 merge cities → drop postings older than 45 days → dedupe (title | company | city)
        │
        ▼
 FetchResult {jobs[], sources, attempts[], from_cache, stale, warnings}
        │
        ▼  step 2
 matching → ranking → salary intelligence → negotiation → POST /api/v1/jobs/search
```

Module 3 never imports modules 1 or 2 (context/AGENTS.md §3/§7). It calls module 2 over REST and receives module 1 data as request fields.

---

## 2. Providers (official APIs only, no scraping)

| Provider | Call | Keys (`.env`) | Free quota |
|---|---|---|---|
| **Adzuna India** (primary) | `GET https://api.adzuna.com/v1/api/jobs/in/search/1` with `what`, `where`, `results_per_page=50`, `max_days_old=30` | `ADZUNA_APP_ID`, `ADZUNA_APP_KEY` | ~25/min, 250/day, 2,500/month |
| **JSearch** (fallback) | `GET https://jsearch.p.rapidapi.com/search` with `query="<role> in <city>"`, `country=in`, `date_posted=month`; headers `X-RapidAPI-Key`, `X-RapidAPI-Host` | `RAPIDAPI_KEY` (subscribed to JSearch) | 200/month |
| **Snapshot** (last resort) | module 3's own SQLite of earlier fetches | — | — |
| Adzuna histogram (salary, step 2) | `GET .../jobs/in/histogram` with `what`, `where` → `{histogram: {lower_bound_inr: vacancies}}` | Adzuna keys | shared with search |

**Verification.**
- Adzuna's fields were checked against a real response, recorded on 2026-10-05 in `tests/mocks/adzuna_search_sample.json` (tracking ids redacted), and against the histogram docs.
- The project's RapidAPI key is **not subscribed to JSearch** (HTTP 403 *"You are not subscribed to this API"*). JSearch is coded against its documented fields, and its mock (`tests/mocks/jsearch_search_sample.json`) is labelled as constructed.
- The live JSearch test is skipped until the key is subscribed; the chain reports `not_subscribed` and moves on.

Each failure gets a status the chain and the API can show: `not_configured` (no key), `not_subscribed`, `timeout`, `error` (HTTP error or non-JSON response), `empty` (no jobs: try the next provider).

---

## 3. Normalised job (`src/models/schemas.py::Job`)

Every source maps to the spec's shape, plus fields for freshness and trust:

| Field | Meaning | Adzuna | JSearch |
|---|---|---|---|
| `job_id` | `<source>:<provider id>` | `id` | `job_id` |
| `title`, `company` | — | `title`, `company.display_name` | `job_title`, `employer_name` |
| `description`, `description_quality` | text, and `ok` / `short` (< 200 chars) / `missing` | `description` (**truncated to ~500 chars by Adzuna**) | `job_description` |
| `location`, `location_raw` | normalised city (§4), original text | `location.area[]`, `location.display_name` | `job_city`, `job_state` |
| `employment_type` | `full_time` · `part_time` · `contract` · `internship` · `temporary` · `unknown` | `contract_time`, `contract_type`, "intern" in title | `job_employment_type` |
| `work_mode` | `remote` · `hybrid` · `onsite` · `unknown` | from text | `job_is_remote`, then text |
| `experience_min/max` | years, parsed from title + description (`3-5 years`, `4+ yrs`, `at least 6 years`) | text | text, else `job_required_experience.required_experience_in_months` |
| `skills`, `skills_source` | module 2 skill ids; `m2` / `unavailable` / `none` | module 2 | module 2 |
| `salary_min/max`, `currency` | INR per year; missing stays `null`; outside ₹50k–₹2 crore treated as noise | `salary_min/max` | `job_min/max_salary` × period (YEAR, MONTH×12); non-INR dropped |
| `salary_is_predicted` | **Adzuna's own estimate, never shown as the employer's figure** | `salary_is_predicted == "1"` | `false` |
| `posted_at`, `last_observed_at` | when posted / when we last saw it | `created` | `job_posted_at_datetime_utc` |
| `source`, `publisher`, `source_url` | provider, underlying site (LinkedIn, Naukri…), link | `redirect_url` | `job_publisher`, `job_apply_link` |
| `stale`, `age_hours` | served from the snapshot after the TTL, and how old | — | — |

**Missing data.** Jobs without an id or title are dropped. Missing company, location, salary, dates or skills stay `null`/empty rather than being guessed. Expired JSearch listings (`job_offer_expiration_datetime_utc` in the past) and any posting older than 45 days (`MAX_JOB_AGE_DAYS`) are dropped with a warning.

---

## 4. Cities

`engines/locations.py` normalises to the names modules 1 and 2 use. Bangalore → Bengaluru, Gurgaon → Gurugram, Bombay → Mumbai, New Delhi → Delhi, Secunderabad → Hyderabad, "Work from home" → Remote; unknown cities are title-cased, not dropped. **Delhi NCR** (`Delhi-NCR`, `NCR`, …) expands to Delhi, Noida and Gurugram, one query each, merged. Providers get the names they index (`Bangalore`, `Gurgaon`, `New Delhi`).

## 5. Cache, snapshot and freshness

- Module-local SQLite (`data/cache/jobs.sqlite`, git-ignored; `M3_CACHE_PATH` overrides it). No MongoDB.
- A query (role, city) younger than `LIVE_CACHE_TTL_HOURS` (default 6) is served from cache with no network call.
- Once it is older, providers are tried again. If they all fail, the old jobs are served as a **snapshot** with `stale: true` and `age_hours`, plus a warning. Stale data is never presented as fresh.
- `FetchResult.attempts` lists every step per city: `cache_hit`, provider statuses, `snapshot: used / missing`.

## 6. Dedupe

Key = sha1 of normalised `title | company | city`. Company suffixes like Pvt Ltd, Private Limited and Inc are ignored, so *"VY SYSTEMS PRIVATE LIMITED"* on Adzuna equals *"VY Systems Pvt Ltd"* on JSearch. Among duplicates the kept copy is the one with:
1. a real (not predicted) posted salary;
2. then any salary;
3. then the longer description;
4. then provider order.

Adzuna also repeats its own listings (8 of 50 in a live Bengaluru query were merged).

## 7. Module 2 dependency (job skills)

Each new job's `title + description` is sent to module 2's `POST /api/v1/skills/extract` (`M2_BASE_URL`, default `http://127.0.0.1:8002`, 1 s connect / 5 s read timeout).
- If module 2 is unreachable, jobs are still returned with `skills_source: "unavailable"` and a warning. The rest of that batch isn't retried, so a down module isn't hammered.
- Unavailable jobs are retried on the next search, even from cache, and the stored copy is updated.
- Step 2's matching falls back to title/keyword overlap for those jobs.

Live check: with module 2 running, a real Adzuna job came back with `skills: ["ai", "langchain", "langgraph", "llm", "rag"]`. Some jobs get few or no skills because Adzuna truncates descriptions.

## 8. Pre-warm

```bash
cd module-3-job-matching-salary
python scripts/prewarm.py --dry-run   # plan: 7 roles x 7 cities (Delhi-NCR expanded) = at most 49 Adzuna calls
python scripts/prewarm.py             # fetch what isn't fresh; --force refetches everything
```

Run it the night before a demo:
- Start module 2 first, so skills are extracted.
- Set `LIVE_CACHE_TTL_HOURS` high enough (e.g. 36) that the cache is still fresh on demo day.

The script prints calls per provider against the free quotas.

---

## 9. Step 2 plan (next round)

1. **Matching** per job, with the spec's starting weights as named, configurable constants: skills 40%, experience 20%, education 10%, location 10%, seniority 10%, preferences 10%.
   - The skill match uses module 2 ids, levels and evidence; `maps_to` counts PostgreSQL for SQL.
   - It reports missing skills per job.
   - It falls back to title/keyword overlap when `skills_source` is `unavailable`.
2. **Ranking:** overall match + location preference + experience fit + salary attractiveness + recency. Salary is never the only factor.
3. **Salary intelligence:** estimated range, median and confidence, never false precision. Sources in order:
   - posted salaries of matched jobs, excluding Adzuna-predicted ones;
   - the Adzuna histogram for role + city;
   - module 1's `regional_breakdown.india.median_salary_inr_lpa`, when given.
4. **Candidate market value and negotiation:** posted vs. market vs. candidate range, a suggested target and a reasonable minimum, with reasons. Wording follows AGENTS.md §13 ("estimated", "market-based", never guaranteed).
5. **API:** `POST /api/v1/jobs/search` plus salary and negotiation endpoints, `schema_m3.json`, a full WORKING.md and the readiness checklist.

---

## 10. Integration contracts (inputs)

- **Candidate:** module 2's `UserProfile` as-is (`skills[].name/level/evidence`, `experience_years`, `education`, `location`, `preferred_locations`; extra fields ignored; see module-2-skill-gap/HANDOFF_TO_M3.md). For standalone use, a minimal manual input (`skills`, `experience_years`, `location`) works too.
- **Gap (optional):** module 2's `match_score`, `verdict`, `learning_priorities`, `critical_missing`.
- **Market baseline (optional):** module 1's `regional_breakdown.india` (`median_salary_inr_lpa`, `top_locations`, `top_skills`, `posting_volume`). Module 3 works without it.

---

## 11. Tests

```bash
pytest module-3-job-matching-salary/tests/ -v
```

**55 passed, 1 skipped** (the live JSearch test: key not subscribed). Offline tests fake the provider keys and fail on any real network call. Providers are served through `httpx.MockTransport` from the recorded or constructed samples.

| File | Covers |
|---|---|
| `test_providers.py` | Adzuna normalisation of the recorded response, predicted-salary flag, missing fields, request params, every error status, histogram; JSearch normalisation (yearly/monthly/USD salary, remote, expiry, experience months), headers, `not_subscribed`; experience parsing, salary sanity, work mode, dedupe key |
| `test_fetcher.py` | Adzuna first, cache hit with no network, TTL expiry, force refresh, fallback to JSearch on timeout/500/empty, unconfigured provider, stale snapshot with age, nothing available, Delhi-NCR expansion, cross-provider dedupe, old postings dropped, module 2 down then back |
| `test_locations_api.py` | city aliases and expansion, provider names, module 2 profile / manual / baseline inputs, health |
| `test_live_providers.py` | one real call per provider (skipped without a key or JSearch subscription) |

**Known limitations**
- Adzuna truncates descriptions to ~500 characters, which limits skill and experience extraction.
- Adzuna's `redirect_url` carries the app id as `utm_source` (its attribution scheme; not the secret key). Links keep it; committed mocks redact it.
- JSearch is untested live until the RapidAPI key is subscribed.
