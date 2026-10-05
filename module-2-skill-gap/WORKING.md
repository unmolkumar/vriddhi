# Module 2: Skill Gap & Resume Intelligence — Working Specification

**Branch:** `feat/module-2-skill-gap` · **Owner:** Chaitanya Sharma · **API port:** 8002
**Question answered:** *"Where is this person now, how well do they match the target role, and what should they learn next?"*

Status: complete and integration-ready per context/AGENTS.md §15/§18: self-contained, contract models + JSON Schema, structured errors, 209 passing tests, no cross-module imports.

---

## 1. Architecture

```text
 Resume (PDF / DOCX / TXT)                              Manual entry (skills [+ claimed level], years, city)
        │ POST /api/v1/skills/analyze_resume                     │
        ▼                                                        │
 parsers/resume_parser.parse_document                            │
   PDF  → PyMuPDF text per page                                  │
          page < 30 non-space chars → EasyOCR (≤ 3 pages)        │
          → ocr_repair ("Power Bl" → "Power BI")                 │
   DOCX → python-docx (paragraphs + tables) · TXT → utf-8         │
   bad input → ResumeParseError(code)                            │
        ▼                                                        │
 parsers/section_segmenter                                       │
   segment → header/summary/experience/projects/skills/education │
   extract_work_history → titles, experience_years, internships  │
   extract_education                                             │
        ▼                                                        ▼
 engines/skill_extractor.extract_skills  (taxonomy dictionary + context rules; optional Groq pass)
        ▼
 engines/profile_builder → UserProfile (evidence → level + confidence)
        │
        │  + Module 1 target via integration (plain data):            POST /api/v1/skills/gap_analysis
        │    target_role, required_skills, knowledge_graph?, skill_importance?
        ▼
 engines/gap_analyzer.analyze_gap
   resolve required ids → importance (skill_importance → knowledge_graph → rank decay) → required level
   per skill: matched (exact / maps_to) → adjacent (maps_to / prerequisite / semantic via engines/similarity)
              → missing
   coverage + experience factor → match_score → verdict (+ suggested role) → evidence-based advice
        ▼
 engines/roadmap_generator.build_roadmap
   missing + adjacent + weak skills, missing prerequisites pulled in, topological order by importance,
   estimated hour ranges by difficulty tier, optional weekly milestones
        ▼
 GapAnalysisResult (JSON) → integration layer → Module 3 (UserProfile) / frontend
```

**Storage.** None required (team decision): no MongoDB, no database, so tests and the demo run with zero setup. The only thing written to disk is the optional LLM cache (`data/cache/llm_skills/*.json`, git-ignored, keyed by text hash). Resume files and resume text are never persisted; a profile keeps only `source.text_sha1`.

---

## 2. Input parsing

| Input | How | Errors (HTTP status) |
|---|---|---|
| PDF | PyMuPDF `get_text(sort=True)` per page | `FILE_TOO_LARGE` > 5 MB (413) · `TOO_MANY_PAGES` > 10 (413) · `ENCRYPTED_FILE` (400) · `CORRUPT_FILE` (400) |
| Scanned PDF page | rendered at 200 dpi, EasyOCR (English, CPU), boxes grouped into lines; at most `MAX_OCR_PAGES = 3` pages, the rest skipped with a warning | `OCR_FAILED` (400) |
| DOCX | python-docx paragraphs + table rows | `CORRUPT_FILE` (400) |
| Legacy `.doc` / protected Office | detected by OLE magic bytes | `UNSUPPORTED_FORMAT` (415) |
| Plain text | utf-8 | binary → `UNSUPPORTED_FORMAT` (415); blank → `EMPTY_DOCUMENT` (400) |
| Manual entry | `ManualProfileInput` (Pydantic) | invalid → `INVALID_REQUEST` (422) |

The format is detected from the bytes; the filename only breaks ties. Every error is `{"error": {"code": "...", "message": "..."}}` (context/INTEGRATION.md).

