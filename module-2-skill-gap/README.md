# Module 2: Skill Gap Engine

## Scope & Responsibility
This module handles **Skill Extraction & Gap Analysis**:
- Resume & profile parsing (PDF / text)
- Skill taxonomy normalization (ESCO / O*NET / custom)
- Proficiency & evidence-weighted skill rating
- Gap calculation between user skills and target occupation requirements
- Learning recommendation paths

## Documentation & Rules
- Spec: [MODULE-2-SKILL-GAP.md](../context/MODULE-2-SKILL-GAP.md)
- Development Rules: [AGENTS.md](../context/AGENTS.md)
- Integration Contract: [INTEGRATION.md](../context/INTEGRATION.md)
- Git Workflow: [GIT_WORKFLOW.md](../GIT_WORKFLOW.md)

## Dedicated Git Branch
`feat/module-2-skill-gap`

## Input / Output Contract
Refer to `context/INTEGRATION.md` for JSON schema contracts.
Do not import private modules from `module-1` or `module-3`. Use mocks for tests.
