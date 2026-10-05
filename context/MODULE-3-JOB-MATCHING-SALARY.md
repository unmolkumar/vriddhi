# Module 3 — Job Matching & Salary Intelligence

## Mission

Build the engine that answers:

> **"Which current jobs match me, and what compensation should I reasonably target?"**

This module handles current job discovery, candidate-job matching, salary intelligence, and negotiation analysis.

---

# Ownership

This module has exactly **one development agent**.

The agent owns:

```text
module-3-job-matching-salary/
```

The agent must not implement:

- Career forecasting
- Resume parsing internals
- Skill-gap internals
- Another module's code
- Frontend business logic

If skill information is required, consume the integration contract.

---

# Core Pipeline

```text
User Profile
     ↓
Job Data Retrieval
     ↓
Job Normalization
     ↓
Filtering
     ↓
Requirement Extraction
     ↓
Candidate ↔ Job Matching
     ↓
Ranking
     ↓
Salary Intelligence
     ↓
Negotiation Analysis
```

---

# Job Search Inputs

Support:

```text
Target role
Skills
Experience
Education
Location
Preferred locations
Remote/hybrid/on-site
Salary preference
Employment type
```

---

# Job Normalization

Every source should map into a common structure:

```json
{
  "job_id": "",
  "title": "",
  "company": "",
  "description": "",
  "location": "",
  "employment_type": "",
  "experience_min": null,
  "experience_max": null,
  "skills": [],
  "salary_min": null,
  "salary_max": null,
  "currency": "INR",
  "posted_at": "",
  "source": "",
  "source_url": ""
}
```

Do not let every source create a different internal format.

---

# Job Matching

Possible score:

```text
40% Skill Match
20% Experience Match
10% Education Match
10% Location Match
10% Seniority Match
10% Preference Match
```

These are starting weights, not immutable rules.

Keep weights configurable.

---

# Example

Candidate:

```text
Python
FastAPI
SQL
Docker
Redis

Experience:
2 years

Location:
Bengaluru
```

Job:

```text
Backend Developer
Required:
Python
FastAPI
SQL
Docker

Experience:
3+ years
```

Possible result:

```text
Skill Match: 92%
Experience Match: 67%
Overall Match: 84%

Classification:
Good Match
```

---

# Job Ranking

Rank jobs using:

```text
Overall match
+
Location preference
+
Experience fit
+
Salary attractiveness
+
Recency
```

Do not rank solely by salary.

---

# Salary Intelligence

Salary estimation should consider:

```text
Role
Location
Experience
Education
Skills
Industry
Company characteristics
Seniority
Market demand
Candidate-job match
```

Output a range rather than false precision.

Example:

```json
{
  "estimated_min": 1300000,
  "estimated_median": 1450000,
  "estimated_max": 1600000,
  "currency": "INR",
  "confidence": 0.72
}
```

---

# Candidate Market Value

Example:

```text
Posted salary:
₹12 LPA

Estimated market range:
₹13–16 LPA

Candidate estimated range:
₹13.5–15 LPA
```

The system should explain why.

Possible evidence:

```text
Strong skill match
Relevant experience
High-demand skills
Comparable job salaries
Location market
Seniority
```

---

# Negotiation Engine

Output:

```text
Posted compensation
Estimated market range
Candidate estimated range
Suggested target
Reasonable minimum
Confidence
Reasons
```

Example:

```json
{
  "posted_salary": "12 LPA",
  "market_range": "13-16 LPA",
  "candidate_range": "13.5-15 LPA",
  "recommended_target": "14.5 LPA",
  "confidence": 0.71,
  "reasons": [
    "Strong skill match",
    "Relevant experience",
    "High-demand specialization"
  ]
}
```

Do not tell users that a salary is guaranteed.

Use:

```text
Estimated
Likely
Indicative
Market-based
```

---

# Live Job Data

Job sources may change.

Every job record should preserve:

```text
Source
Source URL
Posted date
Last observed date
```

The system should handle:

- Expired jobs
- Duplicate jobs
- Missing salaries
- Missing locations
- Missing skills
- Incomplete descriptions

---

# Suggested API

```http
POST /api/v1/jobs/search
```

Input:

```json
{
  "location": "Bengaluru",
  "skills": [
    "python",
    "sql",
    "docker"
  ],
  "experience_years": 2,
  "target_role": "Backend Developer"
}
```

Output:

```json
{
  "jobs": [
    {
      "job_id": "12345",
      "title": "Backend Developer",
      "company": "Example Company",
      "location": "Bengaluru",
      "salary": {
        "min": 1000000,
        "max": 1400000,
        "currency": "INR"
      },
      "match_score": 86,
      "missing_skills": []
    }
  ]
}
```

---

# Testing

At minimum test:

- Job normalization
- Duplicate handling
- Skill matching
- Experience matching
- Location filtering
- Ranking
- Missing salary
- Salary extraction
- Salary estimation
- No matching jobs
- Expired jobs

---

# Module Boundary

This module may produce:

```text
Live/current job results
Job match scores
Missing skills per job
Salary estimates
Market ranges
Candidate value estimates
Negotiation recommendations
```

It must NOT produce:

```text
Five-year occupation forecasts
Resume extraction internals
General skill-gap logic
```

Those belong to other modules.