**OCR.** About 10 s per page on CPU, plus ~25 s to load the model the first time. Profiles report `source.ocr_used`, `ocr_pages`, `ocr_seconds` and a warning (*"Scanned resume: read 1 page(s) with OCR in 12.3 s. Check the extracted skills."*) so a UI can show a "this takes a moment" hint. `ocr_repair` then fixes OCR misreads of skill names. A 1–3 word phrase is replaced only if two things hold. First, it isn't already a known alias. Second, after folding `i/l/1/|/! → l`, `0 → o`, `rn → m` and `vv → w`, it equals exactly one taxonomy alias of ≥ 4 characters. Repairs are listed in `source.ocr_repairs` (*"Power Bl -> Power BI"*).

**Sections.** A heading must be alone on its line (*Work Experience*, *Technical Skills*, *Academic Projects*, …). `Skills: Python` is content. Text before the first heading is `header`.

**Work history.**
- Date ranges recognised: `Jun 2021 - Present`, `Jan'22 – Jun'22`, `01/2020 - 12/2021`, `2018 - 2020`, `March 2024 to Current`, and OCR's `Jun 2021 Present`. Year-only dates count from January; future starts are ignored.
- `experience_years` = the union of non-internship spans (inclusive months, overlaps counted once) ÷ 12, rounded to 0.5.
- Internships (the title or line contains "intern") go to `internship_years`.
- Title and company are split from the date line on `|`, `,`, `;`, ` - ` or ` at `. With no separators, the company is the text after the last title word (*"Data Analyst Swiggy"*). Failing both, a short line (≤ 6 words) just above is used.

**Education.** Degree keywords (B.Tech, B.E., M.Tech, B.Sc, M.Sc, BCA, MCA, BBA, MBA, B.Com, PhD, Diploma, PGDM, Bachelor/Master of …) are found first. The field is the text after the degree, the institution is a nearby part containing Institute/University/College/IIT/NIT/IIIT/BITS/…, and the year is the latest year on that line or the next two.

---

## 3. Skill taxonomy and extraction

### 3.1 Taxonomy (`data/taxonomy/skills.json`)
482 curated tech skills in 9 categories. Fields:
- `id`: Module 1-compatible snake_case.
- `display`, `aliases`, `category`.
- `maps_to`: coarser id (378 skills).
- `prerequisites`: 139 skills; acyclic.
- `difficulty_tier`: 1 foundational tool/syntax · 2 working skill · 3 deep specialisation.
- `context`: rules for ambiguous aliases.
- `esco_uri`: `null`. This is an **ESCO-aligned taxonomy, mapping in progress**, not ESCO.

### 3.2 Extraction (`engines/skill_extractor.py`)
`extract_skills(text, use_llm=True, skills_context=False)`, shared by resumes and (via the integration layer) job descriptions.
- **Pass 1, dictionary.** Word boundaries are symbol-aware, so `C` never matches inside `C++`, `C#` or `R&D`. Separators are flexible (`machine-learning` = `machine learning`), and the longest match wins on overlap (*React Native* over *React*). Emails and profile URLs are stripped first.
- **Ambiguous aliases.**
  - `list` rule (`go`, `c`, `spring`, `express`, `node`, `m.l.`, …): the alias must be a whole item of a delimited list, or the text must be a skills list.
  - `case` rule (`r`, `excel`, `rust`, `spark`, `ai`, …): the list rule, or the alias capitalised mid-sentence (*"analysis in R"*, *"Advanced Excel"*).
  - *"go the extra mile"*, *"Grade C"*, *"Spring 2023"* and *"M.L. Sharma"* don't match.
- **Pass 2, Groq** (JSON mode, temperature 0, 10 s timeout, no retries). The default model is `openai/gpt-oss-120b`; override it with `GROQ_MODEL`. The originally planned `llama-3.3-70b-versatile` is no longer served by Groq. `gpt-oss-120b`, `gpt-oss-20b` and `qwen/qwen3.8-27b` all returned identical results on the test prompt in ~1 s.
  - Only skills the dictionary missed are requested, and each must literally appear in the text or it is dropped.
  - Results are cached by `sha1(prompt version | model | text)`.
  - No key, timeout, rate limit, unknown model or bad JSON → dictionary results, never an exception.
