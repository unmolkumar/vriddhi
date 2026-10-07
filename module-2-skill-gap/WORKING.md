# Module 2: Skill Gap & Resume Intelligence — Working Specification

**Branch:** `feat/module-2-skill-gap` · **Owner:** Chaitanya Sharma · **API port:** 8002
**Question answered:** *"Where is this person now, how well do they match the target role, and what should they learn next?"*

Status: complete and integration-ready per context/AGENTS.md §15/§18: self-contained, contract models + JSON Schema, structured errors, 285 passing tests, no cross-module imports. See §10 for the readiness checklist. Backend only: the UI is built separately on top of this API.

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
483 curated tech skills in 9 categories. Fields:
- `id`: Module 1-compatible snake_case.
- `display`, `aliases`, `category`.
- `maps_to`: coarser id (378 skills).
- `prerequisites`: 139 skills; acyclic.
- `difficulty_tier`: 1 foundational tool/syntax · 2 working skill · 3 deep specialisation.
- `context`: rules for ambiguous aliases.
- `is_category`: true for 16 field-level ids that other skills map into and that aren't learned as one skill: `ai`, `generative_ai`, `data_science`, `data_engineering`, `big_data`, `backend`, `frontend_development`, `full_stack_development`, `web_development`, `mobile_development`, `api_development`, `devops`, `automation`, `cloud`, `software_testing`, `cybersecurity`. Learnable areas with children (`machine_learning`, `etl`, `ci_cd`, `statistics`) stay concrete. Exposed on `/skills/extract`, profile skills and the gap matrix.
- `non_skill_ids` (top level): ids module 1 can emit that aren't learnable skills. Today only `data`, a broad posting tag. Gap analysis skips them with a warning instead of scoring or recommending them; a request with only such ids returns 422.
- Product variants are aliases of their base skill (Tableau Desktop/Server/Public/Prep → Tableau, Power BI Desktop/Service → Power BI, MS Excel → Excel, Google Colab → Jupyter, Docker Desktop → Docker, MySQL Workbench → MySQL, SSMS → SQL Server). AWS EC2, S3 and Lambda are their own skills with `maps_to` aws → cloud.
- `esco_uri`: `null`. This is an **ESCO-aligned taxonomy, mapping in progress**, not ESCO.

### 3.2 Extraction (`engines/skill_extractor.py`)
`extract_skills(text, use_llm=True, skills_context=False)`, shared by resumes and (via the integration layer) job descriptions.
- **Pass 1, dictionary.** Word boundaries are symbol-aware, so `C` never matches inside `C++`, `C#` or `R&D`. Separators are flexible (`machine-learning` = `machine learning`), and the longest match wins on overlap (*React Native* over *React*). Emails and profile URLs are stripped first.
- **Ambiguous aliases.**
  - `list` rule (`go`, `c`, `spring`, `express`, `node`, `m.l.`, …): the alias must be a whole item of a delimited list, or the text must be a skills list.
  - `case` rule (`r`, `excel`, `rust`, `spark`, `ai`, …): the list rule, or the alias capitalised mid-sentence (*"analysis in R"*, *"Advanced Excel"*).
  - *"go the extra mile"*, *"Grade C"*, *"Spring 2023"* and *"M.L. Sharma"* don't match.
- **Pass 2, Groq** (JSON mode, temperature 0, 10 s timeout, no retries). The default model is `openai/gpt-oss-120b`; override it with `GROQ_MODEL`.
  - **Team-facing change (approved):** the originally planned `llama-3.3-70b-versatile` is no longer served by Groq. `gpt-oss-120b`, `gpt-oss-20b` and `qwen/qwen3.8-27b` all returned identical results on the test prompt in ~1 s, and `gpt-oss-120b` was approved as the default.
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
2. `top_skill_weights` from module 1's response, passed through unchanged (`importance_source: "m1_weights"`).
3. `knowledge_graph` node weight, for `skill`/`technology` nodes whose label (or id without `skill_`/`tech_`) resolves to the required id.
4. Rank decay over the `required_skills` order: **w_i = 1 / (1 + 0.15 · i)** (`RANK_DECAY`).

Skills without an explicit value fall back to rank decay, and `importance_source` records which source applied. Priority labels: High if w ≥ 0.80, Medium if w ≥ 0.60, Low otherwise.

### 5.2 Required level
- With explicit importance: w ≥ 0.85 → 4, w ≥ 0.70 → 3, else 2 (`IMPORTANCE_TO_LEVEL`).
- With rank decay: the first 3 required skills need 3 (`TOP_REQUIRED_LEVEL`), the rest 2.

