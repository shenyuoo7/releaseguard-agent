from pathlib import Path

from fastapi.testclient import TestClient

from releaseguard_agent.api import create_app
from releaseguard_agent.services import ReleaseReviewService


def _create_reviewable_project(project_path: Path) -> None:
    project_path.mkdir(parents=True)
    (project_path / "requirements.txt").write_text(
        "pytest\n",
        encoding="utf-8",
    )
    (project_path / ".env.example").write_text(
        "APP_ENV=local\n",
        encoding="utf-8",
    )
    (project_path / "pytest.ini").write_text(
        "[pytest]\ntestpaths = tests\n",
        encoding="utf-8",
    )
    tests_dir = project_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n",
        encoding="utf-8",
    )


def test_health_is_synchronous_and_reports_deterministic_mode(
    tmp_path: Path,
) -> None:
    client = TestClient(create_app(allowed_project_roots=[tmp_path]))

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "releaseguard-agent",
        "deterministic_mode_available": True,
    }


def test_review_uses_shared_service_and_returns_structured_results(
    tmp_path: Path,
) -> None:
    project_path = tmp_path / "project"
    _create_reviewable_project(project_path)
    client = TestClient(create_app(allowed_project_roots=[tmp_path]))

    response = client.post(
        "/reviews",
        json={
            "project_path": str(project_path),
            "include_pytest_execution": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["project_path"] == str(project_path.resolve())
    assert payload["include_pytest_execution"] is False
    assert payload["release_allowed"] is True
    assert payload["summary"]["blocking"] == 0
    assert payload["summary"]["total"] > 0
    assert payload["artifacts"] == {}
    assert all("should_block_release" in item for item in payload["results"])
    assert payload["retrieval_evidence"]
    assert all(
        {
            "evidence_id",
            "rule_id",
            "source_url",
            "local_source",
            "chunk_id",
            "retrieval_method",
            "raw_score",
            "fusion_score",
            "rerank_score",
        }
        <= item.keys()
        for item in payload["retrieval_evidence"]
    )


def test_review_rejects_path_outside_allowed_roots(tmp_path: Path) -> None:
    allowed_root = tmp_path / "allowed"
    outside_root = tmp_path / "outside"
    allowed_root.mkdir()
    outside_root.mkdir()
    client = TestClient(create_app(allowed_project_roots=[allowed_root]))

    response = client.post(
        "/reviews",
        json={
            "project_path": str(outside_root),
            "include_pytest_execution": False,
        },
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "project_path_not_allowed"


def test_review_maps_missing_and_file_paths_to_safe_errors(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "not-a-project.txt"
    file_path.write_text("not a directory\n", encoding="utf-8")
    client = TestClient(create_app(allowed_project_roots=[tmp_path]))

    missing_response = client.post(
        "/reviews",
        json={"project_path": str(tmp_path / "missing")},
    )
    file_response = client.post(
        "/reviews",
        json={"project_path": str(file_path)},
    )

    assert missing_response.status_code == 404
    assert missing_response.json()["error"]["code"] == (
        "project_path_not_found"
    )
    assert file_response.status_code == 400
    assert file_response.json()["error"]["code"] == "project_path_invalid"


def test_invalid_request_uses_uniform_error_shape(tmp_path: Path) -> None:
    client = TestClient(create_app(allowed_project_roots=[tmp_path]))

    response = client.post("/reviews", json={"unknown": "value"})

    assert response.status_code == 422
    payload = response.json()
    assert payload["error"]["code"] == "invalid_request"
    assert payload["error"]["details"]
    assert "input" not in payload["error"]["details"][0]


def test_verification_endpoint_rescans_and_returns_before_after_delta(
    tmp_path: Path,
) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    _create_reviewable_project(before)
    _create_reviewable_project(after)
    source_before = before / "src"
    source_after = after / "src"
    source_before.mkdir()
    source_after.mkdir()
    source_code = "from fastapi import FastAPI\napp = FastAPI()\n"
    (source_before / "main.py").write_text(source_code, encoding="utf-8")
    (source_after / "main.py").write_text(source_code, encoding="utf-8")
    (after / "requirements.txt").write_text(
        "pytest\nfastapi\n", encoding="utf-8"
    )
    client = TestClient(create_app(allowed_project_roots=[tmp_path]))

    response = client.post(
        "/verifications",
        json={
            "before_project_path": str(before),
            "after_project_path": str(after),
            "include_pytest_execution": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "resolved"
    assert payload["before_release_allowed"] is False
    assert payload["release_allowed"] is True
    assert any(
        item.startswith("RG-FASTAPI-001::") for item in payload["resolved"]
    )
    assert payload["new"] == []
    assert payload["route_history"] == [
        "scan",
        "verifier_agent",
        "verification_complete",
    ]


def test_openapi_lists_only_approved_first_api_routes(tmp_path: Path) -> None:
    client = TestClient(create_app(allowed_project_roots=[tmp_path]))

    paths = set(client.get("/openapi.json").json()["paths"])

    assert paths == {"/health", "/reviews", "/verifications"}


def test_unexpected_service_error_is_sanitized(tmp_path: Path) -> None:
    class FailingService(ReleaseReviewService):
        def review(self, **_kwargs):
            raise RuntimeError("provider-secret-must-not-leak")

    project_path = tmp_path / "project"
    project_path.mkdir()
    client = TestClient(
        create_app(
            review_service=FailingService(),
            allowed_project_roots=[tmp_path],
        ),
        raise_server_exceptions=False,
    )

    response = client.post(
        "/reviews",
        json={
            "project_path": str(project_path),
            "include_pytest_execution": False,
        },
    )

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "The review request could not be completed.",
            "details": [],
        }
    }
    assert "provider-secret" not in response.text
