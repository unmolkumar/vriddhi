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

**285 passed, 0 failed, 0 skipped** (~1.5–3.5 min; OCR and MiniLM dominate). The general engine (v2, §11) adds 48 tests; the v1 tests above are unchanged. The live Groq test (`test_llm_live.py`) runs only when `GROQ_API_KEY` is set and is skipped otherwise.

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

## 11. General engine (v2) — A1

**Status:** A1 only (requirements, evidence, matching, calibration). There is no public endpoint yet; scoring, verdicts, roadmaps and `/api/v2/*` are A2. Everything in §1–10 (v1, `/api/v1/*`, the 483-skill taxonomy, the gap analyzer) is unchanged; the new code lives in `src/general/` and nothing in v1 imports it.

### 11.1 Design

```
module 1 (REST)                      resume / free text / typed skills / v1 profile
  /occupations/search ─► Resolution            │
  /occupations/{soc}/requirements              ▼
          │                            evidence.py: EvidenceUnit(text, evidence_type, section, span, skill_ids)
          ▼                                    │
  requirements.py: filter + weight             │
  RequirementItem(layer, weight, flags)        │
          │                                    │
          └──────────► matcher.py ◄────────────┘
                 1. alias: same taxonomy skill  → met
                 2. semantic: MiniLM cosine     → met / partial / missing (per item type)
                 → RequirementMatch(status, similarity, reason, best evidence + span)
```

- **`m1_client.py`.** Calls module 1's four occupation endpoints over REST (`M1_BASE_URL`, default `http://localhost:8001`, 10 s timeout). Failures become `M1Error(code)`: `unreachable`, `timeout`, `not_found` or `bad_response`.
  - Requirements, profiles and related lists are cached on disk under `data/cache/m1/<version>/`. Module 1 responses carry no `db_meta` yet, so the version is the API version from `/openapi.json`. If the version is unknown, nothing is cached.
  - Search returns a `Resolution` with `low_confidence` (top match below `LOW_CONFIDENCE = 0.85`, or two different occupation families within `AMBIGUOUS_GAP = 0.05`) and `did_you_mean`.
  - `FixtureM1Client` has the same interface over `tests/mocks/m1_occupation_requirements_export.json` (module 1 v2.0.0, 15 occupations).
- **`requirements.py`.** Turns module 1 rows into `RequirementItem(soc, item_type, item_id, name, description, importance, level, source, reliable, layer, weight, flags)`.
  - **Layers:** core (`market_skill`, `tech`, `tool`, `knowledge`, `task`, `dwa`), transferable (`skill`, `work_activity`), fit_indicator (`ability`: never something to learn).
  - **Weight:** `importance_norm × LAYER_WEIGHT[layer] × reliability`, with `LAYER_WEIGHT` = core 1.0, transferable 0.5, fit_indicator 0.
  - **Required level:** `level_norm`, or `DEFAULT_LEVEL[type]` when it is null (0.6 for tasks and DWAs, 0.5 otherwise).
  - **Embedded text:** `"{name}: {description}"`, or just the name when the description repeats it or is module 1's generic "Extracted from Indian job postings".
- **`evidence.py`.** Splits resume or free text into units, using v1's `section_segmenter`.
  - Experience bullets become `work`, project bullets `project`, and everything else (summary, skills items, education) `mentioned`. Typed skills become `self`.
  - Bullets are split into sentences, and skills sections into items (a "Software:" style label is stripped). Contact lines are dropped.
  - Each unit keeps its character `span` in the source text and the taxonomy ids v1's dictionary finds in it.
  - `from_resume` (v1 parser) and `from_profile` (a v1 `UserProfile`) are also supported.
- **`embeddings.py`.** `all-MiniLM-L6-v2` by default (`EMBEDDING_MODEL` to change), CPU only, normalised vectors.
  - Requirement vectors are cached as float16 `.npy` under `data/cache/embeddings/<model>/<soc>-<hash of the item texts>.npy` (gitignored), so an edited requirement list is re-encoded.
- **`matcher.py`.** Each requirement is matched in two steps:
  1. **Alias.** A tech, tool or market_skill requirement that the v1 taxonomy resolves to the same skill as a unit is `met`, with `reason: "alias"` ("Advanced Excel" meets "Microsoft Excel").
  2. **Semantic.** Otherwise, the best cosine over all units is compared with that type's `(met, partial)` thresholds.

  Every match returns its status, similarity, reason, and the deciding unit's text, type, section and span. `coverage()` is a **provisional** 0–1 score for calibration only. Within each item type, it is the weight-averaged credit (met 1, partial 0.5). The types are then combined by `TYPE_SHARE`, so an occupation with 474 tech rows isn't scored on tech alone. A2 replaces this score.

### 11.2 Filters (module 1 v2.0 data issues)

Each filter is a named constant. A drop is recorded in the per-occupation `FilterReport` (reason → names, and logged); a down-weight is recorded in the item's `flags`. Every filter is a no-op on clean data (test `test_clean_data_passes_through_untouched`).

