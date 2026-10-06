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
- **Data age, fresh or not.** Every returned job has `fetched_at` (when its provider was last called for it) and `age_hours`. The search response has `fetched_at` and `age_hours` for the oldest returned job, and `data_age` in words. A 10-hour-old cache hit reads `"fetched 10 h ago"`, not just `stale: false`. Under an hour, it reads in minutes.
- `scripts/prewarm.py` fills the cache for 7 roles × 5 cities: at most 49 Adzuna calls, and no JSearch unless `--with-jsearch` is passed. `--roles`/`--cities` limit the JSearch calls (search and salary) to those pairs. The script prints the most JSearch calls it can make (one per leaf city per pair for search, Delhi-NCR = 3, plus one per pair for salary) and refuses a plan above 20 without `--yes`. The demo-day commands are in the README runbook.

---

## 5. Job skills: extraction, full text, inference

1. **Extraction.** Module 2's `/skills/extract` gives skill ids and their `maps_to` parents for each job's title + description. If module 2 is down: `skills_source: "unavailable"`, a warning, keyword matching for those jobs, and a retry on the next search.
2. **Full text.** Adzuna truncates descriptions to ~500 characters. When the same job also came from JSearch with a full description, its skills replace the Adzuna ones (`skills_source: "jsearch_full"`).
3. **Role market profile.** `share(skill)` = jobs mentioning the skill ÷ jobs with extracted skills, for the role and city. It is returned as `role_market_profile` (useful to module 2 and integration).
4. **Inference.** A job with fewer than `MIN_JOB_SKILLS = 3` of its own skills is topped up to `INFER_FILL_TO = 5` from profile skills with share ≥ `MIN_PROFILE_SHARE = 0.15`. It is marked `skills_inferred: true` with `inferred_skills` listed. Inferred skills count at `INFERRED_SKILL_WEIGHT = 0.5`, and the job's skill score is also penalised (§6.1).
5. **Confidence label.** Each result carries `skills_confidence`: `extracted` (no inferred skills), `partly_inferred` or `inferred` (none of its own). Inferred results get a `skills_note` such as *"Skills inferred from similar Data Scientist jobs in Bengaluru; the listing's text was too short."*
6. **Broad categories.** Module 2's `is_category` flag on each extracted skill is the source of truth. It is stored per job as `skill_is_category`, and `is_broad(skill, flags)` reads it. `BROAD_SKILL_IDS` is the fallback for skills without the flag (jobs cached before module 2 sent it, or an older module 2): `ai`, `data`, `data_science`, `big_data`, `data_engineering`, `generative_ai`, `backend`, `frontend`, `frontend_development`, `full_stack_development`, `web_development`, `mobile_development`, `api_development`, `devops`, `automation`, `cloud`, `software_testing`, `cybersecurity`. Broad categories:
   - stay in the market profile as demand signals;
   - are never inferred into a job;
   - count at half weight in a job's skill list (`BROAD_SKILL_WEIGHT`);
   - always come after concrete skills in `missing_skills`;
   - are never offered as an unlock (§9).
7. **Typed skills.** Without a module 2 profile, typed skills go to module 2's `/skills/extract` as one comma-separated list, so "Postgres" becomes `postgresql` with `maps_to: sql`. Unrecognised names are kept as keywords with a warning. If module 2 is down, all typed skills are matched as keywords, again with a warning.

In the live Bengaluru run, 3 of the top 20 results had fully extracted skills, 5 were partly inferred and 12 inferred.

---

## 6. Matching (`engines/matching.py`)

### 6.1 Components (each in [0, 1])

| Component | Weight | Rule |
|---|---|---|
| **skills** | 0.40 | [Σ w_s · credit_s / Σ w_s] × (1 − `INFERRED_PENALTY` × inferred share) over the job's skills, with `INFERRED_PENALTY = 0.3`.<br>• **w**: 1, × 0.5 if inferred, × 0.5 if a broad category.<br>• **credit**: by candidate level, 0.6 (L1), 0.8 (L2), 1.0 (L3+); × 0.5 if module 2 set `needs_verification`.<br>• A candidate skill whose `maps_to` is the job skill counts (PostgreSQL covers `sql`). If the candidate only has the job skill's parent, credit is 0.5 × that skill's credit.<br>• Jobs with no skills: the share of the candidate's top 8 skills found in the job text (`skill_method: "keywords"`). |
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

