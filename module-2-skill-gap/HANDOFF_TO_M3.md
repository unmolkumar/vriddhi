# Module 2 → Module 3 Handoff

For the Module 3 (Job Matching & Salary) owner. What Module 2 gives you, where to get it, and what each field means. Full schemas: [`src/models/schema_m2.json`](src/models/schema_m2.json). Formulas: [WORKING.md](WORKING.md).

## How to call Module 2

Base URL: `http://localhost:8002` (Module 1 is on 8001). Start it with `cd module-2-skill-gap && uvicorn src.api.main:app --port 8002`. Don't import Module 2's Python code (context/AGENTS.md §7); use the API, or let the integration layer pass the JSON along.

| You need | Call | You get |
|---|---|---|
| A user's profile from a resume | `POST /api/v1/skills/analyze_resume` (multipart `file`, optional `location`) | `{"profile": UserProfile, "gap_analysis": null}` |
| The gap against a target role | `POST /api/v1/skills/gap_analysis` (`target_role`, `required_skills`, `profile` or `manual_profile`) | `GapAnalysisResult` |
| Skills in a job description | `POST /api/v1/skills/extract` (`{"text": ..., "use_llm": false}`) | `{"skills": [{id, display, maps_to, in_taxonomy, ...}], "warnings": []}` |
| A gap against one job listing | `/skills/extract` on the description, then `/skills/gap_analysis` with those ids as `required_skills` (INTEGRATION.md scenario 3) | `GapAnalysisResult` for that job |

Skill ids are the same snake_case ids Module 1 uses (`python`, `machine_learning`, `sql`, `cloud`, …). Variants resolve automatically (`apache_spark`, `Postgres`, `Tableau Desktop`).

## Fields Module 3 consumes

### 1. `profile.skills[]`: what the user can do
Use `name` to match job skills, `level` (0–5) for how well, and `evidence`/`confidence` for how much to trust it. `maps_to` gives the coarser skill (PostgreSQL → `sql`), so a job asking for `sql` can count a PostgreSQL user.
```json
[
  {"name": "python", "display": "Python", "level": 4, "confidence": 0.9,
   "evidence": ["work_supported", "project_supported", "resume_mentioned"], "maps_to": null, "needs_verification": false},
  {"name": "postgresql", "display": "PostgreSQL", "level": 3, "confidence": 0.9,
   "evidence": ["work_supported"], "maps_to": "sql", "needs_verification": false},
  {"name": "aws", "display": "AWS", "level": 2, "confidence": 0.4,
   "evidence": ["self_reported"], "maps_to": "cloud", "claimed_level": 5, "needs_verification": true}
]
```
Evidence order, strongest first: `work_supported` > `project_supported` > `resume_mentioned` > `self_reported`. A skill that only shows `resume_mentioned` or `self_reported` hasn't been demonstrated; please don't treat it as expertise when ranking jobs or estimating salary (AGENTS.md §14).

### 2. `profile.experience_years`, `profile.internship_years`
Full-time experience from merged work dates (overlaps counted once), rounded to 0.5. Internships are separate.
```json
{"experience_years": 7.5, "internship_years": 0.5}
```

### 3. `profile.location`, `profile.preferred_locations`
Normalised to the city names Module 1 uses (`Bengaluru`, `Gurugram`, `Mumbai`, `Delhi NCR`, …).
```json
{"location": "Bengaluru", "preferred_locations": ["Bengaluru", "Pune"]}
```

### 4. `gap_analysis.gap_matrix[]`: per-skill fit for a role or job
`status` is `matched`, `adjacent` (a related skill, quick to learn) or `missing`. For adjacent skills `current_level` is 0, and `related_level` is the level of the related skill.
```json
[
  {"skill": "python", "status": "matched", "reason": "exact", "required_level": 3, "current_level": 4, "gap": -1,
   "importance": 1.0, "priority": "High"},
  {"skill": "deep_learning", "status": "adjacent", "reason": "maps_to", "relation": "is related to scikit-learn",
   "via": "scikit_learn", "required_level": 2, "current_level": 0, "related_level": 2, "gap": 2,
   "importance": 0.6897, "priority": "Medium"},
  {"skill": "cloud", "status": "missing", "required_level": 2, "current_level": 0, "gap": 2,
   "importance": 0.625, "priority": "Medium"}
]
```
Also useful from the same response: `match_score` (0–1), `verdict` (`under_skilled` / `good_fit` / `over_qualified`), and `skills.critical_missing` (ids by importance).

### 5. `gap_analysis.learning_priorities`
The first five skills of the learning roadmap, in the order to learn them (prerequisites first). Use these for "jobs you could reach after learning X" or salary-uplift views.
```json
{"learning_priorities": ["Machine Learning", "Deep Learning", "Cloud Computing", "Docker"]}
```
The full `roadmap.milestones[]` adds estimated hours and weeks per skill. These are estimate ranges, not guarantees.

## Errors

Every error is `{"error": {"code": "...", "message": "..."}}`. Upload problems return 400/413/415 (`CORRUPT_FILE`, `ENCRYPTED_FILE`, `FILE_TOO_LARGE`, `UNSUPPORTED_FORMAT`, …), and invalid JSON returns 422 `INVALID_REQUEST`. If Module 2 is down, job search can still run on typed skills; per INTEGRATION.md, the product shouldn't fail because one module is unavailable.

## Not in Module 2

No live jobs, job ranking, salary or negotiation: those are yours. Module 2 doesn't call Module 3.
