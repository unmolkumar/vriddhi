# Module 1 (Career Intelligence) — UI & Feature Integration Guide

**Owner:** Anmol (Module 1 Lead)  
**Target Audience:** Frontend / UI Engineer & Integration Layer Lead  
**Service Base URL:** `http://localhost:8001`  
**Swagger / Interactive OpenAPI Docs:** `http://localhost:8001/docs`  
**Latest Schema Version:** `2.2.0` (Full 1,016 O*NET Occupations, 43,372 Official Tools, 24,087 DWAs, 2023+ Clean Indian Salaries & Experience)

---

## 1. Executive Summary & Purpose

Module 1 serves as the **foundational labour-market intelligence and occupational knowledge base** for the entire Vriddhi platform.

It answers the core questions:
1. *"What occupation does the user's search or job title map to?"*
2. *"What are the real-world daily requirements, proficiencies, tools, and tasks needed for this occupation?"*
3. *"What are the realistic salary bands, typical experience criteria, and education qualifications in India?"*
4. *"Which related occupations can the user transition into?"*

### Architecture in the 3-Module Vriddhi Flow

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        FRONTEND / USER INTERFACE                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
    ┌───────────────────────────────┼───────────────────────────────┐
    │ 1. Search & Profile           │ 2. Resume / Gap Analysis      │ 3. Live Job Matching & Salary
    ▼                               ▼                               ▼
┌────────────────────────┐      ┌────────────────────────┐      ┌────────────────────────┐
│  MODULE 1 (Port 8001)  │      │  MODULE 2 (Port 8002)  │      │  MODULE 3 (Port 8003)  │
│  Career Intelligence   │─────▶│  Skill Gap Engine      │─────▶│  Job Match & Salary    │
│                        │      │                        │      │                        │
│ • Title -> SOC search  │      │ • Resume & text parser │      │ • Live Adzuna/JSearch  │
│ • 360° Profile & Bands │      │ • Semantic fit to reqs │      │ • Match score (0-100)  │
│ • Unified Requirements │      │ • Gap & Roadmap Matrix │      │ • Salary negotiation   │
│ • Related occupations  │      │ • Hours estimation     │      │ • "Unlocks N jobs"     │
└────────────────────────┘      └────────────────────────┘      └────────────────────────┘
```

---

## 2. Quickstart & Service Verification

### Start the Service Locally
```bash
# From workspace root:
cd module-1-career-intelligence

# Install dependencies if not already installed:
pip install -r requirements.txt

