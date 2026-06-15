# ReleaseGuard Agent Rule Index

This file is the global index for the first ReleaseGuard release-rule knowledge base.

## Field Definitions

- `support_level`: `source-backed`, `releaseguard-default`, or `needs-source-mapping`.
- `blocking_policy`: `block`, `warn`, `info`, or `conditional`.
- `evidence_type`: the main evidence shape expected from a checker.
- `phase`: the intended implementation phase.

## Rules

| rule_id | rule_name | checker | source | support_level | priority | blocking_policy | evidence_type | phase |
|---|---|---|---|---|---|---|---|---|
| RG-CONFIG-001 | Root environment example file exists | EnvExampleChecker | The Twelve-Factor App - Config; ReleaseGuard default policy | releaseguard-default | high | conditional | file_exists | phase-1 |
| RG-CONFIG-002 | Suspected hardcoded configuration is detected | ConfigHardcodeChecker | The Twelve-Factor App - Config | source-backed | high | conditional | matched_lines | phase-1 |
| RG-DEPS-001 | Explicit dependency declaration exists | DependencyChecker | The Twelve-Factor App - Dependencies | source-backed | high | block | file_exists | phase-1 |
| RG-LOG-001 | Basic logging usage traces exist | LoggingChecker | The Twelve-Factor App - Logs; ReleaseGuard default policy | releaseguard-default | medium | warn | matched_lines | phase-1 |
| RG-DEPLOY-001 | Startup command or runtime entrypoint guidance exists | DeploymentChecker | The Twelve-Factor App - Processes; ReleaseGuard default policy | releaseguard-default | high | conditional | matched_lines | phase-1 |
| RG-PORT-001 | Web project has a service entrypoint or port-binding signal | WebEntryChecker | The Twelve-Factor App - Port binding | source-backed | medium | conditional | matched_lines | phase-1 |
| RG-TEST-001 | Root `tests/` directory exists | TestStructureChecker | pytest Good Integration Practices; ReleaseGuard default policy | releaseguard-default | high | warn | directory_exists | phase-1 |
| RG-TEST-002 | Pytest-discoverable test files exist | TestStructureChecker | pytest Good Integration Practices | source-backed | high | block | file_glob_matches | phase-1 |
| RG-TEST-003 | Pytest can collect at least one test item | PytestExecutionChecker | pytest Good Integration Practices | source-backed | high | block | collected_tests | phase-1 |
| RG-TEST-004 | Pytest collect-only command succeeds | PytestExecutionChecker | pytest Good Integration Practices | source-backed | high | block | command_result | phase-1 |
| RG-TEST-005 | Pytest run command succeeds | PytestExecutionChecker | pytest Good Integration Practices | source-backed | high | block | command_result | phase-1 |
| RG-TEST-006 | Pytest configuration exists | PytestConfigChecker | pytest Good Integration Practices; ReleaseGuard default policy | releaseguard-default | medium | warn | file_exists | phase-1 |
| RG-TEST-007 | `src` layout import behavior is reproducible | PytestConfigChecker | pytest Good Integration Practices | source-backed | medium | conditional | config_value | phase-1 |
| RG-FASTAPI-001 | FastAPI dependency is declared when FastAPI is used | FastAPIDetector | FastAPI Testing; ReleaseGuard dependency policy | releaseguard-default | high | block | dependency_line | phase-1 |
| RG-FASTAPI-002 | FastAPI app instance is detectable | FastAPIDetector | FastAPI Testing | source-backed | high | block | matched_lines | phase-1 |
| RG-FASTAPI-003 | FastAPI `TestClient` is used | FastAPITestChecker | FastAPI Testing | source-backed | medium | warn | matched_lines | phase-1 |
| RG-FASTAPI-004 | Tests are tied to the FastAPI app | FastAPITestChecker | FastAPI Testing | source-backed | high | warn | matched_lines | phase-1 |
| RG-FASTAPI-005 | FastAPI tests can run through pytest | FastAPITestChecker | FastAPI Testing | source-backed | high | block | command_result | phase-1 |
| RG-FASTAPI-006 | FastAPI tests assert response status codes | FastAPITestChecker | FastAPI Testing | source-backed | medium | warn | matched_lines | phase-1 |
| RG-FASTAPI-007 | Health-check route exists | FastAPIHealthChecker | ReleaseGuard default policy; stronger operations source needed | releaseguard-default | medium | warn | route_match | phase-1 |
| RG-DOCKER-001 | Dockerfile exists when containerized release is expected | DockerChecker | Dockerfile reference; ReleaseGuard container policy | releaseguard-default | high | conditional | file_exists | phase-1 |
| RG-DOCKER-002 | Dockerfile contains `FROM` | DockerChecker | Dockerfile reference | source-backed | high | block | docker_instruction | phase-1 |
| RG-DOCKER-003 | `FROM` appears in a valid position | DockerChecker | Dockerfile reference | source-backed | high | block | docker_instruction_order | phase-1 |
| RG-DOCKER-004 | Dockerfile contains `WORKDIR` | DockerChecker | Dockerfile reference; ReleaseGuard default policy | releaseguard-default | medium | warn | docker_instruction | phase-1 |
| RG-DOCKER-005 | Dockerfile contains `COPY` or `ADD` | DockerChecker | Dockerfile reference; ReleaseGuard default policy | releaseguard-default | medium | warn | docker_instruction | phase-1 |
| RG-DOCKER-006 | Dockerfile contains dependency installation steps | DockerChecker | Dockerfile reference; ReleaseGuard Python image policy | releaseguard-default | medium | warn | docker_instruction | phase-1 |
| RG-DOCKER-007 | Dockerfile contains `CMD` or `ENTRYPOINT` | DockerChecker | Dockerfile reference; ReleaseGuard runnable image policy | releaseguard-default | high | conditional | docker_instruction | phase-1 |
| RG-DOCKER-008 | Dockerfile instruction casing follows uppercase convention | DockerStyleChecker | Dockerfile reference | source-backed | low | info | docker_instruction_style | phase-1 |
| RG-SEC-001 | Suspected hardcoded sensitive values are detected | SecurityBaselineChecker | OWASP ASVS general basis; exact mapping needed | needs-source-mapping | high | block | redacted_matched_lines | phase-1 |
| RG-SEC-002 | Flask debug mode is not enabled for release | SecurityBaselineChecker | ReleaseGuard security baseline; Flask source needed | needs-source-mapping | high | block | matched_lines | phase-1 |
| RG-SEC-003 | Overly broad CORS configuration is detected | SecurityBaselineChecker | OWASP ASVS general basis; exact mapping needed | needs-source-mapping | high | conditional | matched_lines | phase-1 |
| RG-SEC-004 | Risky command execution patterns are detected | SecurityBaselineChecker | OWASP ASVS command-injection reference | source-backed | medium | conditional | matched_lines | phase-1 |
| RG-SEC-005 | Production security configuration guidance exists | SecurityBaselineChecker | ReleaseGuard default policy | releaseguard-default | medium | warn | doc_section_match | phase-1 |
| RG-SEC-006 | Security report records ASVS version and requirement IDs | SecurityReportChecker | OWASP ASVS | source-backed | medium | info | rule_metadata | phase-2 |

## Recommended Implementation Order

1. Already implemented: `RG-DEPS-001`, `RG-CONFIG-001`, and `RG-TEST-001` through `RG-TEST-005`.
2. Good next slice: `RG-TEST-006` and `RG-TEST-007`, because the project already has pytest configuration context.
3. Good web-framework slice: `RG-FASTAPI-001` and `RG-FASTAPI-002`.
4. Good container slice: `RG-DOCKER-001` through `RG-DOCKER-003`.
5. Security baseline should wait until evidence redaction and source mapping are designed carefully.

## Report Language Guardrails

- Do not claim full ASVS audit coverage.
- Do not claim `.env.example` is an official Twelve-Factor requirement.
- Do not claim `/health` is an official FastAPI requirement.
- Do not display full secret values in evidence.
- Label ReleaseGuard policies clearly when they are not direct official requirements.