### 6.4 Inferred-skill penalty, worked example (test `test_fully_inferred_job_cannot_outrank_a_comparable_job_with_real_skills`)
A candidate with Python and SQL at level 3 looks at two jobs that both list Python, SQL and Spark. In one, all three skills were extracted; in the other, all three were inferred.
- **Extracted:** (1 + 1 + 0) / 3 = **0.667**.
- **Inferred:** the weighted ratio is the same, (0.5 + 0.5 + 0) / 1.5 = 0.667, then × (1 − 0.3 × 3/3) = **0.467**.

The fully inferred job loses 0.08 overall (0.4 × 0.2) and can't outrank a comparable job with real skills.

In the live run, this fix plus `BROAD_SKILL_WEIGHT` moved a real Honeywell listing (which asks for "data science") from a skill score of 0.60 to 0.69. An all-inferred listing dropped from rank 1 to rank 2.

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
2. **Module 1 percentiles.** `market_salary_percentiles` is module 1's object from `POST /api/v1/career/analyze`, passed unchanged. Module 3 never calls module 1. The object has:
   - `overall_inr_lpa`: on-site and hybrid roles;
   - `remote_inr_lpa`: pure remote roles, split out by module 1 in 9aed0f8;
   - `by_experience_inr_lpa` (on-site/hybrid): `entry` <3 years, `mid` 3–5, `senior` >5;
   - `by_city_inr_lpa`: Bengaluru, Hyderabad, Pune, Mumbai, Delhi NCR.

   Each band holds p25/p50/p75 in LPA plus `sample_size`; `overall_usd` is ignored. An older module 1 sends no `remote_inr_lpa` (remote mixed into overall) and is still accepted. The adapter `salary.m1_salary` works as follows:
   - **Remote.** Only when the request's `work_mode` is exactly `["remote"]`: use `remote_inr_lpa` as is (`method: "remote"`, no tier or city scaling) if it has ≥ 30 points. Otherwise, or with an older module 1, fall back to the on-site rules below and say why in the note.
   - **Tier.** Take the candidate's experience tier. If the tier is missing or has fewer than `MIN_PERCENTILE_SAMPLE = 30` points, use `overall_inr_lpa` instead (`method: "overall"`).
   - **City.** When the candidate's city band has at least `MIN_CITY_SAMPLES = 50` points, scale the tier by city p50 ÷ overall p50 (`method: "experience_x_city_ratio"`). Otherwise use the tier alone (`method: "experience_bucket"`). Delhi, Noida and Gurugram map to module 1's "Delhi NCR".
   - **Output.** Convert LPA × 100,000 to INR. Confidence = min(0.85, 0.55 + 0.05 × log10 n), minus `SMALL_SAMPLE_PENALTY = 0.1` below `SMALL_SAMPLE = 100` points.
   - **Cross-check with JSearch.** When a JSearch estimate is also available, `source_check` carries both sources' numbers (module 1 p25/p50/p75 and n, JSearch min/median/max and n), the gap, `agree`, `primary` and `rule`.
     - Within `SALARY_SOURCE_TOLERANCE = 25%`: module 1 stays primary (`rule: "module_1_agrees"`) and the note says JSearch agrees.
     - Beyond it, **`PREFER_LARGER_INDIA_SAMPLE`**: the source with more India-located salaries is primary, using its own range (module 1 on a tie). The other stays in `source_check`, and confidence drops by `DISAGREE_PENALTY = 0.1`. Both sources are India-located: module 1's on-site bands are India postings, and JSearch is queried for the Indian city. The range is never stretched across both. That gave 22–55.9 LPA for a senior Data Scientist, which is too wide to act on.

   Module 1's documented Data Scientist example (test `test_documented_data_scientist_example_small_city_band_is_ignored`) at 4 years in Bengaluru:
   - mid tier 15.0/22.1/30.0 (n = 188);
   - Bengaluru 15.0 has only 39 points, so it is not used: `experience_bucket`, 15–30 LPA, confidence 0.55 + 0.05 × log10 188 = 0.66.

   **Module 1 vs JSearch, real data (6 October 2026).** Module 1 was run locally on its `career_intel.db` (`POST /api/v1/career/analyze`, region india). Its on-site percentiles are compared with the JSearch estimates for the same role, city and experience bucket. Four were already cached or recorded; Data Analyst and Backend Developer took 2 new calls.

   | Role / city / years | Module 1 on-site p25/p50/p75 LPA (n, method) | JSearch min/median/max LPA (n, bucket) | Gap | Primary under the rule | Result |
   |---|---|---|---|---|---|
   | Data Scientist / Bengaluru / 4 | 15.0/21.1/30.0 (107, mid tier) | 12.0/17.7/24.1 (2,070, 4–6 y, recorded) | −16.1% | module 1 (agree) | 15–30, conf 0.65 |
   | Data Engineer / Bengaluru / 4 | 15.9/20.3/25.0 (299, mid × Bengaluru ratio) | 9.9/13.8/20.0 (2,055, 4–6 y) | −31.9% | JSearch | 9.9–20.0, conf 0.60 |
   | Data Analyst / Bengaluru / 4 | 9.0/17.5/24.6 (113, mid tier) | 6.6/11.0/15.5 (683, 4–6 y) | −37.1% | JSearch | 6.6–15.5, conf 0.60 |
   | Backend Developer / Bengaluru / 4 | 15.9/20.0/27.2 (83, mid tier) | 9.5/13.8/22.0 (172, 4–6 y) | −31.0% | JSearch | 9.5–22.0, conf 0.60 |
   | Data Scientist / Bengaluru / 7.5 | 21.8/32.5/58.9 (63, senior tier) | 15.2/22.0/31.7 (466, 7–9 y) | −32.3% | JSearch | 15.2–31.7, conf 0.60 |
   | Data Scientist / Hyderabad / 4 | 15.0/21.1/30.0 (107, mid tier; Hyderabad n = 32) | 9.4/15.5/22.4 (551, 4–6 y) | −26.5% | JSearch | 9.4–22.4, conf 0.60 |

   With remote split out, module 1's on-site medians still run 16–37% above JSearch's. Only Data Scientist at mid level now agrees. So module 1 is primary after posted salaries only where the two agree, or where module 1 has the larger India sample. The senior Data Scientist range went from 22.0–55.9 under the old stretch rule to 15.2–31.7.
