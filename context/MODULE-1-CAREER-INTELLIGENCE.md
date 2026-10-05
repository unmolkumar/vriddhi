# Module 1 — Career Intelligence & Forecasting

## Mission

Build the engine that answers:

> **"Which careers/jobs are likely to be valuable over the next five years?"**

This module is responsible for career-market intelligence only.

---

# Ownership

This module has exactly **one development agent**.

The agent owns:

```text
module-1-career-intelligence/
```

The agent must not implement:

- Resume parsing
- User skill-gap calculation
- Live job matching
- Salary negotiation
- Frontend features
- Another module's internal logic

If another module needs information from this module, expose it through the contract defined in `INTEGRATION.md`.

---

# Core Responsibilities

## 1. Historical Job Analysis

Analyze historical job-posting data.

Possible signals:

- Number of postings
- Posting growth rate
- Skill frequency
- Industry distribution
- Location distribution
- Salary trends where available
- Seniority trends

Example:

```text
Year       Job Postings
2022       10,000
2023       12,500
2024       16,000
2025       19,000
2026       23,000
```

Calculate trends rather than relying only on absolute counts.

---

# 2. Current Demand

Determine current demand for an occupation.

Possible signals:

```text
Current posting volume
Posting growth
Unique employers
Skill demand
Geographic demand
Salary movement
Industry demand
```

---

# 3. AI / Automation Exposure

Do NOT make simplistic claims such as:

```text
AI can perform task X
→ occupation will disappear
```

Instead distinguish:

```text
Occupation
   ↓
Tasks
   ↓
AI exposure per task
   ↓
Potential task transformation
   ↓
Potential occupation impact
```

Use categories such as:

```text
Low transformation exposure
Moderate transformation exposure
High transformation exposure
```

---

# 4. Five-Year Forecast

The system should estimate the outlook from the current year through approximately five years ahead.

The output should be probabilistic.

Example:

```json
{
  "occupation": "Data Engineer",
  "growth_score": 0.84,
  "current_demand_score": 0.87,
  "ai_exposure_score": 0.42,
  "confidence_score": 0.78,
  "outlook": "Strong Growth"
}
```

Never present the forecast as a guarantee.

---

# 5. Career Ranking

Given multiple occupations, rank them according to configurable signals.

Example:

```text
Career
Current Demand
Growth
AI Exposure
Salary Trend
Confidence
```

Do not permanently hard-code scoring weights throughout the code.

Use configuration.

---

# 6. Evidence

Every major prediction should have supporting evidence.

Example:

```json
{
  "drivers": [
    "Increasing job-posting volume",
    "Growth in cloud infrastructure",
    "Increasing demand for data systems"
  ]
}
```

The UI should eventually be able to explain:

> "Why is this career recommended?"

---

# Suggested Internal Pipeline

```text
Raw Job Data
    ↓
Cleaning
    ↓
Deduplication
    ↓
Occupation Mapping
    ↓
Skill Extraction
    ↓
Time-Series Features
    ↓
Demand Analysis
    ↓
AI Exposure Analysis
    ↓
Forecasting
    ↓
Career Ranking
    ↓
API Output
```

---

# Suggested API

```http
POST /api/v1/career/analyze
```

Input:

```json
{
  "occupation": "Data Engineer"
}
```

Output:

```json
{
  "occupation": "Data Engineer",
  "current_demand_score": 0.87,
  "growth_score": 0.84,
  "ai_exposure_score": 0.42,
  "confidence_score": 0.78,
  "outlook": "Strong Growth",
  "top_skills": [
    "Python",
    "SQL",
    "Spark",
    "Cloud"
  ],
  "drivers": []
}
```

---

# Data Requirements

Document every dataset in:

```text
module-1-career-intelligence/data/SOURCES.md
```

For each source record:

```text
Source
URL
Access date
License
Fields used
Preprocessing
Limitations
```

---

# Testing

At minimum test:

- Trend calculation
- Growth calculation
- Forecast generation
- Missing data
- Sparse occupation data
- Confidence calculation
- Unknown occupations
- Skill extraction
- Ranking

---

# Module Boundary

The module may produce:

```text
Career outlook
Career ranking
Occupation metadata
Demand signals
Skill-demand signals
AI exposure
Confidence
Evidence
```

It must NOT produce:

```text
User resume skill level
Individual skill gaps
Live job matches
Individual salary negotiation advice
```

Those belong to other modules.
