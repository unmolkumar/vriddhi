# Module 2: Skill Gap & Resume Intelligence — Working Specification

**Branch:** `feat/module-2-skill-gap` · **Owner:** Chaitanya Sharma
**Question answered:** *"Where is this person now, how well do they match the target role, and what should they learn next?"*

> **Status: step 1 of 2 done.** Parsing, skill extraction, taxonomy and evidence-based profiles are built and tested (§1–§6). Gap matrix, match score, overqualification, roadmap and the API are step 2 (§8). This document grows with each step.

---

## 1. Architecture

```text
 Resume upload (PDF / DOCX / TXT)          Manual entry (skills [+ claimed level], years, city)
          │                                              │
          ▼                                              │
 parsers/resume_parser.parse_document                    │
   PDF  → PyMuPDF text per page                          │
          page with < 30 non-space chars → EasyOCR       │
   DOCX → python-docx paragraphs + table cells           │
   TXT  → utf-8                                          │
   bad input → ResumeParseError(code)                    │
          │                                              │
          ▼                                              │
 parsers/section_segmenter.segment                       │
   header · summary · experience · projects ·            │
   skills · education · other                            │
          │                                              │
   ┌──────┴───────────────┬───────────────────┐          │
   ▼                      ▼                   ▼          ▼
 extract_work_history   engines/skill_extractor.extract_skills   (same extractor)
   titles, companies,     pass 1: taxonomy dictionary + context rules
   experience_years,      pass 2: Groq (optional, fail-safe, cached, grounded)
   internship_years       → SkillHit(id, display, category, source, matches)
 extract_education               │
   │                             ▼
   └──────────► engines/profile_builder ◄──────────────────────┘
                  evidence per section → level + confidence
                  → UserProfile (Pydantic, superset of INTEGRATION.md profile)
                                 │
                                 ▼
                 step 2: gap_analyzer → roadmap_generator → FastAPI
```

Nothing is written to disk except the optional LLM cache (`data/cache/`, git-ignored, keyed by text hash). The resume file and its text are not stored; the profile keeps only `source.text_sha1`.

---

## 2. Input parsing

| Input | How | Limits / errors |
|---|---|---|
| PDF | PyMuPDF `get_text(sort=True)` per page | > 5 MB → `FILE_TOO_LARGE`; > 10 pages → `TOO_MANY_PAGES`; password → `ENCRYPTED_FILE`; unreadable → `CORRUPT_FILE` |
| Scanned PDF page | Page rendered at 200 dpi, read with EasyOCR (English, CPU), boxes grouped into lines by vertical centre | ~10 s per page on CPU plus ~25 s model load on first use; `OCR_FAILED` if EasyOCR errors |
| DOCX | python-docx paragraphs and table rows (many templates use tables) | broken zip/XML → `CORRUPT_FILE` |
| Legacy `.doc` / protected Office | detected by OLE magic bytes | `UNSUPPORTED_FORMAT` (save as PDF/DOCX) |
| Plain text | utf-8 (invalid bytes replaced) | binary data → `UNSUPPORTED_FORMAT`; blank → `EMPTY_DOCUMENT` |
| Manual entry | `ManualProfileInput` (Pydantic-validated) | years 0–60, levels 0–5 |

The format is detected from the bytes; the filename only breaks ties. Every error serialises to the integration error shape: `{"error": {"code": "CORRUPT_FILE", "message": "..."}}`.

### Sections
A heading must be alone on its line, matched case-insensitively against a list per section (e.g. *Work Experience*, *Technical Skills*, *Academic Projects*). `Skills: Python, SQL` is content, not a heading. Text before the first heading is `header`.

### Work history and experience
- Date ranges: `Jun 2021 - Present`, `Jan'22 – Jun'22`, `01/2020 - 12/2021`, `2018 - 2020`, `March 2024 to Current`, and OCR output where the dash is gone (`Jun 2021 Present`).
- Year-only dates count from January. Future starts and spans over 50 years are ignored.
- **experience_years** = length of the union of all non-internship spans (inclusive months, overlaps counted once) ÷ 12, rounded to the nearest 0.5.
- **internship_years** = same over entries whose title or line contains "intern". Kept separate.
- Title/company come from the date line split on `|`, `,`, `;`, ` - `, ` at `; if there are no separators, the company is the text after the last title word (*"Data Analyst Swiggy"*); if still missing, a short line (≤ 6 words) just above is used.

