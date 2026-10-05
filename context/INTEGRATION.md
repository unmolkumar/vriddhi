# Integration Contract

## Purpose

This document defines how the three independently developed modules become one system.

Integration is a separate concern.

**No module agent should modify another module's implementation to perform integration.**

---

# Modules

```text
M1 = Career Intelligence
M2 = Skill Gap
M3 = Job Matching & Salary
```

---

# Integration Architecture

```text
                    Frontend / User
                           │
                           ▼
                    Integration API
                           │
          ┌────────────────┼────────────────┐
          │                │                │
          ▼                ▼                ▼
         M1               M2               M3
      Career            Skills            Jobs
    Intelligence          Gap            + Salary
          │                │                │
          └────────────────┼────────────────┘
                           ▼
                     Final Response
```

The integration layer is responsible for orchestration.

---

# Common User Profile

The shared user representation should be approximately:

```json
{
  "user_id": "uuid",
  "skills": [
    {
      "name": "python",
      "level": 3,
      "evidence": [
        "project",
        "internship"
      ]
    }
  ],
  "experience_years": 2,
  "education": [
    "B.Tech Computer Science"
  ],
  "location": "Bengaluru",
  "preferred_locations": [
    "Bengaluru",
    "Hyderabad"
  ],
  "target_occupation": "Backend Developer"
}
```

The exact schema can evolve, but breaking changes must be communicated to all affected modules.

---

# M1 → M2

Career Intelligence may return:

```json
{
  "occupation": "Data Engineer",
  "outlook": "Strong Growth",
  "growth_score": 0.84,
  "current_demand_score": 0.87,
  "ai_exposure_score": 0.42,
  "confidence_score": 0.78,
  "top_skills": [
    "python",
    "sql",
    "spark",
    "cloud"
  ]
}
```

M2 uses the occupation and market skill requirements as inputs.

M2 does not need M1's internal model.

---

# M2 → M3

Skill Gap provides a structured user profile:

```json
{
  "skills": [
    {
      "name": "python",
      "level": 3
    },
    {
      "name": "sql",
      "level": 2
    }
  ],
  "experience_years": 2,
  "education": [
    "B.Tech Computer Science"
  ],
  "location": "Bengaluru"
}
```

M3 uses this profile for job matching.

---

# M3 Output

M3 returns:

```json
{
  "jobs": [],
  "salary_intelligence": {},
  "negotiation": {}
}
```

---

# Integration Sequence

## Step 1 — Career Discovery

```text
User
 ↓
M1
 ↓
Career recommendations
```

User selects a career.

---

## Step 2 — Skill Assessment

```text
Selected career
+
Resume
 ↓
M2
 ↓
Skill profile
+
Skill gaps
+
Learning priorities
```

---

## Step 3 — Job Search

```text
Skill profile
+
Target career
+
Location
 ↓
M3
 ↓
Ranked live jobs
```

---

## Step 4 — Salary Analysis

```text
Selected job
+
User profile
 ↓
M3
 ↓
Market range
+
Candidate value
+
Negotiation guidance
```

---

# API Versioning

All public APIs should start with:

```text
/api/v1/
```

Do not silently break response structures.

Breaking changes require:

```text
/api/v2/
```

or an explicitly versioned contract.

---

# Integration Adapter Rule

If Module 1 returns:

```json
{
  "growth": 0.84
}
```

but the integration layer needs:

```json
{
  "growth_score": 0.84
}
```

the integration layer should adapt it.

Do NOT modify Module 1 simply because the integration layer prefers a different field name.

---

# Error Handling

Modules must return structured errors.

Example:

```json
{
  "error": {
    "code": "OCCUPATION_NOT_FOUND",
    "message": "Occupation could not be analyzed."
  }
}
```

Integration should handle failures gracefully.

Example:

```text
M1 unavailable
 ↓
Still allow resume analysis
 ↓
Show career intelligence temporarily unavailable
```

The entire product should not crash because one module is unavailable.

---

# Integration Testing

At least three full integration scenarios:

## Scenario 1

```text
Career selection
→ Skill analysis
→ Job search
→ Salary analysis
```

## Scenario 2

```text
Resume
→ Skill extraction
→ Direct job search
```

## Scenario 3

```text
Job listing
→ Skill gap
→ Salary analysis
```

---

# Contract Testing

Before integration:

```text
M1 contract test
M2 contract test
M3 contract test
```

Then:

```text
Cross-module integration test
```

Do not rely only on manual testing.

---

# Merge Strategy

Each module has its own branch/workspace.

```text
member1/module-1
member2/module-2
member3/module-3
```

Integration is performed separately.

Recommended flow:

```text
Module branches
      ↓
Pull Requests
      ↓
Integration branch
      ↓
Contract tests
      ↓
End-to-end tests
      ↓
main
```

---

# Important Rule

Integration code belongs to the integration layer.

It does not belong inside another module merely because that is easier.

This keeps the modules independently replaceable.
