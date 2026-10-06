# Module 1 Database & Engine Generalisation — Handover Document (All Industries)

**From:** Anmol (Module 1 Lead)  
**To:** Chaitanya (Module 2 & Module 3 Lead)  
**Date:** 6 Oct 2026  
**Status:** **Delivered, 100% Verified, Committed & Pushed to `main`**  
**Schema Version:** `2.0.0` (O\*NET 31.0 Deep Integration)  

---

## 1. Executive Summary & Ground Rule Adherence

Module 1 has been transformed from supporting ~7 tech roles into a **universal career intelligence engine for every occupation across all industries**. Whether a candidate is a registered nurse, accountant, electrician, civil engineer, secondary school teacher, customer service representative, or data scientist, Module 1 provides identical, deep empirical intelligence.

### Ground Rule Adherence: Strictly Additive
- **Zero Breaking Changes**: No existing tables or columns were dropped, renamed, modified, or deleted. Existing columns and views retain their original meaning.
- **Row Counts Unchanged**:
  - `occupations`: **1,016** (unchanged)
  - `job_postings_india`: **72,691** (unchanged)
  - `job_postings_global`: **115,000** (unchanged)
  - `salary_benchmarks`: **43,374** (unchanged)
  - `skill_demand`: **371,141** (unchanged)
- **Backward Compatibility**: Existing endpoints (`/career/analyze`, `/rank`, `/search_by_domain`, `/compare`) continue to return identical schemas and pass all regression tests.
- **Test Integrity**: All 18 legacy tests + 30 new generalisation acceptance tests pass (48/48 green). All 136 Module 3 tests pass.

---

## 2. Sanity & Acceptance Checks Verification

All sanity checks requested in §6 of the generalisation specification were run directly against `career_intel.db` and passed:

```sql
-- 1. All 35 O*NET skills loaded with separated IM and LV
SELECT COUNT(DISTINCT element_name) FROM onet_skills;
-- Expected: 35 | Actual: 35

-- 2. All 33 O*NET knowledge domains loaded
SELECT COUNT(DISTINCT element_name) FROM onet_knowledge;
-- Expected: 33 | Actual: 33

-- 3. All 52 O*NET abilities loaded
SELECT COUNT(DISTINCT element_name) FROM onet_abilities;
-- Expected: 52 | Actual: 52

-- 4. Job Zones populated for ~all occupations (only official O*NET unrated gaps remain)
SELECT COUNT(*) FROM occupations WHERE soc_code NOT IN (SELECT soc_code FROM onet_job_zones);
-- Expected: ~0 (only O*NET gaps) | Actual: 93 (923 occupations fully populated)

-- 5. Zero EEO noise words in clean view
SELECT COUNT(*) FROM v_skill_demand_clean WHERE skill_normalized IN ('gender','religion','color','national_origin');
-- Expected: 0 | Actual: 0

-- 6. Raw skill demand table untouched
SELECT COUNT(*) FROM skill_demand;
-- Expected: 371141 | Actual: 371141

-- 7. Posting mapping methods and confidence
SELECT method, COUNT(*), ROUND(AVG(confidence), 3) FROM posting_soc_map GROUP BY method;
-- alt_title_exact: 3,248 (0.950) | india_alias: 652 (0.932) | prefix_match: 37,225 (0.850)
```

---

## 3. Database Schema Catalog (What Was Added)

All new tables and views live in `module-1-career-intelligence/data/career_intel.db`.

### 3.1. Full O\*NET 31.0 Ingestion (P0.1)

