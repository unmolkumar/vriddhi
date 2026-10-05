# AGENTS.md — Development Rules

## Read This First

This file defines the operating rules for all coding agents working on this repository.

The project has **three independent modules**.

```text
Agent 1 → Module 1
Agent 2 → Module 2
Agent 3 → Module 3
```

There must never be two agents implementing the same module.

---

# 1. Module Ownership

## Agent 1

Owns:

```text
module-1-career-intelligence/
```

Context:

```text
MODULE-1-CAREER-INTELLIGENCE.md
```

---

## Agent 2

Owns:

```text
module-2-skill-gap/
```

Context:

```text
MODULE-2-SKILL-GAP.md
```

---

## Agent 3

Owns:

```text
module-3-job-matching-salary/
```

Context:

```text
MODULE-3-JOB-MATCHING-SALARY.md
```

---

# 2. Absolute Isolation Rule

An agent must not implement another module.

Do NOT:

```text
Agent 1 → edit Module 2
Agent 2 → edit Module 3
Agent 3 → edit Module 1
```

Do not make cross-module code changes to "help integration".

If integration is blocked:

```text
Do not modify the other module.
Document the contract problem.
Use the integration layer.
```

---

# 3. Integration Is Separate

Integration belongs to:

```text
integration/
```

Integration context:

```text
INTEGRATION.md
```

The integration process may call the modules through:

- APIs
- adapters
- shared contracts
- explicitly agreed schemas

Do not directly import another module's private implementation.

---

# 4. Before Coding

Every agent must:

1. Read this file.
2. Read their assigned module context.
3. Inspect their assigned directory.
4. Understand the input/output contract.
5. Check existing tests.
6. Check existing schemas before creating duplicates.

Do not immediately start rewriting architecture.

---

# 5. Scope Discipline

If your task is:

```text
Skill extraction
```

do not also redesign:

```text
Career forecasting
Job matching
Salary prediction
```

Keep changes focused.

---

# 6. Shared Files

The following are sensitive:

```text
context/*
shared/*
integration/*
```

Do not casually modify them.

If a shared contract must change:

```text
1. Identify why.
2. Document the proposed change.
3. Check affected modules.
4. Update contract.
5. Update your module.
6. Notify integration.
```

---

# 7. No Hidden Coupling

Bad:

```python
from module_2.some_private_file import SkillExtractor
```

Good:

```text
Module 2 exposes a stable API.
Integration consumes the API.
```

Modules must be replaceable.

---

# 8. Git Branching Strategy & Workflow

### The 3-Branch Architecture

**Yes, each module MUST be developed on its own dedicated feature branch.**
Working directly on `main` is strictly forbidden.

```text
main (stable, production-ready, verified integration)
 ├── feat/module-1-career-intelligence   (Agent 1 / Member 1)
 ├── feat/module-2-skill-gap             (Agent 2 / Member 2)
 ├── feat/module-3-job-matching-salary   (Agent 3 / Member 3)
 └── integration/pipeline                (Integration Agent - created ONLY after modules complete)
```

### Branch Naming Conventions

All agents and contributors must follow this naming convention:

| Scope | Branch Name | Assigned To | Target Merge |
|---|---|---|---|
| Module 1 | `feat/module-1-career-intelligence` | Agent 1 / Member 1 | `main` |
| Module 2 | `feat/module-2-skill-gap` | Agent 2 / Member 2 | `main` |
| Module 3 | `feat/module-3-job-matching-salary` | Agent 3 / Member 3 | `main` |
| Integration | `integration/pipeline` | Integration Team | `main` |
| Hotfix | `fix/module-<X>-<short-description>` | Assigned Agent | Feature Branch or `main` |

### Step-by-Step Workflow for Module Agents

1. **Checkout your designated branch:**
   ```bash
   # Make sure you have latest main
   git checkout main
   git pull origin main

   # Switch to your module branch
   git checkout feat/module-1-career-intelligence
   # (or feat/module-2-skill-gap / feat/module-3-job-matching-salary)
   ```

2. **Work ONLY within your module directory:**
   - Agent 1: `module-1-career-intelligence/`
   - Agent 2: `module-2-skill-gap/`
   - Agent 3: `module-3-job-matching-salary/`

3. **Stage only your changes:**
   ```bash
   git add module-1-career-intelligence/
   # NEVER use `git add .` blindly without checking `git status`
   ```

4. **Verify status before committing:**
   ```bash
   git status
   ```
   Ensure no untracked secrets, `.env` files, temporary artifacts, or peer module files are staged.

5. **Push to remote branch:**
   ```bash
   git push -u origin feat/module-1-career-intelligence
   ```

---

# 9. Git Commit Standards & Hygiene

Every agent and developer must use the **Conventional Commits** specification.
Commits must be **atomic** (one logical change per commit) with clear, descriptive intent.

### Commit Format

```text
<type>(<scope>): <short imperative summary>

[optional body: explain WHAT changed and WHY, not HOW]

[optional footer: references to issues, breaking changes]
```

### Allowed Types

- `feat`: A new feature or capability for the module
- `fix`: A bug fix
- `test`: Adding or updating test suites
- `docs`: Documentation updates (README, markdown guides)
- `refactor`: Code restructuring without changing external API behavior
- `perf`: Code change that improves execution speed or memory usage
- `chore`: Maintenance tasks, dependencies, setup scripts

### Standard Scopes

Use the exact module or domain scope:
- `(module-1)` or `(career)`
- `(module-2)` or `(skill-gap)`
- `(module-3)` or `(jobs)`
- `(integration)`
- `(contracts)`
- `(docs)`

### Examples of Professional Commits

