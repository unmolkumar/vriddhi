# Career Intelligence Platform — Project Context

## Purpose

This repository contains a hackathon system designed to help students and working professionals make better career decisions and move from **career discovery → skill assessment → job discovery → salary intelligence**.

The project is intentionally divided into **three completely independent modules**.

### Core product flow

```text
User
 │
 ▼
Career Intelligence
"What career should I pursue?"
 │
 ▼
Skill Gap Engine
"What do I currently have and what am I missing?"
 │
 ▼
Job Matching & Salary Intelligence
"What jobs can I apply for and what am I worth?"
 │
 ▼
Personalized Career Decision
```

---

# Repository Structure

```text
repo/
│
├── context/
│   ├── README.md
│   ├── MODULE-1-CAREER-INTELLIGENCE.md
│   ├── MODULE-2-SKILL-GAP.md
│   ├── MODULE-3-JOB-MATCHING-SALARY.md
│   ├── INTEGRATION.md
│   └── AGENTS.md
│
├── module-1-career-intelligence/
├── module-2-skill-gap/
├── module-3-job-matching-salary/
│
├── integration/
│
└── README.md
```

The `context/` directory is the source of truth for the development agents.

---

# Three Modules

## Module 1 — Career Intelligence

Answers:

> **"What career/job should I pursue, and what is its future outlook?"**

Responsibilities:

- Historical job-posting analysis
- Current demand analysis
- Occupation growth/decline
- Skill demand trends
- AI/automation exposure
- Five-year career forecasting
- Career ranking/recommendation
- Evidence and confidence

---

## Module 2 — Skill Gap Engine

Answers:

> **"Where am I currently, and what do I need to reach my target career?"**

Responsibilities:

- Resume parsing
- User skill extraction
- Skill normalization
- Experience extraction
- Target-job requirement extraction
- Skill matching
- Skill-gap detection
- Overqualification detection
- Learning priorities
- Job-readiness score

---

## Module 3 — Job Matching & Salary Intelligence

Answers:

> **"Which jobs can I apply for now, and what salary should I target?"**

Responsibilities:

- Live/current job data
- Job normalization
- Location filtering
- Skill matching
- Experience matching
- Job ranking
- Salary extraction
- Salary prediction
- Candidate market-value estimation
- Negotiation guidance

---

# Critical Development Rule

**No two agents work on the same module.**

Each module has exactly one owner.

```text
Agent 1 → Module 1 only
Agent 2 → Module 2 only
Agent 3 → Module 3 only
```

Agents must not implement features inside another module.

They must not modify another module's internal code to make integration easier.

If another module is required, define or request an **integration contract** instead.

---

# Integration Philosophy

The modules are independently developed and integrated only after their internal implementations are complete enough to expose stable interfaces.

```text
Module 1 ──────┐
               │
Module 2 ──────┼──→ Integration Layer
               │
Module 3 ──────┘
```

Modules communicate through:

- API contracts
- JSON schemas
- Shared data structures
- Explicit integration adapters

They should **not** communicate through internal imports such as:

```python
from module_1.internal_model import something
```

Prefer:

```text
Module 1
   ↓
Stable API / Contract
   ↓
Integration Layer
   ↓
Module 2
```

---

# Source of Truth

The following files define the project:

```text
context/README.md
context/MODULE-1-CAREER-INTELLIGENCE.md
context/MODULE-2-SKILL-GAP.md
context/MODULE-3-JOB-MATCHING-SALARY.md
context/INTEGRATION.md
context/AGENTS.md
```

Agents must read `AGENTS.md` first and then read only the module context assigned to them.

The integration agent/process reads `INTEGRATION.md`.

---

# Definition of Done

A module is ready for integration when:

- Core functionality works
- Tests exist
- Internal code is documented where necessary
- Input/output contract is implemented
- Errors are handled
- No dependency on another module's internal implementation exists
- README for the module explains how to run it
- Integration contract is satisfied

---

# Final Product

The final system should provide a continuous career loop:

```text
Discover
   ↓
Assess
   ↓
Identify gaps
   ↓
Learn
   ↓
Find jobs
   ↓
Estimate value
   ↓
Negotiate / Apply
   ↓
Improve profile
   ↓
Repeat
```