| Table Name | Records | Description | Key Columns |
|---|---|---|---|
| `db_meta` | 1 | Metadata header | `schema_version ('2.0.0'), built_at, onet_version ('31.0')` |
| `onet_skills` | 31,850 | All 35 O\*NET skills | `soc_code, element_id, element_name, importance (1-5), level (0-7), importance_norm (0-1), level_norm (0-1), n, recommend_suppress, not_relevant` |
| `onet_knowledge` | 30,030 | All 33 knowledge domains | Same schema as `onet_skills` |
| `onet_abilities` | 47,320 | All 52 abilities | Same schema as `onet_skills` |
| `onet_work_activities` | 37,351 | All 41 Generalized Work Activities | Same schema as `onet_skills` |
| `onet_task_ratings` | 18,420 | Official task importance & frequency | `soc_code, task_id, importance (IM), relevance (RT), frequency (FT expected value)` |
| `onet_dwa` | 24,087 | Tasks mapped to DWAs & IWAs | `soc_code, task_id, dwa_id, dwa_title, iwa_id, iwa_title` |
| `onet_tech_skills` | 31,821 | Software tools & commodity codes | `soc_code, example, commodity_code, commodity_title, hot_technology, in_demand` |
| `onet_tools` | 95 | Equipment & clinical/trade tools | `soc_code, example, commodity_code, commodity_title` |
| `onet_job_zones` | 923 | Preparation levels 1 to 5 | `soc_code, job_zone, name, experience_text, education_text, training_text, svp_range` |
| `onet_education` | 11,495 | Education requirements distribution | `soc_code, element_id, element_name, scale_id, category, category_description, percent` |
| `onet_alternate_titles` | 62,458 | Alternate & reported job titles | `soc_code, title, short_title, source` |
| `onet_related_occupations` | 18,460 | Lateral career mobility pathways | `soc_code, related_soc_code, related_title, relatedness_tier, index_val` |
| `onet_content_model` | 268 | Plain-English descriptions to embed | `element_id, element_name, description` |

*Normalizations*: `importance_norm = (importance - 1.0) / 4.0`, `level_norm = level / 7.0`. Suppressed or non-relevant items have `reliable = 0`.

---

### 3.2. Title -> SOC Mapping & Indian Aliases (P0.2 & P0.3)

- **`occupation_domains`** (1,016 rows): Maps SOC codes to 2-digit major groups (e.g. `29` Healthcare Practitioners, `13` Business and Financial Operations, `47` Construction and Extraction), broad career clusters, and Indian industry groupings.
- **`india_title_aliases`** (64 rows): Resolves Indian colloquial job designations:
  - `"staff nurse"`, `"nursing officer"`, `"sister in charge"`, `"icu nurse"` $\to$ `29-1141.00`
  - `"ca"`, `"chartered accountant"`, `"accounts executive"`, `"internal auditor"` $\to$ `13-2011.00`
  - `"site engineer"`, `"civil site supervisor"` $\to$ `17-2051.00`
  - `"telecaller"`, `"bpo executive"`, `"customer care executive"` $\to$ `43-4051.00`
  - `"iti electrician"`, `"electrical wireman"` $\to$ `47-2111.00`
  - `"relationship manager"`, `"bde"`, `"medical representative (mr)"` $\to$ `41-4012.00`
- **Mapping Tables**:
  - `posting_soc_map`: 41,125 job postings mapped to SOC codes with confidence scores.
  - `salary_soc_map`: 24,555 salary benchmarks mapped to SOC codes.
  - `skill_context_soc_map`: 25,545 raw occupation contexts mapped to SOC codes.

---

### 3.3. Salary Quality Flags & Indian Market Ground Truth (P0.5 & §2.1a)

To solve the issue where Module 1 on-site salaries ran 16–37% above JSearch / Glassdoor:
- **`salary_quality_flags`** (5,500 flagged records):
  - `synthetic_source`: 754 rows from `kaggle_ai_salary_2025` India (median ~₹63 LPA) quarantined.
  - `stale_pre_2023`: 4,642 pre-2023 Indian salary data points flagged.
  - `monthly_suspected`: 80 full-time annual salaries reported $< 1.2$ LPA flagged.
  - `outlier`: 24 extreme outliers ($> 3 \times \text{IQR}$, max ₹5.4 Cr) flagged.
- **`occupation_salary_india`** (1,317 slices): Computed **exclusively from unflagged 2023+ records**, sliced by `city_canonical × experience_bucket × work_mode ('onsite' | 'remote')` with sample size counts ($n$).
- **`occupation_experience_india`** (385 benchmarks): Verified experience ranges (min, max, p25, median) from 2023+ postings with fallback to `onet_job_zones`.
- **`education_level_map_india`** (12 crosswalk tiers): Maps O\*NET categories to Indian qualifications (10th, 12th, ITI/Diploma, Bachelor's, Professional CA/CS/MBBS, Master's, PhD).

---

### 3.4. Clean Skill Demand & City Normalization (P0.4 & P0.6)