### 5.3 Matching (per required skill, first rule that applies)
| Status | Reason | Rule | `relation` | `current_level` |
|---|---|---|---|---|
| matched | `exact` | the profile has the id | — | that skill's level |
| matched | `maps_to` | a profile skill's `maps_to` chain reaches it (PostgreSQL → **sql**, AWS → **cloud**, scikit-learn → **machine_learning**) | — | best such skill's level |
| matched | `semantic` | cosine(display names) ≥ **0.92** (`SEMANTIC_MATCH_THRESHOLD`): the same skill worded differently | — | that skill's level |
| adjacent | `maps_to` | the profile has the required skill's parent, or a skill under that parent (MySQL ↔ PostgreSQL, SQL → PostgreSQL, scikit-learn ↔ Deep Learning) | *is related to X* | 0 |
| adjacent | `prerequisite` | the profile has a prerequisite of it (Docker → Kubernetes), or a skill that builds on it (Kubernetes → Docker) | *builds on X* / *is a foundation of X* | 0 |
| adjacent | `semantic` | 0.82 ≤ cosine < 0.92 (`SEMANTIC_THRESHOLD`, MiniLM `all-MiniLM-L6-v2`; TF-IDF char 3-gram cosine with the same bars if the model can't load) | *is similar to X* | 0 |
| missing | — | none of the above | — | 0 |

For adjacent skills, `related_level` holds the level of the related `via` skill. The required skill itself isn't known yet, so `current_level` is 0 and `gap` equals `required_level`.

`gap = required_level − current_level`: > 0 is a gap, 0 is met, < 0 is above the requirement. The buckets are `matched` (gap ≤ 0), `weak` (matched with gap > 0), `adjacent`, `critical_missing` (by importance) and `above_requirement`.

Measured MiniLM cosines show why 0.82 is a conservative bar:
- **Adjacent (0.82–0.92):** *Data Visualization ~ Data Visualisation Tools* 0.88, *Machine Learning ~ Machine Learning Models* 0.87.
- **Matched (≥ 0.92):** *Data Visualization ~ Data Visualisations* 0.97, *Microsoft Excel ~ Excel* 0.97. Product names like *Tableau Desktop* are now taxonomy aliases, so they match exactly.
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
`role_min`/`role_max` come from `typical_experience` in the request (`experience_source: "request"`), e.g. a per-occupation band from module 1. Otherwise they come from the title (`experience_source: "title_heuristic"`): intern/junior/graduate 0–2, senior 4–8, lead/manager 6–12, principal/staff/head/architect 8–15, anything else 0–5.

**What 0.72 means.** The profile covers about 72% of the importance-weighted requirement at the required levels, after a small experience adjustment. Adjacent skills count 40%, and a level-1 skill against a level-2 requirement counts 50%. It is an estimate of fit, not a hiring probability.

**Worked example** (test `test_worked_example_data_scientist`: the text-PDF fixture against Module 1's Data Scientists `top_skills`):

| i | Required | w_i | Status (via) | Current / required | credit | w·credit |
|---|---|---|---|---|---|---|
| 0 | python | 1.0000 | matched exact | 4 / 3 | 1.0000 | 1.0000 |
| 1 | machine_learning | 0.8696 | matched maps_to (scikit-learn) | 2 / 3 | 0.6667 | 0.5797 |
| 2 | sql | 0.7692 | matched exact | 3 / 3 | 1.0000 | 0.7692 |
| 3 | deep_learning | 0.6897 | adjacent maps_to (scikit-learn, related_level 2) | 0 / 2 | 0.40 | 0.2759 |
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
- An adjacent skill, worded by its relation: *"Deep Learning is related to scikit-learn, which you know, so it's a quick win."*, *"Kubernetes builds on Docker, which you know…"*, *"Data Visualisation Tools is similar to Data Visualization, which you know…"*. Roadmap milestone `reason`s use the same wording.

---

## 6. Roadmap (`engines/roadmap_generator.py`)

1. **Items:** missing skills, adjacent skills and weak matched skills (gap > 0).
2. **Prerequisites:** for missing and adjacent items, taxonomy prerequisites the user doesn't know are added recursively (`kind = prerequisite`; `required_for` lists the dependants). "Known" includes skills implied through `maps_to` (pandas implies Python).
3. **Order:** Kahn's topological sort over prerequisite edges. Among ready skills, the highest importance goes first, and pulled-in prerequisites inherit their dependant's importance. Prerequisites always come before dependants (tested over the whole taxonomy).
4. **Hours (estimates):** by difficulty tier, tier 1 = 10–25 h, tier 2 = 30–60 h, tier 3 = 60–120 h (`TIER_HOURS`). Adjacent skills are multiplied by `ADJACENT_HOURS_DISCOUNT = 0.5` and weak skills by `WEAK_HOURS_DISCOUNT = 0.5`. Each milestone reports its `hours_factor`, so adjacent Deep Learning (tier 3) shows 30–60 h with factor 0.5, against 60–120 h if it were missing.
5. **Weeks:** with `hours_per_week`, each milestone gets `weeks` (this skill alone, `ceil(hours / hours_per_week)`) and `cumulative_weeks` (the running total: done by this week).
6. **Length:** at most 15 milestones (`MAX_MILESTONES`); the note says how many were omitted.

The wording follows AGENTS.md §12: every figure is an "estimated" range, and the roadmap note says they are not guarantees.

---

### 6.1 Categories (`is_category`)
A required category (e.g. module 1 asking for `cloud` or `devops`) stays in the gap matrix and is scored like any skill: a concrete child the user has, such as AWS for `cloud`, can match it through `maps_to`. But it is:
- **never a roadmap milestone**, and never pulled in as a prerequisite (terraform's prerequisite `cloud` isn't scheduled), so it never leads `learning_priorities`;
- listed after concrete skills in `critical_missing`, and left out of the "yet to learn" and "close X" messages;
- explained instead: its `category_children` are found by walking `maps_to` downwards, nearest first, then most central (skills with the most skills mapping into them). Unless the requirement is already met, its `advice` reads *"Cloud Computing is a broad field, not a single skill. Concrete skills that build it: AWS, Microsoft Azure, Google Cloud Platform, Cloudflare."* A category the taxonomy has nothing under (`backend`, `data_science`, `automation`) says so, and isn't added to the roadmap. An unbacked claim ("you rate yourself level 5…") still takes priority over the category note.

## 7. Output variable dictionary

| Field | Type | Meaning |
|---|---|---|
| `match_score` | float 0–1 | §5.4. ≥ 0.85 strong, 0.60–0.85 good, < 0.60 sizeable gaps |
| `match_percent` | int | `round(match_score × 100)`, for display |
| `verdict` | `under_skilled` · `good_fit` · `over_qualified` | §5.5 |
| `verdict_message` | str | Plain-language summary |
| `suggested_role` | str \| null | Next seniority rung when over-qualified |
| `score_breakdown` | object | `coverage`, `experience_factor`, weights, formula text |
| `importance_source` | `skill_importance` · `m1_weights` · `knowledge_graph` · `rank_decay` | Where the weights came from |
| `typical_experience` | {min, max} | Role band used for the experience factor and over-qualification |
| `experience_source` | `request` · `title_heuristic` | Whether the band came from the request or the job title |
| `gap_matrix[]` | SkillGap | Per required skill: `is_category` and, for categories, `category_children` (up to 4 concrete skills under it); `status`, `reason` (`exact`/`maps_to`/`prerequisite`/`semantic`), `via`, `similarity`, `relation` (adjacent only), `importance`, `priority`, `required_level`, `current_level` (0 unless matched), `related_level` (adjacent only), `gap`, `evidence`, `advice` |
| `skills` | buckets | `matched`, `weak`, `adjacent`, `critical_missing`, `above_requirement` (ids) |
| `strengths` | list[str] | Matched skills meeting the requirement |
| `learning_priorities` | list[str] | First 5 roadmap skills |
| `advice` | list[str] | §5.6 |
| `roadmap` | Roadmap | §6: `milestones[]` (`order`, `kind`, `reason`, `prerequisites`, `required_for`, `difficulty_tier`, `hours_factor`, `estimated_hours`, `weeks`, `cumulative_weeks`), totals, `note` |
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
| `POST /api/v1/skills/gap_analysis` | `GapAnalysisRequest` JSON: `target_role`, `required_skills`, optional `knowledge_graph`, `skill_importance`, `typical_experience` (`{min, max}`; the old name `typical_experience_years` is still accepted), `hours_per_week`, and exactly one of `profile` / `manual_profile` | `GapAnalysisResult` |
| `POST /api/v1/skills/extract` | `{"text": str, "use_llm": false}` (≤ 50,000 chars) | `SkillExtractResponse` = `{skills: [{id, display, category, maps_to, in_taxonomy, source, matches}], warnings}`. For job descriptions: no evidence or levels. Errors: 422 `EMPTY_TEXT`, 413 `TEXT_TOO_LARGE` |
| `GET /api/v1/health` | — | status, version, taxonomy size, similarity backend, whether an LLM key is configured |

**Module 1 → Module 2** (team decision with the Module 1 owner). Module 2 is self-contained and never imports Module 1: the hyphenated folder isn't importable, and AGENTS.md §3/§7 forbid it. The integration layer calls Module 1 over REST and passes:
- `occupation` → `target_role`
- `top_skills` → `required_skills`
- `top_skill_weights` → `top_skill_weights`
- `typical_experience` (per role, from postings) → `typical_experience` (`experience_source: "request"`)
- `knowledge_graph`, unchanged
- `skill_importance`, when available

Module 1 ids resolve through a copy of its `normalize_skill`. **Every id in module 1's `m1_target_roles_skills_export.json` is covered: each resolves to a taxonomy entry, as an exact id (incl. `backend` and `automation`) or through an alias (`pyspark` → `spark`), or is a documented non-skill (`data`).** The coverage test reads module 1's live export when present and a vendored copy (`tests/mocks/m1_target_roles_skills_export.json`) otherwise. Tests also cover the 17 documented ids, its alias targets and 32 derived ids, using mocks in `tests/mocks/m1_contract.json` shaped like `schema_m1.json`.

**Module 2 → Module 3.** `UserProfile` is a superset of the INTEGRATION.md profile (`skills[].name/level/evidence`, `experience_years`, `education: list[str]`, `location`, `preferred_locations`, `target_occupation`), with additive fields. Evidence values are Module 2 tags (`work_supported`, …); the integration layer can map them to free-text labels if needed.

**Notes for the integration layer**
- **Role suggestions.** When over-qualified, module 2 only suggests the next seniority rung (`suggested_role`, e.g. *Senior Data Analyst*). Lateral, higher-demand role suggestions belong to the integration layer, which has module 1's ranking (`POST /api/v1/career/rank`): it can take `verdict`, `strengths` and the profile and ask module 1 for related occupations.
- **Experience bands.** Pass module 1's per-occupation experience band as `typical_experience` when it becomes available; the response's `experience_source` shows which was used.
- Modules 1 and 2 both use a top-level `src` package, so they clash if loaded into one Python process. Call them over REST (8001, 8002), as agreed.
- Module 2 doesn't need MongoDB, so there's nothing to provision.

**Module 1 items now delivered:** `top_skill_weights` (consumed as `m1_weights`), the C/R `len > 1` fix, and the 7-role id export.
- Module 1's own WORKING.md §5.1 still shows a direct-import example for other modules.

---

## 9. Test results

```bash
pip install -r module-2-skill-gap/requirements.txt
pytest module-2-skill-gap/tests/ -v
```

**285 passed, 0 failed, 0 skipped** (~1.5–3.5 min; OCR and MiniLM dominate). The general engine (v2, §11–15) adds 190 tests; the v1 tests above are unchanged. The live Groq test (`test_llm_live.py`) runs only when `GROQ_API_KEY` is set and is skipped otherwise.

| File | Tests | Covers |
|---|---|---|
| `test_taxonomy.py` | 133 | fields, unique ids/aliases, no cycles, Module 1 id resolution incl. 100% of the 7-role export, non-skill ids, automation/backend, spec normalisation examples, product-variant aliases |
| `test_skill_extractor.py` | 34 | dictionary pass, ambiguous aliases, longest match, LLM off / no key / timeout / rate limit / bad JSON / grounding / cache |
| `test_parsers.py` | 29 | PDF, scanned PDF, DOCX, TXT, all error codes, sections, date formats, overlaps, internships, education, OCR repair, OCR page cap |
| `test_profile.py` | 12 | evidence tags, levels, OCR parity with the text PDF, manual entry, verification flags, INTEGRATION profile shape, privacy |
| `test_gap_analyzer.py` | 33 | worked example, module 1 weights, skipped categories, each matching reason (incl. semantic matched/adjacent and the TF-IDF fallback), relation wording, adjacent levels, importance sources, experience source, all verdicts + 1-year control, advice, validation |
| `test_roadmap.py` | 8 | prerequisites before dependants, pulled-in prerequisites, implied knowledge, hour ranges, adjacent < missing estimate, per-skill and cumulative weeks, whole-taxonomy ordering |
| `test_category.py` | 7 | the category set, children ordering, the flag on extract / analyze_resume / gap matrix, categories explained but never scheduled (incl. as prerequisites), notes when met vs. weak |
| `test_api.py` | 28 | health, upload formats, every error status, gap analysis, resume → gap round trip, `/skills/extract` (results, warnings, errors), module 1 weights, schema export, OpenAPI paths |
| `test_llm_live.py` | 1 | real Groq call, grounded output, cache |

**Known limitations**
- Section and date parsing are heuristic. Two-column layouts and headings inside tables may lose structure; warnings flag a missing Experience or Skills section.
- OCR is slow on CPU and still misreads uncommon names; `ocr_repair` only fixes taxonomy aliases.
- Without importance weights or experience bands from Module 1, weights come from list order and the experience band from the title.
- MiniLM must be downloaded once (~80 MB). Without it, the TF-IDF fallback is less semantic.

---

## 10. Integration readiness (context/AGENTS.md §18)

| # | Gate criterion | Status | Evidence |
|---|---|---|---|
| 1 | Self-contained execution | ✅ | Own FastAPI service: `cd module-2-skill-gap && uvicorn src.api.main:app --port 8002`; verified over HTTP (`/api/v1/health` → `status: ok`). No database or other module needed |
| 2 | Contract compliance | ✅ | `UserProfile` is a superset of INTEGRATION.md's common profile (tested in `test_integration_profile_shape`); input takes module 1's `occupation`, `top_skills`, `knowledge_graph` as plain data; errors use `{"error": {"code", "message"}}`. JSON Schema: `src/models/schema_m2.json` (drift-tested) |
| 3 | 100% passing tests | ✅ | `pytest module-2-skill-gap/tests/ -v` → **285 passed, 0 failed, 0 skipped**, with module 1 data from mocks (`tests/mocks/m1_contract.json`). The live Groq test skips cleanly without a key |
| 4 | Error handling | ✅ | Corrupt, encrypted, oversized, too many pages, empty, wrong type and legacy files → 400/413/415 with codes; invalid JSON → 422 `INVALID_REQUEST`; LLM timeout, rate limit, missing key or unknown model → dictionary fallback; MiniLM unavailable → TF-IDF fallback; unexpected errors → 500 `INTERNAL_ERROR` without a stack trace |
| 5 | Zero cross-module imports | ✅ | `src/` and `tests/` import only `src.*` and third-party packages; the only module-1 references are comments, test names and a documented replica of its `normalize_skill` |
| 6 | Documentation | ✅ | `README.md`: install, run, test, example request/response payloads. This file: architecture, formulas, contracts, results. `HANDOFF_TO_M3.md` for module 3 |
| 7 | Clean git history | ✅ | Conventional Commits with `(module-2)` scope on `feat/module-2-skill-gap`, one change per commit, author Chaitanya Sharma |

Endpoints: `POST /api/v1/skills/analyze_resume`, `POST /api/v1/skills/gap_analysis`, `POST /api/v1/skills/extract`, `GET /api/v1/health` on port **8002** (OpenAPI at `/docs`).

---

## 11. General engine (v2) — A1, A1b and A1c

**Status:** A1 (requirements, evidence, matching, calibration), A1b (provenance, core-only scoring, inferred generic layers, held-out calibration) and A1c (clause-level evidence, item-count scaling, a fresh held-out set). Scoring, verdicts, roadmaps and `/api/v2/*` are A2 (§12). Everything in §1–10 (v1, `/api/v1/*`, the 483-skill taxonomy, the gap analyzer) is unchanged; the new code lives in `src/general/` and nothing in v1 imports it.

### 11.1 Design

```
module 1 (REST)                      resume / free text / typed skills / v1 profile
  /occupations/search ─► Resolution            │
  /occupations/{soc}/requirements              ▼
          │                            evidence.py: EvidenceUnit(text, evidence_type, section, span, skill_ids)
          ▼                                    │
  requirements.py: filter, weight,             │
  provenance → RequirementItem                 │
          │                                    │
          ├── core (market_skill, tech, tool, task, dwa) ──► matcher.py ◄──┘
          │        1. alias: same taxonomy skill → met
          │        2. semantic: MiniLM cosine → met / partial / missing
          │                         │
          └── knowledge, skill, work_activity, ability ──► inference.py (from the core matches)
```

- **`m1_client.py`.** Calls module 1's four occupation endpoints over REST (`M1_BASE_URL`, default `http://localhost:8001`, 10 s timeout). Failures become `M1Error(code)`: `unreachable`, `timeout`, `not_found` or `bad_response`.
  - **Cache.** Requirements, profiles and related lists are cached on disk under `data/cache/m1/<version>/`. The version comes from `GET /api/v1/meta` (schema version + export hash or build time) when module 1 has that endpoint (feature-detected; not in v2.1). Otherwise it's the API version from `/openapi.json`. If neither is available, nothing is cached.
  - **Search.** Returns a `Resolution` with `low_confidence` (top match below `LOW_CONFIDENCE = 0.85`, or two different occupation families within `AMBIGUOUS_GAP = 0.05`) and `did_you_mean`. Live example: "physical therapist" ties Physical Therapists, Aides and Assistants at 0.88, so it gets flagged.
  - **Fixtures.** `FixtureM1Client(*paths)` merges export files, and each occupation keeps its file's `version` (used in the embedding cache key):
    - `tests/mocks/m1_occupation_requirements_export.json`: module 1 v2.1.0, 15 occupations.
    - `tests/mocks/m1_heldout_occupations.json`: 10 more occupations, fetched from a local module 1.
    - `…_v2.0.json`: kept for a filter regression test.
- **`requirements.py`.** Turns module 1 rows into `RequirementItem(soc, item_type, item_id, name, description, importance, level, source, provenance, reliable, layer, weight, reliability, flags)`.
  - **Layers:**
    - **core** (`market_skill`, `tech`, `tool`, `task`, `dwa`): matched and scored.
    - **transferable** (`knowledge`, `skill`, `work_activity`): inferred and reported, never scored or listed as gaps.
    - **fit_indicator** (`ability`): reported only.
  - **Weight:** `importance_norm × LAYER_WEIGHT[layer] × reliability`. Reliability multiplies together `CURATED_WEIGHT` and `OFF_DOMAIN_FACTOR`, when they apply.
  - **Required level:** `level_norm`, or `DEFAULT_LEVEL[type]` when it is null.
  - **Embedded text:** `"{name}: {description}"`, or the name alone when the description repeats it or is generic ("Extracted from Indian job postings", "Curated domain competency").
- **`evidence.py`.** Splits resume or free text into units, using v1's `section_segmenter`.
  - Experience bullets become `work`, project bullets `project`, and everything else `mentioned`. Typed skills become `self`.
  - Bullets are split into sentences, and skills sections into items. Contact lines are dropped.
  - Each unit keeps its character `span` and the taxonomy ids v1's dictionary finds in it.
- **`embeddings.py`.** `all-MiniLM-L6-v2` by default (`EMBEDDING_MODEL` to change), CPU only. Vectors are cached as float16 `.npy` per model, key (module 1 version + SOC) and text hash, under the gitignored `data/cache/embeddings/`.
- **`matcher.py`.** Matches core items only:
  1. **Alias.** A tech, tool or market_skill requirement that the v1 taxonomy resolves to the same skill as a unit is `met`, with `reason: "alias"`.
  2. **Semantic.** Otherwise, the best cosine over all units is compared with that type's `(met, partial)` thresholds.

  Every match carries its `provenance`, status, similarity, reason and the deciding unit (text, type, section, span). `coverage()` is a **provisional** 0–1 score for calibration only:
  - Within each core type, it is the weight-averaged credit (met 1, partial 0.5).
  - Types are combined by `TYPE_SHARE × the type's mean reliability`, so a market-skill list made only of curated rows counts half.

  A2 replaces this score.
- **`inference.py`.** Handles the generic O*NET layers, which don't separate occupations (§11.3):
  - **work_activity (GWA):** `evidenced` when any of its DWAs is met. The ids nest: a DWA or IWA id starts with its GWA's element id (`4.A.2.a.4.I01.D03` and module 1's `4.A.4.a.5.c.3` both sit under GWA `4.A.4.a.5`). The status is `not_evidenced` when its DWAs exist but none is met, and `no_dwa_data` when module 1 sent none.
  - **knowledge, skill:** the top `DRAWS_ON_TOP` (5 each) by importance are reported as "what this role draws on". One is marked `inferred` when a met task or DWA reaches `SUPPORT_SIM = 0.45`, or an education line reaches `EDUCATION_SUPPORT_SIM = 0.35`. These are never gaps.

    Measured on the tuning profiles:
    - nurse: Medicine and Dentistry 0.54;
    - electrician: Troubleshooting 0.58 and Installation 0.77;
    - accountant: Economics and Accounting 0.66.

    Generic skills (Active Listening, Critical Thinking) stay at 0.23–0.29, so they are not inferred.
  - **ability:** the top `FIT_TOP` (6) are fit indicators.

### 11.2 Provenance and filters

**Provenance (module 1 v2.1).**
- **Hand-written rows:**
  - 47 of the export's 64 market skills are `source='curated'`, all at a fixed importance of 0.80.
  - The 156 `tool` rows are hand-written in module 1's ETL but labelled `source='onet'`. They have `tool_*` ids and exist only for the 15 export occupations.
- **Handling until module 1 replaces them with real data:**
  - Both kinds get `provenance="curated"` and weight × `CURATED_WEIGHT = 0.5`, with flag `"curated"`.
  - Their share is never shown as market data (`india_demand_share` is forced to `null`).
  - Every requirement and every match carries `provenance`: `onet`, `india_postings` or `curated`.
- **Not tool rows:** `tech_*` ids are real O*NET Technology Skills, so they are not treated as curated.

**Filters.** Each is a named constant. A drop is recorded in the per-occupation `FilterReport` (reason → names, and logged); a down-weight is recorded in the item's `flags`. Every filter is a no-op on clean data (`test_clean_data_passes_through_untouched`).

| Filter | Constant(s) | Applies to | Effect |
|---|---|---|---|
| duplicate | normalised (type, name) | all | drop, keeping the higher-importance copy (module 1 v2.0 exported every tech row twice; v2.1 has none) |
| unreliable | `reliable = 0` | all | drop |
| low support | `MARKET_MIN_SHARE = 0.005` safety net; **`posting_count ≥ MARKET_MIN_POSTINGS = 3` whenever module 1 sends `posting_count`** (feature-detected per row) | posting-derived market_skill | drop. Module 1 v2.1 already filters at ≥ 3 postings and share ≥ 0.02, so this only catches single-posting noise |
| noise | `INDUSTRY_LABELS` (+ "IT Software - …"), `INDIAN_PLACES`, `SENIORITY_WORDS`, `GENERIC_TITLES`, `BENEFITS`, `GENERIC_TERMS`, the occupation's own title; case-insensitive | market_skill | drop |
| cap | `MARKET_CAP = 50` by share | posting-derived market_skill (curated never) | drop the tail |
| off-domain | `OFF_DOMAIN_SIM = 0.18` against the title + module 1's domain labels, `OFF_DOMAIN_FACTOR = 0.3` | tech, tool, and posting-derived market skills with share < `MARKET_OFF_DOMAIN_MAX_SHARE = 0.05` (curated and high-share exempt) | weight × 0.3, flag `off_domain(sim)` |
| curated | `CURATED_WEIGHT = 0.5` | `source='curated'`, `tool_*` | weight × 0.5, flag `curated` |

**Why off-domain uses the domain labels** (measured in A1): comparing with the description and tasks let Epic Systems through for Accountants (0.34) and Apache Spark for Registered Nurses (0.28). With the title and domain labels, those score 0.12 and 0.11, while Epic for Nurses (0.42) and QuickBooks for Accountants (0.44) stay. General office software also falls below 0.18 for non-office occupations; that's acceptable, because it says nothing about the occupation.

**Per occupation, v2.1 export** (rows → kept; dropped; down-weighted):
- **Accountants:** 472 → 435. Dropped 37 unreliable. Down-weighted 45 off-domain (Epic, MEDITECH, SPSS, Word, …) and 16 curated (8 market skills, 8 tools).
- **Electricians:** 251 → 241. Dropped 10 unreliable. Down-weighted 17 off-domain and 22 curated.
- **Civil Engineers:** 289 → 265. Dropped 24 unreliable. Down-weighted 29 off-domain and 17 curated. The 5 posting-derived market skills (Construction, Project management, AutoCAD, …) are kept at full weight.
- **Graphic Designers:** module 1's "IT Software - Other" and "IT Software - eCommerce" (share 0.5) are still dropped as industry labels.

**The 10 extra occupations** (v2.0 database, see §11.3): Software QA keeps 30 of 699 market skills (667 single-posting rows dropped, 10 capped); HR Specialists keeps 50 of 151.

### 11.3 Calibration

**Profile sets** (`tests/calibration/profiles/`). All are Indian context and none copies O*NET sentences:

| Set | Profiles | Style | Used for |
|---|---|---|---|
| `tuning/` | 15 + 3 partial | mostly resume-shaped (2 free text) | tuning only |
| `heldout/` (A1b) | 15, one per export occupation | senior nursing superintendent, Hinglish (pharmacist, accountant, civil engineer, truck driver), freshers, career changers, one-liners, bullet-only, head of analytics | never tuned on |
| `new/` (A1b) | 10, one per extra occupation | resume-shaped | never tuned on |
| `heldout2/` (A1c) | 15 export + 10 extra | career-break returnees, third-person bios, interview Q&A, LinkedIn "About", WhatsApp messages, a government posting, a cover letter, key-value tables, two-liners, Fiverr freelancer, Hindi narrative, a fresher with projects | never tuned on; the A2 gate |

**The 10 extra occupations:**
- Physical Therapists, Plumbers, HR Specialists, Tellers, Lawyers, Pharmacy Technicians, Retail Salespersons, Software QA, Hotel Desk Clerks, Dental Hygienists.
- They were resolved through module 1's `/occupations/search` and fetched from `/profile` and `/requirements` on a local module 1.
- That database predates v2.1, so the fixture is **labelled as v2.0 data**: no DWA, tool or curated rows, and v2.0's noisier market skills.
- `scripts/refetch_extra_occupations.py` refetches them over REST when a v2.1+ database is available.

**Procedure:**
1. `python scripts/calibrate.py --tune` searches the thresholds (name-like, task-like), the partial gap and five type-share presets, on the **tuning profiles against the 15 export occupations only**.
2. The result is frozen in `matcher.py`.
3. `python scripts/calibrate.py` reports every held-out set on the 25-occupation matrix.

No held-out result was used to change a constant.

**A1c fixes**, applied before re-tuning (general correctness fixes for the A1b failures):
1. **Clause-level evidence.** A free-text sentence with comma, semicolon or "and" parts yields the sentence plus one unit per part. Each part keeps its own `span`, plus the sentence's `context_span` (`evidence.clauses`, `MIN_CLAUSES = 2`). Skills-section items also split on "and".
2. **Item-count scaling.** `effective_share = TYPE_SHARE × mean reliability × min(1, n_items / MIN_ITEMS_FOR_FULL_SHARE)` with `MIN_ITEMS_FOR_FULL_SHARE = 5`, renormalised over the types present. A one-item type can no longer swing 40% of the score.

**Frozen constants (A1c):**

| Core type | met | partial | TYPE_SHARE |
|---|---|---|---|
| tech, tool | 0.60 | 0.50 | 0.10 each |
| market_skill | 0.60 | 0.50 | 0.40 |
| task | 0.55 | 0.45 | 0.30 |
| dwa | 0.55 | 0.45 | 0.10 |

**Results (MiniLM, frozen A1c constants, 25-occupation matrix unless noted):**

| Set | Top-1 | Top-3 | Mean margin | Worst margin |
|---|---|---|---|---|
| tuning, as tuned (15 export occupations) | 15/15 | 15/15 | +0.359 | +0.166 |
| tuning | 14/15 | 15/15 | +0.292 | −0.082 (pharmacist → Pharmacy Technicians) |
| held-out (A1b) | 12/15 | 14/15 | +0.114 | −0.061 (teacher one-liner → Software QA) |
| held-out (A1b), 15 export occupations | 15/15 | 15/15 | +0.143 | +0.003 |
| 10 extra occupations (A1b `new/`) | 10/10 | 10/10 | +0.195 | +0.041 |
| **held-out-2, 15 export-occupation profiles** | **13/15** | **15/15** | **+0.135** | −0.086 (pharmacist, third person → Pharmacy Technicians) |
| **held-out-2, 10 extra-occupation profiles** | **10/10** | **10/10** | **+0.173** | +0.041 |
| held-out-2 (export profiles), curated rows removed | 13/15 | 15/15 | +0.122 | −0.077 |
| held-out-2 (export profiles), 15 export occupations | 14/15 | 15/15 | +0.177 | −0.041 (nurse after a career break → Medical Assistants) |

**A2 gate** (held-out-2 top-1 ≥ 12/15 and extra-occupation top-1 ≥ 7/10): **passed**, at 13/15 and 10/10.

**What changed against A1b:**
- **Fixed:**
  - The teacher one-liner went from coverage 0.00 to 0.13. It is still rank 4, but no longer zero.
  - The fresher clinic assistant and the advocate now rank their own occupation first.
  - The extra occupations went from 8/10 to 10/10.
- **New miss on the old held-out set:** the Hinglish truck driver ("gaadi nikalne se pehle tyre, brake…") scores 0.00 for every occupation. MiniLM is English-only, so Hinglish text barely embeds. The Hinglish accountant and civil engineer still pass, thanks to English domain terms (GST, Tally, BOQ). See open questions.
- **Remaining misses are adjacent occupations:**
  - Pharmacist → Pharmacy Technicians (both A1b and A1c);
  - returning nurse → Medical Assistants (0.12 vs 0.16; the profile describes basic ward care).

  A2 presents such runners-up as close alternatives.

**Best-evidence similarity, own vs other occupations** (tuning set, median):

| Type | Own | Other |
|---|---|---|
| market_skill | 0.71 | 0.31 |
| task | 0.52 | 0.29 |
| dwa | 0.48 | 0.31 |
| tech | 0.35 | 0.25 |
| tool | 0.35 | 0.20 |

### 11.4 Tests (general engine)

| File | Tests | Covers |
|---|---|---|
| `test_general_m1_client.py` | 10 | cache per module 1 version (none when unknown), `/api/v1/meta` preferred and OpenAPI fallback, timeout/unreachable/404/500 as `M1Error`, search confidence, low confidence and ties, fixture files merging with their versions |
| `test_general_requirements.py` | 34 | layers, weights, default levels, embedding text, provenance (curated source, `tool_*`, `tech_*`), curated weight and no share, duplicate, unreliable, safety-net floor and `posting_count` switch, noise lists, cap, off-domain, domain texts, clean data untouched, v2.1 Accountants, v2.0 regression |
| `test_general_evidence.py` | 5 | sections → work/project/mentioned, spans, clause units with `context_span`, list-like one-liner, contact lines, typed skills, v1 profile |
| `test_general_matcher.py` | 8 | core types only with provenance, alias first, best evidence and span, per-type thresholds, coverage with reliability and item-count scaling, a one-item type can't swing the score |
| `test_general_inference.py` | 7 | GWA from DWA/IWA ids, evidenced / not evidenced / no DWA data, draws-on inferred and never a gap, fit indicators |
| `test_general_scoring.py` | 11 | credit by evidence type and required level, v1 evidence confidences, **the §12.1 worked example**, experience band and factor, verdicts incl. over-qualified |
| `test_general_roadmap.py` | 6 | prerequisites first and listed (minus known), hours by taxonomy tier or type table and job zone, weeks, practice ideas (other unmet tasks above the bar) |
| `test_general_service.py` | 10 | resolution (alias, SOC, not found, tie → did you mean), experience parsed / overridden / unknown, full output shape and spans, advice for self-reported evidence, close alternatives and search suggestions, over-qualified, experience penalty on alternatives, job-text clauses, match_text blend |
| `test_api_v2.py` | 12 | `/api/v2` gap analysis (role, SOC, skills, v1 profile), validation 422s, 404 role/occupation, **503 when module 1 is down while v1 works**, analyze_resume (and v1 file errors), match_text, OpenAPI paths, `schema_m2_v2.json` drift |
| `calibration/test_calibration.py` | 15 | tuning set (top-1 ≥ 13/15, nurse vs Electricians, Accountants top-2, Data Scientists top-1, partial < full); held-out floors one profile below the A1c results for every set; the A2 gate; curated rows change top-1 by at most one |
| `calibration/test_latency.py` | 1 | warm `/gap_analysis` under 2 s with MiniLM |

The v1 suite is untouched: **284 passed + 1 skipped (live Groq without a key) = 285**. With the general engine, the total is **403 passed, 1 skipped**.

---

## 12. General engine (v2) — A2: score, verdict, gaps, roadmap, API

Code lives in `src/general/`:
- `scoring.py`, `roadmap.py`, `service.py` and `schemas.py`;
- routes in `src/api/routes_v2.py` (same app, port 8002);
- the contract in `src/models/schema_m2_v2.json` (`python -m src.general.schemas`, drift-tested).

Every constant below is named in its module.

### 12.1 Match score

```
credit(item)       = min(1, EVIDENCE_STRENGTH[evidence type] / required level)   met
                   = PARTIAL_CREDIT = 0.5                                         partial
                   = 0                                                            missing
EVIDENCE_STRENGTH  = v1's evidence confidence: work 0.90, project 0.75, mentioned 0.50, self 0.40
required level     = level_norm, else DEFAULT_LEVEL[type] (task/dwa 0.6, others 0.5)

effective_share(t) = TYPE_SHARE[t] × mean reliability of t × min(1, items in t / 5), renormalised
skill_score        = Σ_t effective_share(t) × Σ_i∈t weight_i·credit_i / Σ_i∈t weight_i
experience_factor  = 1 if years unknown or ≥ band.low
                   = 1 − (1 − EXPERIENCE_MIN_FACTOR) × min(1, (band.low − years) / EXPERIENCE_GAP_YEARS)
                     with EXPERIENCE_MIN_FACTOR = 0.8, EXPERIENCE_GAP_YEARS = 3
match_score        = skill_score × experience_factor
```

**Experience band:** module 1's `/profile` → `indian_experience` (`typical_min`–`typical_max`) when it has postings behind it, else the O*NET job zone: `JOB_ZONE_YEARS` = 1: 0–1, 2: 0–2, 3: 1–4, 4: 2–6, 5: 4–10 years. Years come from `experience_years`, else the v1 profile, else the work history parsed from `free_text`. When none is known, the factor is 1 and a warning says so.

**Worked example** (test `test_worked_example`). An occupation has 5 tasks and 5 posting-derived market skills, all at importance 0.8, in job zone 3. The candidate has no experience.
- **Tasks:** 3 met by work or project evidence (credit 1), 1 partial (0.5), 1 missing. Coverage = (1 + 1 + 1 + 0.5 + 0) / 5 = **0.70**.
- **Market skills:** 1 met by a typed skill (0.40 / 0.5 = 0.8), 1 met by work (1.0), 3 missing. Coverage = 1.8 / 5 = **0.36**.
- **Shares:** 0.30 and 0.40, at full reliability with 5 items each, so renormalised 0.4286 and 0.5714.
- **skill_score** = 0.4286 × 0.70 + 0.5714 × 0.36 = **0.5057**.
- **Band:** job zone 3, 1–4 years. At 0 years: factor = 1 − 0.2 × (1/3) = **0.9333**.
- **match_score** = 0.5057 × 0.9333 = **0.472**.

### 12.2 Verdict

| Label | When |
|---|---|
| `under_skilled` | match_score < `GOOD_FIT_THRESHOLD` (0.29 in A2; 0.3254 in A3; 0.22 in A3b, with `insufficient_evidence` for short descriptions (§14.1); **0.29 since A4, with `insufficient_evidence` for oblique write-ups too**, §15.3) |
| `over_qualified` | ≥ threshold, years > band.high + `OVERQUALIFIED_EXTRA_YEARS = 3`, and a related occupation with a higher job zone also scores ≥ threshold. That occupation is returned as `suggested_role` |
| `good_fit` | otherwise |

**A2 calibration** (superseded by §13.4; `python scripts/calibrate.py --verdict`, tuning set, A2 scores on each profile's own occupation):
- The 15 full profiles score 0.295–0.656; the 3 partial profiles (fresher nurse, accountant without GST/Tally, ITI apprentice) score 0.176–0.288.
- The threshold is the midpoint, 0.29. It separates the two groups, but the gap is thin (0.007): the truck driver's free-text profile sits at 0.295.
- The verdict judges fit *for the chosen target*, not whether it's the right occupation. 5 of 15 full profiles also clear 0.29 on some other occupation (e.g. the pharmacist on Pharmacy Technicians, 0.60).

### 12.3 Alternatives, gaps, strengths, generic layers

- **Close alternatives.** Module 1's `/related` (first `RELATED_LIMIT = 5`) are prepared and scored with the same evidence; one within `ALTERNATIVE_MARGIN = 0.05` of the target's score, or above it, is returned ("You're also a close fit for X (0.61)" / "an even stronger fit"). When the role resolution is low-confidence, module 1's other search matches are added as "Did you mean X?". Related occupations module 1 can't return are skipped.
- **Gaps and strengths.** Every core result carries status, similarity, credit, weight, required level, provenance, reason, flags and the deciding evidence (text, type, section, span, context span). Strengths are the top 10 met by weight × credit; gaps are partial or missing by weight (top 15, plus `gaps_total`). A requirement met only by `mentioned` or `self` evidence gets advice: *"You mention 'X', but nothing in your work or projects shows it. Add an example of where you did this."*
- **Generic layers.** `draws_on` lists knowledge and skills (inferred or not) and `work_activities` lists GWAs evidenced through met DWAs (§11.1). Abilities appear only as `fit_indicators`. None of these is ever a gap.
- **Provenance summary.** Core items and weight share per provenance, with a note when curated rows are present.

### 12.4 Roadmap

Missing and partial core items, heaviest first, up to `ROADMAP_MAX_ITEMS` (10 in A2; 8 since A3, with the rest in `later` and role-implied items last, §13.1). Each item has:
- **Order:** a taxonomy prerequisite of a heavier item moves ahead of it.
- **Prerequisites:** tech, tool or market items that resolve to v1 taxonomy ids list that skill's prerequisites the evidence doesn't already show (Apache Spark → Python, SQL).
- **Practice ideas:** the `PRACTICE_IDEAS = 2` nearest *other* unmet O*NET tasks or DWAs of the same occupation, at cosine ≥ `PRACTICE_MIN_SIM = 0.35`. For example, "Maintain accurate, detailed reports and records" → "Maintain medical facility records", "Maintain inventory of medical supplies or equipment".
- **Hours, as estimated ranges.**
  - Taxonomy skills use v1's difficulty-tier hours, halved for partial.
  - Other items use `HOURS_PER_LEVEL[type]` (task 60, dwa 40, market skill 80, tech 50, tool 20) × the level still to reach × `JOB_ZONE_FACTOR` (0.5 / 0.75 / 1.0 / 1.25 / 1.5 for zones 1–5), ± 30%, rounded to 5 hours.
  - With `hours_per_week`, the hours are also given as weeks.

### 12.5 Endpoints

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/api/v2/skills/gap_analysis` | JSON: `target_role` or `soc_code`; one or more of `free_text`, `skills`, `profile` (v1 `UserProfile`); optional `experience_years`, `city`, `hours_per_week` | `resolution`, `match_score`, `verdict`, `score_breakdown`, `strengths`, `gaps`, `gaps_total`, `draws_on`, `work_activities`, `fit_indicators`, `close_alternatives`, `roadmap`, `provenance_summary`, `m1_version`, `warnings` |
| POST | `/api/v2/skills/analyze_resume` | multipart `file` + `target_role` or `soc_code` (+ optional fields) | `{profile, gap_analysis}`. v1 parsing and the same file checks; the gap analysis runs on the resume text |
| POST | `/api/v2/skills/match_text` | JSON: `job_text`, optional `soc_code`, `job_title`; `free_text` / `skills` / `profile` | `match_score`, `job_text_score`, `occupation_score`, `blend`, `met`, `missing`, `job_requirements` |

**match_text** (for module 3):
- The job text is clause-split. A split sentence is replaced by its clauses, clauses shorter than `JOB_MIN_WORDS = 3` are dropped, and at most 60 are kept.
- Each clause is a task-like requirement with provenance `job_text`. They are scored like tasks.
- With a `soc_code`: `match_score = JOB_TEXT_BLEND (0.6) × job_text_score + 0.4 × the occupation's match_score`.

**Errors** (`{"error": {"code", "message"}}`):
- 422 `INVALID_REQUEST`: no role, no evidence, malformed SOC.
- 404 `ROLE_NOT_RESOLVED` and 404 `OCCUPATION_NOT_FOUND`.
- 503 `M1_UNAVAILABLE` when module 1 is unreachable or times out; 502 `M1_BAD_RESPONSE`.
- File errors as in v1 (400/413/415).

v1 endpoints never call module 1 and keep working when it is down (tested).

### 12.6 Performance

Occupations are prepared once per process: filtered, weighted and encoded, with requirement vectors read from the disk cache. A request encodes only its own evidence.

Measured on CPU with MiniLM, fixture client, `hours_per_week` set, through the HTTP test client:
- **Warm:** 0.24 s for the nurse profile vs "staff nurse" and 0.15 s for the electrician (median of 5). The direct engine call was 0.3–0.6 s, including related occupations.
- **Cold:** about 20 s on first use. A3 measured this more precisely: most of it is loading the model, once per process (§13.6).

`test_latency.py` checks warm < 2 s.

### 12.7 Known limits (A2; see §13.8 for what A3 fixed)

- **Hinglish / Indian-language text:** MiniLM is English-only; see §11.3.
- **Job-title lines are evidence too.** "Staff Nurse" meets "Direct or supervise less-skilled nursing personnel" at 0.66. A title says what someone was called, not what they did.
- **US-centric O*NET tasks.** Some tasks don't fit Indian practice (e.g. Registered Nurses "Prescribe or recommend drugs") and show up as gaps.
- **Long, generic O*NET tasks can miss clear evidence** ("Assemble, install, test, or maintain electrical wiring…" for the ITI electrician).
- **The verdict threshold** rests on 3 partial profiles and a 0.007 gap (§12.2).
- **The fixture client stands in for module 1's search and related lists:** documented Indian aliases plus title tokens, and related = same SOC major group. Live module 1 uses its alias table, 62k alternate titles and O*NET related occupations.

---

## 13. General engine (v2) — A3: hardening and readiness

A3 changes how evidence and requirements are read, then re-tunes on the tuning set only and freezes. Where it changes a value in §11–12, this section wins.

### 13.1 What changed

1. **Requirement-side clauses** (`requirements.requirement_clauses`). Long task and DWA sentences are also matched as clauses, and a requirement's similarity is the **max over the full sentence and its clauses**; the response shows the full sentence.
   - The clauses are the sentence without its example tail (", using …", ", such as …", ", including …", "; …").
   - A leading verb list is expanded, each verb with the first object ("Assemble, install, test, or maintain electrical or electronic wiring, …" → "install electrical wiring", "maintain electrical wiring", …).
   - Two "and" halves of 3+ words each also become clauses.
   - Up to `MAX_REQUIREMENT_CLAUSES = 8`. Clause vectors are encoded with the occupation and cached.
2. **Job-title lines are role history, not evidence** (`evidence.role_history`).
   - Experience lines with a date range, and short header lines (≤ 6 words) of a sectioned resume, become `role_title` units that are never matched.
   - Each title (cut at "|", "," or "(") is resolved through module 1's search. At confidence ≥ `ROLE_MIN_CONFIDENCE = 0.7`, the title counts for that occupation and for related ones in `ROLE_RELATED_TIERS` (O*NET Primary-Short / Primary-Long; the fixture's stand-in tier never counts).
   - For that occupation, tasks and DWAs without real evidence become `partial` with `reason: "implied_by_role"` and credit `min(0.4, 0.08 × years)` (0.1 when years are unknown). That is always below `PARTIAL_CREDIT`, never `met`.
   - The evidence text reads like "11 years as Maintenance Electrician".
   - Real evidence always wins. Implied items are listed after real gaps in the roadmap.
3. **Not applicable in India** (`data/general/india_not_applicable.json`). Seven tasks and DWAs that are outside the scope of practice in India:
   - Registered Nurses prescribing: task 1859 and DWAs 4.A.4.c.3.f.3 and .f.4;
   - Pharmacists prescribing: DWAs .f.3 and .f.1;
   - Medical Assistants authorising refills: task 2031 and DWA .f.2.

   They are excluded from scoring and listed in `not_applicable_in_india` with the reason, never as gaps or roadmap items. The list is kept to clear legal cases; module 1 should eventually send an `india_relevant` flag.
4. **Non-English evidence.**
   - **Language check:** `english_share` is the share of words that are neither Devanagari nor in a list of frequent romanised-Hindi words (`ROMAN_HINDI_MARKERS`, which leaves out English look-alikes such as *main*, *to*, *do*). A sentence below `ENGLISH_MIN_SHARE = 0.75` counts as non-English. On all 93 calibration profiles it flags 52 of 66 Hinglish/Hindi sentences and 0 English ones; the 14 it misses are English-heavy ("B.Sc Nursing, Delhi Nursing Council registered").
   - **Translation:** non-English sentences are rewritten in English by Groq (`GROQ_MODEL`) in one batch per request, cached by text hash. The unit keeps `original_text` and the original span and is marked `translated`.
   - **Fail-safe:** without a key, on a timeout or with a bad answer, the sentence is matched as written and a warning is added.
   - **Offline:** calibration and tests use committed rewrites (`tests/calibration/translations.json`, 54 sentences, refreshed with `scripts/refresh_translations.py`).
5. **`fit_percent` and `fit_label`.** `match_score` stays raw, and `fit_percent` maps it piecewise-linearly:
   - 0 → 0, `GOOD_FIT_THRESHOLD` → 50, `FIT_MEDIAN_FULL = 0.60` (the median full-profile own-occupation score on the tuning set) → 80;
   - the same slope continues above, capped at 100.

   Labels: ≥ 80 "Strong fit", ≥ 50 "Good fit", ≥ 25 "Developing", else "Early stage".
6. **Roadmap focus.** The main roadmap is the `ROADMAP_MAX_ITEMS = 8` heaviest gaps, with role-implied items after real ones; the rest are in `roadmap.later`. Totals cover the main roadmap only.
7. **Cold start.**
   - `scripts/prewarm_embeddings.py <SOC …> | --all-fixture` prepares occupations and their related ones into the disk cache.
   - `M2_PREWARM_SOCS="29-1141.00,47-2111.00"` does the same in a background thread at startup.
8. **Fixture search.** Token-match confidence averages the share of the query matched and the share of the title matched, so "Maintenance Electrician" → Electricians (0.75). Live module 1 uses its 62k alternate titles.

### 13.2 Final frozen constants (tuned on the tuning set only)

| Core type | met | partial | TYPE_SHARE |
|---|---|---|---|
| tech, tool, market_skill | 0.60 | 0.50 | 0.10, 0.10, 0.40 |
| task, dwa | 0.55 | 0.45 | 0.30, 0.10 |

`MIN_ITEMS_FOR_FULL_SHARE = 5`, `CURATED_WEIGHT = 0.5`, `GOOD_FIT_THRESHOLD = 0.3254`, `FIT_MEDIAN_FULL = 0.60`.

### 13.3 Final calibration (25 occupations unless noted)

| Set | MiniLM + translation (**shipped**) | MiniLM, no translation | multilingual MiniLM-L12, own tuning, no translation |
|---|---|---|---|
| tuning | 14/15, +0.339 | 14/15, +0.339 | 15/15, +0.318 |
| held-out (A1b) | **14/15**, +0.140 | 12/15, +0.126 | 14/15, +0.130 |
| A1b extra occupations | **10/10**, +0.228 | 10/10, +0.225 | 8/10, +0.212 |
| held-out, curated rows removed | 14/15, +0.125 | 12/15, +0.111 | 13/15, +0.105 |
| held-out-2, export occupations | 13/15, +0.163 | 13/15, +0.149 | 14/15, +0.140 |
| held-out-2, extra occupations | **10/10**, +0.184 | 10/10, +0.184 | 9/10, +0.171 |
| held-out-2, curated rows removed | 13/15, +0.157 | 13/15, +0.144 | 13/15, +0.123 |
| **Hinglish / Hindi (new, 5)** | **4/5, +0.142** | 2/5, +0.014 | 4/5, +0.063 |
| held-out, 15 export occupations | 15/15, +0.173 | 15/15, +0.158 | 15/15, +0.166 |
| held-out-2, 15 export occupations | 14/15, +0.200 | 14/15, +0.186 | 15/15, +0.191 |
| **held-out top-1, all sets** | **51/55** | 47/55 | 49/55 |

Top-3 is 14–15/15, 10/10 and 5/5 throughout for the shipped setting.

**Choice: MiniLM with translation.**
- It is best on held-out top-1 (51 of 55) and on mean margins.
- English matching is unchanged.
- The model is a third of the size.

The multilingual model gains one held-out-2 profile but loses two extra occupations, and its Hinglish margin is less than half. MiniLM without translation drops the Hindi and Hinglish profiles: Hinglish 2/5, and two A1b Hinglish profiles miss (the truck driver and the pharmacist).

**Remaining misses:**
- **Adjacent occupations:**
  - pharmacist → Pharmacy Technicians (tuning, and held-out-2 third person);
  - a nurse after a career break → Medical Assistants.
- **The teacher one-liner** → Physical Therapists, rank 3 (0.17).
- **The Devanagari electrician** → Plumbers: the rewrite says "lay pipes and pull wires", and pipes pull towards plumbing.

The A2 gate still passes (13/15, 10/10).

### 13.4 Verdict separation

The threshold is tuned on the tuning profiles only:
- **Tuning set:** 15 full profiles, against 3 + 8 partial and 3 wrong-role profiles (someone from another field applying to the target).
- **Validation set:** the 15 held-out-2 full profiles, against 6 partial and 3 wrong-role profiles.

| | Full profiles (min) | Partial / wrong (max) | Accuracy at 0.3254 |
|---|---|---|---|
| tuning (29) | 0.326 | 0.325 | 1.00 |
| **validation (24)** | 0.146 | 0.192 | **0.58** |

**The tuned threshold does not transfer.** No full profile in validation scores above a negative one by a wide margin, and 10 of the 15 held-out-2 full profiles fall below 0.3254 (WhatsApp, two-liner, Q&A, third-person, key-value styles). Every partial and wrong-role validation profile is correctly below it.
- **Cause:** the score measures how much of the occupation the evidence *shows*, so short descriptions score low whatever the person's real level. The tuning profiles are long and resume-shaped.
- **Not fixed by tuning on validation.** Options for the next round:
  - a separate `insufficient_evidence` verdict when there are few matchable units or little evidenced weight;
  - a tuning set with mixed styles;
  - a threshold per evidence-volume band.
- **Until then:** `under_skilled` on a short description means "not shown", and `fit_label` is the better user-facing signal.

### 13.5 Examples, before and after A3

| Profile vs role | A2 (score → fit %) | A3 (score → fit %, label, verdict) |
|---|---|---|
| tuning staff nurse vs "staff nurse" | 0.575 → 77 | 0.651 → **86**, Strong fit, good_fit |
| tuning ITI electrician vs "electrician" | 0.362 → 54 | 0.496 → **69**, Good fit, good_fit |

A2 scores are mapped with the A3 `fit_percent` for comparison.

**Electrician:**
- "Assemble, install, test, or maintain electrical or electronic wiring…" was missing in A2 and is now **met** (0.65, via the clause "install electrical wiring" against the skills item "House wiring"). The work bullets "Lay conduits and pull wires…" and "Install and connect DBs, MCBs…" reach 0.53 on their own, which is partial; MiniLM doesn't read DB/MCB as wiring.
- "Repair or replace wiring…" is met at 0.81 by the clause "repair faults in wiring".
- Role history: "Maintenance Electrician" (9 years), "Apprentice Electrician" (2 years) and the header "Electrician" give the blueprint, ladder and tool tasks an implied 0.4.

**Nurse:**
- The title line "Staff Nurse" no longer meets the supervision task (now partial, from the B.Sc Nursing line).
- Prescribing (1 task, 2 DWAs) is listed as not applicable in India.
- "Maintain accurate, detailed reports and records" becomes partial from 7.5 years of role history.

### 13.6 Latency (CPU, MiniLM, nurse vs "staff nurse", fixture client)

| Start | Model load | First request | Warm (median of 5) |
|---|---|---|---|
| empty embedding cache | 12.9 s | 1.25 s | 0.19 s |
| disk cache, no prewarm | 16.5 s | 0.33 s | 0.19 s |
| after `prewarm(["29-1141.00"])` | 11.5 s | 0.32 s | 0.19 s |

Most of the earlier "~20 s cold" was loading the model (once per process). With `M2_PREWARM_SOCS`, both the model load and the occupation preparation happen at startup, so the first user request takes about 0.3 s.

### 13.7 Readiness checklist (context/AGENTS.md §18) for the v2 engine

| # | Gate criterion | Status | Evidence |
|---|---|---|---|
| 1 | Self-contained execution | ✅ | Same FastAPI service on port 8002 (`uvicorn src.api.main:app --port 8002`). Module 1 is reached only over REST (`M1_BASE_URL`). Offline fixtures cover all tests; `scripts/prewarm_embeddings.py --all-fixture` runs without module 1 |
| 2 | Contract compliance | ✅ | Pydantic request/response models in `src/general/schemas.py`, exported to `src/models/schema_m2_v2.json` with a drift test (`test_v2_schema_export_is_current`). v2 accepts v1's `UserProfile`. Errors use INTEGRATION.md's `{"error": {"code", "message"}}` |
| 3 | 100% passing tests | ✅ | A3: 426 passed, 1 skipped. **Superseded by §14.7 (A3b): 453 passed, 1 skipped** |
| 4 | Error handling | ✅ | Module 1 down → 503 `M1_UNAVAILABLE` for v2 while v1 keeps working (tested). Unknown role → 404 `ROLE_NOT_RESOLVED`; unknown SOC → 404 `OCCUPATION_NOT_FOUND`; bad module 1 response → 502. Invalid input → 422; v1's file errors on `/v2/analyze_resume`. Groq missing or failing → original text plus a warning. Related occupations module 1 can't return are skipped |
| 5 | Zero cross-module imports | ✅ | `src/general` imports only `src.*` (module 2) and third-party packages. Module 1 data comes over REST or from fixture files; `career_intel.db` is never opened |
| 6 | Documentation | ✅ | README (v2 quick start, env vars, payload examples), this file §11–13 (design, formulas, worked example, calibration, limits), HANDOFF_TO_M3 (`/api/v2/skills/match_text` contract and example) |
| 7 | Clean git history | ✅ | Conventional Commits with `(module-2)` scope on `feat/module-2-skill-gap`, one change per commit |

### 13.8 Known limits after A3

- **Verdict on short descriptions** (§13.4).
- **Roadmap ordering.** The main roadmap is ordered by weight, as specified, so heavy O*NET software (Epic, Outlook, Word) can fill it for a nurse or an electrician. Ranking by expected score gain (each item's share of its type × its missing credit) would favour tasks; worth deciding before the UI uses it.
- **Education lines are still evidence** (e.g. "B.Sc Nursing…" as partial evidence of supervision).
- **Not yet validated on live module 1 data.** `/related` (alternatives, over-qualified, related roles in role history) still uses the fixture stand-in, and the 10 extra occupations are module 1 v2.0 data.

---

## 14. General engine (v2) — A3b: short descriptions, roadmap ranking, module 1 v2.2

Where this section changes a value in §11–13, this section wins.

### 14.1 Verdict for short descriptions

A short description can't show much of an occupation, so a low score from little text means "not shown yet", not "under-skilled".

**Evidence volume** (`verdict.volume`, returned as `evidence_volume`):
- `units`: substantive evidence sentences or items. Clause copies, title lines, education lines and answers to questions are not counted. A unit has at least `MIN_UNIT_WORDS = 2` words.
- `related_share`: the share of the core requirements (with the score's type shares and item-count scaling) that have any evidence at cosine ≥ `RELATED_FLOOR = 0.35`, or are met or partial through an alias, role history or an answer.
- `short`: `units < SHORT_UNITS`.

**Verdict:**

| Condition | Label |
|---|---|
| score ≥ `GOOD_FIT_THRESHOLD = 0.22` (`GOOD_FIT_THRESHOLD_SHORT = 0.17` when short) | `good_fit`, or `over_qualified` (§12.2) |
| below, short (`SHORT_UNITS = 3`) and `related_share ≥ MIN_RELATED_SHARE = 0.05` | `insufficient_evidence` |
| otherwise | `under_skilled` |

**With `insufficient_evidence`:**
- **Provisional fit:** `fit_provisional: true` and `fit_range` {low: the current `fit_percent`, high: the fit if the questions were all answered yes}.
- **`follow_up_questions`:** 3–5 questions (`QUESTIONS_MIN`, `QUESTIONS_MAX`) from the heaviest core requirements with no evidence yet. Tasks, DWAs and market skills come first, then tech and tools; one per phrase.
  - Templates: "In your work, do you {requirement without its example tail}?", "Have you used {tool} in your work?", "Do you have experience with {market skill}?".
  - With `GROQ_API_KEY`, Groq rewrites them in plain English for the occupation (cached). On any failure the templates are kept.
- **Answering:** the request takes `answers: [{requirement_id, answer: yes|no|some, detail?}]`.
  - `yes`: the requirement is met by self-reported evidence (credit 0.40 / required level).
  - `some`: partial at `ANSWER_SOME_CREDIT = 0.3`.
  - `no`: no change.
  - Answered items carry `reason: "answered"` and evidence "You answered yes: {detail}". A `detail` also becomes a self-reported evidence unit.
  - Answers raise the score and `related_share` but not `units`, so a short description keeps getting the next questions until the score clears the threshold.

**Tuning** (`python scripts/calibrate.py --verdict`, tuning profiles only): a grid over the two thresholds, `SHORT_UNITS` and `MIN_RELATED_SHARE`, minimising a cost.

| Profile kind | `good_fit` | `insufficient_evidence` | `under_skilled` |
|---|---|---|---|
| full | 0 | 1 | 5 |
| partial | 4 | 1 | 0 |
| wrong role | 5 | 2 | 0 |

The tuning profiles (48) are `tuning/` and `verdict_tuning/` (A3), plus `tuning_short/` (A3b): 12 short-style practitioners (WhatsApp, two-liners, Q&A, third person, typed skill lists), 4 short partial and 3 short wrong-role profiles. `FIT_MEDIAN_FULL = 0.42` is the median full-profile score in that set.

**Validation** ("acceptable" = full → good_fit or insufficient_evidence; partial → under_skilled or insufficient_evidence; wrong role → under_skilled):

| Set | Acceptable | full: good / insufficient / under | partial: good / insufficient / under | wrong: good / insufficient / under |
|---|---|---|---|---|
| tuning (48) | 47 | 24 / 3 / **0** | 1 / 4 / 10 | 0 / 0 / 6 |
| **fresh validation, `verdict_validation2/` (20, never tuned on)** | **19** | 7 / 3 / **0** | 1 / 4 / 0 | 0 / 0 / 5 |
| A3 validation: held-out-2 full + `verdict_validation/` (24) | 15 | 5 / 1 / **9** | 0 / 1 / 5 | 0 / 0 / 3 |

**Reading the validation results:**
- **Fresh validation:** no practitioner is called `under_skilled`. That includes one-line practitioners (ICU nurse, ITI wireman, trailer driver, pharmacist), who get `good_fit` or `insufficient_evidence`. One partial profile (a canteen kitchen helper, 0.19) is called a good fit.
- **A3 validation:** 9 held-out-2 practitioners are still `under_skilled`: third-person, cover letter, Q&A, key-value, WhatsApp-with-bullets.
  - They have 4–9 units, so they aren't "short", and score 0.02–0.21.
  - Their `related_share` is 0.56–0.85, while every wrong-role profile in any set is ≤ 0.41.
  - A rule of "`insufficient_evidence` when related_share ≥ ~0.45, at any length" would fix all nine. It comes from looking at validation data, so it is **not shipped**; it needs a decision and a new fresh set.

### 14.2 Roadmap ranking

`roadmap.plan` ranks missing and partial core items by **expected score gain**: the type's effective share (reliability, item count, renormalised, as in the score) × the item's share of its type's weight × the credit still missing. Role-implied items rank after real gaps; taxonomy prerequisites still move ahead.

The main roadmap is at most `ROADMAP_MAX_ITEMS = 8` items, of which at most `ROADMAP_MAX_TECH = 3` are tech/tool. Generic office and productivity software goes to `roadmap.basics` unless the occupation's market skills name it (e.g. Excel for Accountants via "Advanced Excel"): Word, Excel, Outlook, Access, PowerPoint, Office, Windows, SharePoint, Exchange, Google Docs/Sheets/Drive, Adobe Acrobat, email, browsers, `BASIC_SOFTWARE`. Everything else goes to `later`.

Examples (tuning profiles):
- **Staff nurse:**
  - main: General Nursing; Record patients' medical information and vital signs; Assess needs…; Perform physical examinations…; Consult with institutions…; Inform physician…; Engage in nursing research; Administer non-intravenous medications;
  - basics: Access, Office, Outlook, PowerPoint, SharePoint, Windows, Exchange, Google Docs.
- **ITI electrician:**
  - main: ITI Electrical Standards; Circuit Troubleshooting; Place conduit…; Connect wires to circuit breakers…; Direct or train workers…; Diagnose malfunctioning systems…; Inspect electrical systems…; Install ground leads…;
  - basics: Outlook, Word, Acrobat, Excel, Office, Windows.

### 14.3 Education lines

The Education section, and lines elsewhere that name a degree (v1's degree pattern) with an institution, a year, or ≤ 8 words, become `education` units (`evidence.is_education_line`). They are never task, DWA or tool evidence and carry no taxonomy ids. They still support "draws on" (knowledge inference) and are returned as `qualifications`. "B.Sc Nursing, Government College of Nursing, 2019" no longer counts towards supervising nursing personnel (test `test_nurse_title_line_is_role_history_not_evidence`).

### 14.4 Module 1 v2.2

- **Fixtures.**
  - `tests/mocks/m1_occupation_requirements_export.json` is v2.2.0 (15 occupations, 771 O*NET tool rows, posting-backed market skills with `posting_count`/`soc_posting_total`, curated market skills at importance 0.50, `india_relevant` flags).
  - The v2.0 and v2.1 copies stay for filter regression tests.
  - The 10 extra occupations were refetched from a live module 1 v2.2 (§14.6).
- **Provenance.** `tool_*` ids are hand-written only before v2.2 (`REAL_TOOLS_FROM = (2, 2)`, keyed on the data's module 1 version); from v2.2 they are O*NET Tools Used.
- **Market support.** `posting_count ≥ 3` is used automatically when present. Module 1 already keeps ≥ 3 postings and the top 50.
- **Not applicable in India.** Module 1's `india_relevant = 0` rows (v2.2: Registered Nurses prescribing, with module 1's reason) are merged with the local list (§13.1), which still covers Pharmacists and Medical Assistants.
- **Version.** `GET /api/v1/meta` is used for the cache version (`2.2.0+<export hash>`).

**Calibration on v2.2.** Frozen A3 constants on v2.2 fell from 49/55 to 47/55 held-out top-1: the real tools are hundreds of specific equipment names that resumes rarely mention. Re-tuning on the tuning set only (allowed by the brief) gave:
- tech, tool and market_skill 0.55/0.45; task and DWA 0.60/0.50;
- `TYPE_SHARE` task 0.40, DWA 0.20, market_skill 0.30, tech 0.05, tool 0.05.

| Set (25 occupations) | A3 constants, v2.1 export | A3 constants, v2.2 export | **A3b re-tuned, all v2.2** |
|---|---|---|---|
| tuning | 14/15 | 14/15 | **15/15**, +0.328 |
| held-out | 12/15 | 11/15 | **14/15**, +0.111 |
| A1b extra occupations | 10/10 | 10/10 | **10/10**, +0.169 |
| held-out, curated removed | 12/15 | 11/15 | **14/15**, +0.082 |
| held-out-2, export occupations | 13/15 | 12/15 | **13/15**, +0.124 |
| held-out-2, extra occupations | 10/10 | 10/10 | **10/10**, +0.129 |
| held-out-2, curated removed | 12/15 | 12/15 | **11/15**, +0.097 |
| Hinglish / Hindi | 4/5 | 4/5 | **3/5**, +0.120 |
| **held-out top-1, all sets** | 49/55 | 47/55 | **50/55** |

The first two columns already include A3b's evidence changes (education lines). The last column also uses the 10 extra occupations from live v2.2.

**Regressions to note:**
- Mean margins are lower (e.g. held-out +0.140 → +0.111).
- Held-out-2 without curated rows: 12 → 11.
- Hinglish: 4/5 → 3/5. The Hinglish nurse now loses to a neighbour.
- Strong profiles saturate `fit_percent` at 100, since the threshold is now 0.22 and the median 0.42.

The A2 gate passes (13/15, 10/10).

### 14.5 Short-description examples (MiniLM, v2.2 fixture, template questions)

| Profile | Before answering | Answers | After |
|---|---|---|---|
| WhatsApp nurse: "hi mam i am staff nurse 3 yrs govt hospital medicine ward. injection, BP checking, dressing" | `insufficient_evidence`, 0.043, fit 10 (range 10–26), units 1, related 0.56 | yes, yes, some | `insufficient_evidence`, 0.078, fit 18 (range 18–33), next 5 questions |
| Two-line electrician: "Electrician, 7 years. / House wiring and repair work." | `insufficient_evidence`, 0.143, fit 32 (range 32–52), units 2, related 0.52 | yes, yes, no | **`good_fit`**, 0.180, fit 41 |

Nurse questions:
1. "In your work, do you record patients' medical information and vital signs?"
2. "…administer medications to patients and monitor patients for reactions or side effects?"
3. "…maintain accurate, detailed reports and records?"
4. "…monitor, record, and report symptoms or changes in patients' conditions?"
5. "…provide health care, first aid, immunizations, or assistance in convalescence or rehabilitation?"

Electrician questions:
1. "…prepare sketches or follow blueprints?"
2. "…place conduit, pipes, or tubing, inside designated partitions, walls, or other concealed areas?"
3. "…use a variety of tools or equipment?"
4. "…plan layout and installation of electrical wiring, equipment, or fixtures?"
5. "…test electrical systems or continuity of circuits…?"

### 14.6 Live check (module 1 v2.2 on port 8001)

Module 1's current code ran against its own database file in place, with no copy, and was queried only over REST.
- **`/api/v1/meta`:** schema 2.2.0, built 2026-10-06T13:38Z, export hash 02b93f2a…. `table_counts.onet_dwa` = **24,087**.
- **DWA duplication:**
  - Across all 1,016 occupations, `/requirements?item_type=dwa` returned **18,583 rows for 923 occupations, all distinct (soc, dwa)**: no duplication in what module 1 serves.
  - The table's 24,087 rows are task-level (several tasks map to one DWA).
  - The **264,957** in module 1's DATABASE.md and HANDOVER.md doesn't match the live count and looks like a stale doc number.
- **`/related`:** returns real neighbours with job zones (Registered Nurses → Acute Care Nurses (4), Nurse Practitioners (5), Critical Care Nurses (4), Clinical Nurse Specialists (5), LPNs (3)). `relatedness_tier` is null, so role history's "closely related" rule (`ROLE_RELATED_TIERS`) never applies live.
- **Over-qualified:**
  - The tuning staff nurse (7.5 years; module 1's Indian-postings band 1.3–3.3 years) is `over_qualified`, with Clinical Nurse Specialists (job zone 5) suggested.
  - The 22-year nursing superintendent is `good_fit` (0.31). Live search resolved her titles poorly ("Lt. Col." → Industrial Ecologists, "Nursing Superintendent" → Nursing Assistants); such non-applying, low-confidence titles are now hidden from `role_history` (`ROLE_DISPLAY_MIN_CONFIDENCE = 0.9`).
- **Close alternatives:** none returned for the nurse, pharmacist and electrician tuning profiles; their related occupations scored more than 0.05 below the target.
- **Refetch:** `scripts/refetch_extra_occupations.py` refreshed the 10 extra occupations from this module 1 (§14.4).

### 14.7 Readiness checklist (AGENTS.md §18) for the v2 engine, A3b

| # | Gate criterion | Status | Evidence |
|---|---|---|---|
| 1 | Self-contained execution | ✅ | Same service on port 8002. Module 1 only over REST (`M1_BASE_URL`); offline fixtures for every test; prewarm script and `M2_PREWARM_SOCS` |
| 2 | Contract compliance | ✅ | `src/general/schemas.py` → `src/models/schema_m2_v2.json` with a drift test; v2 accepts v1's `UserProfile`; INTEGRATION.md error shape |
| 3 | 100% passing tests | ✅ | A3b: 453 passed, 1 skipped. **Superseded by §15.9 (A4): 474 passed, 1 skipped** |
| 4 | Error handling | ✅ | Module 1 down → 503 `M1_UNAVAILABLE` while v1 works; 404/502/422 as documented; Groq translation and question rephrase fail safe to the original text or templates |
| 5 | Zero cross-module imports | ✅ | `src/general` imports only module 2 and third-party packages; `career_intel.db` is never opened by module 2 |
| 6 | Documentation | ✅ | README (quick start, env vars, payloads incl. answers), §11–14, HANDOFF_TO_M3 |
| 7 | Clean git history | ✅ | Conventional Commits with `(module-2)` scope on `feat/module-2-skill-gap` |

### 14.8 Known limits after A3b

- **Verdict on oblique, not short, descriptions** (§14.1): 9 of 15 held-out-2 practitioners are still `under_skilled`. A related-share rule fixes them but needs a decision and fresh validation.
- **Hinglish:** 3/5 on v2.2 (4/5 in A3).
- **`fit_percent` saturates at 100** for strong profiles.
- **Module 1 data:** `relatedness_tier` is missing from `/related`, and the DATABASE.md DWA count is stale.
- **Over-qualified** relies on module 1's Indian experience bands, which can be narrow (1.3–3.3 years for Registered Nurses); a 7.5-year staff nurse is told she is over-qualified.

## 15. General engine (v2) — A4: oblique write-ups, shorthand, fit, module 1 fixes

A4 finishes the v2 engine for its pull request. As in every earlier round, constants were tuned on tuning sets only, frozen (commit `e8ec08d`), and then measured on validation sets; `verdict_validation3` was written after the freeze.

### 15.1 What changed

| Piece | Change |
|---|---|
| C1 verdict | `insufficient_evidence` for oblique write-ups (third person, cover letter, Q&A, key-value) by related share at any length; `under_skilled` when past titles point to another field (`other_role`); Q&A questions and negative answers are no longer evidence |
| C2 shorthand | `data/general/shorthand.json` (79 entries): literal expansions of Indian workplace abbreviations with context guards, in a matching copy; the original and the rewrites are returned. Optional Groq normalisation of very short units (`M2_NORMALISE_SHORT=1`), off by default |
| C3 fit_percent | Re-anchored on good-fit tuning profiles; 99 at most unless every core requirement is met |
| C4 experience band | Module 1's Indian band only with `sample_size >= 30` and data from 2023 on; otherwise the job zone. Over-qualified at band max + 2 years |
| C5 related | With no `relatedness_tier` anywhere, module 1's first 5 related occupations (by `index_val`) count as close for role history; real tiers are used when present |
| C6 basics | At most 4 office tools of weight >= 0.5; the rest go to `later` |
| C7 fixtures | Main fixture = module 1's latest v2.2 export (first v2.2 export kept as `_v2.2a`); module 1's live title search captured for the profiles' past titles (`tests/mocks/m1_title_search.json`, `scripts/capture_title_search.py`) |

### 15.2 Frozen constants (tuning sets only)

| Constant | A3b | A4 |
|---|---|---|
| `scoring.GOOD_FIT_THRESHOLD` | 0.22 | **0.29** |
| `verdict.GOOD_FIT_THRESHOLD_SHORT` | 0.17 | **0.20** |
| `verdict.SHORT_UNITS`, `MIN_RELATED_SHARE` | 3, 0.05 | 3, 0.05 |
| `verdict.OBLIQUE_RELATED_SHARE` | — | **0.50** |
| `verdict.FOCUS_MIN` | — | 0.0 (off: the search never chose it) |
| `verdict.OTHER_ROLE_EXTRA` | — | **0.0** (other_role on: no `insufficient_evidence`) |
| `service.OTHER_ROLE_MIN_CONFIDENCE` | — | 0.9 |
| `scoring.FIT_MEDIAN_GOOD`, `FIT_P90_GOOD`, `FIT_CAP` | median 0.42 → 80, cap 100 | **0.48 → 80, 0.59 → 95, 99** |
| `scoring.INDIA_BAND_MIN_SAMPLE`, `INDIA_BAND_MIN_YEAR` | — | 30, 2023 |
| `scoring.OVERQUALIFIED_EXTRA_YEARS` | 3.0 | **2.0** |
| `service.ROLE_RELATED_UNTIERED_TOP` | — | 5 |
| `roadmap.BASICS_MAX`, `BASICS_MIN_WEIGHT` | — | 4, 0.5 |

Matching thresholds and type shares are unchanged from A3b.

### 15.3 Verdict for oblique write-ups (C1)

**Tuning data.** `tuning_oblique` has 25 new profiles:
- 13 practitioners, at least 3 per style;
- 4 beginners;
- 8 people from other fields, 5 of them career changers (two in Q&A form, three as a resume or cover letter with past titles).

With the A3b tuning sets that makes 73 tuning profiles.

**Findings on tuning data that shaped the design:**
- **Q&A questions were matched as the person's evidence.** "Have you assessed a loan yourself?" counted as a claim, so two Q&A career changers scored 0.24–0.27, above the good-fit threshold. Questions (a `Q:` line, or a sentence ending in "?") and negative answers ("No, …", "Not yet") are now skipped; inline answers ("Q: Years? A: 6") are kept.
- **Focus doesn't separate.** The share of a person's sentences related to the target is as high for career changers (0.67–1.0) as for practitioners (0.5–1.0).
- **Past titles do separate**, when there are dated titles. `other_role` is set when a past title resolves at confidence >= 0.9 to another SOC major group and no title is close to the target. The confidence floor and major-group rule are needed because module 1's search misresolves titles at 0.7 ("senior staff nurse" → Nurse Midwives, "accounts assistant" → Dental Assistants) and sometimes at 0.95 ("teller" → Cashiers).

**Rule** (`verdict.label_for`):

| Condition | Verdict |
|---|---|
| score >= threshold (0.29; 0.20 for short) | good_fit, or over_qualified |
| `other_role` | under_skilled |
| short and related share >= 0.05 | insufficient_evidence |
| related share >= 0.50 | insufficient_evidence |
| otherwise | under_skilled |

**Search** (`scripts/calibrate.py --verdict`, 200,970 combinations):
- The objective is the lowest `VERDICT_COST`; ties go to fewer partial or wrong-role profiles called a good fit, then the simpler rule, then the larger margin.
- The good-fit threshold moved to 0.29 because the new beginner profiles score up to 0.28.
- Costs on tuning: 0.29–0.31 all cost 38; 0.25–0.28 cost 40; 0.22 costs 46.

**Confusion (acceptable = practitioner good_fit or insufficient, beginner under_skilled or insufficient, other field under_skilled; as in A3b):**

| Set | Acceptable | Practitioners g / i / u | Beginners g / i / u | Other field g / i / u |
|---|---|---|---|---|
| Tuning (73) | 71/73 | 22 / 18 / **0** | 0 / 16 / 3 | **0** / 2 / 12 |
| **verdict_validation3 (35, written after freezing)** | **30/35** | 5 / 13 / **0** | 0 / 4 / 3 | **0** / 5 / 5 |
| verdict_validation2 (A3b's fresh set, 20) | 20/20 | 5 / 5 / 0 | 0 / 5 / 0 | 0 / 0 / 5 |
| A3 validation (held-out-2 + verdict_validation, 24) | 24/24 | 3 / 12 / 0 | 0 / 3 / 3 | 0 / 0 / 3 |

g / i / u = good_fit / insufficient_evidence / under_skilled.

On `verdict_validation3`:
- **Met:** no practitioner is `under_skilled`, and no beginner or person from another field is a good fit.
- **Not met:** "other field mostly `under_skilled`". It is 5/10. The other five get `insufficient_evidence`: teacher → loan officer (cover letter), data entry → designer (third person), pharma rep → nurse (Q&A), delivery rider → chef (short), electrician → customer support (Q&A).
  - The two resume-format career changers are `under_skilled` through `other_role`.
  - In the five, a related share >= 0.5 comes from overlap in what the person does (people contact, records, numbers) and no past title is detected.
  - Follow-up questions are their next step.
- **The price of fewer false `under_skilled`:** most practitioners writing obliquely get `insufficient_evidence` with questions rather than `good_fit` (13 of 18 in validation3).
- **Earlier validation sets:** A3's 9 `under_skilled` practitioners (§14.1) are all fixed.

### 15.4 Shorthand (C2)

**The map** (`src/general/shorthand.py`, `data/general/shorthand.json`). The design was fixed before any held-out result was looked at:
- **Literal expansions:** BP → blood pressure, MCB → miniature circuit breaker, GST → goods & services tax, yrs → years.
- **Case:** `upper`-only for ambiguous abbreviations (IV, OT, CA, DB).
- **Context guards** checked against the whole input:
  - DB → distribution board only near MCB, wiring, panel …, never near SQL or database;
  - OT → operation theatre only near nurse, surgery, ward ….
- **What it changes:** a matching copy only. Spans stay on the original text; each unit keeps `original_text` and `rewrites`, and evidence in the response shows both.
- **Clause splitting:** expansions contain no clause separators, so clauses keep their own spans.

On the calibration sets the map is neutral for top-1 (held-out 50/55 before and after) and lowers the tuning mean margin from +0.328 to +0.322.

**Groq normalisation of very short units** (<= 4 words, rewritten with the whole description as context; batched, cached, fail-safe). Measured live with the key:

| Set | Map only | Map + Groq |
|---|---|---|
| Tuning top-1 / mean margin | 15/15, +0.322 | 15/15, +0.325 |
| Tuning verdicts | 46/48 | 46/48, identical labels |
| Held-out top-1 (held-out, new, held-out-2, Hinglish) | 14, 10, 23/25, 3 | **13**, 10, 23/25, 3 |
| verdict_validation2 | 19/20 | 19/20 |

The verdict rows in this table were measured with the A3b constants, before the A4 freeze.

It was decided on tuning: no gain, plus a network call per request, so it is **off by default** (`M2_NORMALISE_SHORT=1` turns it on). Held-out confirms it: one top-1 lost.

**Examples** (fixture, template questions; before = map off):

| | Before | After map | After 3 answers (yes, yes, some) | Map + Groq |
|---|---|---|---|---|
| WhatsApp nurse ("hi mam i am staff nurse 3 yrs govt hospital medicine ward. injection, BP checking, dressing") | insufficient, 0.043, fit 7 (7–19), related 0.56 | insufficient, 0.053, fit 9 (9–21), related 0.67 | insufficient, 0.087, fit 15 (15–27), next 3 questions | insufficient, 0.086, fit 15 |
| Two-line electrician ("ITI electrician 7 yrs. / House wiring, DB and MCB fitting, earthing, fault repair.") | insufficient, 0.149, fit 26 (26–41) | insufficient, 0.158, fit 27 (27–43) | **good_fit**, 0.204, fit 35 | insufficient, 0.158, fit 27 |

The nurse's first questions ask about recording vital signs, giving medications and watching for reactions, keeping records, monitoring symptoms, and first aid or immunisations. The electrician's ask about blueprints, conduit in walls, ladders and scaffolds, tools, and the licence.

Rewrites shown on the evidence: "BP -> blood pressure" for the nurse; "DB -> distribution board" and "MCB -> miniature circuit breaker" for the electrician. Groq added "blood pressure checking -> checking patients' blood pressure", "House wiring -> wiring houses" and "fault repair -> repairing faults".

### 15.5 fit_percent (C3)

`fit_percent` is piecewise-linear through these points:

| Score | fit_percent |
|---|---|
| 0 | 0 |
| 0.29 (the good-fit threshold) | 50 |
| 0.48 (median good-fit tuning profile) | 80 |
| 0.59 (90th percentile) | 95 |
| 1.0 | 99 |

It is 100 only when every core requirement is met. The anchors come from the 18 full tuning profiles (incl. short and oblique) at or above the threshold. The median of *all* full tuning profiles (0.27) is below the threshold, so it can't anchor 80.

| Profile | Score | fit_percent | Verdict |
|---|---|---|---|
| Full staff nurse (tuning) | 0.596 | 95 | good_fit |
| Full electrician (tuning) | 0.491 | 82 | good_fit |
| WhatsApp nurse | 0.053 | 9 | insufficient_evidence |
| Accountant applying as a nurse | 0.000 | 0 | under_skilled |

### 15.6 Experience band, related occupations, basics (C4–C6)

- **Experience band.**
  - `scoring.india_band_reliable` uses module 1's `indian_experience` only when `fallback_to_job_zone` is false and `sample_size >= 30`, and the newest year in `years_covered` is >= 2023. Missing fields count as unreliable, and the job-zone band is used.
  - Over-qualified needs more than band max + 2 years and a more senior related fit.
  - Tests: the 7.5-year staff nurse on module 1's n=3, 2015–16 band (1.3–3.3) is `good_fit` on the job-zone band (2–6); with the same band at n=120 and 2023–2025 she is `over_qualified`.
- **Related occupations.** `service.close_related` returns `Primary-Short`/`Primary-Long` tiers when module 1 sends any tier, otherwise the first 5 by `index_val`. Fixture stand-in tiers are still not close.
- **Basics.** Nurse: Access, Office, Outlook, PowerPoint (SharePoint, Windows and the rest move to `later`).

### 15.7 Module 1 update, live check (C7) and Hinglish (C8)

**Module 1 on main** (merged `78c0ae1`):
- populated `relatedness_tier`;
- 2023+ experience bands with a job-zone fallback below n = 30;
- `sample_size` and `years_covered` in `/profile`;
- a deduplicated `onet_dwa` (24,087 rows; docs corrected);
- a rebuilt export.

The local `career_intel.db` was built before the rebuild, so main's latest module 1 code returns 500 on `/profile` against it (`no such column: fallback_to_job_zone`). The live check therefore ran module 1 at `e84674a` (the code that built this DB) from its own folder, against its own DB in place, with no copy, over REST only. The DB needs the new loader run by module 1's owner.

**Live results:**
- **`/api/v1/meta`:** schema 2.2.0, built 2026-10-06T13:38Z, export hash 02b93f2a…
- **Data:**
  - The live requirement rows for the 15 export occupations match the latest export item for item. Only the first v2.2 export (`_v2.2a`) differs, in the Data Scientists market skills.
  - The refetch of the 10 extra occupations returned the committed data unchanged.
- **`/related`:** `relatedness_tier` is still null in this DB, so C5's fallback applies. Registered Nurses → Acute Care, Nurse Practitioners, Critical Care, Clinical Nurse Specialists, LPN/LVN. An "Acute Care Nurse" past title now counts as role history for Registered Nurses (`applies_to_target: true`).
- **Over-qualified:** the stale bands are ignored. The 7.5-year staff nurse is `good_fit` (job-zone band 2–6; A3b said over-qualified). The electrician, pharmacist and accountant at 12 years are `good_fit`; no more senior related fit clears the threshold.
- **Close alternatives:**
  - The electrician gets Electrical Power-Line Installers (0.48).
  - The pharmacist gets **Emergency Medicine Physicians (0.51)**. It is the same job zone, so it is never an over-qualified suggestion, but it is a questionable alternative across a licensing boundary (§15.10).
- **Calibration, fixture vs live:** identical for every set (top-1 and margins in §15.8, verdict tables in §15.3), as expected from identical rows.

**Hinglish / Hindi** (5 profiles, frozen constants, not tuned on):

| Translation | Top-1 | Top-3 | Mean margin | Verdicts |
|---|---|---|---|---|
| Committed translations | 3/5 | 5/5 | +0.112 | 1 good_fit, 4 insufficient, 0 under_skilled |
| **Live Groq** | **4/5** | 5/5 | +0.123 | 1 good_fit, 4 insufficient, 0 under_skilled |
| None | 3/5 | 3/5 | +0.009 | 5 under_skilled |

The remaining miss is the Devanagari electrician, beaten by Plumbers.

### 15.8 Final calibration (MiniLM, frozen constants; fixture = live)

| Set | Top-1 | Top-3 | Mean margin |
|---|---|---|---|
| Tuning | 15/15 | 15/15 | +0.317 |
| Held-out | 14/15 | 15/15 | +0.124 |
| Extra occupations | 10/10 | 10/10 | +0.196 |
| Held-out, curated removed | 13/15 | 15/15 | +0.099 |
| Held-out-2 | 13/15 | 14/15 | +0.122 |
| Held-out-2, extra occupations | 10/10 | 10/10 | +0.129 |
| Held-out-2, curated removed | 12/15 | 14/15 | +0.099 |
| Hinglish (committed translations) | 3/5 | 5/5 | +0.112 |
| **Held-out total** | **50/55** | | |

The A2 gate passes (held-out-2 >= 12/15, extra occupations >= 7/10).

### 15.9 Readiness checklist (AGENTS.md §18) for the v2 engine, A4

| # | Gate criterion | Status | Evidence |
|---|---|---|---|
| 1 | Self-contained execution | ✅ | Same service on port 8002. Module 1 only over REST (`M1_BASE_URL`); offline fixtures for every test (incl. captured title searches); prewarm script and `M2_PREWARM_SOCS` |
| 2 | Contract compliance | ✅ | `src/general/schemas.py` → `src/models/schema_m2_v2.json` with a drift test (new: `evidence.rewrites`, `evidence_volume.focus` / `other_role`); v2 accepts v1's `UserProfile`; INTEGRATION.md error shape |
| 3 | 100% passing tests | ✅ | `pytest module-2-skill-gap/tests/` → **474 passed, 1 skipped** (live Groq without a key). The v1 suite is unchanged (284 + 1 skipped); 190 general-engine tests, including held-out floors, the A2 gate and verdict regressions on both fresh validation sets |
| 4 | Error handling | ✅ | Module 1 down → 503 `M1_UNAVAILABLE` while v1 works; 404/502/422 as documented; Groq translation, question rephrase and normalisation fail safe |
| 5 | Zero cross-module imports | ✅ | `src/general` imports only module 2 and third-party packages; `career_intel.db` is never opened by module 2 |
| 6 | Documentation | ✅ | README (quick start, env vars, payloads incl. answers and rewrites), §11–15, HANDOFF_TO_M3 |
| 7 | Clean git history | ✅ | Conventional Commits with `(module-2)` scope on `feat/module-2-skill-gap` |

### 15.10 Known limits after A4

- **People from other fields with oblique write-ups** get `insufficient_evidence` rather than `under_skilled` when nothing names their past role (5/10 in validation3). They are never called a good fit.
- **Practitioners writing obliquely** mostly get follow-up questions, not `good_fit`.
- **Hinglish:** 4/5 with live Groq, 3/5 with the committed translations.
- **Close alternatives can cross licensing boundaries** (pharmacist → Emergency Medicine Physicians).
- **Module 1 local DB:**
  - It needs a rebuild with main's loader for tiers, the new bands and `/profile` under main's code.
  - Its search misresolves some titles even at 0.95 ("teller" → Cashiers), which `other_role`'s guards absorb.

## 16. General engine (v2) — A4b: licensing guard, match_texts for module 3

### 16.1 Licensing guard for alternatives and more senior fits

Bug found in the A4 live check: a pharmacist was offered Emergency Medicine Physicians (0.51) as a close alternative.

- `data/general/regulated_occupations.json` lists occupations whose practice Indian law restricts to holders of a qualification or registration, with the Act behind each: physicians and surgeons (NMC), dentists (DCI), pharmacists (PCI), nurses and midwives (INC), physiotherapists (NCAHP), lawyers (Bar Council), architects (CoA), airline and commercial pilots (DGCA), veterinarians (VCI). For each it gives SOC prefixes and qualification patterns (MBBS, MD/MS with a speciality, BDS, B.Pharm / D.Pharm / Pharm.D, GNM / ANM / B.Sc Nursing, BPT / MPT, LLB, B.Arch, CPL / ATPL, B.V.Sc, …). Chartered accountants are listed but **not gated**: O*NET's Accountants and Auditors (13-2011) also covers accountants who need no licence.
- `regulated.blocked(soc, evidence texts, past SOCs)`: a regulated occupation is offered (close alternative or over-qualified suggestion) only when the user's evidence (any unit, incl. education lines and typed skills, as written and expanded) shows one of its qualifications, or a past title resolves into it.
- A related occupation with a higher job zone than the target is offered only when the user's score on it clears the good-fit threshold.
- What was left out, and why, is in the response's `alternatives_excluded` (debug).
- Tests (fake module 1 with pharmacists, physicians and nurses sharing tasks): a pharmacist with B.Pharm gets no physician or nursing alternatives; a nurse with GNM gets nursing alternatives and no physicians, and without GNM none; an MBBS doctor gets physician alternatives; a higher job zone without a good fit is left out.

### 16.2 `POST /api/v2/skills/match_texts`

- One user's evidence (`free_text` / `skills` / `profile`, `experience_years`) + 1–50 `jobs` (`job_id` unique, `job_text`, optional `job_title`, `soc_code`) → `results[]`: exactly `match_text`'s fields + `job_id`, `job_title`, in order; user-level warnings once at the top.
- The evidence is parsed, encoded and its past titles resolved once (`UserEvidence`); each occupation is scored once per SOC; all new job clauses are encoded in one deduplicated batch; parsed job texts (`JOB_CACHE_TEXTS` = 4,096) and clause vectors (`JOB_CACHE_VECTORS` = 50,000) are kept in memory.
- A job's SOC that module 1 can't give falls back to its text alone, with a warning on that job; size and duplicate errors are 422 `INVALID_REQUEST`.
- Two speed-ups that also apply to `match_text`: job texts skip the v1 taxonomy lookup (job-text items are tasks, never matched by alias; it took 3.5 of 5.9 s), and job clauses are encoded one row each instead of being split into clauses again.
- **Latency** (CPU, 14 threads, MiniLM; 50 jobs of six O*NET tasks each, ~150 words, half with `soc_code`; occupations warm): never-seen listings 2.78 / 2.86 / 3.21 s (median 2.86 s, target 3 s); listings seen before 0.2 s. `tests/calibration/test_latency.py` guards 4.0 s / 3.0 s.

### 16.3 Fields for module 3's unlocks

On every `met[]` / `missing[]` / `strengths[]` / `gaps[]` item:
- `requirement_id`: `'{item_type}:{module 1 item_id}'` (+ `':curated'`: module 1 has a curated and a posting row both with id `autocad` for Civil Engineers), or `'job:{sha1 of the clause, lower-cased, punctuation removed}'[:12]` for job-text clauses. The roadmap's gain ranking is keyed by it too (it was keyed by `item_id`, which collided for that pair).
- `effective_weight`: the share of the score the item carries when fully met: its type's effective share × its share of the type's weight, × blend (job text 0.6 / occupation 0.4, or 1.0) × the experience factor for occupation rows. The score is linear in each item's credit, so for one job, Σ `effective_weight × credit` = `match_score` (tested).
- `score_gain_if_met` (partial and missing items): `effective_weight × (1 − credit)`. `missing[]` in `match_text(s)` is now ordered by it.
- `weight` is unchanged: the raw item weight. The new effective weight is a separate field so existing consumers keep their meaning.

### 16.4 Tests

`pytest module-2-skill-gap/tests/` → **486 passed, 1 skipped** (live Groq without a key). The v1 suite is unchanged (284 + 1 skipped); 202 general-engine tests.