✅ **Good Examples:**
```text
feat(module-1): implement ONET occupation demand forecast extractor
feat(module-2): add resume pdf parser and skill entity extraction
feat(module-3): implement job ranking cosine similarity algorithm
fix(module-3): handle missing salary range fallback to regional median
test(module-2): add unit tests for skill taxonomy normalizer
docs(module-1): document input output schema with example payload
refactor(module-2): decouple skill taxonomy cache from memory store
```

❌ **Prohibited Commits (Never Do This):**
```text
update
changes
wip
fixed stuff
final commit
hackathon code
done
asdf
```

### Commit Hygiene Rules

1. **Imperative Mood**: Use imperative verbs ("add", "fix", "implement", "refactor") instead of past tense ("added", "fixed").
2. **First Line Length**: Keep the header line under 72 characters (ideally under 50).
3. **No Unfinished Code on Push**: Ensure tests run or at minimum no syntax errors before pushing.
4. **Never Rewrite Shared History**: Never use `git push --force` on `main`.

---

# 10. Testing Rules

Every meaningful feature must have tests.

Before committing:

```text
Run unit tests
Run linting if configured
Run formatting if configured
Check API output
```

Do not commit known failing tests unless the failure is explicitly documented.

---

# 11. Data Rules

External data must be reproducible.

Record:

```text
Source
URL
Access date
License
Fields used
Cleaning
Transformations
Limitations
```

Never silently change the source data format.

---

# 12. AI / ML Rules

Predictions must not be represented as guaranteed facts.

Use:

```text
Prediction
Estimate
Confidence
Probability
Outlook
```

Avoid unsupported statements such as:

```text
"This job will disappear in 5 years."
```

Prefer:

```text
"This occupation has a high estimated transformation exposure."
```

---

# 13. Salary Rules

Salary predictions are estimates.

Do not claim:

```text
"You are definitely worth ₹15 LPA."
```

Prefer:

```text
"Estimated market range: ₹13–15 LPA."
```

Show confidence where possible.

---

# 14. Resume / Skill Rules

Do not assume:

```text
Skill mentioned = Expert
```

Use evidence when determining skill level.

Distinguish:

```text
Self-reported
Resume-supported
Project-supported
Work-experience-supported
```

---

# 15. Integration Readiness

A module is integration-ready when:

```text
Core functionality works
        +
Tests pass
        +
Input contract implemented
        +
Output contract implemented
        +
Errors handled
        +
README exists
        +
No cross-module internal dependency
```

---

# 16. When Blocked

If your module depends on another module:

Do NOT edit that module.

Instead:

```text
1. Identify required input.
2. Define the required contract.
3. Document it.
4. Continue using mock/stub data.
5. Integration connects the real module later.
```

This is especially important during parallel development.

---

# 17. Mock Data

Agents may create mocks inside their own module.

Example:

```text
module-3-job-matching-salary/tests/mocks/
```

Do not modify another module to provide test data.

---

# 18. Strict Integration Phase Gate (Only After Full Module Completion)

### The Golden Rule of Integration Timing

> **NO integration work may begin until Module 1, Module 2, AND Module 3 are independently complete, tested, and verified.**

Premature integration during active module development causes merge conflicts, broken contracts, dependency chaos, and blocked teammates.

```text
Phase 1: Isolated Module Development
  │
  ├── Agent 1 on feat/module-1-career-intelligence  ──► Completes M1 + passes unit tests
  ├── Agent 2 on feat/module-2-skill-gap            ──► Completes M2 + passes unit tests
  └── Agent 3 on feat/module-3-job-matching-salary  ──► Completes M3 + passes unit tests
  │
  ▼
Phase 2: Module Readiness Verification Gate (MANDATORY CHECKPOINT)
  │  [✓] All 3 modules pass the Readiness Checklist below
  │  [✓] All 3 feature branches merged into `main`
  │
  ▼
Phase 3: Integration & Orchestration Phase (ONLY NOW)
  │
  ├── Cut branch: integration/pipeline
  ├── Work strictly within integration/
  ├── Connect modules via API / Adapters defined in INTEGRATION.md
  ├── Write end-to-end pipeline tests
  └── Merge integration/pipeline into main
```

### Module Readiness Checklist (Gate Criteria)

Before any module branch is merged into `main` or used for integration, the module must satisfy:

1. **Self-Contained Execution**: The module runs independently with its own entrypoint or API service.
2. **Contract Compliance**: Strictly conforms to the JSON schemas defined in `INTEGRATION.md` for both inputs and outputs.
3. **100% Passing Tests**: Contains comprehensive unit and integration tests with mocks (no failing or skipped tests).
4. **Error Handling**: Gracefully handles invalid inputs, missing fields, or external service timeouts without crashing the process.
5. **Zero Cross-Module Imports**: Does NOT import or reference files from peer module directories.
6. **Documentation**: Contains a `README.md` in its own folder explaining:
   - How to install dependencies
   - How to run tests
   - Example input payload and expected output payload
7. **Clean Git History**: Commits follow Conventional Commits format (`feat(...)`, `test(...)`, `fix(...)`).

### Rules for the Integration Team / Agent

1. **Work in `integration/` Only**: The integration layer lives strictly in `integration/`.
2. **Read-Only Peer Modules**: Never edit code inside `module-1-*`, `module-2-*`, or `module-3-*` during integration.
3. **Contract Discrepancies**: If a module's output doesn't match `INTEGRATION.md`, file a contract bug for that module owner. Do NOT patch the module yourself.
4. **Adapter Pattern**: Use adapters and orchestrators in `integration/` to reconcile minor differences, transform schemas, and chain requests cleanly.

---

# 19. Golden Rule

> **Build independently. Communicate through contracts. Integrate separately.**

The goal is to make it possible to remove or replace any one module without rewriting the other two.

