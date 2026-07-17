from pathlib import Path

from releaseguard_agent.services.verification_service import (
    ReleaseVerificationService,
)


def test_verification_service_rescans_and_runs_verifier_graph(
    tmp_path: Path,
) -> None:
    before = tmp_path / "before"
    after = tmp_path / "after"
    _write_project(before, has_dependency=False)
    _write_project(after, has_dependency=True)

    result = ReleaseVerificationService().verify(
        before_project_path=before,
        after_project_path=after,
        include_pytest_execution=False,
    )

    assert result.before.release_allowed is False
    assert result.after.release_allowed is True
    assert result.delta.status == "resolved"
    assert result.delta.resolved
    assert result.delta.new == ()
    assert result.after_workflow.state["route_history"] == [
        "scan",
        "verifier_agent",
        "verification_complete",
    ]


def _write_project(path: Path, *, has_dependency: bool) -> None:
    path.mkdir()
    if has_dependency:
        (path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    (path / ".env.example").write_text("APP_ENV=test\n", encoding="utf-8")
    (path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    tests = path / "tests"
    tests.mkdir()
    (tests / "test_sample.py").write_text(
        "def test_sample():\n    assert True\n", encoding="utf-8"
    )