### Education
Degree keywords (B.Tech, B.E., M.Tech, B.Sc, M.Sc, BCA, MCA, BBA, MBA, B.Com, PhD, Diploma, PGDM, Bachelor/Master of …), field after the degree (*in Computer Science*, *(Statistics)*), institution from a nearby part containing Institute/University/College/IIT/NIT/IIIT/BITS/…, year = latest 4-digit year on the line or the next two. School rows (Class XII) are skipped. `education` (list of strings, per INTEGRATION.md) is `"<degree> <field>"`.

---

## 3. Skill taxonomy (`data/taxonomy/skills.json`)

482 curated tech skills relevant to Indian tech hiring, 9 categories (language, framework, data, ml, cloud, devops, database, tool, concept).

| Field | Meaning |
|---|---|
| `id` | snake_case id, the same string module 1's `normalize_skill` produces for the display name (overrides: `spark`, `cloud`, `dotnet`, `dotnet_core`, `fsharp`) |
| `display` | Human name ("Apache Spark") |
| `aliases` | Lowercase variants, abbreviations, misspellings ("ml", "m.l.", "machine-learning", "pyhton") |
| `category` | One of the 9 categories |
| `maps_to` | Coarser id, forming a chain: `amazon_s3 → aws → cloud`, `postgresql → sql`, `tensorflow → deep_learning → machine_learning → ai`. 378 skills have one |
| `prerequisites` | Ids to learn first (`spark → [python, sql]`, `kubernetes → [docker, linux]`). 139 skills have them; the graph is acyclic (tested) |
| `difficulty_tier` | 1 foundational tool/syntax · 2 working skill · 3 deep specialisation. Used for step-2 time estimates |
| `context` | Rules for ambiguous aliases (below) |
| `esco_uri` | `null` for every skill. This is an **ESCO-aligned taxonomy, mapping in progress**, not ESCO. Don't describe it as ESCO until the URIs are filled from the ESCO CSV |

---

## 4. Skill extraction (`engines/skill_extractor.py`)

One function, `extract_skills(text, use_llm=True, skills_context=False) -> list[SkillHit]`, is used for resumes and (in step 2) job descriptions.

**Pass 1, dictionary.** Each alias compiles to a regex with symbol-aware boundaries (`C` doesn't match inside `C++`, `C#` or `R&D`) and flexible separators (`machine learning` = `machine-learning` = `machinelearning`). On overlapping matches the longest wins (*React Native* over *React*, *Spring Boot* over *Spring*, *PySpark* doesn't also yield *SQL* via *MySQL*). Emails and profile URLs are removed first.

**Ambiguous aliases.** Common English words or letters only match with context:
- `list` rule (`go`, `c`, `spring`, `express`, `node`, `m.l.`, `rest`, …): the match must be a whole item of a delimited list (`Languages: Python, Go, SQL`) or the text must be a skills list (`skills_context=True`, used for the resume Skills section and typed input).
- `case` rule (`r`, `excel`, `rust`, `swift`, `spark`, `ai`, …): the list rule, **or** written with a capital letter mid-sentence (*"data analysis in R"*, *"Advanced Excel"*). *"excel in teamwork"*, *"Excel in teamwork"* at a sentence start, *"Go-to-market"*, *"Grade C"*, *"Spring 2023"* and *"M.L. Sharma"* don't match.

**Pass 2, Groq** (`llama-3.3-70b-versatile`, JSON mode, temperature 0, 10 s timeout, no retries). Asked only for skills the dictionary missed. Each returned name is resolved through the taxonomy; unknown ones are kept with `in_taxonomy=false` and a slug id. **A name is dropped unless it, or one of its aliases, appears in the text**, so the LLM can't add skills that aren't there. Results are cached on disk by `sha1(prompt version | model | text)`. No API key, timeout, rate limit, auth error or bad JSON → pass-1 results, never an exception.

**Normalisation.** `resolve_skill(name)` accepts a taxonomy id, a module 1 id (`apache_spark`, `rest_api`, `weights___biases`), an alias (`k8s`, `Postgres`) or a display name. `coarser_ids(id)` walks `maps_to`.

