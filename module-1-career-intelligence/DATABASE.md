# `career_intel.db` — Database Architecture & Module 2 Integration Guide

**Database File**: `module-1-career-intelligence/data/career_intel.db`  
**Size**: ~109 MB (Single SQLite3 database file, zero external database servers required)  
**Read Mode**: Safe for concurrent, read-only multi-process access (`PRAGMA query_only = ON`)

---

## 1. Executive Summary & Purpose

Module 2 (Skill Gap & Roadmap Engine) originally had to make assumptions or rely on static taxonomies and hardcoded heuristics (e.g. synthetic rank decay `1 / (1 + 0.15 * i)`, title-based regex experience bands `intern -> 0–2y`, and static course lists) because the full scope of Module 1's empirical dataset was not documented.

This document details:
1. **The complete schema and content of `career_intel.db`** (1,016 O\*NET occupations, 371k+ empirical skill observations, 31k+ tech tools, 18k+ official tasks, and 187k+ live job postings).
2. **Exact SQL recipes and Python patterns** for Module 2 to replace hardcoded assumptions with real labor market evidence.
3. **Canonical skill taxonomy mapping** so that skill extraction, resume parsing, and gap matrices achieve 100% coverage with Module 1.

---

## 2. Database Entity Catalog & Row Counts

| Entity / Table Name | Type | Record Count | Description & Primary Use Case |
|---|---|---|---|
| **`db_meta`** | Table | **1** | Schema version (`2.0.0`), built timestamp, O*NET version (`31.0`). |
| **`occupations`** | Table | **1,016** | **Standard O\*NET 31.0 occupations** with SOC codes, standardized titles, comprehensive descriptions, job zones, and typical education requirements. |
| **`occupation_domains`** | Table | **1,016** | SOC 2-digit major groups, broad career clusters, and Indian industry classifications. |
| **`onet_skills`** | Table | **31,850** | All 35 O\*NET skills with separate `importance` (1–5) and `level` (0–7), normalized $[0, 1]$, and suppression flags. |
| **`onet_knowledge`** | Table | **30,030** | All 33 O\*NET knowledge domains (e.g. Medicine, Economics & Accounting, Mechanical, Law) with IM and LV scores. |
| **`onet_abilities`** | Table | **47,320** | All 52 cognitive, physical, and sensory abilities required per occupation. |
| **`onet_work_activities`** | Table | **37,351** | All 41 generalized work activities (GWA) with importance and level. |
| **`onet_task_ratings`** | Table | **18,420** | Criticality ratings for tasks: importance (IM), relevance (RT), and frequency (FT). |
| **`onet_dwa`** | Table | **24,087** | Granular Detailed Work Activities (DWA) and Intermediate Work Activities (IWA) linked to tasks. |
| **`onet_tech_skills`** | Table | **31,821** | Software tools, commodity codes, commodity titles, and hot/in-demand technology indicators. |
| **`onet_tools`** | Table | **95** | Equipment and specialized tools used across trades and clinical professions. |
| **`onet_job_zones`** | Table | **923** | O\*NET Job Zones 1–5 with full experience, education, and vocational training narratives. |
| **`onet_education`** | Table | **11,495** | Empirical education, training, and experience requirements distribution across 12 credential tiers. |
| **`onet_alternate_titles`** | Table | **62,458** | Alternate titles and reported job titles for robust NLP and semantic title matching. |
| **`onet_related_occupations`** | Table | **18,460** | Lateral career mobility pathways and related occupation mappings. |
| **`onet_content_model`** | Table | **268** | Plain-English definitions and descriptions of every O\*NET skill, knowledge, ability, and activity element (ready to embed). |
| **`city_aliases`** | Table | **583** | Indian city normalization collapsing colloquial names to canonical metros, tiers (1/2/3), and metro groups (e.g. Delhi NCR). |
| **`india_title_aliases`** | Table | **64** | Indian colloquial job titles (e.g. *staff nurse*, *CA*, *site engineer*, *telecaller*, *ITI electrician*) mapped to SOC codes. |
| **`posting_soc_map`** | Table | **41,125** | High-precision mapping from Indian & global job postings to standard SOC codes with confidence scores. |
| **`salary_soc_map`** | Table | **24,555** | Mapping from salary benchmarks to standard SOC codes. |
| **`skill_context_soc_map`** | Table | **25,545** | Mapping from raw posting occupation contexts to standard SOC codes. |
| **`salary_quality_flags`** | Table | **5,500** | Quality flags isolating synthetic data (Kaggle AI India), stale pre-2023 records, monthly pay confusion, and IQR outliers. |
| **`skill_noise_terms`** | Table | **44** | EEO boilerplate (gender, religion, color...), employee benefits (dental, vision...), and numeric junk fragments. |
| **`v_skill_demand_clean`** | View | **Clean** | `skill_demand` minus noise terms, joined to `skill_context_soc_map` for pristine SOC-level skill intelligence. |
| **`skill_demand_by_soc`** | Table | **22,396** | Cleaned empirical skill demand grouped by SOC code and region (`india` vs `global`). |
| **`education_level_map_india`** | Table | **12** | Crosswalk mapping O\*NET education categories to Indian qualifications (10th, 12th, ITI/Diploma, Bachelor's, CA/MBBS, Master's, PhD). |
| **`occupation_experience_india`** | Table | **385** | Empirical Indian min/max experience distributions with sample sizes ($n$) for 2023+ postings. |
| **`occupation_salary_india`** | Table | **1,317** | Unflagged 2023+ Indian salary percentiles (p25 / p50 / p75) sliced by canonical city, experience bucket, and onsite/remote work mode. |
| **`v_occupation_requirements`** | View | **206,193** | **Unified contract view** providing all skills, knowledge, abilities, tasks, DWAs, tech, and market skills per SOC for M2 & M3. |
| **`job_postings_india`** | Table | **72,691** | Real Indian postings (Naukri, PromptCloud) with titles, companies, cities, INR salaries, and experience bands (UNTOUCHED). |
| **`job_postings_global`** | Table | **115,000** | Global postings (LinkedIn) with standardized titles and salary signals (UNTOUCHED). |
| **`salary_benchmarks`** | Table | **43,374** | Raw normalized salary records (UNTOUCHED). |
| **`skill_demand`** | Table | **371,141** | Raw skill frequency observations across regions (UNTOUCHED). |
| **`occupation_skills`** | Table | **18,200** | Legacy O\*NET basic skills table (UNTOUCHED). |
| **`occupation_tech`** | Table | **31,821** | Legacy O\*NET technology table (UNTOUCHED). |
| **`occupation_tasks`** | Table | **18,838** | Legacy O\*NET tasks table (UNTOUCHED). |
| **`v_skill_comparison`** | View | **13,458** | Legacy regional skill comparison view (UNTOUCHED). |
| **`v_india_vs_global`** | View | **8** | Legacy macro comparison view (UNTOUCHED). |

