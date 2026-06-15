# FastAPI Testing

## Source

- URL: https://fastapi.tiangolo.com/tutorial/testing/
- Type: official FastAPI documentation
- Use in ReleaseGuard: baseline guidance for detecting FastAPI applications and checking FastAPI-specific test evidence.

## Source-Backed Facts

- FastAPI testing documentation shows `app = FastAPI()`.
- It uses `TestClient` by passing the FastAPI application to it.
- It uses pytest-style functions whose names start with `test_`.
- It demonstrates assertions on response status codes and response content.
- FastAPI testing can be run directly with pytest.

## ReleaseGuard Rule Mapping

| rule_id | ReleaseGuard rule | support_level | blocking_policy | evidence_type | boundary |
|---|---|---|---|---|---|
| RG-FASTAPI-001 | Check whether a FastAPI dependency is declared | releaseguard-default | block | dependency_line | FastAPI docs show FastAPI usage; dependency declaration is ReleaseGuard's reproducible-release policy. |
| RG-FASTAPI-002 | Check for a detectable `FastAPI()` app instance | source-backed | block | matched_lines | Directly supported by official examples using `app = FastAPI()`. |
| RG-FASTAPI-003 | Check for `fastapi.testclient.TestClient` usage | source-backed | warn | matched_lines | Directly supported by official testing documentation. |
| RG-FASTAPI-004 | Check for tests tied to the FastAPI app | source-backed | warn | matched_lines | Supported by the official pattern of importing the app and passing it to `TestClient`. |
| RG-FASTAPI-005 | Check that FastAPI tests can run through pytest | source-backed | block | command_result | Supported by FastAPI's pytest-based testing guidance. |
| RG-FASTAPI-006 | Check for basic HTTP status-code assertions | source-backed | warn | matched_lines | Official examples assert response status codes. |
| RG-FASTAPI-007 | Check for a health-check route such as `/health` | releaseguard-default | warn | route_match | Health routes are a ReleaseGuard release-readiness policy, not a FastAPI testing requirement. |

## Implementation Notes

- Phase one may detect explicit `FastAPI()` calls first.
- Factory patterns and router-level inference can be added later.
- Health route detection should remain warning-level until stronger platform or operations references are added.
- TestClient checks should search tests and avoid treating examples or virtual environments as product evidence.

## Report Language Boundary

Use wording like:

- "FastAPI official testing docs show using `TestClient(app)` and pytest-style tests."
- "ReleaseGuard recommends a health-check route as a release-readiness policy."

Avoid wording like:

- "FastAPI requires every application to expose `/health`."