---

## 5. Evidence and skill levels (`engines/profile_builder.py`)

Per context/AGENTS.md §14, a mentioned skill is not an expert skill.

| Evidence | Assigned when | Base level | Confidence |
|---|---|---|---|
| `work_supported` | found in an Experience entry (bullets, not the job-title line) | 3 Intermediate | 0.90 |
| `project_supported` | found in a Projects entry | 2 Basic | 0.75 |
| `resume_mentioned` | found anywhere else (Skills list, Summary, Education, …) | 1 Beginner | 0.50 |
| `self_reported` | typed by the user | 1 Beginner | 0.40 |

- A skill keeps every evidence it earns; level and confidence come from the strongest.
- Work **and** project evidence: +1 level (`MULTI_EVIDENCE_BONUS`).
- Evidence alone never exceeds level 4 (`MAX_DERIVED_LEVEL`); 5 (Expert) is never inferred.
- Typed levels (`claimed_level`): without work/project evidence the level is capped at 2 (`SELF_REPORTED_CAP`), and a claim above that sets `needs_verification=true` (the spec's *"Cloud = Expert, no cloud project → requires verification"*).
- Unknown typed skills are kept (`in_taxonomy=false`) with a warning, never dropped.
- Locations normalise to the names module 1 uses (`bangalore` → `Bengaluru`, `Gurgaon` → `Gurugram`).

All constants are named at the top of the module and will be tuned on real resumes.

---

## 6. Contracts

### Output: `UserProfile` (`src/models/schemas.py`)
A superset of the common user profile in context/INTEGRATION.md: `user_id`, `skills[].name/level/evidence`, `experience_years`, `education: list[str]`, `location`, `preferred_locations`, `target_occupation`. Additive fields: per-skill `display`, `category`, `in_taxonomy`, `maps_to`, `confidence`, `claimed_level`, `needs_verification`; profile `internship_years`, `education_details`, `work_history`, `source` (`format`, `pages`, `ocr_pages`, `text_sha1`, `sections_found`), `warnings`.

Evidence values are module-2 tags (`work_supported`, …). INTEGRATION.md's example uses free text (`"project"`, `"internship"`); the integration layer can map them if it needs to.

Example (text-PDF fixture, `use_llm=False`):
```json
{
  "skills": [
    {"name": "python", "display": "Python", "category": "language", "maps_to": null, "level": 4, "confidence": 0.9,
     "evidence": ["work_supported", "project_supported", "resume_mentioned"], "needs_verification": false},
    {"name": "sql", "level": 3, "confidence": 0.9, "evidence": ["work_supported", "resume_mentioned"]},
    {"name": "xgboost", "maps_to": "machine_learning", "level": 2, "confidence": 0.75, "evidence": ["project_supported"]},
    {"name": "docker", "maps_to": "containerization", "level": 1, "confidence": 0.5, "evidence": ["resume_mentioned"]}
  ],
  "experience_years": 7.5,
  "internship_years": 0.5,
  "education": ["B.Tech Computer Science"],
  "work_history": [{"title": "Data Analyst", "company": "Swiggy", "start": "2021-06", "end": null, "current": true}],
  "location": "Bengaluru",
  "source": {"format": "pdf", "pages": 1, "ocr_pages": [], "text_sha1": "…",
             "sections_found": ["summary", "experience", "projects", "skills", "education"]},
  "warnings": []
}
```
(Abridged; the full output has 13 skills, 3 work entries and `education_details`.)

### Integration contracts: module 1 → module 2

The handover suggests `from module_1_career_intelligence.src import CareerIntelligenceService`. That doesn't work as written (the folder is `module-1-career-intelligence`, with hyphens, so it isn't an importable package), and context/AGENTS.md §3/§7 forbid importing another module's implementation anyway.

**Module 2's input contract therefore takes plain data:**

| Field | Type | From module 1 |
|---|---|---|
| `target_role` | str | `CareerAnalysisResponse.occupation` |
| `required_skills` | list[str], module-1 skill ids | `top_skills` (ordered by demand) |
| `skill_importance` | optional dict id → weight in [0, 1] | not exported today; if absent, step 2 derives weights from `top_skills` order |