---

## 3. Schema Reference & Column Definitions

### 3.1. `skill_demand` (Core Table for Skill Extraction & Weights)
Contains 371k+ empirical occurrences of technical skills and tools parsed directly from real job descriptions.
```sql
CREATE TABLE skill_demand (
    id INTEGER PRIMARY KEY,
    skill_name TEXT NOT NULL,          -- Raw extracted skill token (e.g. "Python", "SQL", "Machine Learning")
    skill_normalized TEXT NOT NULL,    -- Canonical lowercase snake_case ID (e.g. "python", "sql", "machine_learning")
    region TEXT NOT NULL,              -- 'india' or 'global'
    source TEXT NOT NULL,              -- Data source (e.g. 'naukri', 'linkedin')
    year INTEGER,                      -- Observation year (2021-2026)
    month INTEGER,                     -- Observation month (1-12)
    frequency INTEGER,                 -- Occurrence count in that batch
    occupation_context TEXT            -- Job title / domain context (e.g. "data scientist", "data engineer")
);
```

### 3.2. `occupations` (Standard Roles Taxonomy)
```sql
CREATE TABLE occupations (
    soc_code TEXT PRIMARY KEY,         -- Standard O*NET code (e.g. "15-2051.00", "15-1252.00")
    title TEXT NOT NULL,               -- Standard occupation title (e.g. "Data Scientists")
    description TEXT,                  -- Comprehensive description of role responsibilities
    onet_title TEXT,                   -- Canonical O*NET label
    domain TEXT,                       -- Broader domain (e.g. "Information Technology", "Analytics")
    job_zone INTEGER,                  -- O*NET Job Zone 1 to 5 (educational and experience requirement depth)
    education_typical TEXT,            -- e.g. "Bachelor's Degree", "Master's Degree"
    created_at TEXT
);
```

