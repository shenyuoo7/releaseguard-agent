# FastAPI Rules

This file groups phase-one FastAPI detection and FastAPI testing rules.

## Rule Table

| rule_id | rule_name | checker | source | support_level | blocking_policy | evidence_type | phase |
|---|---|---|---|---|---|---|---|
| RG-FASTAPI-001 | FastAPI dependency is declared when FastAPI is used | FastAPIDetector | FastAPI Testing; ReleaseGuard dependency policy | releaseguard-default | block | dependency_line | phase-1 |
| RG-FASTAPI-002 | FastAPI app instance is detectable | FastAPIDetector | FastAPI Testing | source-backed | block | matched_lines | phase-1 |
| RG-FASTAPI-003 | FastAPI `TestClient` is used | FastAPITestChecker | FastAPI Testing | source-backed | warn | matched_lines | phase-1 |
| RG-FASTAPI-004 | Tests are tied to the FastAPI app | FastAPITestChecker | FastAPI Testing | source-backed | warn | matched_lines | phase-1 |
| RG-FASTAPI-005 | FastAPI tests can run through pytest | FastAPITestChecker | FastAPI Testing | source-backed | block | command_result | phase-1 |
| RG-FASTAPI-006 | FastAPI tests assert response status codes | FastAPITestChecker | FastAPI Testing | source-backed | warn | matched_lines | phase-1 |
| RG-FASTAPI-007 | Health-check route exists | FastAPIHealthChecker | ReleaseGuard default policy; stronger operations source needed | releaseguard-default | warn | route_match | phase-1 |

## Checker Guidance

- Start with explicit `FastAPI()` detection in Python files.
- Dependency checking should compare source usage and dependency declaration files.
- `TestClient` detection should inspect pytest-discoverable test files first.
- `RG-FASTAPI-005` can reuse pytest execution evidence where possible.
- Health-check route detection should match common route strings such as `/health`, `/healthz`, `/ready`, or `/live`.

## Boundary Notes

- Do not treat `/health` as an official FastAPI requirement.
- Factory-pattern app detection can be deferred until the explicit app-instance checker is stable.
- Missing FastAPI dependency is blocking when FastAPI usage is detected because release environments need reproducible dependency installation.
