# Module 2 — Skill Gap & Resume Intelligence

## Mission

Build the engine that answers:

> **"Where is this person now, how well do they match the target role, and what should they learn next?"**

This module converts a person's resume/profile into a structured skill profile and compares it against a target occupation/job.

---

# Ownership

This module has exactly **one development agent**.

The agent owns:

```text
module-2-skill-gap/
```

The agent must not implement:

- Career forecasting
- Historical job-market forecasting
- Live job retrieval
- Salary prediction
- Salary negotiation
- Another module's internal implementation

Dependencies on other modules must use integration contracts.

---

# Input Sources

The user may provide:

1. Resume
2. Manually entered skills
3. Experience
4. Education
5. Chatbot responses
6. Target occupation
7. Target job description

---

# Core Pipeline

```text
Resume / User Input
       ↓
Document Parsing
       ↓
Information Extraction
       ↓
Skill Extraction
       ↓
Skill Normalization
       ↓
Experience Extraction
       ↓
Education Extraction
       ↓
User Skill Profile
       ↓
Target Requirement Extraction
       ↓
Skill Comparison
       ↓
Gap / Strength / Overqualification
       ↓
Learning Priorities
```

---

# Skill Levels

Use a consistent internal scale:

```text
0 = Not demonstrated
1 = Beginner
2 = Basic
3 = Intermediate
4 = Advanced
5 = Expert
```

Skill levels should preferably be supported by evidence.

Example:

```text
Python
Level: 3
Evidence:
- 2 projects
- internship
- coursework
```

Do not automatically treat every skill mentioned in a resume as expert-level.

---

# Skill Normalization

Different representations must map to canonical skills.

Example:

```text
Python
Python 3
Python programming
Python development
```

→

```text
python
```

Another:

```text
Postgres
PostgreSQL
PostgreSQL DB
```

→

```text
postgresql
```

Maintain a shared/canonical taxonomy where practical.

---

# Gap Calculation

For each skill:

```text
Gap = Required Level - Current Level
```

Example:

| Skill | Required | Current | Gap |
|---|---:|---:|---:|
| Python | 4 | 3 | 1 |
| SQL | 4 | 2 | 2 |
| Git | 3 | 4 | -1 |

Interpretation:

```text
Gap > 0 → Skill Gap
Gap = 0 → Requirement Met
Gap < 0 → Above Requirement
```

---

# Important Classifications

The engine should distinguish:

```text
Missing Skill
Weak Skill
Matched Skill
Preferred Skill
Required Skill
Transferable Skill
Overqualification
```

---

# Example Output

```json
{
  "target_role": "Backend Developer",
  "match_score": 72,
  "classification": "Partially Qualified",
  "strengths": [
    "Python",
    "FastAPI",
    "SQL"
  ],
  "skill_gaps": [
    {
      "skill": "Docker",
      "required_level": 3,
      "current_level": 1,
      "gap": 2,
      "priority": "High"
    }
  ],
  "learning_priorities": [
    "Docker",
    "Redis",
    "System Design"
  ]
}
```

---

# Resume Evidence

The system should distinguish between:

```text
Self-reported skill
```

and:

```text
Resume-supported skill
```

Example:

```text
User claims:
Cloud = Expert

Resume evidence:
No cloud project
No cloud work experience

Result:
Cloud skill requires verification
```

This is more reliable than blindly accepting self-reported levels.

---

# Suggested API

```http
POST /api/v1/skills/analyze
```

Input:

```json
{
  "target_occupation": "Data Engineer",
  "resume_text": "...",
  "skills": [],
  "experience_years": 2
}
```

Output:

```json
{
  "match_score": 72,
  "classification": "Partially Qualified",
  "skills": {
    "matched": [],
    "weak": [],
    "missing": [],
    "above_requirement": []
  },
  "learning_priorities": []
}
```

---

# Learning Roadmap

Prioritize skills using:

```text
Skill importance
×
Skill gap
×
Market relevance
```

A missing skill that appears in many target jobs should receive higher priority.

---

# Testing

At minimum test:

- Resume extraction
- Skill extraction
- Skill aliases
- Skill normalization
- Skill-level calculation
- Gap calculation
- Missing skills
- Overqualification
- No resume
- Empty skills
- Unknown skills
- Malformed resume

---

# Module Boundary

This module may produce:

```text
Structured user profile
Extracted skills
Skill levels
Evidence
Skill gaps
Match score
Learning priorities
```

It must NOT produce:

```text
Five-year career forecast
Live job listings
Salary prediction
Salary negotiation recommendations
```

Those belong elsewhere.