### 3.3. `occupation_skills` (Proficiency & Importance Baselines)
Provides scientific proficiency targets for each skill, eliminating guesswork on whether a candidate needs "basic awareness" vs "expert proficiency".
```sql
CREATE TABLE occupation_skills (
    id INTEGER PRIMARY KEY,
    soc_code TEXT NOT NULL,            -- References occupations(soc_code)
    skill_name TEXT NOT NULL,          -- e.g. "Mathematics", "Programming", "Critical Thinking"
    skill_category TEXT,               -- e.g. "Content", "Process", "Technical Skills"
    importance REAL,                   -- O*NET Importance score (1.0 to 5.0)
    level REAL                         -- Required proficiency Level (1.0 to 7.0)
);
```

### 3.4. `occupation_tech` (Hot & In-Demand Tools)
Over 31,000 tool mappings with industry demand flags.
```sql
CREATE TABLE occupation_tech (
    id INTEGER PRIMARY KEY,
    soc_code TEXT NOT NULL,            -- References occupations(soc_code)
    technology_name TEXT NOT NULL,     -- e.g. "Amazon Web Services AWS", "Apache Spark", "Docker"
    hot_technology INTEGER,            -- 1 if designated as an O*NET Hot Technology, 0 otherwise
    in_demand INTEGER                  -- 1 if high recruitment velocity in job postings, 0 otherwise
);
```

### 3.5. `occupation_tasks` (Real-World Work Activities)
Enables Module 2 to generate project-based learning roadmaps grounded in actual tasks that candidates will perform on the job.
```sql
CREATE TABLE occupation_tasks (
    id INTEGER PRIMARY KEY,
    soc_code TEXT NOT NULL,            -- References occupations(soc_code)
    task_id TEXT,                      -- O*NET task identifier
    task_description TEXT NOT NULL,    -- Official task statement (e.g. "Develop ML algorithms...")
    task_type TEXT,                    -- 'Core' (essential daily work) or 'Supplemental'
    importance REAL,                   -- Task criticality score
    relevance REAL,
    frequency REAL
);
```

### 3.6. `job_postings_india` (Experience & Compensation Ground Truth)
72,691 Indian tech listings to calibrate realistic seniority bands and geographic demands.
```sql
CREATE TABLE job_postings_india (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,              -- e.g. 'naukri'
    job_id TEXT,
    title TEXT NOT NULL,               -- e.g. "Senior Data Scientist"
    title_normalized TEXT,             -- e.g. "senior data scientist"
    company TEXT,
    location TEXT,
    city TEXT,                         -- e.g. "Bengaluru", "Hyderabad", "Pune", "Remote"
    soc_code_mapped TEXT,
    salary_min_inr REAL,               -- Annual salary in INR
    salary_max_inr REAL,               -- Annual salary in INR
    salary_min_usd REAL,
    salary_max_usd REAL,
    experience_min REAL,               -- Minimum required experience in years (e.g. 3.0)
    experience_max REAL,               -- Maximum experience in years (e.g. 7.0)
    skills TEXT,                       -- Raw comma-separated skill string
    industry TEXT,
    listed_date TEXT,
    listed_year INTEGER,
    listed_month INTEGER,
    region TEXT                        -- 'india'
);
```

---

## 4. How Module 2 Should Leverage This Data

### 4.1. Replacing Hardcoded Required Skills with Market Demand
**Problem in Module 2**: Static lists or hardcoded dictionaries can become outdated and may miss emerging tools.  
**Solution**: Query `skill_demand` for the candidate's target role to get real-time market frequency:

```sql
SELECT 
    skill_normalized, 
    SUM(frequency) AS market_mentions
FROM skill_demand
WHERE (occupation_context LIKE '%data engineer%' OR 'data engineer' LIKE '%' || occupation_context || '%')
GROUP BY skill_normalized
HAVING skill_normalized NOT IN ('data', 'engineering', 'software', 'coding', 'development')
ORDER BY market_mentions DESC
LIMIT 10;
```
*Result for Data Engineer*:
`sql` (1,437), `python` (1,420), `data_modeling` (675), `aws` (656), `spark` (562), `etl` (420), `kafka` (380).

---

### 4.2. Replacing Synthetic Rank Decay with Empirical Demand Weights
**Problem in Module 2**: Using an arbitrary rank decay formula like $w_i = \frac{1}{1 + 0.15 \cdot i}$ treats the 1st, 2nd, and 3rd skills identically across all jobs, regardless of whether skill #1 is mentioned in 95% of postings while skill #2 is only in 30%.  
**Solution**: Normalize market frequency directly into $[0.0, 1.0]$:
$$w_s = \frac{\text{freq}(s)}{\max_{s'} \text{freq}(s')}$$

