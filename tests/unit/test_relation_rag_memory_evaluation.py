from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path

import pytest

from releaseguard_agent.evaluation import EvaluationRunner
from releaseguard_agent.models.retrieval_evidence import RelationPath
from releaseguard_agent.rag.retrieval_service import RuleRetrievalService


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "evals" / "datasets" / "relation_rag_memory_cases.json"
METRICS = {
    "graph_seed_recall_at_k",
    "relation_path_precision",
    "citation_provenance_rate",
    "incremental_correctness_rate",
    "snapshot_reproducibility_rate",
    "project_scope_isolation_rate",
    "fallback_correctness_rate",
    "memory_context_budget_compliance_rate",
}


def _write_dataset(tmp_path: Path, mutate) -> Path:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    mutate(payload)
    digest_payload = dict(payload)
    digest_payload.pop("content_sha256", None)
    payload["content_sha256"] = hashlib.sha256(
        json.dumps(
            digest_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    path = tmp_path / "dataset.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_relation_rag_memory_fixture_reports_exact_deterministic_metrics() -> None:
    result = EvaluationRunner(PROJECT_ROOT).run(DATASET)

    assert result.passed is True
    assert set(result.metrics) == METRICS
    assert result.metrics == {name: 1.0 for name in sorted(METRICS)}
    metric_details = result.details["relation_rag_memory"]["metrics"]
    assert set(metric_details) == METRICS
    assert all(item["denominator"] > 0 for item in metric_details.values())
    assert all(item["numerator"] == item["denominator"] for item in metric_details.values())
    assert all(item["failed_case_ids"] == [] for item in metric_details.values())


def test_malformed_fixture_fails_closed_with_case_reason(tmp_path: Path) -> None:
    path = _write_dataset(
        tmp_path,
        lambda payload: payload["relation_cases"][0].pop("query"),
    )

    with pytest.raises(ValueError, match="local-one-hop.*query"):
        EvaluationRunner(PROJECT_ROOT).run(path)


def test_tampered_dataset_digest_is_rejected(tmp_path: Path) -> None:
    payload = json.loads(DATASET.read_text(encoding="utf-8"))
    payload["relation_cases"][0]["query"] = "changed without readdressing"
    path = tmp_path / "tampered.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="dataset digest mismatch"):
        EvaluationRunner(PROJECT_ROOT).run(path)


def test_memory_fixture_missing_provenance_fails_closed(tmp_path: Path) -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["memory_versions"]["baseline"]["records"][0]["provenance"] = {}  # type: ignore[index]

    with pytest.raises(ValueError, match="memory-container-v1.*provenance"):
        EvaluationRunner(PROJECT_ROOT).run(_write_dataset(tmp_path, mutate))


def test_non_object_dataset_fails_closed(tmp_path: Path) -> None:
    path = tmp_path / "not-an-object.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="top-level JSON object"):
        EvaluationRunner(PROJECT_ROOT).run(path)


def test_dataset_path_components_cannot_escape_fixture_root(tmp_path: Path) -> None:
    def mutate(payload: dict[str, object]) -> None:
        corpora = payload["corpora"]  # type: ignore[assignment]
        corpora["../escape"] = corpora.pop("baseline")
        corpora["incremental"]["parent"] = "../escape"
        for collection_name in (
            "relation_cases",
            "fallback_cases",
            "incremental_cases",
            "reproducibility_cases",
        ):
            for case in payload[collection_name]:  # type: ignore[index]
                for field in ("corpus", "baseline"):
                    if case.get(field) == "baseline":
                        case[field] = "../escape"

    with pytest.raises(ValueError, match="safe path component"):
        EvaluationRunner(PROJECT_ROOT).run(_write_dataset(tmp_path, mutate))


def test_two_hop_case_proves_a_verified_two_edge_traversal() -> None:
    result = EvaluationRunner(PROJECT_ROOT).run(DATASET)
    case = next(
        item
        for item in result.details["relation_rag_memory"]["cases"]
        if item["id"] == "local-two-hop-budget"
    )

    assert case["verified_traversal_hops"] == [2]


def test_path_precision_counts_fabricated_returned_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = RuleRetrievalService.retrieve

    def inject_fabricated_path(self, query, **kwargs):
        result = original(self, query, **kwargs)
        if query != "container user" or not result.evidence:
            return result
        evidence = result.evidence[0]
        fabricated = RelationPath(
            node_ids=(f"rule:{evidence.rule_id}", "chunk:fabricated"),
            edge_ids=("edge:fabricated",),
            source_chunk_ids=(evidence.chunk_id,),
            index_version=evidence.index_version or "ri-" + "0" * 64,
            hop_count=1,
            path_score=1.0,
        )
        changed = replace(
            evidence,
            relation_paths=(*evidence.relation_paths, fabricated),
        )
        return replace(result, evidence=(changed, *result.evidence[1:]))

    monkeypatch.setattr(RuleRetrievalService, "retrieve", inject_fabricated_path)

    result = EvaluationRunner(PROJECT_ROOT).run(DATASET)
    detail = result.details["relation_rag_memory"]["metrics"][
        "relation_path_precision"
    ]

    assert result.passed is False
    assert detail["numerator"] < detail["denominator"]
    assert detail["failed_case_ids"] == ["local-one-hop"]


def test_zero_metric_denominator_is_rejected(tmp_path: Path) -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["relation_cases"] = []

    path = _write_dataset(tmp_path, mutate)

    with pytest.raises(ValueError, match="metric denominators must be non-zero"):
        EvaluationRunner(PROJECT_ROOT).run(path)


@pytest.mark.parametrize(
    ("field", "replacement", "metric"),
    (
        (
            "expected_path_node_ids",
            [["rule:RG-EVAL-001", "chunk:not-real"]],
            "relation_path_precision",
        ),
        (
            "expected_citations",
            [
                {
                    "rule_id": "RG-EVAL-001",
                    "source_url": "https://wrong.invalid",
                    "local_source_suffix": "wrong.md",
                    "chunk_id": "wrong-chunk",
                }
            ],
            "citation_provenance_rate",
        ),
    ),
)
def test_wrong_expected_path_or_provenance_is_reported(
    tmp_path: Path,
    field: str,
    replacement: object,
    metric: str,
) -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["relation_cases"][0][field] = replacement  # type: ignore[index]

    path = _write_dataset(tmp_path, mutate)
    result = EvaluationRunner(PROJECT_ROOT).run(path)

    assert result.passed is False
    detail = result.details["relation_rag_memory"]["metrics"][metric]
    assert detail["numerator"] < detail["denominator"]
    assert detail["failed_case_ids"] == ["local-one-hop"]


def test_fallback_mismatch_is_reported_with_stable_case_id(tmp_path: Path) -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["fallback_cases"][0]["expected_reason"] = "wrong_reason"  # type: ignore[index]

    result = EvaluationRunner(PROJECT_ROOT).run(_write_dataset(tmp_path, mutate))
    detail = result.details["relation_rag_memory"]["metrics"][
        "fallback_correctness_rate"
    ]

    assert result.passed is False
    assert detail["failed_case_ids"] == ["no-seed"]


def test_older_version_replay_mismatch_is_reported(tmp_path: Path) -> None:
    def mutate(payload: dict[str, object]) -> None:
        replay = next(
            case
            for case in payload["reproducibility_cases"]  # type: ignore[index]
            if case["id"] == "older-relation-version-replay"
        )
        replay["expected_text"] = "incremental-only text"

    result = EvaluationRunner(PROJECT_ROOT).run(_write_dataset(tmp_path, mutate))
    detail = result.details["relation_rag_memory"]["metrics"][
        "snapshot_reproducibility_rate"
    ]

    assert result.passed is False
    assert detail["failed_case_ids"] == ["older-relation-version-replay"]


def test_unknown_relation_mode_is_rejected_with_case_id(tmp_path: Path) -> None:
    def mutate(payload: dict[str, object]) -> None:
        payload["relation_cases"][0]["mode"] = "global_graph"  # type: ignore[index]

    with pytest.raises(ValueError, match="local-one-hop.*mode"):
        EvaluationRunner(PROJECT_ROOT).run(_write_dataset(tmp_path, mutate))


def test_report_ordering_is_reproducible() -> None:
    runner = EvaluationRunner(PROJECT_ROOT)

    first = runner.run(DATASET)
    second = runner.run(DATASET)

    assert first.metrics == second.metrics
    assert first.details == second.details
    cases = first.details["relation_rag_memory"]["cases"]
    assert [case["id"] for case in cases] == sorted(case["id"] for case in cases)