| Filter | Constant(s) | Applies to | Effect |
|---|---|---|---|
| duplicate | normalised (type, name) | all | drop; keep the higher-importance copy. Module 1 v2.0 exports **every tech row twice** (e.g. 237 of 474 for Accountants) |
| unreliable | `reliable = 0` | all | drop |
| low support | `MARKET_MIN_SHARE = 0.02`, `MARKET_MIN_POSTINGS = 3` (when a `posting_count` is sent) | market_skill | drop |
| noise | `INDUSTRY_LABELS` (+ "IT Software - …" pattern), `INDIAN_PLACES`, `SENIORITY_WORDS`, `GENERIC_TITLES`, `BENEFITS`, `GENERIC_TERMS`, the occupation's own title and aliases; case-insensitive | market_skill | drop, with the list as the reason |
| cap | `MARKET_CAP = 50` by share | market_skill | drop the tail |
| off-domain | `OFF_DOMAIN_SIM = 0.18`, `OFF_DOMAIN_FACTOR = 0.3` | tech, tool | weight × 0.3, flag `off_domain(sim)`; never deleted |

**Off-domain, measured.** The brief suggested comparing tech with the occupation's description and tasks. On the fixture, that let Epic Systems through for Accountants (0.34) and Apache Spark for Registered Nurses (0.28), while flagging Excel for Accountants. Comparing with the **title and module 1's domain labels** (major group, career cluster, Indian industry; `domain_texts()`) separates them cleanly:

| Tech | Occupation | Description + tasks | Title + domain labels |
|---|---|---|---|
| Apache Spark | Registered Nurses | 0.28 | 0.11 (off) |
| Epic Systems | Registered Nurses | 0.35 | 0.42 |
| Epic Systems | Accountants | 0.34 | 0.12 (off) |
| Intuit QuickBooks | Accountants | 0.63 | 0.44 |
| Apache Subversion | Civil Engineers | 0.15 | 0.01 (off) |
| Bentley MicroStation | Civil Engineers | 0.52 | 0.42 |

General office software (Word, Outlook) also falls below 0.18 for non-office occupations and is down-weighted. That's acceptable, because it says nothing about the occupation.

**Per-occupation result:**

| Occupation | Rows | Kept | Dropped | Down-weighted |
|---|---|---|---|---|
| Registered Nurses | 280 | 220 | duplicate 43, unreliable 16, industry label 1 (Medical) | off-domain 10 |
| Accountants and Auditors | 674 | 399 | duplicate 237, unreliable 37, industry label 1 (Hotels) | off-domain 39 (Epic, MEDITECH, SPSS, Word, …) |
| Customer Service Reps | 417 | 256 | duplicate 112, unreliable 37, low support 11, industry label 1 (ITES) | off-domain 17 |
| Mechanical Engineers | 418 | 266 | duplicate 92, low support 38, unreliable 21, industry label 1 (IT Hardware) | off-domain 31 |
| Civil Engineers | 388 | 236 | duplicate 73, low support 55 (incl. Ahmedabad, BE, Basic, C++), unreliable 24 | off-domain 23 (SVN, Office, …) |
| Electricians | 247 | 206 | duplicate 30, unreliable 10, industry label 1 (IT Hardware) | off-domain 14 |
| Data Scientists | 1,603 | 234 | low support 1,245, duplicate 87, unreliable 37 | — |

The full report for all 15 occupations is in `tests/calibration/results/all-MiniLM-L6-v2.json`.

**Still noisy after the filters:**
- Customer Service keeps HTML and Javascript (share 0.034, about 2 postings).
- Graphic Designers keep "Design" (0.68, effectively the industry label).
- Data Scientists keep only 7 market skills (ML, PYTHON, SQL, TensorFlow, …). Module 1 sends no posting counts, so the 0.02 share floor drops PyTorch (0.014) and NLP along with the noise. A `posting_count` field would let `MARKET_MIN_POSTINGS` keep them.

### 11.3 Calibration

`python scripts/calibrate.py [--tune] [--model …] [--grid-shift …]` scores every profile in `tests/calibration/profiles/` against every fixture occupation.
- **Profiles:** 15, one per occupation, plus 3 partial ones (a GNM fresher nurse, an accounts assistant without GST or Tally, and an ITI apprentice). They are written in Indian context and paraphrased, not copied from O*NET. Two (customer care, truck driver) are free text rather than resume-shaped.
- **Report:** top-1 and top-3 accuracy, the margin (own occupation minus the best other), per-type similarity of true vs false matches, filter reports, and the nurse sample.
- **`--tune`:** a grid search over three threshold groups (name-like, task-like, generic), the partial gap, and five type-share presets. It maximises (top-1, top-3, worst margin, mean margin).

**Results (15 full profiles):**