- **Normalisation.** `resolve_skill` accepts taxonomy ids, Module 1 ids (`apache_spark`, `rest_api`), aliases (`k8s`, `Postgres`) and display names. `coarser_ids` walks `maps_to`.

---

## 4. Evidence and skill levels (`engines/profile_builder.py`)

Per context/AGENTS.md §14, a mentioned skill is not an expert skill. The scale follows context/MODULE-2-SKILL-GAP.md: 0 not demonstrated … 5 expert.

| Evidence | Assigned when | Base level | Confidence |
|---|---|---|---|
| `work_supported` | in an Experience entry (bullets, not the job-title line) | 3 | 0.90 |
| `project_supported` | in a Projects entry | 2 | 0.75 |
| `resume_mentioned` | anywhere else (Skills list, Summary, …) | 1 | 0.50 |
| `self_reported` | typed by the user | 1 | 0.40 |

- Level and confidence come from the strongest evidence. Work **and** project evidence together add 1 (`MULTI_EVIDENCE_BONUS`).
- Evidence never derives level 5 (`MAX_DERIVED_LEVEL = 4`).
- A typed `claimed_level` without work or project evidence is capped at 2 (`SELF_REPORTED_CAP`). A higher claim sets `needs_verification = true`, as in the spec's *"Cloud = Expert, no cloud project → requires verification"*.
- Unknown typed skills are kept (`in_taxonomy = false`) with a warning.

---

## 5. Gap analysis (`engines/gap_analyzer.py`)

### 5.1 Importance `w` (per required skill)
Priority order:
1. `skill_importance` from the request (clamped to [0, 1]).
2. `knowledge_graph` node weight, for `skill`/`technology` nodes whose label (or id without `skill_`/`tech_`) resolves to the required id.
3. Rank decay over the `required_skills` order: **w_i = 1 / (1 + 0.15 · i)** (`RANK_DECAY`).

Skills without an explicit value fall back to rank decay, and `importance_source` records which source applied. Priority labels: High if w ≥ 0.80, Medium if w ≥ 0.60, Low otherwise.

### 5.2 Required level
- With explicit importance: w ≥ 0.85 → 4, w ≥ 0.70 → 3, else 2 (`IMPORTANCE_TO_LEVEL`).
- With rank decay: the first 3 required skills need 3 (`TOP_REQUIRED_LEVEL`), the rest 2.

### 5.3 Matching (per required skill, first rule that applies)
| Status | Reason | Rule | Current level |
|---|---|---|---|
| matched | `exact` | the profile has the id | that skill's level |
| matched | `maps_to` | a profile skill's `maps_to` chain reaches it (PostgreSQL → **sql**, AWS → **cloud**, scikit-learn → **machine_learning**) | best such skill |
| adjacent | `maps_to` | the profile has the required skill's parent, or a skill under that parent (MySQL ↔ PostgreSQL, SQL → PostgreSQL) | related skill's level |
| adjacent | `prerequisite` | the profile has a prerequisite of it (Docker → Kubernetes) or a skill that builds on it | related skill's level |
| adjacent | `semantic` | cosine(display names) ≥ **0.82** (`SEMANTIC_THRESHOLD`, MiniLM `all-MiniLM-L6-v2`); TF-IDF char 3-gram cosine ≥ 0.82 if the model can't load | related skill's level |
| missing | — | none of the above | 0 |

`gap = required_level − current_level`: > 0 is a gap, 0 is met, < 0 is above the requirement. The buckets are `matched` (gap ≤ 0), `weak` (matched with gap > 0), `adjacent`, `critical_missing` (by importance) and `above_requirement`.

