import json
from pathlib import Path

from releaseguard_agent.cli.main import (
    EXIT_BLOCKING_ISSUES,
    EXIT_USAGE_ERROR,
    main,
)
from releaseguard_agent.llm import FakeLLMClient, LLMRuntime


def _response() -> str:
    return json.dumps(
        {
            "risk_level": "low",
            "summary": "No blocking issue was found.",
            "release_status": "review_recommended",
            "release_allowed": True,
            "prioritized_risks": [],
            "fix_plan": [],
            "evidence_rule_ids": [],
            "evidence_ids": [],
            "unsupported_claims": [],
            "missing_evidence_notes": [],
        }
    )


def test_normal_cli_does_not_resolve_or_call_llm(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "releaseguard_agent.cli.main.build_llm_runtime",
        lambda _environment: (_ for _ in ()).throw(
            AssertionError("provider factory must remain lazy")
        ),
    )

    exit_code = main(
        ["check", str(tmp_path), "--skip-pytest-execution"]
    )

    assert exit_code == EXIT_BLOCKING_ISSUES
    assert "provider factory" not in capsys.readouterr().err


def test_cli_llm_output_uses_fake_runtime_offline(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    client = FakeLLMClient(responses=[_response()])
    runtime = LLMRuntime(
        mode="llm",
        provider="fake",
        model="fake-model",
        client=client,
    )
    monkeypatch.setattr(
        "releaseguard_agent.cli.main.build_llm_runtime",
        lambda _environment: runtime,
    )
    output_dir = tmp_path / "llm-output"

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--llm-analysis-output-dir",
            str(output_dir),
        ]
    )

    capsys.readouterr()
    assert exit_code == EXIT_BLOCKING_ISSUES
    assert len(client.calls) == 1
    assert (output_dir / "agent_decision.json").is_file()
    assert (output_dir / "agent_fix_plan.md").is_file()


def test_cli_requested_llm_without_key_fails_safely(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    monkeypatch.setattr(
        "releaseguard_agent.cli.main.build_llm_runtime",
        lambda _environment: LLMRuntime(
            mode="deterministic",
            provider="deterministic",
            model=None,
            client=None,
            fallback_reason="missing_api_key",
        ),
    )

    exit_code = main(
        [
            "check",
            str(tmp_path),
            "--skip-pytest-execution",
            "--llm-analysis-output-dir",
            str(tmp_path / "llm-output"),
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == EXIT_USAGE_ERROR
    assert "deterministic review remains active" in captured.err
    assert "key" not in captured.err.lower()
