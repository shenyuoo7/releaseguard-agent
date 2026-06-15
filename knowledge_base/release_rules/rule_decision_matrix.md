# Rule Decision Matrix

This file records ReleaseGuard's professional default decisions for phase-one rules.

## Config, Dependency, Logging, And Release

| rule_id | concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|---|
| RG-CONFIG-001 | `.env.example` | Require as ReleaseGuard's practical environment-variable documentation convention | releaseguard-default | conditional | Twelve-Factor supports environment-based config, but `.env.example` is ReleaseGuard's implementation convention. Block only when environment usage is detected. |
| RG-CONFIG-002 | Hardcoded configuration | Scan for suspicious config and secret-like values in code | source-backed | conditional | Twelve-Factor Config supports separating config from code. Secret-like evidence should be treated more severely and redacted. |
| RG-DEPS-001 | Dependency declaration | Require a recognized dependency declaration file for Python projects | source-backed | block | Twelve-Factor Dependencies supports explicit dependency declaration. |
| RG-LOG-001 | Logging traces | Warn when no basic logging usage is found | releaseguard-default | warn | Twelve-Factor supports logs as event streams; static logging detection is ReleaseGuard policy. |
| RG-DEPLOY-001 | Startup command | Require startup evidence for service projects; warn for generic projects | releaseguard-default | conditional | Release readiness requires knowing how to start the system, but exact evidence sources are project-specific. |
| RG-PORT-001 | Web entrypoint or port binding | Require clear entrypoint signals for web service projects | source-backed | conditional | Twelve-Factor Port binding applies to service apps, not all Python code. |

## Test

| rule_id | concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|---|
| RG-TEST-001 | `tests/` directory | Warn if missing | releaseguard-default | warn | pytest supports multiple layouts; ReleaseGuard prefers a visible test entrypoint. |
| RG-TEST-002 | pytest test file names | Block if no pytest-discoverable test files exist | source-backed | block | pytest documents `test_*.py` and `*_test.py` discovery. |
| RG-TEST-003 | collected test count | Block if pytest cannot collect at least one test item | source-backed | block | A release check with zero test items does not verify project behavior. |
| RG-TEST-004 | collect-only command | Block if `python -m pytest --collect-only -q` fails | source-backed | block | Collection failure indicates import, discovery, or configuration issues. |
| RG-TEST-005 | test execution command | Block if `python -m pytest -q` fails | source-backed | block | Failing tests should stop release. |
| RG-TEST-006 | pytest configuration | Warn if missing | releaseguard-default | warn | pytest supports configuration; ReleaseGuard uses it for reproducibility. |
| RG-TEST-007 | `src` layout import config | Conditional release impact | source-backed | conditional | pytest documents `pythonpath` and related import behavior for `src` layout. |

## FastAPI

| rule_id | concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|---|
| RG-FASTAPI-001 | FastAPI dependency declaration | Block if source uses FastAPI but dependency files do not declare it | releaseguard-default | block | FastAPI usage requires reproducible dependency installation. |
| RG-FASTAPI-002 | FastAPI app instance | Block if a FastAPI project has no detectable app instance | source-backed | block | Official examples show explicit `app = FastAPI()`, which phase one can detect. |
| RG-FASTAPI-003 | TestClient usage | Warn if absent | source-backed | warn | FastAPI docs show `TestClient` for app testing, but some projects may use other integration test styles. |
| RG-FASTAPI-004 | app-bound tests | Warn if absent | source-backed | warn | Tests tied to the app provide stronger API release confidence. |
| RG-FASTAPI-005 | FastAPI pytest execution | Block if FastAPI tests fail to run | source-backed | block | FastAPI testing docs rely on pytest-compatible tests. |
| RG-FASTAPI-006 | status-code assertions | Warn if absent | source-backed | warn | Official examples assert status codes, but exact assertion strategy varies. |
| RG-FASTAPI-007 | health route | Warn if absent | releaseguard-default | warn | Useful release-readiness policy, not an official FastAPI requirement. |

## Docker

| rule_id | concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|---|
| RG-DOCKER-001 | Dockerfile existence | Conditional on containerized release expectations | releaseguard-default | conditional | Docker docs define Dockerfile behavior but do not require all projects to have one. |
| RG-DOCKER-002 | `FROM` instruction | Block when Dockerfile exists without `FROM` | source-backed | block | Dockerfile reference requires valid Dockerfiles to start with `FROM` after allowed prelude lines. |
| RG-DOCKER-003 | `FROM` position | Block when `FROM` appears after invalid prelude instructions | source-backed | block | Docker documents what may precede `FROM`. |
| RG-DOCKER-004 | `WORKDIR` | Warn if missing | releaseguard-default | warn | `WORKDIR` improves path clarity; Docker defines it but does not mandate it. |
| RG-DOCKER-005 | `COPY` or `ADD` | Warn if missing | releaseguard-default | warn | Most application images copy code or dependencies, but some valid images may differ. |
| RG-DOCKER-006 | dependency installation | Warn if missing in Python application images | releaseguard-default | warn | ReleaseGuard policy for reproducible Python containers. |
| RG-DOCKER-007 | `CMD` or `ENTRYPOINT` | Conditional on runnable service image expectations | releaseguard-default | conditional | Service images should have a default start command; base/build images may differ. |
| RG-DOCKER-008 | instruction casing | Info only | source-backed | info | Docker documents uppercase as convention, not a validity requirement. |

## Security

| rule_id | concept | default_decision | support_level | blocking_policy | reason |
|---|---|---|---|---|---|
| RG-SEC-001 | hardcoded sensitive values | Block strong secret-like findings with redacted evidence | needs-source-mapping | block | Important baseline, but exact ASVS or secret-scanning source mapping is still needed. |
| RG-SEC-002 | Flask debug mode | Block for release contexts | needs-source-mapping | block | Mature production-security concern; add direct Flask docs before strong source-backed claims. |
| RG-SEC-003 | broad CORS | Conditional impact | needs-source-mapping | conditional | Risk depends on production context and needs stronger source mapping. |
| RG-SEC-004 | risky command execution | Conditional impact | source-backed | conditional | ASVS includes command injection requirement reference example. |
| RG-SEC-005 | production security documentation | Warn if absent | releaseguard-default | warn | ReleaseGuard handoff policy for safer deployments. |
| RG-SEC-006 | ASVS versioned IDs in reports | Info until security reporting exists | source-backed | info | ASVS documents versioned requirement reference format. |

## Default Execution Order

1. Keep implemented base rules stable: dependency, config example, test structure, and pytest execution.
2. Add test config checks next if staying in Python infrastructure.
3. Add FastAPI detection next if moving toward web-framework support.
4. Add Dockerfile structure next if moving toward container release readiness.
5. Add security baseline only after redaction and source-mapping rules are reviewed.