```python
def get_empirical_skill_weights(db_conn, target_role: str) -> dict[str, float]:
    cur = db_conn.cursor()
    cur.execute("""
        SELECT skill_normalized, SUM(frequency) as freq
        FROM skill_demand
        WHERE occupation_context LIKE ?
        GROUP BY skill_normalized
        ORDER BY freq DESC
        LIMIT 15
    """, (f"%{target_role.lower()}%",))
    rows = cur.fetchall()
    if not rows:
        return {}
    max_freq = rows[0][1]
    return {skill: round(freq / max_freq, 4) for skill, freq in rows}
```
*Why this matters*: A candidate missing Python ($w = 1.0$) will rightfully suffer a larger match penalty than a candidate missing an optional secondary tool like Redis ($w = 0.25$).

---

### 4.3. Replacing Seniority Regex with Empirical Experience Bands
**Problem in Module 2**: Guessing experience based on keywords (`intern -> 0-2`, `senior -> 4-8`, `lead -> 6-12`) is brittle. In tech, a "Data Scientist" role typically asks for 3.4–7.4 years, while a "Full Stack Developer" asks for 3.2–5.8 years.  
**Solution**: Query `job_postings_india` for the actual average minimum and maximum years requested by employers:

```sql
SELECT 
    ROUND(AVG(experience_min), 1) AS typical_min_years,
    ROUND(AVG(experience_max), 1) AS typical_max_years,
    COUNT(*) AS sample_size
FROM job_postings_india
WHERE title_normalized LIKE '%data scientist%'
  AND experience_min IS NOT NULL;
```
*Result*: `typical_min_years = 3.4`, `typical_max_years = 7.4` ($n = 2,410$).

---

### 4.4. Enriching Roadmaps with "Hot Technologies" & Real Tasks
**Problem in Module 2**: Recommending generic advice like *"Learn SQL"* or *"Learn Machine Learning"* doesn't tell the user *which* specific stack to build with or *what* tasks they will face in an interview.  
**Solution**:
1. Check `occupation_tech` where `hot_technology = 1` to highlight high-priority tools.
2. Query `occupation_tasks` to give the candidate concrete, portfolio-grade project ideas:

