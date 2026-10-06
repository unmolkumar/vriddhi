# Module 2: Skill Gap & Resume Intelligence

Turns a resume (PDF, DOCX, TXT) or typed skills into an evidence-based skill profile, compares it with a target role's required skills from Module 1, and returns a match score, verdict, skill-gap matrix and a prerequisite-ordered learning roadmap.

- Spec: [context/MODULE-2-SKILL-GAP.md](../context/MODULE-2-SKILL-GAP.md) · Rules: [context/AGENTS.md](../context/AGENTS.md) · Contracts: [context/INTEGRATION.md](../context/INTEGRATION.md)
- How it works, formulas and contracts: [WORKING.md](WORKING.md) · JSON Schema: [src/models/schema_m2.json](src/models/schema_m2.json)
- Branch: `feat/module-2-skill-gap` · Port: **8002** (Module 1 uses 8001)

> **v2 in progress:** a general career engine for any occupation (module 1 v2.0 requirements, not just tech roles) is being built in `src/general/`. Phase A1 (requirements, evidence, semantic matching, calibration) is in; it has no public endpoint yet. Everything below (v1, `/api/v1/*`) is unchanged. Design and calibration results: [WORKING.md §11](WORKING.md#11-general-engine-v2--a1).

## Install

Python 3.11. From the repo root:

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` on macOS/Linux
pip install -r module-2-skill-gap/requirements.txt
```

No database or other setup. The MiniLM and EasyOCR models download on first use (~80 MB and ~100 MB). Optional: put `GROQ_API_KEY` (and `GROQ_MODEL`, default `openai/gpt-oss-120b`) in the root `.env` to enable the LLM pass for skills the dictionary misses. Without it, extraction is dictionary-only.

## Run

```bash
cd module-2-skill-gap
uvicorn src.api.main:app --port 8002
# Swagger UI: http://localhost:8002/docs
```

## Test

```bash
pytest module-2-skill-gap/tests/ -v      # 333 tests (285 v1 + 48 general engine); the live Groq test is skipped without a key
python module-2-skill-gap/scripts/calibrate.py --tune   # general engine: 15 profiles x 15 occupations, thresholds
```

General engine settings (optional, root `.env`): `M1_BASE_URL` (default `http://localhost:8001`), `EMBEDDING_MODEL` (default `all-MiniLM-L6-v2`).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/skills/analyze_resume` | Upload a resume → profile (+ gap analysis if `target_role` and `required_skills` are sent) |
| POST | `/api/v1/skills/gap_analysis` | Profile or typed skills + Module 1 target → gap analysis |
| POST | `/api/v1/skills/extract` | Skills in a job description or other text (no levels or evidence) |
| GET | `/api/v1/health` | Status, taxonomy size, similarity backend |

Errors always look like `{"error": {"code": "ENCRYPTED_FILE", "message": "..."}}`. Codes: `FILE_TOO_LARGE` (413), `TOO_MANY_PAGES` (413), `UNSUPPORTED_FORMAT` (415), `ENCRYPTED_FILE`, `CORRUPT_FILE`, `EMPTY_DOCUMENT`, `OCR_FAILED` (400), `INVALID_REQUEST` (422).

### Example: analyze a resume

```bash
curl -X POST http://localhost:8002/api/v1/skills/analyze_resume \
  -F file=@tests/fixtures/resume_text.pdf -F location=Bangalore -F use_llm=false
```

```json
{
  "profile": {
    "skills": [
      {"name": "python", "display": "Python", "category": "language", "level": 4, "confidence": 0.9,
       "evidence": ["work_supported", "project_supported", "resume_mentioned"], "needs_verification": false},
      {"name": "sql", "display": "SQL", "level": 3, "confidence": 0.9, "evidence": ["work_supported", "resume_mentioned"]},
      {"name": "docker", "display": "Docker", "level": 1, "confidence": 0.5, "evidence": ["resume_mentioned"]}
    ],
    "experience_years": 7.5,
    "internship_years": 0.5,
    "education": ["B.Tech Computer Science"],
    "work_history": [{"title": "Data Analyst", "company": "Swiggy", "start": "2021-06", "end": null, "current": true, "internship": false}],
    "location": "Bengaluru",
    "source": {"format": "pdf", "pages": 1, "ocr_used": false, "ocr_pages": [], "text_sha1": "…",
               "sections_found": ["summary", "experience", "projects", "skills", "education"]},
    "warnings": []
  },
  "gap_analysis": null
}
```
(Abridged: 13 skills in full.)

### Example: extract skills from a job description

```bash
curl -X POST http://localhost:8002/api/v1/skills/extract -H "Content-Type: application/json" \
  -d '{"text": "Backend engineer: Java, Spring Boot, PostgreSQL, Docker. Infrastructure automation a plus."}'
```

```json
{
  "skills": [
    {"id": "automation", "display": "Automation", "category": "devops", "maps_to": "devops", "in_taxonomy": true, "is_category": true, "source": "dictionary", "matches": ["Infrastructure automation"]},
    {"id": "docker", "display": "Docker", "category": "devops", "maps_to": "containerization", "in_taxonomy": true, "is_category": false, "source": "dictionary", "matches": ["Docker"]},
    {"id": "java", "display": "Java", "category": "language", "maps_to": null, "in_taxonomy": true, "source": "dictionary", "matches": ["Java"]},
    {"id": "postgresql", "display": "PostgreSQL", "category": "database", "maps_to": "sql", "in_taxonomy": true, "source": "dictionary", "matches": ["PostgreSQL"]},
    {"id": "spring_boot", "display": "Spring Boot", "category": "framework", "maps_to": "spring", "in_taxonomy": true, "source": "dictionary", "matches": ["Spring Boot"]}
  ],
  "warnings": []
}
```

### Example: gap analysis with typed skills

```bash
curl -X POST http://localhost:8002/api/v1/skills/gap_analysis -H "Content-Type: application/json" -d '{
  "target_role": "Data Engineer",
  "required_skills": ["python", "sql", "spark", "cloud"],
  "manual_profile": {"skills": ["python", "excel"], "experience_years": 1},
  "typical_experience": {"min": 0, "max": 3},
  "hours_per_week": 8
}'
```

Abridged response:
```json
{
  "target_role": "Data Engineer",
  "match_score": 0.3137,
  "match_percent": 31,
  "verdict": "under_skilled",
  "verdict_message": "You cover an estimated 31% of what Data Engineer asks for. You're yet to learn SQL, Apache Spark, Cloud Computing.",
  "skills": {"matched": [], "weak": ["python"], "adjacent": ["spark"], "critical_missing": ["sql", "cloud"], "above_requirement": []},
  "roadmap": {
    "milestones": [
      {"order": 1, "skill": "python", "kind": "weak", "hours_factor": 0.5, "estimated_hours": {"low": 15, "high": 30},
       "weeks": {"low": 2, "high": 4}, "cumulative_weeks": {"low": 2, "high": 4}},
      {"order": 2, "skill": "sql", "kind": "missing", "hours_factor": 1.0, "estimated_hours": {"low": 30, "high": 60},
       "weeks": {"low": 4, "high": 8}, "cumulative_weeks": {"low": 6, "high": 12}},
      {"order": 3, "skill": "spark", "kind": "adjacent", "reason": "Apache Spark builds on Python, which you know.",
       "prerequisites": ["python", "sql"], "hours_factor": 0.5, "estimated_hours": {"low": 30, "high": 60},
       "weeks": {"low": 4, "high": 8}, "cumulative_weeks": {"low": 10, "high": 19}},
      {"order": 4, "skill": "cloud", "kind": "missing", "hours_factor": 1.0, "estimated_hours": {"low": 30, "high": 60},
       "weeks": {"low": 4, "high": 8}, "cumulative_weeks": {"low": 14, "high": 27}}
    ],
    "note": "Hours and weeks are estimated ranges based on each skill's difficulty tier, not guarantees. ..."
  }
}
```

Skills carry `is_category`: broad fields like `cloud` or `devops` are explained (with concrete `category_children`) but never put on the roadmap. Pass module 1's `top_skill_weights` unchanged to weight skills by real demand (`importance_source: "m1_weights"`). `typical_experience` is optional; without it the band comes from the job title, and `experience_source` in the response says which was used. Module 3 integration notes: [HANDOFF_TO_M3.md](HANDOFF_TO_M3.md).

A full response for Module 1's Data Scientists target (from `/analyze_resume` profile) is in WORKING.md §5.4 and the test `test_worked_example_data_scientist`.

## Layout

```text
module-2-skill-gap/
├── data/taxonomy/skills.json      483 skills: ids, aliases, maps_to, prerequisites, difficulty tiers
├── scripts/calibrate.py           general engine calibration (v2)
├── src/
│   ├── api/                       FastAPI app (main.py, routes.py)
│   ├── engines/                   skill_extractor, profile_builder, similarity, gap_analyzer, roadmap_generator
│   ├── general/                   v2: m1_client, requirements, evidence, embeddings, matcher, calibration
│   ├── models/                    schemas.py (Pydantic) + schema_m2.json
│   └── parsers/                   resume_parser (PDF/DOCX/TXT/OCR), section_segmenter
└── tests/                         fixtures/, mocks/ (Module 1 data), calibration/ (v2 profiles), test_*.py
```

Module 2 never imports Module 1 or Module 3. Module 1 data arrives as plain request fields through the integration layer (v1), or over Module 1's REST API (v2 general engine).
