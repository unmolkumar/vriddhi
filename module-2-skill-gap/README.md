# Module 2: Skill Gap & Resume Intelligence

Turns a resume (PDF, DOCX, TXT) or typed skills into an evidence-based skill profile, compares it with a target role's required skills from Module 1, and returns a match score, verdict, skill-gap matrix and a prerequisite-ordered learning roadmap.

- Spec: [context/MODULE-2-SKILL-GAP.md](../context/MODULE-2-SKILL-GAP.md) · Rules: [context/AGENTS.md](../context/AGENTS.md) · Contracts: [context/INTEGRATION.md](../context/INTEGRATION.md)
- How it works, formulas and contracts: [WORKING.md](WORKING.md) · JSON Schema: [src/models/schema_m2.json](src/models/schema_m2.json)
- Branch: `feat/module-2-skill-gap` · Port: **8002** (Module 1 uses 8001)

> **v2: any occupation.** `/api/v2/*` is a general career engine for every O*NET occupation, built on module 1 v2's requirements over REST (see [v2 below](#v2-any-occupation)). v1 (`/api/v1/*`, the tech-role taxonomy and gap analyzer) is unchanged. Design, calibration and formulas: [WORKING.md §11–12](WORKING.md#11-general-engine-v2--a1-a1b-and-a1c). Contract: [src/models/schema_m2_v2.json](src/models/schema_m2_v2.json).

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
pytest module-2-skill-gap/tests/ -v      # 404 tests (285 v1 + 119 general engine); the live Groq test is skipped without a key
python module-2-skill-gap/scripts/calibrate.py --verdict   # general engine: tuning / held-out report, verdict threshold
```

General engine settings (optional, root `.env`): `M1_BASE_URL` (default `http://localhost:8001`), `EMBEDDING_MODEL` (default `all-MiniLM-L6-v2`).

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/v1/skills/analyze_resume` | Upload a resume → profile (+ gap analysis if `target_role` and `required_skills` are sent) |
| POST | `/api/v1/skills/gap_analysis` | Profile or typed skills + Module 1 target → gap analysis |
| POST | `/api/v1/skills/extract` | Skills in a job description or other text (no levels or evidence) |
| GET | `/api/v1/health` | Status, taxonomy size, similarity backend |
| POST | `/api/v2/skills/gap_analysis` | Any occupation: role or SOC + free text / skills / v1 profile → score, verdict, gaps, alternatives, roadmap |
| POST | `/api/v2/skills/analyze_resume` | Upload a resume + role or SOC → v1 profile + v2 gap analysis |
| POST | `/api/v2/skills/match_text` | For module 3: a job's text (+ SOC) vs the user's evidence → job-level match |

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

## v2 (any occupation)

Needs module 1 running (`M1_BASE_URL`, default `http://localhost:8001`); v2 returns `503 M1_UNAVAILABLE` when it isn't, and v1 is unaffected. The first request for an occupation takes about 20 s (preparing and encoding it and its related occupations); warm requests take 0.2–0.6 s on CPU.

### Example: gap analysis for any role

```bash
curl -X POST http://localhost:8002/api/v2/skills/gap_analysis -H "Content-Type: application/json" -d '{
  "target_role": "staff nurse",
  "free_text": "Staff Nurse, ICU | Aster Medcity, Kochi | Jun 2021 - Present\n- Look after 2-3 ventilated patients per shift; chart vitals, intake-output and GCS every hour.\n- Give IV and oral medicines as per the doctor'"'"'s orders ...",
  "hours_per_week": 8
}'
```

`target_role` is resolved through module 1 (Indian titles like "staff nurse", "CA" or "ITI electrician" work); send `soc_code` instead to skip resolution. Evidence can be `free_text` (resume text or a description in any style), `skills` (typed, self-reported) and/or `profile` (from `/api/v1/skills/analyze_resume`). Response, abridged (tuning nurse profile, module 1 v2.1 fixture):