```sql
-- Find Hot Technologies for Data Scientists
SELECT t.technology_name, t.hot_technology, t.in_demand
FROM occupation_tech t
JOIN occupations o ON t.soc_code = o.soc_code
WHERE LOWER(o.title) LIKE '%data scientist%'
  AND t.hot_technology = 1;

-- Find Core Daily Tasks for Data Scientists
SELECT t.task_description
FROM occupation_tasks t
JOIN occupations o ON t.soc_code = o.soc_code
WHERE LOWER(o.title) LIKE '%data scientist%'
  AND t.task_type = 'Core'
LIMIT 5;
```
*Output in Roadmap*:
- *"Hot Tech to Prioritize"*: AWS SageMaker, Apache Spark, Docker.
- *"Portfolio Project Idea"*: *"Analyze, manipulate, or process large sets of data using statistical software; test and validate predictive models to ensure accurate forecasting."* (Derived directly from O\*NET Task #1 and #3).

---

### 4.5. Dynamic Skill Normalization / Taxonomy Alignment
To guarantee 100% interoperability between Module 1 and Module 2, use the standard normalization rule in `career_intel.db`:
- Lowercase all characters.
- Replace non-alphanumeric characters and hyphens with underscores.
- Strip consecutive underscores.
- **Single-Letter Language Exception**: Preserve `'c'` and `'r'`.

```python
import re

def normalize_skill_id(raw_name: str) -> str:
    cleaned = raw_name.strip().lower()
    if cleaned in ('c', 'r'):
        return cleaned
    cleaned = re.sub(r'[^a-z0-9]+', '_', cleaned)
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    return cleaned
```

---

## 5. Reference Profile for the 7 Core Target Roles

Here are the pre-computed findings directly from `career_intel.db` for the 7 primary roles supported across the Vriddhi platform:

| Role Title | O\*NET SOC Code | Top Required Skills (`top_skills`) | Empirical Experience (`typical_experience`) | Top Hot Tech (`occupation_tech`) |
|---|---|---|---|---|
| **Data Scientist** | `15-2051.00` | `machine_learning`, `python`, `sql`, `deep_learning`, `data_analysis`, `statistics` | **3.4 – 7.4 yrs** | AWS SageMaker, Redshift, PyTorch, Scikit-learn |
| **Data Engineer** | `15-1252.00` | `sql`, `python`, `data_modeling`, `aws`, `data_quality`, `pyspark` | **4.2 – 8.0 yrs** | Apache Spark, Kafka, Airflow, Snowflake, AWS Glue |
| **Data Analyst** | `15-2051.01` | `data_analysis`, `sql`, `power_bi`, `python`, `excel`, `data_visualization` | **2.9 – 6.0 yrs** | Microsoft Power BI, Tableau, Advanced Excel, SQL |
| **Machine Learning Engineer** | `15-2051.00` | `machine_learning`, `python`, `tensorflow`, `aws`, `pytorch`, `docker` | **3.2 – 6.2 yrs** | TensorFlow, PyTorch, Kubernetes, MLflow, Docker |
| **Backend Developer** | `15-1252.00` | `redis`, `mongodb`, `nodejs`, `python`, `java`, `fastapi` | **3.2 – 5.1 yrs** | Redis, PostgreSQL, Docker, Spring Boot, Node.js |
| **Full Stack Developer** | `15-1252.00` | `postgresql`, `react`, `python`, `mongodb`, `docker`, `aws` | **3.2 – 5.8 yrs** | React, Next.js, PostgreSQL, Node.js, Docker, AWS |
| **DevOps Engineer** | `15-1244.00` | `linux`, `kubernetes`, `docker`, `aws`, `terraform`, `jenkins` | **4.0 – 7.3 yrs** | Kubernetes, Terraform, Docker, Ansible, AWS |

---

## 6. Ready-to-Use Python Client Snippet for Module 2

Module 2 can either read this data directly from `career_intel.db` using this standalone helper, or receive it over REST from Module 1 (`POST /api/v1/career/analyze`):

```python
import sqlite3
from pathlib import Path
from typing import Dict, List, Any, Optional

class CareerIntelDBReader:
    """Read-only helper to extract empirical labor market intelligence from career_intel.db."""
    
    def __init__(self, db_path: str = "module-1-career-intelligence/data/career_intel.db"):
        self.db_path = str(Path(db_path).resolve())
        
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        return conn

    def get_role_skills_and_weights(self, role_name: str, limit: int = 10) -> Dict[str, float]:
        """Fetch empirical top skills and their normalized [0.0, 1.0] demand weights."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT skill_normalized, SUM(frequency) as freq
                FROM skill_demand
                WHERE occupation_context LIKE ?
                GROUP BY skill_normalized
                ORDER BY freq DESC
                LIMIT ?
            """, (f"%{role_name.lower()}%", limit))
            rows = cur.fetchall()
            if not rows:
                return {}
            max_freq = rows[0]["freq"] or 1
            return {r["skill_normalized"]: round(r["freq"] / max_freq, 4) for r in rows}

    def get_experience_range(self, role_name: str) -> Dict[str, float]:
        """Fetch empirical min and max experience in years."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT AVG(experience_min) as min_exp, AVG(experience_max) as max_exp, COUNT(*) as cnt
                FROM job_postings_india
                WHERE title_normalized LIKE ? AND experience_min IS NOT NULL
            """, (f"%{role_name.lower()}%",))
            row = cur.fetchone()
            if row and row["cnt"] >= 5 and row["min_exp"] is not None:
                return {
                    "min": round(row["min_exp"], 1),
                    "max": round(max(row["min_exp"] + 1.0, row["max_exp"]), 1)
                }
            return {"min": 2.0, "max": 5.0}

    def get_hot_technologies(self, soc_code: str) -> List[str]:
        """Fetch O*NET hot technologies and frameworks."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT technology_name
                FROM occupation_tech
                WHERE soc_code = ? AND (hot_technology = 1 OR in_demand = 1)
                LIMIT 10
            """, (soc_code,))
            return [r["technology_name"] for r in cur.fetchall()]

    def get_portfolio_tasks(self, soc_code: str, limit: int = 5) -> List[str]:
        """Fetch core job activities to formulate realistic portfolio projects in roadmaps."""
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute("""
                SELECT task_description
                FROM occupation_tasks
                WHERE soc_code = ? AND task_type = 'Core'
                LIMIT ?
            """, (soc_code, limit))
            return [r["task_description"] for r in cur.fetchall()]
```

---

---

## 7. Summary for Downstream Integration

- **For Module 2**: Use `v_occupation_requirements` and `GET /api/v1/occupations/{soc}/requirements` for embedding-based semantic matching across all industries.
- **For Module 3**: Use `occupation_salary_india`, `occupation_experience_india`, and `GET /api/v1/occupations/{soc}/profile` to access granular, unflagged 2023+ market salary and experience distributions.
- **For Root Integration Orchestrator**: Legacy endpoints (`/career/analyze`, `/rank`, `/search_by_domain`) remain 100% backward-compatible. New `/api/v1/occupations/*` endpoints empower the general career engine.

---

## 8. Version 2.0: Cross-Industry Generalisation Layer

Module 1 v2.0 expands Vriddhi beyond technology into a **universal career intelligence engine for every occupation across all industries** (Healthcare, Finance, Education, Sales, Trades, Logistics, Hospitality, Creative, and Tech).

### 8.1. Unified Requirements Contract: `v_occupation_requirements`

This view provides 206,193 requirement rows across all 1,016 O\*NET occupations:

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

### 8.2. Additive REST API Endpoints

All new endpoints are strictly additive under `/api/v1/occupations`:

#### 1. Free-Text Multilingual / Alias-Aware Title Search
`GET /api/v1/occupations/search?q={query}&k={top_k}`
- **Parameters**: `q` (e.g. `"staff nurse"`, `"CA"`, `"site engineer"`, `"telecaller"`, `"ITI electrician"`), `k` (default 5).
- **Response**: List of candidate occupations with resolution confidence and match method:
```json
[
  {
    "soc_code": "29-1141.00",
    "title": "Registered Nurses",
    "confidence": 1.0,
    "method": "india_alias_exact",
    "matched_term": "staff nurse"
  }
]
```

#### 2. Unified Occupation Requirements
`GET /api/v1/occupations/{soc}/requirements?item_type={type}&limit={n}`
- **Path**: `soc` (e.g. `29-1141.00` or `29-1141`).
- **Response**: List of requirements from `v_occupation_requirements`.

#### 3. 360-Degree Occupation Profile
`GET /api/v1/occupations/{soc}/profile`
- **Response**: Comprehensive profile containing:
  - `domain`: SOC 2-digit major group, career cluster, Indian industry.
  - `job_zone`: Preparation depth (1–5), experience narrative, education narrative.
  - `indian_education`: Primary Indian qualification and distribution across 12 credential tiers.
  - `indian_experience`: Typical min/max years, median, and sample size ($n$) from 2023+ postings.
  - `salary_percentiles_india`: Unflagged 2023+ salary percentiles (p25 / p50 / p75) by city × experience bucket × work mode.
  - `related_occupations`: Top related transfer roles.

#### 4. Related Occupations & Career Mobility
`GET /api/v1/occupations/{soc}/related?limit={n}`
- **Response**: List of related occupations for career transition recommendations.

---

## 9. Test Occupations & Static Mock Export

A static mock export covering **15 representative occupations across 8 diverse industries** is provided at:
`data/m1_occupation_requirements_export.json`

| Industry Domain | Occupation | SOC Code | Total Requirements | Skills | Knowledge | Abilities |
|---|---|---|---|---|---|---|
| Healthcare | Registered Nurses | `29-1141.00` | 280 | 35 | 33 | 52 |
| Healthcare | Pharmacists | `29-1051.00` | 225 | 35 | 33 | 52 |
| Healthcare | Medical Assistants | `31-9092.00` | 246 | 35 | 33 | 52 |
| Finance | Accountants and Auditors | `13-2011.00` | 674 | 35 | 33 | 52 |
| Finance | Loan Officers | `13-2072.00` | 355 | 35 | 33 | 52 |
| Education | Secondary School Teachers | `25-2031.00` | 246 | 35 | 33 | 52 |
| Sales / Service | Sales Representatives | `41-4012.00` | 463 | 35 | 33 | 52 |
| Sales / Service | Customer Service Reps | `43-4051.00` | 417 | 35 | 33 | 52 |
| Engineering | Mechanical Engineers | `17-2141.00` | 418 | 35 | 33 | 52 |
| Engineering | Civil Engineers | `17-2051.00` | 388 | 35 | 33 | 52 |
| Trades | Electricians | `47-2111.00` | 247 | 35 | 33 | 52 |
| Hospitality | Chefs and Head Cooks | `35-1011.00` | 235 | 35 | 33 | 52 |
| Logistics | Heavy Truck Drivers | `53-3032.00` | 233 | 35 | 33 | 52 |
| Creative | Graphic Designers | `27-1024.00` | 367 | 35 | 33 | 52 |
| Tech (regression) | Data Scientists | `15-2051.00` | 1,603 | 35 | 33 | 52 |

