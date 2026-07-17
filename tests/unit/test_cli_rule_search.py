import json

from releaseguard_agent.cli.main import EXIT_SUCCESS, main


def test_cli_search_rules_runs_exact_and_bm25_offline(capsys) -> None:
    exact_code = main(
        ["search-rules", "RG-DOCKER-002", "--mode", "exact", "--top-k", "2"]
    )
    exact = json.loads(capsys.readouterr().out)
    bm25_code = main(
        ["search-rules", "docker FROM instruction", "--mode", "bm25", "--top-k", "3"]
    )
    bm25 = json.loads(capsys.readouterr().out)

    assert exact_code == EXIT_SUCCESS
    assert exact["mode_used"] == "exact"
    assert all(item["rule_id"] == "RG-DOCKER-002" for item in exact["evidence"])
    assert bm25_code == EXIT_SUCCESS
    assert bm25["mode_used"] == "bm25"
    assert len(bm25["evidence"]) == 3


def test_cli_hybrid_reports_offline_degradation(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "releaseguard_agent.cli.main.build_embedding_model",
        lambda _environment: None,
    )

    exit_code = main(["search-rules", "pytest", "--mode", "hybrid"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == EXIT_SUCCESS
    assert payload["requested_mode"] == "hybrid"
    assert payload["mode_used"] == "bm25"
    assert payload["degraded_reason"] == "embedding_unavailable"
