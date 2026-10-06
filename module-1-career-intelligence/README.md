# Module 1: Career Intelligence & Forecasting Engine

Part of the **Vriddhi** Career Advisory Platform.

Answers the foundational market question:
> **"Which careers/jobs are likely to be valuable over the next five years?"**

---

## Architecture Overview

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        RAW DATA SOURCES                                │
│                                                                        │
│  - O*NET 31.0 Database (U.S. Dept of Labor): Taxonomy, Tasks, Skills   │
│  - LinkedIn Global Postings (115,000 records)                         │
│  - Naukri Indian Job Market (72,691 records, multiple metros)          │
│  - Multi-region Salary Benchmarks (43,374 records)                     │
│  - Skill Demand Registry (367,395 records)                             │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│               UNIFIED SQLite STORE (data/career_intel.db)               │
│                                                                        │
│  - occupations           - job_postings_global   - skill_demand        │
│  - occupation_tasks      - job_postings_india    - ai_exposure_tasks   │
│  - occupation_skills     - salary_benchmarks     - v_india_vs_global   │
│  - occupation_tech       - v_skill_comparison                          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        ANALYTICAL ENGINES                              │
│                                                                        │
│  - DemandEngine:         Multi-signal current market scoring           │
│  - AIExposureEngine:     Granular task-level transformation assessment │
│  - CareerForecaster:     5-year probabilistic outlook & confidence     │
│  - RankingEngine:        Dynamic configurable weighted ranker          │
│  - EvidenceEngine:       Data-backed explainability drivers            │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                        FASTAPI REST SERVICE                            │
│                                                                        │
│  - POST /api/v1/career/analyze                                         │
│  - POST /api/v1/career/rank                                            │
│  - GET  /api/v1/career/compare (India vs Global)                       │
│  - GET  /api/v1/career/occupations                                     │
│  - GET  /health                                                        │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Installation & Setup

1. **Install Dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Data Pipeline (Already Populated):**
   ```bash
   # Download raw datasets:
   python data/scripts/01_download_raw.py

   # Build/refresh SQLite database:
   python data/scripts/02_etl_clean_load.py
   ```

3. **Run the API Service:**
   ```bash
   uvicorn src.api.main:app --port 8001 --reload
   ```

4. **Run Test Suite:**
   ```bash
   pytest tests/ -v
   ```

---

## API Contract Reference

### 1. Analyze Career
`POST /api/v1/career/analyze`

**Request:**
```json
{
  "occupation": "Data Scientist",
  "region": "all"
}
```

**Response:**
```json
{
  "occupation": "Data Scientists",
  "soc_code": "15-2051.00",
  "current_demand_score": 0.88,
  "growth_score": 0.89,
  "ai_exposure_score": 0.52,
  "confidence_score": 0.85,
  "outlook": "Strong Growth",
  "top_skills": [
    "python",
    "machine_learning",
    "sql",
    "deep_learning",
    "r",
    "cloud"
  ],
  "drivers": [
    "Substantial real-world market presence with 12,480 verified postings across global and Indian labor markets.",
    "Strong recent hiring momentum with steady acceleration from 2024 through 2026.",
    "High augmentation leverage: 18 tasks enhanced by generative AI tools, increasing worker productivity rather than substituting roles.",
    "Balanced multi-regional hiring spanning leading Indian tech hubs (Bengaluru) and international markets.",
    "High employer demand for foundational and emerging capabilities: python, machine_learning, sql, deep_learning."
  ],
  "tasks_analyzed": 26,
  "regional_breakdown": {
    "india": {
      "region": "india",
      "posting_volume": 4200,
      "posting_growth_yoy_pct": 12.5,
      "median_salary_inr_lpa": 18.5,
      "median_salary_usd": 22200.0,
      "top_locations": ["Bengaluru", "Hyderabad", "Pune"],
      "top_skills": ["python", "machine_learning", "sql"]
    },
    "global": {
      "region": "global",
      "posting_volume": 8280,
      "posting_growth_yoy_pct": 15.0,
      "median_salary_usd": 138500.0,
      "top_locations": ["San Francisco, CA", "New York, NY", "London, UK"],
      "top_skills": ["python", "sql", "aws"]
    }
  }
}
```

### 2. Rank Careers
`POST /api/v1/career/rank`

**Request:**
```json
{
  "occupations": ["Software Engineer", "Data Scientist", "Accountant"],
  "weights": {
    "current_demand": 0.35,
    "growth": 0.35,
    "ai_resilience": 0.20,
    "salary_level": 0.10
  },
  "region": "all",
  "top_k": 3
}
```

---

## Regional Strategy: India vs. Global Side-by-Side

Every signal is tagged by region (`india` or `global`), enabling direct comparisons:
- **Domestic Indian Market**: City-level distribution (Bengaluru, NCR, Hyderabad, Mumbai, Pune, Chennai), INR compensations (LPA), and domestic hiring velocity.
- **Global / Worldwide Market**: Multi-country distributions, USD compensations, and international skill frequencies.

---

## Detailed Documentation & Integration Guides

- **[DATABASE.md](file:///c:/Users/anmol/stuff/projects/vriddhi/module-1-career-intelligence/DATABASE.md)**: Full `career_intel.db` database schema, 371k+ skill demand records, O*NET tables, and SQL/Python recipes for Module 2 to replace hardcoded skill gaps with real market evidence.
- **[WORKING.md](file:///c:/Users/anmol/stuff/projects/vriddhi/module-1-career-intelligence/WORKING.md)**: Comprehensive technical specification, outcome variable formulas, mathematical models, API schemas, and cross-module contracts.
- **[data/SOURCES.md](file:///c:/Users/anmol/stuff/projects/vriddhi/module-1-career-intelligence/data/SOURCES.md)**: Origin, licensing, and ETL pipeline for all datasets.
