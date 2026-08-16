"""Fixed, offline evaluation for relation retrieval and transparent memory."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from releaseguard_agent.models.project_memory import (
    MemoryKind,
    MemoryProvenance,
    MemoryQueryBudget,
    MemoryStatus,
    ProjectMemoryRecord,
)
from releaseguard_agent.models.relation_index import RelationQueryBudget, RelationSnapshot
from releaseguard_agent.rag.project_memory import (
    ProjectMemoryIntegrityError,
    ProjectMemorySnapshot,
    ProjectMemoryStore,
    ProjectMemoryVersionMissingError,
    select_memory_context,
)
from releaseguard_agent.rag.relation_index import RelationIndexBuilder
from releaseguard_agent.rag.retrieval_service import RuleRetrievalService


_METRICS = (
    "citation_provenance_rate",
    "fallback_correctness_rate",
    "graph_seed_recall_at_k",
    "incremental_correctness_rate",
    "memory_context_budget_compliance_rate",
    "project_scope_isolation_rate",
    "relation_path_precision",
    "snapshot_reproducibility_rate",
)
_RELATION_MODES = {"local_graph", "graph_hybrid"}
_CANONICAL_TIME = "2026-08-15T00:00:00+00:00"
_SAFE_COMPONENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


@dataclass(frozen=True)
class RelationMemoryEvaluation:
    metrics: dict[str, float]
    details: dict[str, Any]
    passed: bool


@dataclass(frozen=True)
class _RelationFixture:
    index_path: Path
    snapshot_root: Path
    snapshot: RelationSnapshot
    service: RuleRetrievalService


@dataclass(frozen=True)
class _MemoryFixture:
    source_root: Path
    snapshot: ProjectMemorySnapshot
    store: ProjectMemoryStore


def evaluate_relation_rag_memory(
    payload: Mapping[str, Any],
    *,
    project_root: Path,
) -> RelationMemoryEvaluation:
    """Evaluate a verified fixed fixture using only local deterministic services."""

    dataset_sha256 = _validate_dataset(payload)
    artifact_root = (
        project_root
        / ".runtime"
        / "eval-rm"
        / dataset_sha256[:16]
    )
    artifact_root.mkdir(parents=True, exist_ok=True)
    execution_root = (
        project_root / ".runtime" / "eval-rm-run" / uuid.uuid4().hex[:12]
    )
    execution_root.mkdir(parents=True, exist_ok=False)
    relations = _build_relation_fixtures(payload, artifact_root)
    memories = _build_memory_fixtures(payload, artifact_root)
    contributions: dict[str, list[tuple[str, int, int]]] = {
        name: [] for name in _METRICS
    }
    observations: list[dict[str, Any]] = []

    _evaluate_relation_cases(payload, relations, contributions, observations)
    _evaluate_incremental_cases(
        payload, relations, memories, contributions, observations
    )
    _evaluate_reproducibility_cases(
        payload,
        relations,
        memories,
        execution_root,
        contributions,
        observations,
    )
    _evaluate_scope_cases(payload, memories, contributions, observations)
    _evaluate_fallback_cases(
        payload,
        relations,
        memories,
        execution_root,
        contributions,
        observations,
    )
    _evaluate_memory_cases(payload, memories, contributions, observations)

    zero = sorted(name for name, values in contributions.items() if not values)
    if zero:
        raise ValueError(
            f"relation_rag_memory metric denominators must be non-zero: {zero}"
        )
    metric_details: dict[str, dict[str, Any]] = {}
    metrics: dict[str, float] = {}
    for name in _METRICS:
        values = contributions[name]
        numerator = sum(item[1] for item in values)
        denominator = sum(item[2] for item in values)
        if denominator <= 0:
            raise ValueError(
                f"relation_rag_memory metric denominators must be non-zero: {name}"
            )
        failed = sorted(
            case_id for case_id, case_numerator, case_denominator in values
            if case_numerator != case_denominator
        )
        metrics[name] = numerator / denominator
        metric_details[name] = {
            "numerator": numerator,
            "denominator": denominator,
            "failed_case_ids": failed,
        }
    ordered_observations = sorted(observations, key=lambda item: str(item["id"]))
    passed = all(value == 1.0 for value in metrics.values()) and all(
        bool(item["matched"]) for item in ordered_observations
    )
    return RelationMemoryEvaluation(
        metrics=metrics,
        details={
            "dataset_sha256": dataset_sha256,
            "metrics": metric_details,
            "cases": ordered_observations,
            "limitations": [
                "Fixtures exercise deterministic local mechanics, not production semantic quality.",
                "Relation expansion is trusted-corpus-derived; no LLM fact extraction or network provider is used.",
            ],
        },
        passed=passed,
    )


def _validate_dataset(payload: Mapping[str, Any]) -> str:
    if payload.get("dataset_type") != "relation_rag_memory":
        raise ValueError("relation_rag_memory dataset_type is invalid")
    if payload.get("schema_version") != "1.0":
        raise ValueError("relation_rag_memory schema_version is unsupported")
    expected_digest = payload.get("content_sha256")
    if not isinstance(expected_digest, str) or len(expected_digest) != 64:
        raise ValueError("relation_rag_memory content_sha256 is invalid")
    digest_payload = dict(payload)
    digest_payload.pop("content_sha256", None)
    actual_digest = hashlib.sha256(_canonical_bytes(digest_payload)).hexdigest()
    if actual_digest != expected_digest:
        raise ValueError("relation_rag_memory dataset digest mismatch")
    for name in (
        "corpora",
        "relation_cases",
        "fallback_cases",
        "incremental_cases",
        "reproducibility_cases",
        "memory_versions",
        "memory_cases",
        "scope_cases",
    ):
        if name not in payload:
            raise ValueError(f"relation_rag_memory dataset is missing {name}")
    if not isinstance(payload["corpora"], Mapping):
        raise ValueError("relation_rag_memory corpora must be an object")
    if not isinstance(payload["memory_versions"], Mapping):
        raise ValueError("relation_rag_memory memory_versions must be an object")
    for name in payload["corpora"]:
        _safe_component(name, "corpus name")
    for name in payload["memory_versions"]:
        _safe_component(name, "memory version name")
    case_ids: set[str] = set()
    for name in (
        "relation_cases",
        "fallback_cases",
        "incremental_cases",
        "reproducibility_cases",
        "memory_cases",
        "scope_cases",
    ):
        if not isinstance(payload[name], list):
            raise ValueError(f"relation_rag_memory {name} must be a list")
        for raw_case in payload[name]:
            case = _case(raw_case, name)
            case_id = _safe_component(case.get("id"), "case id")
            if case_id in case_ids:
                raise ValueError(
                    f"relation_rag_memory duplicate case id: {case_id!r}"
                )
            case_ids.add(case_id)
    return expected_digest


def _build_relation_fixtures(
    payload: Mapping[str, Any], runtime_root: Path
) -> dict[str, _RelationFixture]:
    raw_corpora = payload["corpora"]
    assert isinstance(raw_corpora, Mapping)
    snapshot_root = runtime_root / "relation-snapshots"
    fixtures: dict[str, _RelationFixture] = {}
    remaining = set(str(name) for name in raw_corpora)
    while remaining:
        progressed = False
        for name in sorted(remaining):
            raw = raw_corpora[name]
            if not isinstance(raw, Mapping):
                raise ValueError(f"relation_rag_memory corpus {name!r} must be an object")
            parent_name = raw.get("parent")
            if parent_name is not None and parent_name not in fixtures:
                continue
            rule_index = raw.get("rule_index")
            sources = raw.get("sources")
            if not isinstance(rule_index, str) or not rule_index.strip():
                raise ValueError(f"relation_rag_memory corpus {name!r}: rule_index is required")
            if not isinstance(sources, Mapping) or not sources:
                raise ValueError(f"relation_rag_memory corpus {name!r}: sources are required")
            corpus_root = _contained_path(runtime_root / "corpora", name)
            source_root = _contained_path(corpus_root, "sources")
            source_root.mkdir(parents=True, exist_ok=True)
            index_path = _contained_path(corpus_root, "rule_index.md")
            index_path.write_text(rule_index, encoding="utf-8")
            for source_name, source_text in sorted(sources.items()):
                if (
                    not isinstance(source_name, str)
                    or not isinstance(source_text, str)
                    or not source_text.strip()
                ):
                    raise ValueError(
                        f"relation_rag_memory corpus {name!r}: source is invalid"
                    )
                safe_source_name = _safe_component(source_name, "source name")
                _contained_path(source_root, safe_source_name).write_text(
                    source_text,
                    encoding="utf-8",
                )
            parent = None if parent_name is None else fixtures[str(parent_name)].snapshot
            snapshot = RelationIndexBuilder().build(
                index_path, snapshot_root, parent_snapshot=parent
            )
            fixtures[name] = _RelationFixture(
                index_path=index_path,
                snapshot_root=snapshot_root,
                snapshot=snapshot,
                service=RuleRetrievalService(
                    index_path, relation_snapshot_root=snapshot_root
                ),
            )
            remaining.remove(name)
            progressed = True
        if not progressed:
            raise ValueError("relation_rag_memory corpus parent graph is invalid")
    return fixtures


def _build_memory_fixtures(
    payload: Mapping[str, Any], runtime_root: Path
) -> dict[str, _MemoryFixture]:
    raw_versions = payload["memory_versions"]
    assert isinstance(raw_versions, Mapping)
    store = ProjectMemoryStore(runtime_root / "memory-sources")
    fixtures: dict[str, _MemoryFixture] = {}
    remaining = set(str(name) for name in raw_versions)
    while remaining:
        progressed = False
        for name in sorted(remaining):
            raw = raw_versions[name]
            if not isinstance(raw, Mapping):
                raise ValueError(
                    f"relation_rag_memory memory version {name!r} must be an object"
                )
            parent_name = raw.get("parent")
            if parent_name is not None and parent_name not in fixtures:
                continue
            project_id = raw.get("project_id")
            records = raw.get("records")
            if not isinstance(project_id, str) or not project_id:
                raise ValueError(
                    f"relation_rag_memory memory version {name!r}: project_id is required"
                )
            if not isinstance(records, list) or not records:
                raise ValueError(
                    f"relation_rag_memory memory version {name!r}: records are required"
                )
            parsed_records = tuple(
                _memory_record(name, project_id, item) for item in records
            )
            parent_version = (
                None
                if parent_name is None
                else fixtures[str(parent_name)].snapshot.manifest.memory_version
            )
            snapshot = store.publish(
                project_id,
                parsed_records,
                parent_memory_version=parent_version,
            )
            fixtures[name] = _MemoryFixture(
                source_root=runtime_root / "memory-sources",
                snapshot=snapshot,
                store=store,
            )
            remaining.remove(name)
            progressed = True
        if not progressed:
            raise ValueError("relation_rag_memory memory parent graph is invalid")
    return fixtures


def _memory_record(
    version_name: str,
    project_id: str,
    raw: object,
) -> ProjectMemoryRecord:
    if not isinstance(raw, Mapping):
        raise ValueError(
            f"relation_rag_memory memory version {version_name!r}: record is invalid"
        )
    memory_id = raw.get("memory_id")
    provenance = raw.get("provenance")
    if not isinstance(memory_id, str) or not memory_id:
        raise ValueError(
            f"relation_rag_memory memory version {version_name!r}: memory_id is required"
        )
    if not isinstance(provenance, Mapping) or not any(provenance.values()):
        raise ValueError(
            f"relation_rag_memory memory {memory_id!r}: provenance is required"
        )
    try:
        return ProjectMemoryRecord(
            memory_id=memory_id,
            project_id=project_id,
            kind=MemoryKind(_required_string(raw, "kind")),
            content=_required_string(raw, "content"),
            provenance=MemoryProvenance(
                _optional_string(provenance, "run_id"),
                _optional_string(provenance, "event_id"),
                _optional_string(provenance, "evidence_id"),
                _optional_string(provenance, "rule_id"),
                _optional_string(provenance, "human_correction_id"),
            ),
            created_at_utc=_CANONICAL_TIME,
            updated_at_utc=_CANONICAL_TIME,
            status=MemoryStatus(_required_string(raw, "status")),
            confidence=float(raw["confidence"]),
            supersedes=_optional_string(raw, "supersedes"),
            expires_at_utc=None,
            memory_version="pm-pending",
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError(
            f"relation_rag_memory memory {memory_id!r}: record is invalid"
        ) from error


def _evaluate_relation_cases(
    payload: Mapping[str, Any],
    fixtures: Mapping[str, _RelationFixture],
    contributions: dict[str, list[tuple[str, int, int]]],
    observations: list[dict[str, Any]],
) -> None:
    for raw in payload["relation_cases"]:
        case = _case(raw, "relation")
        case_id = _required_string(case, "id")
        mode = _required_string(case, "mode", case_id=case_id)
        if mode not in _RELATION_MODES:
            raise ValueError(f"relation_rag_memory case {case_id!r}: mode is unsupported")
        fixture = _fixture(fixtures, case, "corpus", case_id)
        query = _required_string(case, "query", case_id=case_id)
        expected_rules = _string_tuple(case, "expected_rule_ids", case_id)
        expected_paths = _path_tuple(case, "expected_path_node_ids", case_id)
        expected_type_paths = _optional_path_tuple(
            case,
            "expected_path_node_type_sequences",
            case_id,
        )
        traversal_expectations = _traversal_expectations(case, case_id)
        expected_citations = _citation_tuple(case, case_id)
        budget = _relation_budget(case, case_id)
        result = fixture.service.retrieve(
            query,
            mode=mode,
            top_k=_positive_int(case, "top_k", case_id),
            seed_rule_ids=_string_tuple(case, "seed_rule_ids", case_id),
            relation_index_version=fixture.snapshot.manifest.index_version,
            relation_budget=budget,
        )
        returned_rules = {item.rule_id for item in result.evidence}
        rule_hits = sum(rule_id in returned_rules for rule_id in expected_rules)
        contributions["graph_seed_recall_at_k"].append(
            (case_id, rule_hits, len(expected_rules))
        )
        returned_paths = [
            (path, evidence.chunk_id)
            for evidence in result.evidence
            for path in evidence.relation_paths
        ]
        node_types = {
            node.node_id: node.node_type.value for node in fixture.snapshot.nodes
        }
        verified_expected_paths = [
            path
            for path, chunk_id in returned_paths
            if _path_is_verified(path, chunk_id, fixture.snapshot)
            and (
                path.node_ids in expected_paths
                or tuple(node_types[node_id] for node_id in path.node_ids)
                in expected_type_paths
            )
        ]
        exact_coverage = all(
            any(path.node_ids == expected for path in verified_expected_paths)
            for expected in expected_paths
        )
        type_coverage = all(
            any(
                tuple(node_types[node_id] for node_id in path.node_ids) == expected
                for path in verified_expected_paths
            )
            for expected in expected_type_paths
        )
        traversal_results = [
            _verified_traversal(expectation, fixture.snapshot, budget.max_hops)
            for expectation in traversal_expectations
        ]
        traversal_coverage = all(item is not None for item in traversal_results)
        path_hits = len(verified_expected_paths)
        path_denominator = len(returned_paths)
        all_returned_paths_expected = path_hits == path_denominator
        contributions["relation_path_precision"].append(
            (case_id, path_hits, path_denominator)
        )
        citation_hits = sum(
            any(
                evidence.rule_id == citation["rule_id"]
                and evidence.source_url == citation["source_url"]
                and evidence.local_source.endswith(citation["local_source_suffix"])
                and evidence.chunk_id == citation["chunk_id"]
                and bool(evidence.source_url)
                and bool(evidence.local_source)
                for evidence in result.evidence
            )
            for citation in expected_citations
        )
        contributions["citation_provenance_rate"].append(
            (case_id, citation_hits, len(expected_citations))
        )
        matched = (
            result.mode_used == mode
            and result.degraded_reason is None
            and rule_hits == len(expected_rules)
            and all_returned_paths_expected
            and exact_coverage
            and type_coverage
            and traversal_coverage
            and citation_hits == len(expected_citations)
        )
        observations.append(
            {
                "id": case_id,
                "category": "relation",
                "matched": matched,
                "mode_used": result.mode_used,
                "degraded_reason": result.degraded_reason,
                "returned_rule_ids": sorted(returned_rules),
                "returned_path_count": path_denominator,
                "verified_expected_path_count": path_hits,
                "verified_traversal_hops": sorted(
                    item["hop_count"]
                    for item in traversal_results
                    if item is not None
                ),
                "verified_traversal_edge_ids": sorted(
                    edge_id
                    for item in traversal_results
                    if item is not None
                    for edge_id in item["edge_ids"]
                ),
            }
        )


def _evaluate_incremental_cases(
    payload: Mapping[str, Any],
    relations: Mapping[str, _RelationFixture],
    memories: Mapping[str, _MemoryFixture],
    contributions: dict[str, list[tuple[str, int, int]]],
    observations: list[dict[str, Any]],
) -> None:
    for raw in payload["incremental_cases"]:
        case = _case(raw, "incremental")
        case_id = _required_string(case, "id")
        kind = _required_string(case, "kind", case_id=case_id)
        matched = False
        actual: dict[str, Any] = {}
        if kind == "relation":
            baseline = _fixture(relations, case, "baseline", case_id)
            incremental = _fixture(relations, case, "incremental", case_id)
            if incremental.snapshot.manifest.parent_index_version != baseline.snapshot.manifest.index_version:
                raise ValueError(
                    f"relation_rag_memory case {case_id!r}: unverified relation parent"
                )
            result = incremental.service.retrieve(
                _required_string(case, "query", case_id=case_id),
                mode="local_graph",
                top_k=5,
                seed_rule_ids=_string_tuple(case, "seed_rule_ids", case_id),
                relation_index_version=incremental.snapshot.manifest.index_version,
                relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
            )
            returned = {item.rule_id for item in result.evidence}
            text = "\n".join(item.text for item in result.evidence)
            tombstoned = set(
                incremental.snapshot.manifest.change_set.tombstoned_node_ids
            )
            expected_tombstones = set(
                _string_tuple(case, "expected_tombstoned_node_ids", case_id)
            )
            matched = (
                set(_string_tuple(case, "expected_selected_rule_ids", case_id)).issubset(returned)
                and _required_string(case, "expected_text", case_id=case_id) in text
                and _required_string(case, "forbidden_text", case_id=case_id) not in text
                and expected_tombstones.issubset(tombstoned)
                and not expected_tombstones.intersection(
                    node.node_id for node in incremental.snapshot.nodes
                )
            )
            actual = {
                "returned_rule_ids": sorted(returned),
                "tombstoned_node_ids": sorted(tombstoned),
            }
        elif kind == "memory":
            baseline = _fixture(memories, case, "baseline", case_id)
            incremental = _fixture(memories, case, "incremental", case_id)
            if incremental.snapshot.manifest.parent_memory_version != baseline.snapshot.manifest.memory_version:
                raise ValueError(
                    f"relation_rag_memory case {case_id!r}: unverified memory parent"
                )
            context = select_memory_context(
                incremental.snapshot,
                project_id=_required_string(case, "project_id", case_id=case_id),
                query=_required_string(case, "query", case_id=case_id),
                active_run_references=("run-incremental",),
                budget=MemoryQueryBudget(5, 2_000, 400),
                as_of_utc=_CANONICAL_TIME,
            )
            selected = {item.memory_id for item in context.selected}
            expected = set(_string_tuple(case, "expected_selected_ids", case_id))
            forbidden = set(_string_tuple(case, "forbidden_selected_ids", case_id))
            matched = expected.issubset(selected) and not forbidden.intersection(selected)
            actual = {"selected_ids": sorted(selected)}
        else:
            raise ValueError(f"relation_rag_memory case {case_id!r}: kind is unsupported")
        contributions["incremental_correctness_rate"].append(
            (case_id, int(matched), 1)
        )
        observations.append(
            {"id": case_id, "category": "incremental", "matched": matched, **actual}
        )


def _evaluate_reproducibility_cases(
    payload: Mapping[str, Any],
    relations: Mapping[str, _RelationFixture],
    memories: Mapping[str, _MemoryFixture],
    runtime_root: Path,
    contributions: dict[str, list[tuple[str, int, int]]],
    observations: list[dict[str, Any]],
) -> None:
    for raw in payload["reproducibility_cases"]:
        case = _case(raw, "reproducibility")
        case_id = _required_string(case, "id")
        kind = _required_string(case, "kind", case_id=case_id)
        matched = False
        if kind == "relation":
            original = _fixture(relations, case, "corpus", case_id)
            relation_rebuilt = RelationIndexBuilder().build(
                original.index_path,
                runtime_root / "relation-rebuild",
            )
            matched = (
                relation_rebuilt.manifest.index_version
                == original.snapshot.manifest.index_version
            )
        elif kind == "memory":
            original = _fixture(memories, case, "memory", case_id)
            project_id = _required_string(case, "project_id", case_id=case_id)
            rebuilt_store = ProjectMemoryStore(runtime_root / "memory-rebuild")
            memory_rebuilt = rebuilt_store.publish(
                project_id,
                tuple(
                    ProjectMemoryRecord(
                        **{
                            **record.__dict__,
                            "memory_version": "pm-pending",
                        }
                    )
                    for record in original.snapshot.records
                ),
            )
            matched = (
                memory_rebuilt.manifest.memory_version
                == original.snapshot.manifest.memory_version
            )
        elif kind == "relation_replay":
            fixture = _fixture(relations, case, "corpus", case_id)
            result = fixture.service.retrieve(
                _required_string(case, "query", case_id=case_id),
                mode="local_graph",
                top_k=5,
                seed_rule_ids=_string_tuple(case, "seed_rule_ids", case_id),
                relation_index_version=fixture.snapshot.manifest.index_version,
                relation_budget=RelationQueryBudget(2, 24, 24, 8_000),
            )
            returned = {item.rule_id for item in result.evidence}
            text = "\n".join(item.text for item in result.evidence)
            matched = (
                set(_string_tuple(case, "expected_rule_ids", case_id)).issubset(returned)
                and _required_string(case, "expected_text", case_id=case_id) in text
            )
        elif kind == "memory_replay":
            fixture = _fixture(memories, case, "memory", case_id)
            context = select_memory_context(
                fixture.snapshot,
                project_id=_required_string(case, "project_id", case_id=case_id),
                query=_required_string(case, "query", case_id=case_id),
                active_run_references=("run-baseline",),
                budget=MemoryQueryBudget(5, 2_000, 400),
                as_of_utc=_CANONICAL_TIME,
            )
            selected = {item.memory_id for item in context.selected}
            matched = (
                set(_string_tuple(case, "expected_selected_ids", case_id)).issubset(selected)
                and _required_string(case, "expected_content", case_id=case_id)
                in context.content
            )
        else:
            raise ValueError(f"relation_rag_memory case {case_id!r}: kind is unsupported")
        contributions["snapshot_reproducibility_rate"].append(
            (case_id, int(matched), 1)
        )
        observations.append(
            {"id": case_id, "category": "reproducibility", "matched": matched}
        )


def _evaluate_scope_cases(
    payload: Mapping[str, Any],
    memories: Mapping[str, _MemoryFixture],
    contributions: dict[str, list[tuple[str, int, int]]],
    observations: list[dict[str, Any]],
) -> None:
    for raw in payload["scope_cases"]:
        case = _case(raw, "scope")
        case_id = _required_string(case, "id")
        fixture = _fixture(memories, case, "memory", case_id)
        owner = _required_string(case, "owner_project_id", case_id=case_id)
        other = _required_string(case, "other_project_id", case_id=case_id)
        if fixture.snapshot.manifest.project_id != owner:
            raise ValueError(f"relation_rag_memory case {case_id!r}: owner mismatch")
        matched = False
        try:
            fixture.store.load(other, fixture.snapshot.manifest.memory_version)
        except ProjectMemoryIntegrityError:
            matched = True
        contributions["project_scope_isolation_rate"].append(
            (case_id, int(matched), 1)
        )
        observations.append(
            {"id": case_id, "category": "scope", "matched": matched}
        )


def _evaluate_fallback_cases(
    payload: Mapping[str, Any],
    relations: Mapping[str, _RelationFixture],
    memories: Mapping[str, _MemoryFixture],
    runtime_root: Path,
    contributions: dict[str, list[tuple[str, int, int]]],
    observations: list[dict[str, Any]],
) -> None:
    for raw in payload["fallback_cases"]:
        case = _case(raw, "fallback")
        case_id = _required_string(case, "id")
        kind = _required_string(case, "kind", case_id=case_id)
        expected_reason = _required_string(case, "expected_reason", case_id=case_id)
        reason: str | None = None
        fabricated = False
        if kind == "relation":
            fixture = _fixture(relations, case, "corpus", case_id)
            mode = _required_string(case, "mode", case_id=case_id)
            if mode not in _RELATION_MODES:
                raise ValueError(f"relation_rag_memory case {case_id!r}: mode is unsupported")
            service = fixture.service
            version = fixture.snapshot.manifest.index_version
            snapshot_state = case.get("snapshot_state", "verified")
            if snapshot_state == "missing":
                version = "ri-" + "0" * 64
            elif snapshot_state == "corrupt":
                copied_root = _contained_path(
                    runtime_root / "fallback",
                    case_id,
                )
                source_root = fixture.snapshot.manifest.index_version
                copied_version_root = copied_root / source_root
                copied_root.mkdir(parents=True, exist_ok=False)
                shutil.copytree(
                    fixture.snapshot_root / source_root,
                    copied_version_root,
                )
                nodes_path = copied_version_root / "nodes.json"
                nodes = json.loads(nodes_path.read_text(encoding="utf-8"))
                nodes[0]["description"] = "tampered evaluation bytes"
                nodes_path.write_text(json.dumps(nodes), encoding="utf-8")
                service = RuleRetrievalService(
                    fixture.index_path, relation_snapshot_root=copied_root
                )
            elif snapshot_state != "verified":
                raise ValueError(
                    f"relation_rag_memory case {case_id!r}: snapshot_state is unsupported"
                )
            result = service.retrieve(
                _required_string(case, "query", case_id=case_id),
                mode=mode,
                top_k=2,
                seed_rule_ids=_string_tuple(case, "seed_rule_ids", case_id),
                relation_index_version=version,
                relation_budget=RelationQueryBudget(2, 16, 16, 2_000),
            )
            reason = result.degraded_reason
            fabricated = any(item.relation_paths for item in result.evidence)
        elif kind == "memory":
            fixture = _fixture(memories, case, "memory", case_id)
            snapshot_state = case.get("snapshot_state", "verified")
            if snapshot_state == "missing":
                try:
                    fixture.store.load(
                        _required_string(case, "project_id", case_id=case_id),
                        "pm-" + "0" * 64,
                    )
                except ProjectMemoryVersionMissingError:
                    reason = "memory_version_missing"
            elif snapshot_state == "corrupt":
                copied_root = _contained_path(
                    runtime_root / "fallback-memory",
                    case_id,
                )
                shutil.copytree(
                    fixture.source_root,
                    copied_root,
                    ignore=shutil.ignore_patterns("cache"),
                )
                records_path = (
                    copied_root
                    / fixture.snapshot.manifest.memory_version
                    / "records.json"
                )
                records = json.loads(records_path.read_text(encoding="utf-8"))
                records[0]["content"] = "tampered evaluation bytes"
                records_path.write_text(json.dumps(records), encoding="utf-8")
                try:
                    ProjectMemoryStore(copied_root).load(
                        _required_string(case, "project_id", case_id=case_id),
                        fixture.snapshot.manifest.memory_version,
                    )
                except ProjectMemoryIntegrityError:
                    reason = "memory_source_invalid"
            elif snapshot_state == "verified":
                context = select_memory_context(
                    fixture.snapshot,
                    project_id=_required_string(case, "project_id", case_id=case_id),
                    query=_required_string(case, "query", case_id=case_id),
                    active_run_references=(),
                    budget=_memory_budget(case, case_id),
                    as_of_utc=_CANONICAL_TIME,
                )
                budget_reasons = {
                    item.exclusion_reason for item in context.omitted
                }.intersection({"top_k_budget", "character_budget", "token_budget"})
                if not context.content and budget_reasons:
                    reason = "memory_context_budget_exceeded"
                fabricated = bool(context.content)
            else:
                raise ValueError(
                    f"relation_rag_memory case {case_id!r}: snapshot_state is unsupported"
                )
        else:
            raise ValueError(f"relation_rag_memory case {case_id!r}: kind is unsupported")
        matched = reason == expected_reason and not fabricated
        contributions["fallback_correctness_rate"].append(
            (case_id, int(matched), 1)
        )
        observations.append(
            {
                "id": case_id,
                "category": "fallback",
                "matched": matched,
                "expected_reason": expected_reason,
                "observed_reason": reason,
            }
        )


def _evaluate_memory_cases(
    payload: Mapping[str, Any],
    memories: Mapping[str, _MemoryFixture],
    contributions: dict[str, list[tuple[str, int, int]]],
    observations: list[dict[str, Any]],
) -> None:
    for raw in payload["memory_cases"]:
        case = _case(raw, "memory")
        case_id = _required_string(case, "id")
        fixture = _fixture(memories, case, "memory", case_id)
        budget = _memory_budget(case, case_id)
        context = select_memory_context(
            fixture.snapshot,
            project_id=_required_string(case, "project_id", case_id=case_id),
            query=_required_string(case, "query", case_id=case_id),
            active_run_references=_string_tuple(
                case, "active_run_references", case_id
            ),
            budget=budget,
            as_of_utc=_CANONICAL_TIME,
        )
        selected = tuple(item.memory_id for item in context.selected)
        omissions = {
            item.memory_id: item.exclusion_reason for item in context.omitted
        }
        expected_omissions = case.get("expected_omissions")
        if not isinstance(expected_omissions, Mapping):
            raise ValueError(
                f"relation_rag_memory case {case_id!r}: expected_omissions is required"
            )
        expectations_match = (
            selected == _string_tuple(case, "expected_selected_ids", case_id)
            and all(omissions.get(str(key)) == value for key, value in expected_omissions.items())
        )
        within_budget = (
            len(selected) <= budget.top_k
            and context.character_count <= budget.max_characters
            and context.token_count <= budget.max_tokens
        )
        matched = expectations_match and within_budget
        contributions["memory_context_budget_compliance_rate"].append(
            (case_id, int(matched), 1)
        )
        observations.append(
            {
                "id": case_id,
                "category": "memory",
                "matched": matched,
                "selected_ids": list(selected),
                "omissions": dict(sorted(omissions.items())),
            }
        )


def _path_is_verified(path: Any, chunk_id: str, snapshot: RelationSnapshot) -> bool:
    nodes = {node.node_id for node in snapshot.nodes}
    edges = {edge.edge_id: edge for edge in snapshot.edges}
    if (
        path.index_version != snapshot.manifest.index_version
        or len(path.edge_ids) + 1 != len(path.node_ids)
        or chunk_id not in path.source_chunk_ids
        or not set(path.node_ids).issubset(nodes)
    ):
        return False
    for source, target, edge_id in zip(
        path.node_ids[:-1], path.node_ids[1:], path.edge_ids, strict=True
    ):
        edge = edges.get(edge_id)
        if edge is None or {source, target} != {
            edge.source_node_id,
            edge.target_node_id,
        }:
            return False
        if not set(path.source_chunk_ids).intersection(edge.source_chunk_ids):
            return False
    return True


def _verified_traversal(
    expectation: Mapping[str, Any],
    snapshot: RelationSnapshot,
    max_hops: int,
) -> dict[str, Any] | None:
    node_ids = tuple(expectation["node_ids"])
    expected_types = tuple(expectation["relation_types"])
    expected_chunks = set(expectation["source_chunk_ids"])
    hop_count = int(expectation["hop_count"])
    if hop_count != len(node_ids) - 1 or hop_count > max_hops:
        return None
    nodes = {node.node_id for node in snapshot.nodes}
    if not set(node_ids).issubset(nodes):
        return None
    edge_ids: list[str] = []
    relation_types: list[str] = []
    source_chunks: set[str] = set()
    for source, target in zip(node_ids[:-1], node_ids[1:], strict=True):
        matching = sorted(
            (
                edge
                for edge in snapshot.edges
                if {source, target}
                == {edge.source_node_id, edge.target_node_id}
            ),
            key=lambda edge: edge.edge_id,
        )
        if len(matching) != 1:
            return None
        edge = matching[0]
        edge_ids.append(edge.edge_id)
        relation_types.append(edge.relation_type.value)
        source_chunks.update(edge.source_chunk_ids)
    if tuple(relation_types) != expected_types or source_chunks != expected_chunks:
        return None
    return {
        "node_ids": list(node_ids),
        "edge_ids": edge_ids,
        "source_chunk_ids": sorted(source_chunks),
        "hop_count": hop_count,
    }


def _case(raw: object, category: str) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError(f"relation_rag_memory {category} case must be an object")
    case_id = raw.get("id")
    if not isinstance(case_id, str) or not case_id:
        raise ValueError(f"relation_rag_memory {category} case id is required")
    return raw


def _fixture(
    fixtures: Mapping[str, Any],
    case: Mapping[str, Any],
    field: str,
    case_id: str,
) -> Any:
    name = _required_string(case, field, case_id=case_id)
    try:
        return fixtures[name]
    except KeyError as error:
        raise ValueError(
            f"relation_rag_memory case {case_id!r}: {field} is unknown"
        ) from error


def _required_string(
    value: Mapping[str, Any],
    field: str,
    *,
    case_id: str | None = None,
    default: str | None = "",
) -> str:
    candidate = value.get(field, default)
    if not isinstance(candidate, str) or not candidate.strip():
        prefix = (
            "relation_rag_memory"
            if case_id is None
            else f"relation_rag_memory case {case_id!r}"
        )
        raise ValueError(f"{prefix}: {field} is required")
    return candidate


def _optional_string(value: Mapping[str, Any], field: str) -> str | None:
    candidate = value.get(field)
    if candidate is None:
        return None
    if not isinstance(candidate, str) or not candidate.strip():
        raise ValueError(f"{field} must be a non-empty string or null")
    return candidate


def _string_tuple(
    value: Mapping[str, Any], field: str, case_id: str
) -> tuple[str, ...]:
    candidate = value.get(field)
    if (
        not isinstance(candidate, list)
        or any(not isinstance(item, str) or not item for item in candidate)
    ):
        raise ValueError(f"relation_rag_memory case {case_id!r}: {field} is invalid")
    return tuple(candidate)


def _positive_int(value: Mapping[str, Any], field: str, case_id: str) -> int:
    candidate = value.get(field)
    if not isinstance(candidate, int) or isinstance(candidate, bool) or candidate < 1:
        raise ValueError(f"relation_rag_memory case {case_id!r}: {field} is invalid")
    return candidate


def _relation_budget(case: Mapping[str, Any], case_id: str) -> RelationQueryBudget:
    raw = case.get("budget")
    if not isinstance(raw, Mapping):
        raise ValueError(f"relation_rag_memory case {case_id!r}: budget is required")
    return RelationQueryBudget(
        _positive_int(raw, "max_hops", case_id),
        _positive_int(raw, "max_nodes", case_id),
        _positive_int(raw, "max_edges", case_id),
        _positive_int(raw, "max_context_characters", case_id),
    )


def _memory_budget(case: Mapping[str, Any], case_id: str) -> MemoryQueryBudget:
    raw = case.get("budget")
    if not isinstance(raw, Mapping):
        raise ValueError(f"relation_rag_memory case {case_id!r}: budget is required")
    return MemoryQueryBudget(
        _positive_int(raw, "top_k", case_id),
        _positive_int(raw, "max_characters", case_id),
        _positive_int(raw, "max_tokens", case_id),
    )


def _path_tuple(
    value: Mapping[str, Any], field: str, case_id: str
) -> tuple[tuple[str, ...], ...]:
    candidate = value.get(field)
    if not isinstance(candidate, list) or not candidate:
        raise ValueError(f"relation_rag_memory case {case_id!r}: {field} is invalid")
    paths: list[tuple[str, ...]] = []
    for item in candidate:
        if (
            not isinstance(item, list)
            or len(item) < 2
            or any(not isinstance(node_id, str) or not node_id for node_id in item)
        ):
            raise ValueError(
                f"relation_rag_memory case {case_id!r}: {field} is invalid"
            )
        paths.append(tuple(item))
    return tuple(paths)


def _optional_path_tuple(
    value: Mapping[str, Any], field: str, case_id: str
) -> tuple[tuple[str, ...], ...]:
    candidate = value.get(field, [])
    if not isinstance(candidate, list):
        raise ValueError(f"relation_rag_memory case {case_id!r}: {field} is invalid")
    paths: list[tuple[str, ...]] = []
    for item in candidate:
        if (
            not isinstance(item, list)
            or len(item) < 2
            or any(not isinstance(node_id, str) or not node_id for node_id in item)
        ):
            raise ValueError(
                f"relation_rag_memory case {case_id!r}: {field} is invalid"
            )
        paths.append(tuple(item))
    return tuple(paths)


def _traversal_expectations(
    case: Mapping[str, Any], case_id: str
) -> tuple[dict[str, Any], ...]:
    raw_expectations = case.get("expected_traversal_paths", [])
    if not isinstance(raw_expectations, list):
        raise ValueError(
            f"relation_rag_memory case {case_id!r}: expected_traversal_paths is invalid"
        )
    parsed: list[dict[str, Any]] = []
    for raw in raw_expectations:
        if not isinstance(raw, Mapping):
            raise ValueError(
                f"relation_rag_memory case {case_id!r}: expected_traversal_paths is invalid"
            )
        node_ids = _string_tuple(raw, "node_ids", case_id)
        relation_types = _string_tuple(raw, "relation_types", case_id)
        source_chunk_ids = _string_tuple(raw, "source_chunk_ids", case_id)
        hop_count = _positive_int(raw, "hop_count", case_id)
        if len(node_ids) != hop_count + 1 or len(relation_types) != hop_count:
            raise ValueError(
                f"relation_rag_memory case {case_id!r}: traversal shape is invalid"
            )
        parsed.append(
            {
                "node_ids": node_ids,
                "relation_types": relation_types,
                "source_chunk_ids": source_chunk_ids,
                "hop_count": hop_count,
            }
        )
    return tuple(parsed)


def _safe_component(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or value in {".", ".."}
        or _SAFE_COMPONENT.fullmatch(value) is None
    ):
        raise ValueError(
            f"relation_rag_memory {label} must be a safe path component"
        )
    return value


def _contained_path(root: Path, *components: str) -> Path:
    normalized_root = root.resolve()
    candidate = normalized_root.joinpath(*components).resolve()
    try:
        candidate.relative_to(normalized_root)
    except ValueError as error:
        raise ValueError(
            "relation_rag_memory path must remain inside the runtime fixture root"
        ) from error
    return candidate


def _citation_tuple(
    value: Mapping[str, Any], case_id: str
) -> tuple[dict[str, str], ...]:
    candidate = value.get("expected_citations")
    if not isinstance(candidate, list) or not candidate:
        raise ValueError(
            f"relation_rag_memory case {case_id!r}: expected_citations is invalid"
        )
    citations: list[dict[str, str]] = []
    for item in candidate:
        if not isinstance(item, Mapping):
            raise ValueError(
                f"relation_rag_memory case {case_id!r}: expected_citations is invalid"
            )
        citations.append(
            {
                field: _required_string(item, field, case_id=case_id)
                for field in (
                    "rule_id",
                    "source_url",
                    "local_source_suffix",
                    "chunk_id",
                )
            }
        )
    return tuple(citations)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