```json
{
  "resolution": {"soc_code": "29-1141.00", "title": "Registered Nurses", "confidence": 1.0, "method": "india_alias_exact",
                 "low_confidence": false, "did_you_mean": []},
  "match_score": 0.575,
  "verdict": {"label": "good_fit", "reason": "Your evidence covers 57% of this role's weighted core requirements.",
              "suggested_role": null},
  "score_breakdown": {"skill_score": 0.575, "experience_years": 7.5, "experience_band": [2.0, 6.0],
                      "experience_band_source": "job_zone", "experience_factor": 1.0,
                      "by_type": {"task": {"share": 0.414, "coverage": 0.502, "items": 27},
                                  "market_skill": {"share": 0.276, "coverage": 1.0, "items": 5}, "...": "..."}},
  "strengths": [{"requirement": "Record patients' medical information and vital signs.", "item_type": "task",
                 "status": "met", "similarity": 0.55, "credit": 1.0, "weight": 0.935, "provenance": "onet",
                 "evidence": {"text": "chart vitals", "evidence_type": "work", "section": "experience",
                              "span": [325, 337], "context_span": [279, 372]}}],
  "gaps": [{"requirement": "Maintain accurate, detailed reports and records.", "item_type": "task", "status": "missing",
            "similarity": 0.38, "credit": 0.0, "weight": 0.9225, "provenance": "onet", "evidence": null}],
  "gaps_total": 96,
  "draws_on": [{"name": "Medicine and Dentistry", "item_type": "knowledge", "importance": 0.84, "inferred": true,
                "support": "Inform medical professionals regarding patient conditions and care."}],
  "fit_indicators": [{"name": "Deductive Reasoning", "importance": 0.78, "level": 0.5714}],
  "close_alternatives": [],
  "roadmap": {"items": [{"step": 1, "requirement": "Maintain accurate, detailed reports and records.", "status": "missing",
                         "practice_ideas": ["Maintain medical facility records.",
                                            "Maintain inventory of medical supplies or equipment."],
                         "hours": {"low": 30, "high": 60}, "weeks": {"low": 4, "high": 8}}],
              "total_hours": {"low": 220, "high": 440}, "hours_per_week": 8.0, "note": "Hours and weeks are estimated ranges ..."},
  "provenance_summary": {"scored_items": {"onet": 107, "curated": 23}, "weight_share": {"onet": 0.91, "curated": 0.09},
                         "note": "Curated rows are hand-written in module 1 and count at half weight ..."},
  "m1_version": "2.1.0",
  "warnings": []
}
```

### Example: match a job's text (module 3)

```bash
curl -X POST http://localhost:8002/api/v2/skills/match_text -H "Content-Type: application/json" -d '{
  "job_text": "ICU staff nurse wanted. Administer medications to patients, record vital signs and coordinate with doctors. BLS certification required.",
  "soc_code": "29-1141.00",
  "free_text": "Staff nurse for six years. I administer medications ..."
}'
```

Returns `match_score` (0.6 × the job text's own score + 0.4 × the occupation's score), `job_text_score`, `occupation_score`, and `met` / `missing` items with evidence and provenance (`job_text` for the job's own clauses).

Errors: 422 `INVALID_REQUEST`, 404 `ROLE_NOT_RESOLVED` / `OCCUPATION_NOT_FOUND`, 503 `M1_UNAVAILABLE`, 502 `M1_BAD_RESPONSE`, and v1's file errors for `/analyze_resume`.

## Layout

```text
module-2-skill-gap/
├── data/taxonomy/skills.json      483 skills: ids, aliases, maps_to, prerequisites, difficulty tiers
├── scripts/                       calibrate.py, refetch_extra_occupations.py (v2)
├── src/
│   ├── api/                       FastAPI app (main.py, routes.py v1, routes_v2.py)
│   ├── engines/                   skill_extractor, profile_builder, similarity, gap_analyzer, roadmap_generator
│   ├── general/                   v2: m1_client, requirements, evidence, embeddings, matcher, inference,
│   │                              scoring, roadmap, service, schemas, calibration
│   ├── models/                    schemas.py (Pydantic) + schema_m2.json
│   └── parsers/                   resume_parser (PDF/DOCX/TXT/OCR), section_segmenter
└── tests/                         fixtures/, mocks/ (Module 1 data), calibration/ (v2 profiles), test_*.py
```

Module 2 never imports Module 1 or Module 3. Module 1 data arrives as plain request fields through the integration layer (v1), or over Module 1's REST API (v2 general engine).
