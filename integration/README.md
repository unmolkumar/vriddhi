# Integration & Orchestration Layer

## Scope & Responsibility
This layer is responsible for end-to-end system orchestration:
- API Gateway / Orchestration Pipeline
- Assembling User Profile and passing it through M1 → M2 → M3
- Adapters to convert and normalize responses between modules
- End-to-end integration tests & final demo endpoints

## Phase Gate Notice
> **IMPORTANT: Work on this layer begins ONLY after Module 1, Module 2, and Module 3 have completed their independent development, passed their unit tests, and satisfied their contract checks.**

## Documentation & Rules
- Spec & Contracts: [INTEGRATION.md](../context/INTEGRATION.md)
- Development Rules: [AGENTS.md](../context/AGENTS.md)
- Git Workflow: [GIT_WORKFLOW.md](../GIT_WORKFLOW.md)

## Dedicated Git Branch
`integration/pipeline` (Cut from `main` after M1, M2, and M3 are merged)
