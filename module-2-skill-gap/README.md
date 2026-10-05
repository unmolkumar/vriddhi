# Module 2: Skill Gap & Resume Intelligence

Turns a resume (PDF, DOCX, TXT) or typed skills into an evidence-based skill profile, compares it with a target role's required skills from Module 1, and returns a match score, verdict, skill-gap matrix and a prerequisite-ordered learning roadmap.

- Spec: [context/MODULE-2-SKILL-GAP.md](../context/MODULE-2-SKILL-GAP.md) · Rules: [context/AGENTS.md](../context/AGENTS.md) · Contracts: [context/INTEGRATION.md](../context/INTEGRATION.md)
- How it works, formulas and contracts: [WORKING.md](WORKING.md) · JSON Schema: [src/models/schema_m2.json](src/models/schema_m2.json)
- Branch: `feat/module-2-skill-gap` · Port: **8002** (Module 1 uses 8001)

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
pytest module-2-skill-gap/tests/ -v      # 209 tests; the live Groq test is skipped without a key
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/skills/analyze_resume` | Upload a resume → profile (+ gap analysis if `target_role` and `required_skills` are sent) |
| POST | `/api/v1/skills/gap_analysis` | Profile or typed skills + Module 1 target → gap analysis |
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

### Example: gap analysis with typed skills

```bash
curl -X POST http://localhost:8002/api/v1/skills/gap_analysis -H "Content-Type: application/json" -d '{
  "target_role": "Data Engineer",
  "required_skills": ["python", "sql", "spark", "cloud"],
  "manual_profile": {"skills": ["python", "excel"], "experience_years": 1},
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
      {"order": 1, "skill": "python", "kind": "weak", "estimated_hours": {"low": 15, "high": 30}, "estimated_weeks": {"low": 2, "high": 4}},
      {"order": 2, "skill": "sql", "kind": "missing", "estimated_hours": {"low": 30, "high": 60}, "estimated_weeks": {"low": 6, "high": 12}},
      {"order": 3, "skill": "spark", "kind": "adjacent", "reason": "Builds on your Python.", "prerequisites": ["python", "sql"],
       "estimated_hours": {"low": 30, "high": 60}, "estimated_weeks": {"low": 10, "high": 19}},
      {"order": 4, "skill": "cloud", "kind": "missing", "estimated_hours": {"low": 30, "high": 60}, "estimated_weeks": {"low": 14, "high": 27}}
    ],
    "note": "Hours and weeks are estimated ranges based on each skill's difficulty tier, not guarantees. ..."
  }
}
```

A full response for Module 1's Data Scientists target (from `/analyze_resume` profile) is in WORKING.md §5.4 and the test `test_worked_example_data_scientist`.

## Layout

```text
module-2-skill-gap/
├── data/taxonomy/skills.json      482 skills: ids, aliases, maps_to, prerequisites, difficulty tiers
├── src/
│   ├── api/                       FastAPI app (main.py, routes.py)
│   ├── engines/                   skill_extractor, profile_builder, similarity, gap_analyzer, roadmap_generator
│   ├── models/                    schemas.py (Pydantic) + schema_m2.json
│   └── parsers/                   resume_parser (PDF/DOCX/TXT/OCR), section_segmenter
└── tests/                         fixtures/, mocks/ (Module 1 data), test_*.py
```

Module 2 never imports Module 1 or Module 3. Module 1 data arrives as plain request fields through the integration layer.
