# Git Workflow & Collaboration Guide

This document establishes the official Git branching model, commit conventions, and phase-gate rules for the **Vriddhi Career Intelligence Platform**.

---

## 1. Branching Strategy

The repository follows a strict **Feature-Branch Architecture** to ensure that independent modules never interfere with one another or break `main`.

```text
main (Production-ready / Stable Integration)
 ├── feat/module-1-career-intelligence   ──► Owned by Agent 1 / Member 1
 ├── feat/module-2-skill-gap             ──► Owned by Agent 2 / Member 2
 ├── feat/module-3-job-matching-salary   ──► Owned by Agent 3 / Member 3
 └── integration/pipeline                ──► Created ONLY after M1, M2, M3 are complete
```

### Active Branches

| Branch | Module / Role | Working Directory | Context Guide |
|---|---|---|---|
| `main` | Production & Integration releases | Whole repo (Protected) | [README.md](file:///c:/Users/anmol/stuff/projects/vriddhi/README.md) |
| `feat/module-1-career-intelligence` | Career Intelligence Engine | `module-1-career-intelligence/` | [MODULE-1-CAREER-INTELLIGENCE.md](file:///c:/Users/anmol/stuff/projects/vriddhi/context/MODULE-1-CAREER-INTELLIGENCE.md) |
| `feat/module-2-skill-gap` | Skill Gap & Taxonomy Engine | `module-2-skill-gap/` | [MODULE-2-SKILL-GAP.md](file:///c:/Users/anmol/stuff/projects/vriddhi/context/MODULE-2-SKILL-GAP.md) |
| `feat/module-3-job-matching-salary` | Job Matching & Salary Intelligence | `module-3-job-matching-salary/` | [MODULE-3-JOB-MATCHING-SALARY.md](file:///c:/Users/anmol/stuff/projects/vriddhi/context/MODULE-3-JOB-MATCHING-SALARY.md) |
| `integration/pipeline` | System Orchestration & API Gateway | `integration/` | [INTEGRATION.md](file:///c:/Users/anmol/stuff/projects/vriddhi/context/INTEGRATION.md) |

### Branch Rules
1. **Never commit directly to `main`**. All work must happen on dedicated branches.
2. **Directory Isolation**: Changes on a module branch must touch **only** that module's directory.
3. **No Cross-Module Edits**: Never touch files in another module's folder to "fix" an issue. Use mocks and interfaces.

---

## 2. Commit Message Standards (Conventional Commits)

All commits must follow the **Conventional Commits** standard to maintain an audit trail.

### Format
```text
<type>(<scope>): <short imperative subject>

[optional body: explain WHAT changed and WHY, not HOW]

[optional footer: issue references, breaking changes]
```

### Commit Types

- `feat`: A new feature or endpoint
- `fix`: A bug fix
- `test`: Adding or modifying unit / integration tests
- `docs`: Documentation changes
- `refactor`: Internal code improvement without contract or behavioral change
- `perf`: Performance enhancement
- `chore`: Build config, dependency updates, tooling

### Permitted Scopes
- `(module-1)` or `(career)`
- `(module-2)` or `(skill-gap)`
- `(module-3)` or `(jobs)`
- `(integration)`
- `(contracts)`
- `(docs)`

### Examples

```text
feat(module-1): implement ONET occupation demand forecast extractor
feat(module-2): add resume pdf parser and skill entity extraction
feat(module-3): implement job ranking cosine similarity algorithm
fix(module-3): handle missing salary range fallback to regional median
test(module-2): add unit tests for skill taxonomy normalizer
docs(module-1): document input output schema with example payload
refactor(module-2): decouple skill taxonomy cache from memory store
```

---

## 3. Strict Phase Gates: When Does Integration Happen?

> **Rule: Integration happens ONLY after all 3 modules are independently complete, tested, and passing.**

```text
┌────────────────────────────────────────────────────────┐
│ Phase 1: Parallel Isolated Module Development          │
│ - Agent 1: module-1-career-intelligence/               │
│ - Agent 2: module-2-skill-gap/                         │
│ - Agent 3: module-3-job-matching-salary/               │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ Phase 2: Readiness Verification Gate                   │
│ [✓] 100% unit tests pass                               │
│ [✓] Inputs/Outputs conform to INTEGRATION.md schemas   │
│ [✓] Standalone module README + mocks present           │
│ [✓] Zero cross-module private imports                  │
│ [✓] Feature branches merged into `main`                │
└──────────────────────────┬─────────────────────────────┘
                           │
                           ▼
┌────────────────────────────────────────────────────────┐
│ Phase 3: Integration & System Orchestration            │
│ - Cut branch: integration/pipeline                     │
│ - Work strictly inside: integration/                   │
│ - Wire APIs/adapters to M1, M2, M3                     │
│ - End-to-end integration tests & demo script           │
│ - Merge integration/pipeline into `main`               │
└────────────────────────────────────────────────────────┘
```

---

## 4. Daily Commands Cheat Sheet

### Starting Work on a Module
```bash
# Fetch latest main
git checkout main
git pull origin main

# Switch to your module branch
git checkout feat/module-1-career-intelligence
# (or feat/module-2-skill-gap / feat/module-3-job-matching-salary)
```

### Staging and Committing Work
```bash
# Check modified files
git status

# Stage only your module's files
git add module-1-career-intelligence/

# Commit with conventional commits message
git commit -m "feat(module-1): add occupation trend forecasting model"

# Push to your feature branch
git push origin feat/module-1-career-intelligence
```

### If You Need Data from Another Module Before Integration
Do **not** edit their code or wait blocked:
1. Refer to [INTEGRATION.md](file:///c:/Users/anmol/stuff/projects/vriddhi/context/INTEGRATION.md) for expected schema.
2. Put mock JSON data in your module's `tests/mocks/` directory.
3. Test your module against the mock data.
