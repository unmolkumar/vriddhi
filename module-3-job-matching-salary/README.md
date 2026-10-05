# Module 3: Job Matching & Salary Intelligence

## Scope & Responsibility
This module handles **Job Matching & Salary Estimation**:
- Live job posting parsing & index
- Multi-factor job matching (skills match score, experience match, location preference)
- Salary prediction & market range estimation with confidence scores
- Company / role tier adjustments

## Documentation & Rules
- Spec: [MODULE-3-JOB-MATCHING-SALARY.md](../context/MODULE-3-JOB-MATCHING-SALARY.md)
- Development Rules: [AGENTS.md](../context/AGENTS.md)
- Integration Contract: [INTEGRATION.md](../context/INTEGRATION.md)
- Git Workflow: [GIT_WORKFLOW.md](../GIT_WORKFLOW.md)

## Dedicated Git Branch
`feat/module-3-job-matching-salary`

## Input / Output Contract
Refer to `context/INTEGRATION.md` for JSON schema contracts.
Do not import private modules from `module-1` or `module-2`. Use mocks for tests.