The integration layer (`integration/`) calls module 1 (API `POST /api/v1/career/analyze` or its adapter) and passes these fields in. Module 2 tests use mocks in `tests/mocks/m1_contract.json`, built from module 1's WORKING.md, README and INTEGRATION.md examples.

**Vocabulary.** Module 1 has no fixed skill list: `top_skills` are whatever posting skill strings become after its `normalize_skill` (lowercase, non-alphanumerics → `_`, ~30 aliases). Module 2 replicates that function (`m1_slug`) and indexes every alias through it, so `apache_spark`, `power_bi` or `rest_api` resolve even though our ids are `spark` / `power_bi` / `rest_apis`. Tested: every id in module 1's docs (`python, machine_learning, sql, deep_learning, cloud, docker, aws, spark`), every target of its alias table, and 40 ids derived from common posting strings.

**Notes for the module 1 owner** (no change made to module 1):
1. The direct-import example in HANDOVER.md §3.1 and WORKING.md §5.1 can't run from another module (hyphenated folder; cross-module imports are against AGENTS.md). Consumers should go through the API or the integration layer.
2. Single-character skills (`c`, `r`) are dropped by `normalize_skill`'s `len(ns) > 1` filter, so C and R demand never reaches `top_skills`.
3. A skill-importance signal (e.g. frequency share per occupation, which `get_top_skills` already computes as `freq`) would let module 2 weight the match score by real demand instead of list order. Suggest adding `top_skill_weights` (additive, non-breaking).
4. The distinct `skill_normalized` values for the seven target roles (a short export) would let module 2 test full vocabulary coverage instead of documented examples only.

---

## 7. Tests

```bash
pip install -r module-2-skill-gap/requirements.txt
pytest module-2-skill-gap/tests/ -v
```

**154 passed** (~75 s; the two OCR tests take most of it). Covered: taxonomy integrity (fields, unique ids and aliases, no `maps_to`/prerequisite cycles); module 1 id resolution; spec normalisation examples; ambiguous aliases; longest match; LLM off / no key / timeout / rate limit / bad JSON / grounding / cache; PDF, scanned PDF, DOCX, TXT; corrupt, encrypted, oversized, too many pages, empty, binary and legacy files; sections; seven date formats; overlap merging; internships; education; evidence tags; levels; manual entry and verification flags; INTEGRATION profile shape; resume text not retained.

Fixtures are generated by `tests/fixtures/make_fixtures.py` (fictional person and companies).

**Known limitations**
- OCR misreads some names (*Power BI* → *Power Bl*); the scanned-resume test allows a 10% skill-recall gap.
- Section headings and date formats are heuristic; unusual layouts (two columns, headings inside tables) may lose structure. Warnings are returned when Experience or Skills isn't found.
- The Groq pass is untested against the live API (no key configured yet); its behaviour is tested with mocks.

---

## 8. Step 2 plan (next round)

1. **Gap matrix** for `required_skills` vs the profile:
   - *matched*: the profile has the id (or a finer skill whose `maps_to` chain reaches it, e.g. PostgreSQL for `sql`);
   - *adjacent*: a related skill via `maps_to` siblings, shared prerequisites, or MiniLM (`all-MiniLM-L6-v2`) cosine similarity ≥ a named threshold;
   - *critical missing*: neither, ranked by module 1 importance.
   Per skill: required level, current level, `gap = required − current` (spec), evidence.
2. **match_score ∈ [0, 1]**: importance-weighted coverage; adjacent skills earn partial credit; an experience factor; all weights named constants, documented here with the formula.
3. **Overqualification and verdict**: under-qualified / good fit / over-qualified (high coverage + experience above the role's typical range + many strong skills the role doesn't use).
4. **Roadmap**: missing skills in prerequisite (topological) order; time as labelled **estimate ranges** by difficulty tier, discounted for adjacent skills, worded per AGENTS.md §12 (*"estimated 20–40 hours"*), never as guarantees.
5. **API** (own port): `POST /api/v1/skills/analyze_resume` (multipart file or JSON), `POST /api/v1/skills/gap_analysis`, `GET /api/v1/health`; structured errors; OpenAPI at `/docs`.
6. README payload examples and a module-2 contract test.

**Out of scope for module 2:** live jobs, job ranking, salary (module 3); forecasting (module 1).