3. **JSearch salary estimate** (`GET /estimated-salary`: Glassdoor-backed, INR, with a sample count and its own confidence label) for role × city × experience bucket. Its min/median/max are used as given. Confidence comes from JSearch's label: VERY_HIGH 0.70, HIGH 0.60, MEDIUM 0.50, LOW 0.35.
   - Cached 7 days (`SALARY_CACHE_TTL_DAYS`), keyed by role × city × bucket, falling back to the bucket `ALL`.
   - A fresh call is made only when the request sets `jsearch_salary: true`. Otherwise a cached estimate is used if there is one.
   - `scripts/prewarm.py --with-jsearch-salary` caches the `ALL` bucket for every role × city (35 calls).
4. **Adzuna histogram** for role + city (bucket midpoints weighted by vacancies). Needs ≥ 20 vacancies; P25/P50/P75; confidence 0.5. It is Adzuna's aggregate, so it may include its own predicted salaries.
5. **Module 1 baseline** (`market_baseline.median_salary_inr_lpa`): median ± 25%, confidence 0.3.
6. **Otherwise:** no estimate (`null` fields, confidence 0) and a note listing what was tried.

In the live Bengaluru run (7.5 years, `SEVEN_TO_NINE` bucket), JSearch gave 14–28 LPA from 466 salaries at confidence 0.70. The histogram alone had given 5–25 LPA at 0.50.

Figures are rounded half-up to ₹10,000, and display text reads "Estimated market range: 14.3–17.8 LPA". Every estimate carries the note *"Estimated, market-based range … indicative, not a guarantee or an offer"* (AGENTS.md §13).

### 8.2 Candidate market value
The candidate is placed inside the market range by experience, rather than given the whole distribution:

```
position   = clamp((years − band.min) / (band.max − band.min), 0, 1)      0.5 without a band
adjustment = clamp(1 + 0.20 × (match − 0.70), 0.8, 1.2)                     MATCH_ADJUSTMENT, MATCH_PIVOT
centre     = (market_min + position × (market_max − market_min)) × adjustment
half_width = (market_max − market_min) × (0.15 + 0.35 × (1 − confidence))  NARROW_BASE, NARROW_UNCERTAINTY
range      = centre ± half_width, rounded to ₹10k
```

