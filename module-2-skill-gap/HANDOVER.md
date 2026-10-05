# Module 2 Handover & Engineering Directive

**From:** Module 1 Lead Engineer (Career Intelligence & Forecasting)  
**To:** Module 2 Lead Engineer / Agent (Skill Gap & Resume Intelligence)  
**Date:** October 2026  
**Status:** Module 1 Complete, Tested (18/18 Passing), and Merged. Handing over for Module 2 Development.  

---

## 1. Executive Summary & Objective

Module 1 has completed the foundational labor market database, 5-year forecasts, and career competency profiles. Your mission in **Module 2** is to build the personalized intelligence engine answering:

> **"Where is this candidate today, how well do they match their target role, and what prioritized roadmap should they follow to bridge the gap?"**

You own: `module-2-skill-gap/`  
Your active branch: `feat/module-2-skill-gap`

---

## 2. Quickstart & Git Setup

To begin development, run:
```bash
git fetch origin
git checkout feat/module-2-skill-gap
git pull origin feat/module-2-skill-gap
```

---

## 3. How to Consume Module 1 Data & Contracts

You **do not** need to re-scrape job sites or re-generate career demand data. Module 1 exports the canonical source of truth for all target careers, skills, and industry requirements.

### 3.1. Direct In-Process Python Import
```python
from module_1_career_intelligence.src import (
    CareerIntelligenceService,
    CareerAnalysisResponse,
)

service = CareerIntelligenceService()
target: CareerAnalysisResponse = service.analyze_career("Data Engineer", region="all")

# Core inputs for your Skill Gap & Resume Intelligence pipeline:
target_role     = target.occupation         # e.g., "Data Engineer"
growth_score    = target.growth_score       # e.g., 0.76 (momentum)
current_demand  = target.current_demand_score# e.g., 0.91 (market appetite)
ai_exposure     = target.ai_exposure_score   # e.g., 0.45 (augmentation level)
required_skills = target.top_skills         # e.g., ['python', 'sql', 'spark', 'cloud']
knowledge_graph = target.knowledge_graph    # Nodes & Edges (tasks, competencies, tools)
```

### 3.2. Static Schema Contract
A language-agnostic JSON Schema is available at:
`module-1-career-intelligence/src/models/schema_m1.json`

---

## 4. Required Implementation in `module-2-skill-gap/`

Per `context/MODULE-2-SKILL-GAP.md` and `context/INTEGRATION.md`, implement the following components:

```text
module-2-skill-gap/
├── requirements.txt
├── README.md
├── WORKING.md                    <── MANDATORY DELIVERABLE (See Section 6)
├── src/
│   ├── __init__.py
│   ├── api/
│   │   ├── main.py               (FastAPI application on distinct port or sub-router)
│   │   └── routes.py             (/api/v1/skills/analyze_resume, /gap_analysis)
│   ├── models/
│   │   └── schemas.py            (Pydantic models for user profile, gap matrix, roadmap)
│   ├── parsers/
│   │   ├── resume_parser.py      (PDF, DOCX, text parser -> extracted sections)
│   │   └── section_segmenter.py  (Experience, Skills, Education, Projects)
│   └── engines/
│       ├── skill_extractor.py    (Entity extraction + synonym taxonomy normalizer)
│       ├── gap_analyzer.py       (Target skills vs User skills -> Matched, Adjacent, Missing)
│       └── roadmap_generator.py  (Sequenced, prerequisite-aware milestones with hours)
└── tests/
    └── test_skill_gap.py         (100% passing unit & integration tests)
```

### 4.1. Core Technical Deliverables:
1. **Resume & Input Parsing**: Clean text extraction from PDF / DOCX / text inputs without crashing on corrupted formats.
2. **Taxonomy Normalization**: Mapping colloquial/resume terms to standardized competencies (e.g., `ML` $\rightarrow$ `machine_learning`, `PostgreSQL` $\rightarrow$ `sql`).
3. **Skill Gap Matrix**:
   - **Matched Skills**: Skills present in the candidate profile matching target role expectations.
   - **Adjacent / Partial Skills**: Skills where the candidate has foundational overlap (quick-win upskilling).
   - **Critical Missing Skills**: High-priority blockers that the candidate must learn.
   - **Match Score ($[0.0, 1.0]$)**: Calibrated match ratio.
4. **Learning Roadmap Generator**: Sequenced milestones ordered by dependency (e.g., foundational SQL before distributed Spark), accompanied by realistic time estimates.

---

## 5. Development Rules & Boundaries

1. **Strict Directory Isolation**:
   - Touch **ONLY** files inside `module-2-skill-gap/`.
   - Do **NOT** modify `module-1-career-intelligence/` or `context/`.
2. **Conventional Commits**:
   - `feat(module-2): ...`
   - `fix(module-2): ...`
   - `test(module-2): ...`
   - `docs(module-2): ...`
3. **Independent Test Suite**:
   - All tests must run and pass via `pytest module-2-skill-gap/tests/ -v`.

---

## 6. MANDATORY DELIVERABLE: `WORKING.md`

Just as Module 1 provided `module-1-career-intelligence/WORKING.md`, **the Module 2 agent MUST produce a comprehensive `module-2-skill-gap/WORKING.md` before completing their handover.**

### Your `WORKING.md` must thoroughly detail:
1. **System Architecture**: Flowchart from resume upload to final gap analysis & learning roadmap.
2. **Outcome Variable Dictionary**:
   - Explain what each output field represents (e.g., What does a Match Score of `0.72` mean? How is it mathematically derived?).
   - Clarify the scoring logic for *Matched*, *Adjacent*, and *Critical Missing* competencies.
3. **Mathematical & Algorithmic Formulations**:
   - Formulations for skill similarity, experience weighting, and overall role match.
   - Milestone sequencing heuristic and hours/weeks estimation methodology.
4. **Integration Contracts & Exported Schemas**:
   - Detailed Pydantic models and JSON schemas exported for downstream consumption by **Module 3 (Job Matching & Salary)** and the **Integration Orchestration Layer**.
5. **Test Results**: Proof that all unit and integration tests pass with zero regressions.

---

*Once all requirements are satisfied and `module-2-skill-gap/WORKING.md` is committed and pushed, notify the team for Module 3 handover.*
