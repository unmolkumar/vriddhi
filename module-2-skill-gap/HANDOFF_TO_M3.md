# Module 2 → Module 3 Handoff

For the Module 3 (Job Matching & Salary) owner. What Module 2 gives you, where to get it, and what each field means. Full schemas: [`src/models/schema_m2.json`](src/models/schema_m2.json). Formulas: [WORKING.md](WORKING.md).

## How to call Module 2

Base URL: `http://localhost:8002` (Module 1 is on 8001). Start it with `cd module-2-skill-gap && uvicorn src.api.main:app --port 8002`. Don't import Module 2's Python code (context/AGENTS.md §7); use the API, or let the integration layer pass the JSON along.

| You need | Call | You get |
|---|---|---|
| A user's profile from a resume | `POST /api/v1/skills/analyze_resume` (multipart `file`, optional `location`) | `{"profile": UserProfile, "gap_analysis": null}` |
| The gap against a target role | `POST /api/v1/skills/gap_analysis` (`target_role`, `required_skills`, `profile` or `manual_profile`) | `GapAnalysisResult` |
| Skills in a job description | `POST /api/v1/skills/extract` (`{"text": ..., "use_llm": false}`) | `{"skills": [{id, display, maps_to, in_taxonomy, is_category, ...}], "warnings": []}`; `is_category: true` marks broad fields (ai, cloud, devops) that shouldn't be offered as a skill to learn |
| A gap against one job listing | `/skills/extract` on the description, then `/skills/gap_analysis` with those ids as `required_skills` (INTEGRATION.md scenario 3) | `GapAnalysisResult` for that job |
| **A match against one job, any occupation (v2)** | `POST /api/v2/skills/match_text` (`job_text`, optional `soc_code`, and the user's `free_text` / `skills` / `profile`) | `MatchTextResponse` (below) |
| **A match against a page of jobs (v2)** | `POST /api/v2/skills/match_texts` (the user's evidence once + up to 50 `jobs`) | `{"results": [MatchTextResponse + job_id], "warnings"}` (below) |

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

## v2: `POST /api/v2/skills/match_text` (any occupation)

Use this when the job isn't a tech role, or when you want a job-level score from the listing's own text. It needs module 1 running (`503 M1_UNAVAILABLE` otherwise; the v1 calls above don't).

Request:
```json
{
  "job_text": "ICU staff nurse wanted. Administer medications to patients, record vital signs and coordinate with doctors. BLS certification required.",
  "soc_code": "29-1141.00",
  "job_title": "Staff Nurse - ICU",
  "free_text": "Staff nurse for six years. I administer medications ...",
  "skills": ["BLS"],
  "profile": null
}
```
- `job_text`: the listing's description, 20–50,000 characters. It is split into clauses; clauses under 3 words ("Full time") are ignored.
- `soc_code`: optional. When you know the job's occupation (from module 1's `/occupations/search` on the title), its core O*NET/market requirements are blended in.
- The user's evidence: any of `free_text`, `skills` or a v1 `profile` (from `/api/v1/skills/analyze_resume`).

Response fields:

| Field | Meaning |
|---|---|
| `match_score` | 0–1. `blend × job_text_score + (1 − blend) × occupation_score`; `blend` is 0.6 with a `soc_code`, else 1.0 |
| `job_text_score` | Weighted coverage of the job's own clauses by the user's evidence |
| `occupation_score` | The user's v2 match score for `soc_code` (null without it) |
| `met[]`, `missing[]` | Top 15 each: `requirement`, `status`, `similarity`, `credit`, `reason` (`alias` / `semantic` / `implied_by_role` / `none`), `provenance` (`job_text` for the listing's clauses; `onet` / `india_postings` / `curated` for the occupation's), `evidence` (`text`, `evidence_type` work/project/mentioned/self, `span`, `translated` + `original_text` when a Hinglish or Hindi sentence was rewritten in English, and `original_text` + `rewrites` when shorthand was expanded, e.g. `["BP -> blood pressure"]`) |
| … on each `met[]` / `missing[]` item | `requirement_id`: stable id, `'{item_type}:{module 1 item_id}'` (`':curated'` added for hand-written rows) or `'job:{hash of the normalised clause}'`, so the same requirement has the same id across listings. `weight`: the raw item weight (unchanged). `effective_weight`: the share of `match_score` the item carries when fully met (blend and experience included). `score_gain_if_met` (partial/missing only): how much `match_score` would rise if it were fully met, `= effective_weight × (1 − credit)`. `missing[]` is ordered by `score_gain_if_met` |
| `job_requirements` | How many clauses were taken from the job text |
| `m1_version`, `warnings` | Module 1 data version; notes such as unknown experience |

Education lines (degrees, institutions) are not task evidence either. Job-title lines in the user's evidence count as role history: with a `soc_code`, a past title in that occupation gives its tasks an implied partial credit (`reason: "implied_by_role"`, never `met`). Requirements outside the scope of practice in India (e.g. nurses prescribing) are left out. Scores are comparable across jobs for the same user, so you can rank listings by `match_score` and show `missing[]` as the reasons. For the full picture of a role (verdict, alternatives, roadmap), call `POST /api/v2/skills/gap_analysis` once with the role or SOC. Contract: [`src/models/schema_m2_v2.json`](src/models/schema_m2_v2.json); formulas: WORKING.md §12.

## v2: `POST /api/v2/skills/match_texts` (a page of jobs)

The same match for up to 50 jobs in one call. The user's evidence is parsed, encoded and its past titles resolved once; all jobs' clauses are encoded in one batch, each occupation is scored once per SOC, and clause vectors and parsed job texts are cached in memory (listings recur across users and pages). Measured on CPU (MiniLM, 14 threads), 50 jobs of ~150 words, half with a `soc_code`, occupations warm: **~2.9 s** for a page of never-seen listings (2.8–3.2 s), **~0.2 s** for listings seen before.

Request:
```json
{
  "free_text": "ITI electrician, 7 years. House wiring, DB and MCB fitting, earthing, fault finding with multimeter.",
  "experience_years": 7,
  "jobs": [
    {"job_id": "adz-1", "job_title": "Electrician", "soc_code": "47-2111.00",
     "job_text": "Electrician for commercial sites in Gurugram. Read electrical drawings and install conduit and wiring. Test circuits and repair faults. ITI with 3+ years."},
    {"job_id": "adz-2", "job_text": "Maintenance electrician for a factory. Maintain motors, panels and PLC controls."}
  ]
}
```
- `jobs`: 1–50, unique `job_id`s; `job_text` 1–50,000 characters (no 20-character minimum here: short listings come back with a warning and few requirements, so flag them yourself); `job_title` is echoed back; `soc_code` optional, as in `match_text`.
- Errors: more than 50 jobs, none, duplicate `job_id`s or no evidence → 422 `INVALID_REQUEST`. A `soc_code` module 1 can't give doesn't fail the page: that job is matched on its text alone (`blend: 1.0`) with a warning.

Response (trimmed: one job, one met and two missing items):
```json
{
 "results": [
  {"job_id": "adz-1", "job_title": "Electrician", "match_score": 0.4016, "job_text_score": 0.5556,
   "occupation_score": 0.1706, "blend": 0.6, "soc_code": "47-2111.00", "occupation_title": "Electricians",
   "job_requirements": 3,
   "met": [{"requirement": "Electrician for commercial sites in Gurugram.", "requirement_id": "job:f156254579a7",
            "status": "met", "credit": 0.8333, "weight": 1.0, "effective_weight": 0.2, "score_gain_if_met": null,
            "provenance": "job_text"}],
   "missing": [{"requirement": "Read electrical drawings", "requirement_id": "job:8b2697832318", "status": "missing",
                "credit": 0.0, "weight": 1.0, "effective_weight": 0.2, "score_gain_if_met": 0.2, "provenance": "job_text"},
               {"requirement": "Circuit Troubleshooting", "requirement_id": "market_skill:circuit_troubleshooting:curated",
                "status": "missing", "credit": 0.0, "weight": 0.25, "effective_weight": 0.0151,
                "score_gain_if_met": 0.0151, "provenance": "curated"}],
   "warnings": []}
 ],
 "warnings": []
}
```
Each result has exactly `match_text`'s fields plus `job_id` and `job_title`, in request order. Notes about the user's evidence (e.g. experience unknown) are in the top-level `warnings` once, not repeated per job.

**For "unlocks":** group `missing[]` across jobs by `requirement_id`; `score_gain_if_met` tells you how far each job would move, so you can count the jobs that would cross your good/strong threshold if the user met that requirement.

## Errors

Every error is `{"error": {"code": "...", "message": "..."}}`. Upload problems return 400/413/415 (`CORRUPT_FILE`, `ENCRYPTED_FILE`, `FILE_TOO_LARGE`, `UNSUPPORTED_FORMAT`, …), and invalid JSON returns 422 `INVALID_REQUEST`. v2 adds 404 `OCCUPATION_NOT_FOUND` / `ROLE_NOT_RESOLVED`, 503 `M1_UNAVAILABLE` and 502 `M1_BAD_RESPONSE`. If Module 2 is down, job search can still run on typed skills; per INTEGRATION.md, the product shouldn't fail because one module is unavailable.

## Not in Module 2

No live jobs, job ranking, salary or negotiation: those are yours. Module 2 doesn't call Module 3.
