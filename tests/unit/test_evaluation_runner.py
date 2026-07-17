import json
from pathlib import Path

from releaseguard_agent.cli.main import EXIT_SUCCESS, main
from releaseguard_agent.evaluation import EvaluationRunner


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "evals" / "datasets" / "golden_cases.json"


def test_offline_evaluation_reports_all_required_metrics() -> None:
    result = EvaluationRunner(PROJECT_ROOT).run(DATASET)

    assert result.passed is True
    assert result.metrics == {
        "recall_at_k": 1.0,
        "evidence_source_accuracy": 1.0,
        "deterministic_decision_consistency": 1.0,
        "llm_structured_output_valid_rate": 1.0,
        "graph_path_coverage": 1.0,
        "before_after_delta_accuracy": 1.0,
    }
    assert result.details["retrieval"]
    assert result.details["graph_paths"]
    assert result.details["verification"]


def test_evaluate_cli_is_reproducible_and_offline(capsys) -> None:
    exit_code = main(["evaluate", "--dataset", str(DATASET)])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == EXIT_SUCCESS
    assert payload["passed"] is True
    assert payload["limitations"] == [
        "Fixed fake embeddings validate plumbing, not semantic quality.",
        "FakeLLM validates schema handling, not provider answer quality.",
    ]