# Launch FastAPI on port 8001:
uvicorn src.api.main:app --host 0.0.0.0 --port 8001 --reload
```

### Health Check
```bash
curl -X GET http://localhost:8001/api/v1/health
```
**Response:**
```json
{
  "status": "healthy",
  "database": "connected",
  "occupations_indexed": 1016,
  "version": "2.2.0"
}
```

### Build & Schema Metadata
```bash
curl -X GET http://localhost:8001/api/v1/meta
```
Returns exact table counts and SHA-256 fixture hashes for build verification and caching.

---

## 3. UI Feature Guide & Endpoints

### Feature 1: Smart Career Search & Autocomplete
**Use Case in UI**: The global search bar or occupation dropdown where the user types what they want to become or their current job.

- **Endpoint**: `GET /api/v1/occupations/search`
- **Query Parameters**:
  - `q` (*string, required*): The query string (e.g. `"nurse"`, `"CA"`, `"site engineer"`, `"electrician"`, `"data scientist"`).
  - `k` (*integer, optional, default: 5*): Maximum results to return.

#### How It Works Under the Hood:
Handles colloquial Indian titles (*"staff nurse"*, *"CA"*, *"telecaller"*, *"ITI electrician"*, *"relationship manager"*), official O*NET 31.0 titles, and 62,458 alternate titles with dynamic token-overlap confidence scoring.

#### Example Request:
```bash
curl -X GET "http://localhost:8001/api/v1/occupations/search?q=staff%20nurse&k=2"
```

#### Example Response:
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

**UI Recommendation**: Bind this to an autocomplete combobox. When the user selects a role, store `soc_code` in your frontend state/session.

---

### Feature 2: 360-Degree Occupation Profile
**Use Case in UI**: The main Career Overview screen showing market salary ranges, typical experience needed, education tier, and domain background.

- **Endpoint**: `GET /api/v1/occupations/{soc}/profile`
- **Path Parameter**:
  - `soc` (*string, required*): The standard SOC code (e.g. `"29-1141.00"`, `"13-2011.00"`, `"15-2051.00"`).

#### Example Request:
```bash
curl -X GET "http://localhost:8001/api/v1/occupations/29-1141.00/profile"
```

#### Example Response:
```json
{
  "soc_code": "29-1141.00",
  "title": "Registered Nurses",
  "description": "Assess patient health problems and needs, develop and implement nursing care plans, and maintain medical records. Administer nursing care to ill, injured, convalescent, or disabled patients.",
  "domain": {
    "major_group": "29",
    "major_group_title": "Healthcare Practitioners and Technical",
    "career_cluster": "Healthcare & Medicine",
    "india_industry": "Healthcare & Medical Services"
  },
  "job_zone": {
    "job_zone": 4,
    "name": "Job Zone Four: Considerable Preparation Needed",
    "experience_text": "A considerable amount of work-related skill, knowledge, or experience is needed for these occupations.",
    "education_text": "Most of these occupations require a four-year bachelor's degree, but some do not.",
    "svp_range": "(7.0 to < 8.0)"
  },
  "indian_education": {
    "primary_qualification": "Bachelor's Degree",
    "distribution": [
      {
        "category_id": 6,
        "india_education_level": "Bachelor's Degree",
        "percent": 58.2
      },
      {
        "category_id": 5,
        "india_education_level": "ITI / Diploma / Associate",
        "percent": 35.1
      }
    ]
  },
  "indian_experience": {
    "typical_min": 3.0,
    "typical_max": 7.0,
    "p25_min": 2.5,
    "median_min": 4.5,
    "sample_size": 0,
    "years_covered": null,
    "fallback_to_job_zone": true,
    "job_zone_guidance": "A considerable amount of work-related skill, knowledge, or experience is needed for these occupations."
  },
  "salary_percentiles_india": [
    {
      "city_canonical": "All India",
      "experience_bucket": "all",
      "work_mode": "onsite",
      "p25": 4.2,
      "p50": 6.8,
      "p75": 11.5,
      "sample_size": 142,
      "years_covered": "2024-2026"
    },
    {
      "city_canonical": "Bengaluru",
      "experience_bucket": "mid",
      "work_mode": "onsite",
      "p25": 5.5,
      "p50": 8.0,
      "p75": 12.5,
      "sample_size": 48,
      "years_covered": "2024-2026"
    }
  ],
  "related_occupations": [
    {
      "related_soc_code": "29-1161.00",
      "related_title": "Nurse Midwives",
      "relatedness_tier": "Primary-Long",
      "index_val": 6
    },
    {
      "related_soc_code": "29-1171.00",
      "related_title": "Nurse Practitioners",
      "relatedness_tier": "Primary-Short",
      "index_val": 1
    }
  ]
}
```

**UI Recommendations**:
- Render the `salary_percentiles_india` as a Range Slider or Mini Box Plot: $P_{25} \to P_{50} \to P_{75}$ LPA.
- Display `indian_experience.typical_min` to `typical_max` as the expected years band.
- Display `indian_education.primary_qualification` as a badge (e.g. `Bachelor's Degree`).
- Render `related_occupations` as "Alternative / Transfer Roles" chips that users can click to switch targets.

---

### Feature 3: Unified Occupation Requirements Contract
**Use Case in UI**: The Skills Breakdown, Radar Charts, Tool Checklists, and passing data to Module 2 for Skill Gap analysis.

- **Endpoint**: `GET /api/v1/occupations/{soc}/requirements`
- **Query Parameters**:
  - `item_type` (*string, optional*): Filter by category. Allowed values:
    - `skill` (35 core cognitive & technical skills)
    - `knowledge` (33 domain areas e.g. Medicine, Accounting, Law)
    - `ability` (52 physical & sensory abilities)
    - `work_activity` (41 generalized activities)
    - `task` (official daily occupation tasks)
    - `dwa` (granular detailed work activities)
    - `tool` (official physical tools, machinery, and equipment)
    - `tech` (software, programming languages, and packages)
    - `market_skill` (empirical skills parsed from Indian postings)
  - `limit` (*integer, optional, default: 200*): Max items to return.

#### Example Request:
```bash
curl -X GET "http://localhost:8001/api/v1/occupations/29-1141.00/requirements?item_type=skill&limit=2"
```