Measured MiniLM cosines show why 0.82 is a conservative bar:
- **Pass:** *Data Visualization ~ Data Visualisation Tools* 0.88, *Tableau Desktop ~ Tableau* 0.85, *Microsoft Excel ~ Excel* 0.97.
- **Don't pass:** *MySQL ~ PostgreSQL* 0.55, *PyTorch ~ TensorFlow* 0.49, *Docker ~ Kubernetes* 0.32.

The taxonomy rules catch those related tools instead, so the three reasons complement each other.

### 5.4 Match score
```
credit_r   = min(1, current_level / required_level)   if matched
           = 0.40  (ADJACENT_CREDIT)                   if adjacent
           = 0                                         if missing
coverage   = Σ w_r · credit_r / Σ w_r
experience_factor = min(1, (years + 1) / (role_min + 1))          (EXPERIENCE_SMOOTHING = 1)
match_score = 0.85 · coverage + 0.15 · experience_factor           (COVERAGE_WEIGHT, EXPERIENCE_WEIGHT)
```
`role_min`/`role_max` come from `typical_experience_years` in the request. Otherwise they come from the title: intern/junior/graduate 0–2, senior 4–8, lead/manager 6–12, principal/staff/head/architect 8–15, anything else 0–5.

**What 0.72 means.** The profile covers about 72% of the importance-weighted requirement at the required levels, after a small experience adjustment. Adjacent skills count 40%, and a level-1 skill against a level-2 requirement counts 50%. It is an estimate of fit, not a hiring probability.

