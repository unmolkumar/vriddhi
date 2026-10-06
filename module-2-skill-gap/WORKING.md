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

**285 passed, 0 failed, 0 skipped** (~1.5–3.5 min; OCR and MiniLM dominate). The general engine (v2, §11–12) adds 119 tests; the v1 tests above are unchanged. The live Groq test (`test_llm_live.py`) runs only when `GROQ_API_KEY` is set and is skipped otherwise.

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
| `under_skilled` | match_score < `GOOD_FIT_THRESHOLD = 0.29` |
| `over_qualified` | ≥ threshold, years > band.high + `OVERQUALIFIED_EXTRA_YEARS = 3`, and a related occupation with a higher job zone also scores ≥ threshold. That occupation is returned as `suggested_role` |
| `good_fit` | otherwise |

**Calibration** (`python scripts/calibrate.py --verdict`, tuning set, A2 scores on each profile's own occupation):
- The 15 full profiles score 0.295–0.656; the 3 partial profiles (fresher nurse, accountant without GST/Tally, ITI apprentice) score 0.176–0.288.
- The threshold is the midpoint, 0.29. It separates the two groups, but the gap is thin (0.007): the truck driver's free-text profile sits at 0.295.
- The verdict judges fit *for the chosen target*, not whether it's the right occupation. 5 of 15 full profiles also clear 0.29 on some other occupation (e.g. the pharmacist on Pharmacy Technicians, 0.60).

### 12.3 Alternatives, gaps, strengths, generic layers

- **Close alternatives.** Module 1's `/related` (first `RELATED_LIMIT = 5`) are prepared and scored with the same evidence; one within `ALTERNATIVE_MARGIN = 0.05` of the target's score, or above it, is returned ("You're also a close fit for X (0.61)" / "an even stronger fit"). When the role resolution is low-confidence, module 1's other search matches are added as "Did you mean X?". Related occupations module 1 can't return are skipped.
- **Gaps and strengths.** Every core result carries status, similarity, credit, weight, required level, provenance, reason, flags and the deciding evidence (text, type, section, span, context span). Strengths are the top 10 met by weight × credit; gaps are partial or missing by weight (top 15, plus `gaps_total`). A requirement met only by `mentioned` or `self` evidence gets advice: *"You mention 'X', but nothing in your work or projects shows it. Add an example of where you did this."*
- **Generic layers.** `draws_on` lists knowledge and skills (inferred or not) and `work_activities` lists GWAs evidenced through met DWAs (§11.1). Abilities appear only as `fit_indicators`. None of these is ever a gap.
- **Provenance summary.** Core items and weight share per provenance, with a note when curated rows are present.

### 12.4 Roadmap

Missing and partial core items, heaviest first, up to `ROADMAP_MAX_ITEMS = 10`. Each item has:
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
- **Cold:** about 20 s on first use; most of it is preparing and encoding the target and its related occupations.

`test_latency.py` checks warm < 2 s.

### 12.7 Known limits

- **Hinglish / Indian-language text:** MiniLM is English-only; see §11.3.
- **Job-title lines are evidence too.** "Staff Nurse" meets "Direct or supervise less-skilled nursing personnel" at 0.66. A title says what someone was called, not what they did.
- **US-centric O*NET tasks.** Some tasks don't fit Indian practice (e.g. Registered Nurses "Prescribe or recommend drugs") and show up as gaps.
- **Long, generic O*NET tasks can miss clear evidence** ("Assemble, install, test, or maintain electrical wiring…" for the ITI electrician).
- **The verdict threshold** rests on 3 partial profiles and a 0.007 gap (§12.2).
- **The fixture client stands in for module 1's search and related lists:** documented Indian aliases plus title tokens, and related = same SOC major group. Live module 1 uses its alias table, 62k alternate titles and O*NET related occupations.