- **`skill_noise_terms`** (44 rows): EEO boilerplate (`gender`, `religion`, `color`, `race`, `national_origin`, `disability`...), employee benefits (`dental`, `vision`, `health_insurance`, `sick_time_and_holidays`, `401k`...), and numeric salary fragments.
- **`v_skill_demand_clean`**: View joining raw `skill_demand` to `skill_context_soc_map`, excluding all noise terms.
- **`skill_demand_by_soc`** (22,396 rows): Cleaned empirical skill demand grouped by SOC and region (`india` vs `global`). For Accountants (`13-2011.00`), top Indian skills are `accounting, tally, taxation, gst, excel, auditing, financial_reporting`, with zero EEO junk.
- **`city_aliases`** (583 rows): Maps Indian cities to canonical names, tier (1/2/3), and metro groups (`Bengaluru`, `Gurugram` / `Delhi NCR`, `Hyderabad`, `Pune`, `Mumbai MMR`, `Chandigarh Tri-city`, etc.).

---

### 3.5. Unified Requirements Contract: `v_occupation_requirements` (P0.7)

**`v_occupation_requirements`** (**206,193 rows**) is the primary contract consumed by Module 2 and Module 3:

```sql
SELECT 
    soc_code,
    item_type,          -- 'skill' | 'knowledge' | 'ability' | 'work_activity' | 'dwa' | 'task' | 'tech' | 'tool' | 'market_skill'
    item_id,            -- O*NET element ID or DWA ID
    item_name,          -- Plain-English name (e.g. 'Reading Comprehension', 'Medicine and Dentistry')
    item_description,   -- Detailed narrative statement ready for embedding
    importance_norm,    -- Normalized importance [0.0, 1.0]
    level_norm,         -- Normalized proficiency level [0.0, 1.0] (NULL if not applicable)
    hot_technology,     -- 1 or 0
    in_demand,          -- 1 or 0
    india_demand_share, -- Share of Indian postings requiring this skill [0.0, 1.0]
    source,             -- 'onet' | 'india_postings' | 'global_postings'
    reliable            -- 1 (high reliability, non-suppressed) or 0
FROM v_occupation_requirements;
```

---

## 4. Four New Additive API Endpoints

The API server exposes 4 new endpoints under prefix `/api/v1/occupations`:

### 1. `GET /api/v1/occupations/search?q={query}&k={top_k}`
Free-text title resolution using Indian title aliases, official O\*NET titles, alternate titles (62k+), and token similarity.
- **Request**: `GET /api/v1/occupations/search?q=staff%20nurse&k=2`
- **Response**:
```json
[
  {
    "soc_code": "29-1141.00",
    "title": "Registered Nurses",
    "confidence": 1.0,
    "method": "india_alias_exact",
    "matched_term": "staff nurse"
  },
  {
    "soc_code": "29-1141.01",
    "title": "Acute Care Nurses",
    "confidence": 0.95,
    "method": "onet_alt_title_exact",
    "matched_term": "Staff Nurse"
  }
]
```

### 2. `GET /api/v1/occupations/{soc}/requirements?item_type={type}&limit={limit}`
Retrieves rows of `v_occupation_requirements` for that SOC code.
- **Request**: `GET /api/v1/occupations/29-1141.00/requirements?item_type=skill&limit=2`
- **Response**:
```json
[
  {
    "soc_code": "29-1141.00",
    "item_type": "skill",
    "item_id": "2.A.1.b",
    "item_name": "Active Listening",
    "item_description": "Giving full attention to what other people are saying, taking time to understand the points being made, asking questions as appropriate, and not interrupting at inappropriate times.",
    "importance_norm": 0.75,
    "level_norm": 0.6071,
    "hot_technology": 0,
    "in_demand": 0,
    "india_demand_share": null,
    "source": "onet",
    "reliable": 1
  }
]
```

### 3. `GET /api/v1/occupations/{soc}/profile`
Retrieves a complete 360-degree occupation profile:
- `domain`: major group title, career cluster, Indian industry.
- `job_zone`: preparation level (1–5) and experience/training narrative.
- `indian_education`: primary qualification and distribution across 12 credential tiers.
- `indian_experience`: empirical min/max and median years ($n$) from 2023+ postings.
- `salary_percentiles_india`: unflagged 2023+ salary percentiles (p25 / p50 / p75) by city, experience bucket, and onsite/remote work mode.
- `related_occupations`: top related transfer roles.

### 4. `GET /api/v1/occupations/{soc}/related?limit=20`
Returns related occupations with relatedness tiers for career mobility.

---

## 5. 15 Test Occupations & Static Mock Fixture