#### Example Response:
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
    "reliable": 1,
    "posting_count": 0,
    "soc_posting_total": null,
    "india_relevant": 1,
    "india_irrelevant_reason": null
  },
  {
    "soc_code": "29-1141.00",
    "item_type": "skill",
    "item_id": "2.A.1.a",
    "item_name": "Reading Comprehension",
    "item_description": "Understanding written sentences and paragraphs in work related documents.",
    "importance_norm": 0.75,
    "level_norm": 0.5714,
    "hot_technology": 0,
    "in_demand": 0,
    "india_demand_share": null,
    "source": "onet",
    "reliable": 1,
    "posting_count": 0,
    "soc_posting_total": null,
    "india_relevant": 1,
    "india_irrelevant_reason": null
  }
]
```

**Key Data Fields for UI Display**:
- `importance_norm` $[0.0, 1.0]$: Importance of the skill (can be rendered as a star rating or progress bar).
- `level_norm` $[0.0, 1.0]$: Required proficiency level (0 = basic, 1 = master).
- `hot_technology` ($1$ or $0$): Highlight with a "🔥 Hot" badge.
- `in_demand` ($1$ or $0$): Highlight with a "Trending" badge.
- `india_relevant` ($1$ or $0$): If $0$, grey out or hide by default (with tooltip showing `india_irrelevant_reason`).

---

### Feature 4: Career Mobility & Related Occupations
**Use Case in UI**: "Where can I pivot with my current background?" lateral career mobility view.

- **Endpoint**: `GET /api/v1/occupations/{soc}/related?limit={n}`
- **Query Parameter**: `limit` (*integer, optional, default: 20*)

#### Example Request:
```bash
curl -X GET "http://localhost:8001/api/v1/occupations/13-2011.00/related?limit=3"
```

#### Example Response:
```json
[
  {
    "soc_code": "13-2011.00",
    "related_soc_code": "13-2051.00",
    "related_title": "Financial and Investment Analysts",
    "relatedness_tier": "Primary-Short",
    "index_val": 1,
    "major_group_title": "Business and Financial Operations",
    "career_cluster": "Finance & Business Services",
    "job_zone": 4
  },
  {
    "soc_code": "13-2011.00",
    "related_soc_code": "13-2031.00",
    "related_title": "Budget Analysts",
    "relatedness_tier": "Primary-Short",
    "index_val": 2,
    "major_group_title": "Business and Financial Operations",
    "career_cluster": "Finance & Business Services",
    "job_zone": 4
  },
  {
    "soc_code": "13-2011.00",
    "related_soc_code": "13-2082.00",
    "related_title": "Tax Preparers",
    "relatedness_tier": "Supplemental",
    "index_val": 7,
    "major_group_title": "Business and Financial Operations",
    "career_cluster": "Finance & Business Services",
    "job_zone": 3
  }
]
```

**UI Recommendation**: Show `relatedness_tier` (`Primary-Short` = High overlap, immediate switch; `Primary-Long` = Moderate preparation needed; `Supplemental` = Broad domain sibling).

---

### Feature 5: 5-Year Career Outlook & AI Exposure
**Use Case in UI**: Market trends card showing future growth forecast and AI transformation index.

- **Endpoint**: `POST /api/v1/career/analyze`
- **Request Body**:
```json
{
  "occupation": "Data Scientist",
  "region": "all"
}
```

#### Example Response:
```json
{
  "occupation": "Data Scientist",
  "historical_trend": {
    "cagr_3yr": 0.18,
    "growth_direction": "Strong Growth",
    "sample_size": 3200
  },
  "current_demand_score": 0.88,
  "ai_exposure_score": 0.42,
  "five_year_forecast": {
    "growth_score": 0.85,
    "outlook": "Strong Growth",
    "confidence_score": 0.82
  },
  "evidence": {
    "key_drivers": [
      "High expansion in Indian enterprise analytics",
      "Moderate AI transformation with high human-in-the-loop oversight"
    ]
  }
}
```

---

## 4. Cross-Module Handshake (How M1 Feeds M2 & M3)

### Handshake with Module 2 (Skill Gap & Resume Engine on Port 8002)
1. User enters their target role or selects one from M1's `GET /api/v1/occupations/search`.
2. Integration Gateway calls M1: `GET /api/v1/occupations/{soc}/requirements`.
3. Gateway passes requirements array and the user's resume text/PDF to M2:
   - `POST http://localhost:8002/api/v2/skills/gap_analysis`
4. M2 performs semantic embeddings matching against M1's `item_name + ": " + item_description` and outputs the fit %, missing gaps, and roadmap!

