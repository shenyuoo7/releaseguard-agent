from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
import re
from typing import Literal

from llama_index.core.embeddings import BaseEmbedding

from releaseguard_agent.models.relation_index import (
    RelationNodeType,
    RelationQueryBudget,
    RelationSnapshot,
)
from releaseguard_agent.models.retrieval_evidence import (
    RelationPath,
    RetrievalEvidence,
)
from releaseguard_agent.rag.corpus import RuleCorpusLoader
from releaseguard_agent.rag.hybrid_retriever import (
    BM25RuleRetriever,
    ExactRuleRetriever,
    HybridRuleRetriever,
)
from releaseguard_agent.rag.vector_retriever import LlamaIndexVectorRetriever
from releaseguard_agent.rag.relation_index import (
    RelationIndexIntegrityError,
    RelationIndexSchemaIncompatibleError,
    RelationIndexStore,
    RelationIndexVersionMissingError,
    _source_index_digest,
)
from releaseguard_agent.rag.rule_index_retriever import RuleIndexRetriever


_DEFAULT_RELATION_BUDGET = RelationQueryBudget(2, 24, 24, 8_000)
_GRAPH_CANDIDATE_CAP = 20
_RRF_OFFSET = 60
RetrievalMode = Literal[
    "exact", "bm25", "vector", "hybrid", "local_graph", "graph_hybrid"
]


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    requested_mode: str
    mode_used: str
    degraded_reason: str | None
    evidence: tuple[RetrievalEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "query": self.query,
            "requested_mode": self.requested_mode,
            "mode_used": self.mode_used,
            "degraded_reason": self.degraded_reason,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass
class _GraphFusionCandidate:
    item: RetrievalEvidence
    score: float = 0.0
    methods: set[str] = field(default_factory=set)
    paths: list[RelationPath] = field(default_factory=list)
    text_rank: int | None = None
    text_score: float | None = None
    graph_rank: int | None = None
    graph_path_score: float | None = None


class RuleRetrievalService:
    """Deterministic text retrieval with optional bounded snapshot expansion."""

    def __init__(
        self,
        index_path: Path,
        *,
        embed_model: BaseEmbedding | None = None,
        relation_snapshot_root: Path | None = None,
    ) -> None:
        chunks = RuleCorpusLoader.from_rule_index(index_path)
        self._exact = ExactRuleRetriever(chunks)
        self._bm25 = BM25RuleRetriever(chunks)
        self._hybrid = HybridRuleRetriever()
        self._vector = (
            LlamaIndexVectorRetriever(chunks, embed_model=embed_model)
            if embed_model is not None
            else None
        )
        self._chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
        self._source_index_sha256 = _source_index_digest(
            RuleIndexRetriever.from_file(index_path), chunks
        )
        self._relation_store = (
            RelationIndexStore(relation_snapshot_root)
            if relation_snapshot_root is not None
            else None
        )

    def retrieve(
        self,
        query: str,
        *,
        mode: RetrievalMode | str = "hybrid",
        top_k: int = 5,
        seed_rule_ids: tuple[str, ...] | None = None,
        relation_budget: RelationQueryBudget | None = None,
        relation_index_version: str | None = None,
        relation_snapshot: RelationSnapshot | None = None,
        relation_fallback_reason: str | None = None,
    ) -> RetrievalResult:
        if top_k <= 0:
            raise ValueError("top_k must be positive.")
        normalized_mode = mode.strip().lower()
        if normalized_mode == "exact":
            evidence = self._exact.retrieve(query, top_k=top_k)
            return RetrievalResult(query, mode, "exact", None, tuple(evidence))
        if normalized_mode == "bm25":
            evidence = self._bm25.retrieve(query, top_k=top_k)
            return RetrievalResult(query, mode, "bm25", None, tuple(evidence))
        if normalized_mode in {"local_graph", "graph_hybrid"}:
            return self._retrieve_relation(
                query,
                requested_mode=mode,
                normalized_mode=normalized_mode,
                top_k=top_k,
                seed_rule_ids=seed_rule_ids,
                relation_budget=relation_budget,
                relation_index_version=relation_index_version,
                relation_snapshot=relation_snapshot,
                relation_fallback_reason=relation_fallback_reason,
            )
        if normalized_mode not in {"vector", "hybrid"}:
            raise ValueError(f"Unsupported retrieval mode: {mode!r}.")
        return self._retrieve_text(query, requested_mode=mode, top_k=top_k)

    def _retrieve_text(
        self,
        query: str,
        *,
        requested_mode: str,
        top_k: int,
    ) -> RetrievalResult:
        normalized_mode = requested_mode.strip().lower()
        bm25 = self._bm25.retrieve(query, top_k=max(top_k * 2, top_k))
        if self._vector is None:
            return RetrievalResult(
                query,
                requested_mode,
                "bm25",
                "embedding_unavailable",
                tuple(bm25[:top_k]),
            )
        vector = self._vector.retrieve(query, top_k=max(top_k * 2, top_k))
        if normalized_mode == "vector":
            return RetrievalResult(
                query, requested_mode, "vector", None, tuple(vector[:top_k])
            )
        fused = self._hybrid.fuse(query, [bm25, vector], top_k=top_k)
        return RetrievalResult(query, requested_mode, "hybrid", None, tuple(fused))

    def _retrieve_relation(
        self,
        query: str,
        *,
        requested_mode: str,
        normalized_mode: str,
        top_k: int,
        seed_rule_ids: tuple[str, ...] | None,
        relation_budget: RelationQueryBudget | None,
        relation_index_version: str | None,
        relation_snapshot: RelationSnapshot | None,
        relation_fallback_reason: str | None,
    ) -> RetrievalResult:
        def fallback(reason: str) -> RetrievalResult:
            return self._relation_fallback(query, requested_mode, top_k, reason)

        seeds = tuple(
            sorted(
                {
                    rule_id.strip()
                    for rule_id in seed_rule_ids or ()
                    if rule_id.strip()
                }
            )
        )
        if not seeds:
            return fallback("relation_seed_required")
        if relation_fallback_reason is not None:
            if relation_fallback_reason not in {
                "relation_snapshot_missing",
                "relation_snapshot_not_requested",
                "relation_snapshot_unconfigured",
                "relation_hop_budget_exceeded",
            }:
                raise ValueError("relation_fallback_reason is invalid")
            return fallback(relation_fallback_reason)
        if self._relation_store is None and relation_snapshot is None:
            return fallback("relation_snapshot_unconfigured")
        if relation_index_version is None:
            return fallback("relation_snapshot_required")
        if re.fullmatch(r"ri-[0-9a-f]{64}", relation_index_version) is None:
            return fallback("relation_snapshot_invalid")
        budget = relation_budget or _DEFAULT_RELATION_BUDGET
        if budget.max_hops > 2:
            return fallback("relation_hop_budget_exceeded")
        if relation_snapshot is None:
            assert self._relation_store is not None
            try:
                snapshot = self._relation_store.load(relation_index_version)
            except RelationIndexVersionMissingError:
                return fallback("relation_snapshot_missing")
            except RelationIndexSchemaIncompatibleError:
                return fallback("relation_snapshot_incompatible")
            except RelationIndexIntegrityError:
                return fallback("relation_snapshot_invalid")
        else:
            snapshot = relation_snapshot
            if snapshot.manifest.index_version != relation_index_version:
                raise RelationIndexIntegrityError(
                    "Verified relation snapshot version does not match request."
                )
        if snapshot.manifest.source_index_sha256 != self._source_index_sha256:
            return fallback("relation_source_index_mismatch")
        graph_candidate_cap = (
            _GRAPH_CANDIDATE_CAP if normalized_mode == "graph_hybrid" else top_k
        )
        graph_evidence, reason = self._expand_snapshot(
            snapshot, seeds, budget, graph_candidate_cap
        )
        if reason is not None:
            return fallback(reason)
        if normalized_mode == "local_graph":
            return RetrievalResult(
                query,
                requested_mode,
                "local_graph",
                None,
                graph_evidence,
            )
        text_result = self._retrieve_text(
            query,
            requested_mode="hybrid",
            top_k=_GRAPH_CANDIDATE_CAP,
        )
        return RetrievalResult(
            query,
            requested_mode,
            "graph_hybrid",
            None,
            tuple(
                self._fuse_graph_and_text(
                    text_result.evidence, graph_evidence, top_k
                )
            ),
        )

    def _relation_fallback(
        self, query: str, requested_mode: str, top_k: int, reason: str
    ) -> RetrievalResult:
        text = self._retrieve_text(query, requested_mode="hybrid", top_k=top_k)
        return RetrievalResult(
            query,
            requested_mode,
            text.mode_used,
            reason,
            text.evidence,
        )

    def _expand_snapshot(
        self,
        snapshot: RelationSnapshot,
        seed_rule_ids: tuple[str, ...],
        budget: RelationQueryBudget,
        top_k: int,
    ) -> tuple[tuple[RetrievalEvidence, ...], str | None]:
        node_by_id = {node.node_id: node for node in snapshot.nodes}
        edge_by_id = {edge.edge_id: edge for edge in snapshot.edges}
        seed_nodes = tuple(f"rule:{rule_id}" for rule_id in seed_rule_ids)
        if any(node_id not in node_by_id for node_id in seed_nodes):
            return (), "relation_seed_not_found"
        adjacency: dict[str, list[tuple[str, str]]] = {}
        for edge in snapshot.edges:
            adjacency.setdefault(edge.source_node_id, []).append(
                (edge.edge_id, edge.target_node_id)
            )
            adjacency.setdefault(edge.target_node_id, []).append(
                (edge.edge_id, edge.source_node_id)
            )
        for links in adjacency.values():
            links.sort()
        paths: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
            node_id: ((node_id,), ()) for node_id in seed_nodes
        }
        queue: deque[str] = deque(seed_nodes)
        traversed_edges: set[str] = set()
        while queue:
            node_id = queue.popleft()
            node_path, edge_path = paths[node_id]
            if len(edge_path) >= budget.max_hops:
                continue
            for edge_id, target_id in adjacency.get(node_id, ()):
                if edge_id in traversed_edges:
                    continue
                if len(traversed_edges) >= budget.max_edges:
                    return (), "relation_edge_budget_exceeded"
                traversed_edges.add(edge_id)
                if target_id in paths:
                    continue
                if len(paths) >= budget.max_nodes:
                    return (), "relation_node_budget_exceeded"
                paths[target_id] = (
                    (*node_path, target_id),
                    (*edge_path, edge_id),
                )
                queue.append(target_id)
        evidence_paths: dict[str, list[RelationPath]] = {}
        for node_id, (node_path, edge_path) in sorted(paths.items()):
            if not edge_path:
                continue
            node = node_by_id[node_id]
            is_two_hop_rule = (
                node.node_type is RelationNodeType.RULE and len(edge_path) == 2
            )
            if node.node_type not in {
                RelationNodeType.CHUNK,
                RelationNodeType.SOURCE,
            } and not is_two_hop_rule:
                continue
            path = RelationPath(
                node_ids=node_path,
                edge_ids=edge_path,
                source_chunk_ids=tuple(
                    sorted(
                        {
                            chunk_id
                            for edge_id in edge_path
                            for chunk_id in edge_by_id[edge_id].source_chunk_ids
                        }
                    )
                ),
                index_version=snapshot.manifest.index_version,
                hop_count=len(edge_path),
                path_score=1.0 / len(edge_path),
            )
            path_chunk_ids = path.source_chunk_ids
            if is_two_hop_rule:
                path_chunk_ids = tuple(
                    chunk_id
                    for chunk_id in path.source_chunk_ids
                    if self._chunks_by_id.get(chunk_id) is not None
                    and self._chunks_by_id[chunk_id].rule_id in seed_rule_ids
                )
            for chunk_id in path_chunk_ids:
                if chunk_id in self._chunks_by_id:
                    evidence_paths.setdefault(chunk_id, []).append(path)
        if not evidence_paths:
            return (), "relation_path_not_found"
        selected_ids = sorted(evidence_paths)[:top_k]
        context_characters = sum(
            len(self._chunks_by_id[item].text) for item in selected_ids
        )
        if context_characters > budget.max_context_characters:
            return (), "relation_context_budget_exceeded"
        evidence = []
        for chunk_id in selected_ids:
            paths_for_chunk = tuple(
                sorted(
                    evidence_paths[chunk_id],
                    key=lambda item: (-item.path_score, item.node_ids, item.edge_ids),
                )
            )
            score = paths_for_chunk[0].path_score
            chunk = self._chunks_by_id[chunk_id]
            evidence.append(
                RetrievalEvidence(
                    evidence_id=f"EVID-{chunk.chunk_id}",
                    rule_id=chunk.rule_id,
                    source_url=chunk.source_url,
                    local_source=chunk.local_source,
                    chunk_id=chunk.chunk_id,
                    retrieval_method="local_graph",
                    raw_score=score,
                    fusion_score=score,
                    rerank_score=score,
                    text=chunk.text,
                    metadata={**chunk.metadata, "graph_path_score": str(score)},
                    relation_paths=paths_for_chunk,
                    index_version=snapshot.manifest.index_version,
                )
            )
        return tuple(evidence), None

    def _fuse_graph_and_text(
        self,
        text_evidence: tuple[RetrievalEvidence, ...],
        graph_evidence: tuple[RetrievalEvidence, ...],
        top_k: int,
    ) -> list[RetrievalEvidence]:
        """Fuse at most 20 candidates per channel using RRF ``1 / (60 + rank)``.

        Equal scores sort by chunk ID; the caller's ``top_k`` is the final cap.
        """
        candidates: dict[str, _GraphFusionCandidate] = {}
        for channel, items in (("text", text_evidence), ("graph", graph_evidence)):
            for rank, item in enumerate(items[:_GRAPH_CANDIDATE_CAP], start=1):
                entry = candidates.setdefault(
                    item.chunk_id, _GraphFusionCandidate(item=item)
                )
                entry.score += 1 / (_RRF_OFFSET + rank)
                entry.methods.add(item.retrieval_method)
                if channel == "text":
                    entry.text_rank = rank
                    entry.text_score = item.raw_score
                else:
                    entry.graph_rank = rank
                    entry.graph_path_score = item.raw_score
                    entry.paths.extend(item.relation_paths)
        fused: list[RetrievalEvidence] = []
        for chunk_id, entry in candidates.items():
            metadata = dict(entry.item.metadata)
            for key, value in (
                ("text_rank", entry.text_rank),
                ("text_score", entry.text_score),
                ("graph_rank", entry.graph_rank),
                ("graph_path_score", entry.graph_path_score),
            ):
                if value is not None:
                    metadata[key] = str(value)
            unique_paths = tuple(
                sorted(
                    set(entry.paths),
                    key=lambda path: (-path.path_score, path.node_ids, path.edge_ids),
                )
            )
            fused.append(
                replace(
                    entry.item,
                    retrieval_method="+".join(sorted(entry.methods)),
                    fusion_score=entry.score,
                    rerank_score=entry.score,
                    metadata=metadata,
                    relation_paths=unique_paths,
                    index_version=(
                        unique_paths[0].index_version if unique_paths else None
                    ),
                )
            )
        return sorted(
            fused, key=lambda item: (-item.rerank_score, item.chunk_id)
        )[:top_k]