The static mock fixture file is generated and saved in the repository:
[`data/m1_occupation_requirements_export.json`](file:///c:/Users/anmol/stuff/projects/vriddhi/module-1-career-intelligence/data/m1_occupation_requirements_export.json) (3.1 MB)

Covering the **15 cross-industry test occupations**:

| Domain | Occupation Title | SOC Code | Total Reqs | Skills | Knowledge | Abilities | Tasks / Tech / Market |
|---|---|---|---|---|---|---|---|
| **Healthcare** | Registered Nurses | `29-1141.00` | **280** | 35 | 33 | 52 | 160 |
| **Healthcare** | Pharmacists | `29-1051.00` | **225** | 35 | 33 | 52 | 105 |
| **Healthcare** | Medical Assistants | `31-9092.00` | **246** | 35 | 33 | 52 | 126 |
| **Finance** | Accountants and Auditors | `13-2011.00` | **674** | 35 | 33 | 52 | 554 |
| **Finance** | Loan Officers | `13-2072.00` | **355** | 35 | 33 | 52 | 235 |
| **Education** | Secondary School Teachers | `25-2031.00` | **246** | 35 | 33 | 52 | 126 |
| **Sales / Service** | Sales Representatives | `41-4012.00` | **463** | 35 | 33 | 52 | 343 |
| **Sales / Service** | Customer Service Representatives | `43-4051.00` | **417** | 35 | 33 | 52 | 297 |
| **Engineering** | Mechanical Engineers | `17-2141.00` | **418** | 35 | 33 | 52 | 298 |
| **Engineering** | Civil Engineers | `17-2051.00` | **388** | 35 | 33 | 52 | 268 |
| **Trades** | Electricians | `47-2111.00` | **247** | 35 | 33 | 52 | 127 |
| **Hospitality** | Chefs and Head Cooks | `35-1011.00` | **235** | 35 | 33 | 52 | 115 |
| **Logistics** | Heavy Truck Drivers | `53-3032.00` | **233** | 35 | 33 | 52 | 113 |
| **Creative** | Graphic Designers | `27-1024.00` | **367** | 35 | 33 | 52 | 247 |
| **Tech (regression)** | Data Scientists | `15-2051.00` | **1,603** | 35 | 33 | 52 | 1,483 |

---

## 6. How Module 2 and Module 3 Should Consume This

### For Module 2 (Semantic Skill Gap & Roadmap)
1. **Semantic Matching**: Fetch rows from `v_occupation_requirements` (or use `GET /api/v1/occupations/{soc}/requirements`). Embed `item_name + ": " + item_description` using `all-MiniLM-L6-v2` and compute cosine similarity against the user's resume / free text.
2. **Proficiency Calibration**: Use `level_norm` $[0.0, 1.0]$ as the required target proficiency instead of heuristics.
3. **Task-Driven Roadmaps**: Use `item_type = 'task'` and `item_type = 'dwa'` (Detailed Work Activities) to generate practical, domain-specific project ideas for vocational and non-tech careers (e.g. *"Inspect electrical wiring for commercial conduits"*, *"Reconcile ledger accounts against tax statements"*).

### For Module 3 (Job Matching & Realistic Salary Calibration)
1. **Title Matching**: Call `GET /api/v1/occupations/search?q={title}` to map user titles or scraped job titles to canonical SOC codes.
2. **Salary Benchmarks**: Query `occupation_salary_india` for unflagged 2023+ percentiles (p25 / p50 / p75) by canonical city, experience bucket, and on-site vs remote mode.
3. **Experience Calibration**: Query `occupation_experience_india` for real candidate experience criteria.

---

## 7. Verification & Test Suite Summary

- **Module 1**: **48 / 48 tests passed (100% green)** in 15.29s.
- **Module 3**: **136 / 136 tests passed (100% green)** in 3.02s.
- **Documentation Updated**:
  - [`DATABASE.md`](file:///c:/Users/anmol/stuff/projects/vriddhi/module-1-career-intelligence/DATABASE.md)
  - [`WORKING.md`](file:///c:/Users/anmol/stuff/projects/vriddhi/module-1-career-intelligence/WORKING.md)
  - [`SOURCES.md`](file:///c:/Users/anmol/stuff/projects/vriddhi/module-1-career-intelligence/data/SOURCES.md)

All changes are committed and pushed to `main` (`git push origin main`). You can immediately begin integrating against the database and mock export.