### Handshake with Module 3 (Job Matching & Salary Engine on Port 8003)
1. User receives their skill fit from M2 and target occupation `soc_code` from M1.
2. Integration Gateway calls M1: `GET /api/v1/occupations/{soc}/profile` to fetch:
   - `salary_percentiles_india` (unflagged benchmarks)
   - `indian_experience` (experience range bounds)
3. Gateway calls M3:
   - `POST http://localhost:8003/api/v1/jobs/search` (or `/v2`)
4. M3 queries live jobs (Adzuna / JSearch) using canonical title aliases from M1 and evaluates salary negotiation guidance against M1's percentiles.

---

## 5. TypeScript Types for the Frontend

Copy-paste these types directly into your frontend code (`src/types/career.ts`):

```typescript
export interface OccupationSearchResult {
  soc_code: string;
  title: string;
  confidence: number;
  method: 'india_alias_exact' | 'onet_alt_title_exact' | 'token_prefix_match' | string;
  matched_term: string;
}

export interface SalaryPercentile {
  city_canonical: string;
  experience_bucket: 'entry' | 'mid' | 'senior' | 'all';
  work_mode: 'onsite' | 'remote';
  p25: number;
  p50: number;
  p75: number;
  sample_size: number;
  years_covered: string | null;
}

export interface IndianExperienceBand {
  typical_min: number;
  typical_max: number;
  p25_min: number;
  median_min: number;
  sample_size: number;
  years_covered: string | null;
  fallback_to_job_zone: boolean;
  job_zone_guidance?: string;
}

export interface OccupationProfile {
  soc_code: string;
  title: string;
  description: string;
  domain: {
    major_group: string;
    major_group_title: string;
    career_cluster: string;
    india_industry: string;
  };
  job_zone: {
    job_zone: number;
    name: string;
    experience_text: string;
    education_text: string;
    svp_range: string;
  };
  indian_education: {
    primary_qualification: string;
    distribution: Array<{
      category_id: number;
      india_education_level: string;
      percent: number;
    }>;
  };
  indian_experience: IndianExperienceBand;
  salary_percentiles_india: SalaryPercentile[];
  related_occupations: Array<{
    related_soc_code: string;
    related_title: string;
    relatedness_tier: 'Primary-Short' | 'Primary-Long' | 'Supplemental';
    index_val: number;
  }>;
}

export interface OccupationRequirement {
  soc_code: string;
  item_type: 'skill' | 'knowledge' | 'ability' | 'work_activity' | 'task' | 'dwa' | 'tech' | 'tool' | 'market_skill';
  item_id: string;
  item_name: string;
  item_description: string;
  importance_norm: number;
  level_norm: number | null;
  hot_technology: number;
  in_demand: number;
  india_demand_share: number | null;
  source: 'onet' | 'india_postings' | 'curated';
  reliable: number;
  posting_count: number;
  soc_posting_total: number | null;
  india_relevant: number;
  india_irrelevant_reason: string | null;
}
```

---

## 6. Offline Mock Fixture (Build Without Backend)

If you are designing the UI offline without running Python/Uvicorn, a static JSON fixture covering **15 representative occupations across 8 diverse industries** is located in the repo:

`module-1-career-intelligence/data/m1_occupation_requirements_export.json`

Included showcase roles:
1. `29-1141.00` — **Registered Nurses** (Healthcare)
2. `13-2011.00` — **Accountants and Auditors** (Finance)
3. `47-2111.00` — **Electricians** (Trades / Blue-collar)
4. `15-2051.00` — **Data Scientists** (Tech)
5. `41-4012.00` — **Sales Representatives** (Sales / Commerce)
6. `17-2051.00` — **Civil Engineers** (Engineering)
7. `25-2031.00` — **Secondary School Teachers** (Education)
8. `35-1011.00` — **Chefs and Head Cooks** (Hospitality)

You can import this JSON directly into your Mock Service Worker (MSW) or frontend state for instant UI prototyping.

---

## 7. Summary Checklist for Integration

- [x] Module 1 backend running on port 8001
- [x] Search autocomplete hooked up to `/api/v1/occupations/search`
- [x] Profile overview cards hooked up to `/api/v1/occupations/{soc}/profile`
- [x] Requirements & skill chips hooked up to `/api/v1/occupations/{soc}/requirements`
- [x] Related career pivots hooked up to `/api/v1/occupations/{soc}/related`
- [x] Module 1 `soc_code` and requirements passed to Module 2 on port 8002
- [x] Module 1 `salary_percentiles_india` passed to Module 3 on port 8003
