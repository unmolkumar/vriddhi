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

# 8. Git Rules

Never work directly on `main`.

Use a module-specific branch.

Example:

```bash
git checkout -b member1/module-1
```

or:

```bash
git checkout -b member2/module-2
```

or:

```bash
git checkout -b member3/module-3
```

---

# 9. Commit Rules

Use descriptive commits.

Good:

```text
feat(career): add demand trend analysis
feat(skill-gap): add resume skill extraction
feat(jobs): add job ranking
test(skill-gap): add gap calculation tests
fix(jobs): handle missing salary
```

Avoid:

```text
final
changes
update
stuff
working
test
```

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

# 18. Final Integration

Integration happens only after the individual modules have reached a usable state.

```text
Module 1 complete
       │
Module 2 complete
       │
Module 3 complete
       │
       ▼
Integration
       │
       ▼
End-to-End Testing
       │
       ▼
Final Demo
```

---

# 19. Golden Rule

> **Build independently. Communicate through contracts. Integrate separately.**

The goal is to make it possible to remove or replace any one module without rewriting the other two.