**Worked example** (test `test_worked_example_data_scientist`: the text-PDF fixture against Module 1's Data Scientists `top_skills`):

| i | Required | w_i | Status (via) | Current / required | credit | w·credit |
|---|---|---|---|---|---|---|
| 0 | python | 1.0000 | matched exact | 4 / 3 | 1.0000 | 1.0000 |
| 1 | machine_learning | 0.8696 | matched maps_to (scikit-learn) | 2 / 3 | 0.6667 | 0.5797 |
| 2 | sql | 0.7692 | matched exact | 3 / 3 | 1.0000 | 0.7692 |
| 3 | deep_learning | 0.6897 | adjacent maps_to (scikit-learn) | 2 / 2 | 0.40 | 0.2759 |
| 4 | cloud | 0.6250 | missing | 0 / 2 | 0 | 0 |
| 5 | docker | 0.5714 | matched exact (mentioned only) | 1 / 2 | 0.5000 | 0.2857 |
| | | **Σ 4.5249** | | | | **Σ 2.9105** |

coverage = 2.9105 / 4.5249 = **0.6432**. Experience is 7.5 years against a role_min of 0, so the factor is **1.0**. match_score = 0.85 × 0.6432 + 0.15 × 1.0 = **0.6967** (70%), which gives **good_fit**.

### 5.5 Verdict
| Verdict | Rule |
|---|---|
| `over_qualified` | match_score ≥ 0.85 (`OVER_QUALIFIED_SCORE`) **and** experience > role_max **and** ≥ 3 strong skills (level ≥ 3) the role doesn't use (`OVER_EXTRA_STRONG`) → `suggested_role` = the next rung of Senior → Lead → Principal |
| `under_skilled` | match_score < 0.60 (`UNDER_SKILLED_BELOW`) → *"You're yet to learn X, Y, Z"* in roadmap order |
| `good_fit` | otherwise → *"Apply now"* plus the top missing skills |

Test cases: the fixture against *Data Analyst* (sql, excel, tableau, power_bi, statistics) scores 0.8587, with 7.5 years against a 0–5 band and unused Python, pandas and R. That gives over-qualified, suggesting *Senior Data Analyst*. The same profile at 1 year gives good fit.

### 5.6 Advice
Advice is evidence-based, per required skill:
- The matching skill is only `resume_mentioned`/`self_reported`: *"You list Docker, but nothing in your work or projects shows it. Build a project with Docker."*
- A claim needing verification: *"You rate yourself level 5 in AWS, but your resume doesn't show it yet…"*
- An adjacent skill: *"You already know scikit-learn; Deep Learning builds on it, so this is a quick win."*

---

## 6. Roadmap (`engines/roadmap_generator.py`)

1. **Items:** missing skills, adjacent skills and weak matched skills (gap > 0).
2. **Prerequisites:** for missing and adjacent items, taxonomy prerequisites the user doesn't know are added recursively (`kind = prerequisite`; `required_for` lists the dependants). "Known" includes skills implied through `maps_to` (pandas implies Python).
3. **Order:** Kahn's topological sort over prerequisite edges. Among ready skills, the highest importance goes first, and pulled-in prerequisites inherit their dependant's importance. Prerequisites always come before dependants (tested over the whole taxonomy).
4. **Hours (estimates):** by difficulty tier, tier 1 = 10–25 h, tier 2 = 30–60 h, tier 3 = 60–120 h (`TIER_HOURS`). Adjacent and weak skills are multiplied by 0.5 (`KIND_HOURS_FACTOR`).
5. **Weeks:** with `hours_per_week`, each milestone gets the cumulative week range `ceil(Σ hours / hours_per_week)`.
6. **Length:** at most 15 milestones (`MAX_MILESTONES`); the note says how many were omitted.

The wording follows AGENTS.md §12: every figure is an "estimated" range, and the roadmap note says they are not guarantees.

---

## 7. Output variable dictionary

| Field | Type | Meaning |
|---|---|---|
| `match_score` | float 0–1 | §5.4. ≥ 0.85 strong, 0.60–0.85 good, < 0.60 sizeable gaps |
| `match_percent` | int | `round(match_score × 100)`, for display |
| `verdict` | `under_skilled` · `good_fit` · `over_qualified` | §5.5 |
| `verdict_message` | str | Plain-language summary |
| `suggested_role` | str \| null | Next seniority rung when over-qualified |
| `score_breakdown` | object | `coverage`, `experience_factor`, weights, formula text |
| `importance_source` | `skill_importance` · `knowledge_graph` · `rank_decay` | Where the weights came from |
| `typical_experience_years` | {min, max} | Role band used for the experience factor and over-qualification |
| `gap_matrix[]` | SkillGap | Per required skill: `status`, `reason` (`exact`/`maps_to`/`prerequisite`/`semantic`), `via`, `similarity`, `importance`, `priority`, `required_level`, `current_level`, `gap`, `evidence`, `advice` |
| `skills` | buckets | `matched`, `weak`, `adjacent`, `critical_missing`, `above_requirement` (ids) |
| `strengths` | list[str] | Matched skills meeting the requirement |
| `learning_priorities` | list[str] | First 5 roadmap skills |
| `advice` | list[str] | §5.6 |
| `roadmap` | Roadmap | §6: `milestones[]` (`order`, `kind`, `reason`, `prerequisites`, `required_for`, `difficulty_tier`, `estimated_hours`, `estimated_weeks`), totals, `note` |
| `similarity_backend` | `minilm` · `tfidf` | Which semantic matcher ran |
| `warnings` | list[str] | Duplicates, unknown required skills, ignored knowledge graph |

Profile fields (`UserProfile`) are described in §4 and the schema.

---

## 8. Integration contracts and exported schemas

**Schemas.** The Pydantic models are in `src/models/schemas.py`. Their JSON Schema is exported to **`src/models/schema_m2.json`**: `UserProfile`, `ManualProfileInput`, `GapAnalysisRequest`, `GapAnalysisResult`, `AnalyzeResumeResponse`, `HealthResponse`, `ErrorResponse`. Regenerate it with `python -m src.models.schemas`; a test fails if it drifts.

**API (port 8002, OpenAPI at `/docs`):**

| Method & path | Body | Returns |
|---|---|---|
| `POST /api/v1/skills/analyze_resume` | multipart: `file` (PDF/DOCX/TXT ≤ 5 MB), optional `target_role`, `required_skills` (comma-separated), `location`, `use_llm` | `AnalyzeResumeResponse` = `{profile, gap_analysis \| null}` |
| `POST /api/v1/skills/gap_analysis` | `GapAnalysisRequest` JSON: `target_role`, `required_skills`, optional `knowledge_graph`, `skill_importance`, `typical_experience_years`, `hours_per_week`, and exactly one of `profile` / `manual_profile` | `GapAnalysisResult` |
| `GET /api/v1/health` | — | status, version, taxonomy size, similarity backend, whether an LLM key is configured |

**Module 1 → Module 2** (team decision with the Module 1 owner). Module 2 is self-contained and never imports Module 1: the hyphenated folder isn't importable, and AGENTS.md §3/§7 forbid it. The integration layer calls Module 1 over REST and passes:
- `occupation` → `target_role`
- `top_skills` → `required_skills`
- `knowledge_graph`, unchanged
- `skill_importance`, when available

Module 1 ids resolve through a copy of its `normalize_skill`. Tests cover all 17 documented ids, its alias targets and 32 derived ids, using mocks in `tests/mocks/m1_contract.json` shaped like `schema_m1.json`.

**Module 2 → Module 3.** `UserProfile` is a superset of the INTEGRATION.md profile (`skills[].name/level/evidence`, `experience_years`, `education: list[str]`, `location`, `preferred_locations`, `target_occupation`), with additive fields. Evidence values are Module 2 tags (`work_supported`, …); the integration layer can map them to free-text labels if needed.

**Notes for the integration layer**
- Modules 1 and 2 both use a top-level `src` package, so they clash if loaded into one Python process. Call them over REST (8001, 8002), as agreed.
- Module 2 doesn't need MongoDB, so there's nothing to provision.

**Open items with Module 1** (requested by the team lead):
- `top_skill_weights` per occupation, which would replace rank decay.
- Dropping `normalize_skill`'s `len > 1` filter, so C and R demand reaches `top_skills`.
- Module 1's own WORKING.md §5.1 still shows a direct-import example for other modules.

---

## 9. Test results

```bash
pip install -r module-2-skill-gap/requirements.txt
pytest module-2-skill-gap/tests/ -v
```

**209 passed, 0 failed, 0 skipped** (~40–80 s, mostly OCR). The live Groq test (`test_llm_live.py`) runs only when `GROQ_API_KEY` is set and is skipped otherwise.

| File | Tests | Covers |
|---|---|---|
| `test_taxonomy.py` | 82 | fields, unique ids/aliases, no cycles, Module 1 id resolution, spec normalisation examples |
| `test_skill_extractor.py` | 34 | dictionary pass, ambiguous aliases, longest match, LLM off / no key / timeout / rate limit / bad JSON / grounding / cache |
| `test_parsers.py` | 29 | PDF, scanned PDF, DOCX, TXT, all error codes, sections, date formats, overlaps, internships, education, OCR repair, OCR page cap |
| `test_profile.py` | 12 | evidence tags, levels, OCR parity with the text PDF, manual entry, verification flags, INTEGRATION profile shape, privacy |
| `test_gap_analyzer.py` | 24 | worked example, each matching reason (incl. semantic and the TF-IDF fallback), importance sources, all verdicts + 1-year control, advice, validation |
| `test_roadmap.py` | 7 | prerequisites before dependants, pulled-in prerequisites, implied knowledge, hour ranges, weekly milestones, whole-taxonomy ordering |
| `test_api.py` | 20 | health, upload formats, every error status, gap analysis, resume → gap round trip, schema export, OpenAPI paths |
| `test_llm_live.py` | 1 | real Groq call, grounded output, cache |

**Known limitations**
- Section and date parsing are heuristic. Two-column layouts and headings inside tables may lose structure; warnings flag a missing Experience or Skills section.
- OCR is slow on CPU and still misreads uncommon names; `ocr_repair` only fixes taxonomy aliases.
- Without importance weights or experience bands from Module 1, weights come from list order and the experience band from the title.
- MiniLM must be downloaded once (~80 MB). Without it, the TF-IDF fallback is less semantic.