| Setting | Top-1 | Top-3 | Mean margin | Worst margin |
|---|---|---|---|---|
| MiniLM, initial hand-set constants | 15/15 | 15/15 | +0.211 | +0.026 (Mechanical vs Graphic Designers) |
| **MiniLM, tuned (shipped)** | **15/15** | **15/15** | **+0.283** | **+0.103** (Staff nurse vs Medical Assistants) |
| bge-small-en-v1.5, MiniLM's thresholds | 11/15 | 15/15 | +0.016 | −0.016 |
| bge-small-en-v1.5, its own tuning (grid +0.15) | 15/15 | 15/15 | +0.241 | +0.061 |

MiniLM stays the default: it has a better worst margin and is smaller, and v1 already uses it. bge-small works on a compressed cosine scale (almost everything falls between 0.55 and 0.80), so it needs its own thresholds.

**Partial profiles** still rank their own occupation first, with lower coverage than the full profile:

| Partial profile | Own coverage | Full profile's coverage |
|---|---|---|
| Fresher nurse | 0.26 | 0.41 |
| Accounts assistant without GST/Tally | 0.22 | 0.57 |
| ITI apprentice | 0.33 | 0.39 |

**Shipped constants (`matcher.py`):**

| Item types | met | partial | TYPE_SHARE |
|---|---|---|---|
| tech, tool | 0.65 | 0.52 | 0.05 each |
| market_skill | 0.65 | 0.52 | 0.30 |
| task, dwa | 0.55 | 0.42 | 0.30, 0.10 |
| knowledge | 0.50 | 0.37 | 0.10 |
| skill, work_activity | 0.50 | 0.37 | 0.04 each |
| ability | 0.50 | 0.37 | 0 (fit indicator) |

**Best-evidence similarity, own occupation vs others (MiniLM, median):**

| Item type | Own occupation | Others |
|---|---|---|
| market_skill | 0.65 | 0.26 |
| task | 0.50 | 0.27 |
| tech | 0.34 | 0.24 |
| knowledge | 0.21 | 0.20 |
| skill | 0.25 | 0.25 |
| work_activity | 0.25 | 0.25 |
| ability | 0.16 | 0.16 |

Tasks and market skills carry the signal. The generic O*NET layers (skills, work activities, abilities, and mostly knowledge) are the **same 35/41/52/33 items for every occupation**, and resume text doesn't separate them semantically. A2 should infer them from the matched tasks and work history, not from text similarity. Tech names rarely appear in Indian resumes in O*NET's wording (1.7% met for the right occupation), so tech relies on the alias layer.

**Overfitting caveat:** the thresholds and shares were tuned on the same 15 profiles they are tested on. The calibration tests (`tests/calibration/test_calibration.py`) require top-1 ≥ 13/15 and top-3 = 15/15, plus sanity checks:
- the nurse profile scores Registered Nurses at least 0.10 above Electricians;
- the accountant profile ranks Accountants in the top 2;
- the data scientist profile ranks Data Scientists first;
- each partial profile scores below its full profile.

They need held-out profiles before the numbers are trusted beyond this set.

**Sample, staff nurse vs Registered Nurses** (core requirements, heaviest first):
- Met:
  - "Administer medications…" ← "medication administration" (0.65)
  - "Monitor, record, and report symptoms…" ← "Admitted patients, took history and assessed their condition" (0.64)
  - "Consult and coordinate with healthcare team members…" ← "Prepare nursing care plans with the intensivist…" (0.56)
  - "Direct or coordinate infection control programs…" ← "infection control" (0.71)
- Partial:
  - "Record patients' medical information and vital signs" (0.53)
  - "Provide health care, first aid, immunizations…" (0.49)
- Missing:
  - "Maintain accurate, detailed reports and records" (0.38, against "clinical documentation")
  - "Prescribe or recommend drugs…" (0.40)
  - Knowledge "Psychology"
  - All tech (Epic 0.42, MEDITECH 0.50): the profile names only "hospital EMR"

### 11.4 Tests

| File | Tests | Covers |
|---|---|---|
| `test_general_m1_client.py` | 8 | cache per module 1 version (and none when unknown), timeout/unreachable/404/500 as `M1Error`, search confidence, low confidence and ties, fixture twin |
| `test_general_requirements.py` | 26 | layers, weights, default levels, dwa/tool, embedding text, duplicate, unreliable, support floor and posting count, each noise list, market cap, off-domain down-weight, domain texts, clean data untouched, the Accountants fixture report |
| `test_general_evidence.py` | 4 | sections → work/project/mentioned, spans, sentence and skills-item splitting, contact lines dropped, free text, typed skills, v1 profile |
| `test_general_matcher.py` | 5 | alias before semantics, best evidence and span, per-type thresholds, no evidence, provisional coverage |
| `calibration/test_calibration.py` | 5 | top-1 ≥ 13/15 and top-3, nurse vs Electricians margin, Accountants top-2, Data Scientists top-1, partial < full (MiniLM; skipped if it can't load) |

The v1 suite is untouched: **284 passed + 1 skipped (live Groq without a key) = 285**. With the general engine, the total is **332 passed, 1 skipped**.