**Which band:** the one the salary data describes, so experience isn't counted twice:
- Module 1 percentiles use their experience tier: entry 0–3, mid 3–5, senior 5 to `M1_SENIOR_CAP_YEARS = 12` (senior is open-ended in module 1; 12 is our cap for positioning). The remote band covers all experience levels, so it has no tier band, and the role band applies.
- A JSearch estimate uses its bucket (e.g. 7–9 years).
- Otherwise the role band applies (the request's `typical_experience`, else the title).

The match used is the mean of the top 5 ranked jobs, or the specific job's match for negotiation. `market_position` is returned with the range. The range is narrow with good data and widens only when confidence is low.

**Worked example** (test `test_candidate_range_positions_by_experience_and_narrows_with_confidence`). Module 1 percentiles give 14–21 L with 1,200 points, so confidence = 0.55 + 0.05 × log10 1200 ≈ 0.70. A candidate with 5 years in the 3–7 band at a 70% match:
- position = 0.5; adjustment = 1.0;
- centre = 14 + 0.5 × 7 = 17.5 L;
- half-width = 7 × (0.15 + 0.35 × 0.30) ≈ 1.8 L;
- range ≈ **15.7–19.3 LPA**, against 8.5–21.5 LPA from the histogram alone at confidence 0.5.

Live: 7.5 years in JSearch's 7–9 bucket gives position 0.25 and a range of **14.4–21.5 LPA**, inside the 14–28 market. Before this round, the role band put this profile at the top, which gave 25–32 LPA, above the market maximum.

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

1. **Candidates:** skills the candidate lacks, from module 2's `gap_analysis.critical_missing` and `learning_priorities` first, then the role market profile.
2. **Concrete only:** a broad category (`ai`, `data_science`…) is replaced by its most-asked concrete child in this market. The children come from module 2's `maps_to`, up to two levels down (`ai` → `generative_ai` → `llm`). If there's no concrete child, the category is dropped.
3. **Count:** for up to 3 concrete skills, count the fetched jobs that are Partial or Weak now but would be Good or Strong if the candidate had the skill at level 3. Skills that unlock 0 jobs are left out.
4. **Message:** uses module 2's display name, e.g. *"Learning Large Language Models would move 4 more Data Scientist jobs in Bengaluru to a Good or Strong match."* Results are sorted by jobs unlocked, with example job ids.

Live Bengaluru: Large Language Models (4 jobs), RAG (2). Before this round the list was `ai`, `data_science` and a 0-job entry.

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
| `fetched_at`, `age_hours`, `data_age` | when the oldest returned listing was fetched, e.g. "fetched 10 h ago" (each job has `fetched_at` and `age_hours` too) |

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
- `market_salary_percentiles`: module 1's `market_salary_percentiles` object, unchanged (§8.1 item 2). It's the primary market source after posted salaries, cross-checked against JSearch.
- `market_baseline`: module 1's `regional_breakdown.india` (`median_salary_inr_lpa`, …). It's the last salary fallback.
- `jsearch_salary`, `jsearch_enrichment`: opt-in flags that spend JSearch quota.

**Dependency:** module 2's `POST /api/v1/skills/extract` (on `main` since module 2's PR #2) is used for job skills and typed skills. Without it, module 3 falls back to keyword matching with a warning.

**Outputs:** `JobSearchResponse` and the salary/negotiation responses above. `role_market_profile` is the live demand signal module 2 or the integration layer can show next to module 1's forecast.

---

## 13. Integration readiness (AGENTS.md §18)

| # | Gate | Status | Evidence |
|---|---|---|---|
| 1 | Self-contained execution | ✅ | Own FastAPI service: `cd module-3-job-matching-salary && uvicorn src.api.main:app --port 8003`. Module-local SQLite, no database to provision; runs without provider keys (snapshot) and without module 2 (keyword matching) |
| 2 | Contract compliance | ✅ | Spec job shape and `/api/v1/jobs/search` input/output; accepts module 2's profile and module 1's baseline and band as plain data; integration error shape; `src/models/schema_m3.json` (drift-tested) |
| 3 | 100% passing tests | ✅ | `pytest module-3-job-matching-salary/tests/ -v` → **165 passed**, including both live provider tests (Adzuna, JSearch). Offline tests fail on any real network call and never touch the real cache |
| 4 | Error handling | ✅ | Provider timeout, HTTP error, bad JSON, missing key and unsubscribed key → next provider, then snapshot; JSearch salary failure → next salary source with a warning; module 2 down → keyword matching for jobs and typed skills; no salary data → null estimate with a note; invalid input → 422; unknown job → 404; unexpected → 500 without a stack trace |
| 5 | Zero cross-module imports | ✅ | `src/` imports only `src.*` and third-party packages; module 2 via HTTP, module 1 via request fields |
| 6 | Documentation | ✅ | `README.md` (install, keys, run, test, payloads); this file (formulas, worked example, contracts) |
| 7 | Clean git history | ✅ | Conventional Commits with `(module-3)` scope on `feat/module-3-job-matching-salary`, one change per commit |

---

## 14. Tests

```bash
pytest module-3-job-matching-salary/tests/ -v
```

**165 passed** (~20 s, or ~3 s without the two live calls).

| File | Tests | Covers |
|---|---|---|
| `test_providers.py` | 27 | Adzuna recorded normalisation, predicted flag, missing fields, params, errors, histogram; JSearch v2 recorded and constructed normalisation, currency from the salary string, headers, `not_subscribed`; parsing helpers; dedupe key incl. company suffixes |
| `test_fetcher.py` | 18 | fallback chain, cache hit/expiry/force, stale snapshot, nothing available, Delhi-NCR, dedupe, old postings, module 2 down then back, JSearch kept out, opt-in enrichment, full-text skills, maps_to kept |
| `test_locations_api.py` | 15 | city aliases, expansion, provider names, module 2 / manual / baseline inputs, health |
| `test_matching.py` | 25 | worked example, every component, evidence and parents, inferred weight, keyword fallback, experience sources, weights, classification, market profile and inference |
| `test_salary_ranking.py` | 12 | the 4–25 LPA exclusion, predicted salaries, outliers, posted / histogram / baseline / none, candidate value, negotiation (low, generous, none, predicted posted salary), ranking order |
| `test_search_api.py` | 14 | search end to end, unlocks-N-jobs, unlocks from the market profile, employment filter, module 2 down, salary estimate, negotiate (by job, by offer, no salary, unknown job), invalid requests, readable names, schema drift, OpenAPI paths |
| `test_polish.py` | 25 | inferred penalty worked example, skills confidence, broad ids never inferred or first, broad skills at half weight, unlocks without categories or zero counts, typed 'Postgres' via module 2 and keyword fallback, percentiles tightening the range, source order, positioning worked example, experience counted once, JSearch salary normalisation, buckets, opt-in and 7-day cache |
| `test_followups.py` | 16 | module 2's `is_category` stored and preferred, fallback list when absent, matching by the job's flag; module 1 tiers, its documented Data Scientist payload (small Bengaluru band ignored), city ratio and Delhi NCR, small-sample confidence, overall fallback, sources agreeing, and disagreeing under `PREFER_LARGER_INDIA_SAMPLE` either way, end to end via `/salary/estimate`; pre-warm quota plan |
| `test_salary_sources.py` | 11 | module 1's new on-site shape, the remote preference (band used, too small, absent in the old shape), `work_mode` end to end via `/salary/estimate`, the senior cap, data-age text, and `fetched_at`/`age_hours`/`data_age` on a 10-hour-old search |
| `test_live_providers.py` | 2 | one real call each to Adzuna and JSearch (skipped without keys) |

**Known limitations**
- Adzuna truncates descriptions to ~500 characters, so many jobs need inferred skills (inferred skills are flagged and count half).
- Few Indian listings post a salary (1 usable in 41 in the live Bengaluru run). Module 1 percentiles or JSearch's Glassdoor estimate (14–28 LPA at 0.70 live) are much tighter than Adzuna's histogram (5–25 LPA at 0.50); without them, the histogram is used.
- JSearch is slow (10–20+ s) and has 200 calls a month, so it is kept to fallback and opt-in enrichment.
- Typed skills resolve through module 2; if module 2 is down they're matched as keywords ("Postgres" then won't match `postgresql`).
- `BROAD_SKILL_IDS` is only a fallback now; jobs extracted by the current module 2 carry its `is_category` flag.
- Module 1's on-site percentiles run 16–37% above JSearch's in 5 of the 6 real pairs, so JSearch is primary for most demo roles until the two converge.
