# ReleaseGuard Lightweight Relation RAG and Project Memory Design

**Status:** approved route, design review pending
**Date:** 2026-08-15
**Scope:** the next ReleaseGuard Agent capability slice after the durable tool
runtime. This design adds versioned relation-enhanced retrieval and transparent
project memory without replacing deterministic release policy or introducing
network, reviewed-project writes, or C-drive data.

## 1. Goal and rationale

ReleaseGuard already has a trustworthy baseline: a local rule corpus, exact,
BM25, vector, and hybrid retrieval, deterministic reranking, source evidence,
and a durable tool loop. It lacks explicit relation paths, reproducible index
versions, incremental relation updates, and a separate long-lived project
memory model.

The goal is a lightweight, deterministic graph-enhanced RAG layer that can
answer a release-review question with a bounded path such as:

```text
Finding -> Rule -> Checker -> Phase
                -> Source document -> Rule chunk
Fix step -> Rule
Run -> Evidence
```

The layer is derived from trusted local structures. It does not infer facts
from user source code or use an LLM to create graph facts. It improves
traceability and bounded multi-hop retrieval while retaining the existing
hybrid text retriever as the safe baseline.

The design follows the referenced RAG, GraphRAG/LightRAG, Agent, OpenClaw,
Harness, and Loop Engineering material in a bounded way:

- versioned offline indexing and online bounded querying;
- source/chunk citations and deterministic fallback rather than unsupported
  answer generation;
- LightRAG-style local relation expansion and incremental updates rather than
  costly community summaries and global Map-Reduce queries;
- transparent, local, auditable short-term state and long-term memory;
- hard context/cost/step budgets, independent verification, and durable
  observability.

## 2. Non-goals

This slice does **not** implement:

- a graph database, community detection, Leiden clustering, global community
  summaries, or LLM entity extraction;
- remote embeddings, remote storage, automatic cloud synchronization, or
  credential resolution;
- autonomous project-file changes, auto-PRs, external connectors, or a
  high-privilege resident agent;
- a replacement for `CheckResult.should_block_release`, existing hybrid
  retrieval modes, or the durable runtime event store;
- raw source-code, `.env`, provider response, or unredacted tool-argument
  retention as memory.

## 3. Architecture and source of truth

```text
Trusted rule Markdown + source mappings
  -> RuleIndexRetriever / RuleCorpusLoader
  -> RelationIndexBuilder
  -> immutable RelationIndexManifest + nodes + edges
  -> RelationRetrievalService
  -> existing Hybrid/Exact/BM25/Vector retrieval and deterministic rerank
  -> RetrievalEvidence with provenance and relation path

Durable runtime events and approved redacted observations
  -> ProjectMemoryStore (human-readable facts are source of truth)
  -> ProjectMemoryIndex (derived, rebuildable retrieval cache)
  -> ContextAssembler (bounded injection, trace references)
```

### 3.1 Truth hierarchy

1. Rule Markdown and source mappings are the authoritative rule evidence.
2. Human-readable, redacted project memory records are authoritative only for
   their explicitly declared operational facts.
3. Relation and memory indexes are derived caches. They are never the sole
   truth and must be rebuildable from their declared inputs.
4. Graph/memory results may enrich evidence selection but never override the
   deterministic release decision.

### 3.2 Storage policy

All owned state is below the repository `.runtime/` directory or a configured
ReleaseGuard runtime root. On Windows that root remains restricted to `E:`;
on POSIX it remains restricted to the configured/repository-local root. No
slice component creates or modifies a C-drive file.

## 4. Versioned relation index

### 4.1 Immutable contracts

The implementation introduces versioned, JSON-serializable models:

```text
RelationNode = {
  schema_version, node_id, node_type,
  canonical_key, display_label,
  source_refs[], index_version
}

RelationEdge = {
  schema_version, edge_id, edge_type,
  from_node_id, to_node_id,
  source_refs[], index_version
}

RelationIndexManifest = {
  schema_version, index_version, parent_index_version | null,
  rule_index_digest, source_manifest_digest, chunking_config_digest,
  nodes_digest, edges_digest, graph_digest,
  created_at_utc, change_set
}
```

`node_id`, `edge_id`, `graph_digest`, and `index_version` are SHA-256 values
over canonical data. Timestamps are not part of content identity. Every list
has a documented stable sort key; duplicates and unknown fields fail closed.

### 4.2 Initial graph schema

The initial trusted node types are `RULE`, `CHECKER`, `PHASE`, `SOURCE`, and
`CHUNK`. The trusted edge types are:

```text
RULE --IMPLEMENTED_BY--> CHECKER
RULE --BELONGS_TO-----> PHASE
RULE --SUPPORTED_BY----> SOURCE
RULE --CHUNKED_AS------> CHUNK
SOURCE --CONTAINS------> CHUNK
```

Runtime-only projections use separate, non-persisted relation objects:

```text
FINDING --VIOLATES-----> RULE
FIX_STEP --ADDRESSES---> RULE
RUN --USES_EVIDENCE----> CHUNK
```

They cannot become durable graph facts without explicit, redacted, provenance
checked project-memory promotion.

### 4.3 Build and incremental update

`RelationIndexBuilder` has exactly one initial source: validated
`RuleIndexRetriever` records and `RuleCorpusLoader` chunks. It does not scan a
reviewed project and does not call an LLM.

For a rebuild:

1. Canonicalize rule/source/chunk inputs and compute their content digests.
2. Compare them with the parent manifest.
3. Reuse unchanged derived entries by stable ID.
4. Create/update entries for changed inputs.
5. Mark removed inputs in the manifest change set as tombstones; never silently
   rewrite an older manifest.
6. Persist new nodes, edges, and manifest atomically to the derived index root.

An old `index_version` remains readable for replay. A partially written or
digest-mismatched version is rejected and callers receive a typed unavailable
result rather than an incomplete graph.

## 5. Retrieval and evidence contract

### 5.1 Modes

Existing `exact`, `bm25`, `vector`, and `hybrid` modes retain their behavior.
New modes are additive:

- `local_graph`: start from a supplied/found rule or finding seed, traverse at
  most two approved edge hops, and return source/chunk candidates;
- `graph_hybrid`: merge bounded graph candidates with current hybrid candidates
  before the existing deterministic rerank.

No mode accepts a graph fact without an original source/chunk reference.

### 5.2 Bounded traversal

The query contract carries a `RelationQueryBudget` with maximum depth (1 or
2), nodes, edges, candidate chunks, and total context characters. Edge types
are allowlisted. Canonical deterministic ordering is applied before truncation.
The trace records each consumed bound.

### 5.3 Fallback rules

`RelationRetrievalService` must return a typed `degraded_reason` and fall back
to current hybrid retrieval when any of the following occurs:

- no manifest exists, or the requested manifest cannot be verified;
- a graph seed is absent or no allowed path produces a chunk;
- graph data is stale, incomplete, corrupted, or over budget;
- vector retrieval is unavailable (the existing text/BM25 fallback remains
  applicable).

Fallback may never fabricate a graph path or silently change the selected
retrieval mode.

### 5.4 Result shape

`RetrievalEvidence` is extended additively with a typed relation explanation:

```text
RelationPath = {
  index_version,
  node_ids[], edge_ids[],
  source_chunk_ids[],
  traversal_score
}
```

Existing consumers can omit it for legacy modes. Graph modes require it for
each graph-derived hit. Every final hit continues to include rule ID, chunk ID,
source evidence, retrieval scores, and rerank score.

## 6. Transparent project memory

### 6.1 Model

`ProjectMemoryRecord` is a human-readable Markdown/JSON fact plus canonical
metadata:

```text
ProjectMemoryRecord = {
  schema_version, memory_id, project_scope_digest,
  kind, redacted_content, rule_ids[], provenance_refs[],
  status, confidence, created_at_utc, updated_at_utc,
  supersedes[], expires_at_utc | null
}
```

`kind` is initially one of `REVIEW_SUMMARY`, `FINDING_PATTERN`,
`FIX_OUTCOME`, or `RUN_OUTCOME`. `status` is `ACTIVE`, `SUPERSEDED`,
`DISABLED`, or `EXPIRED`. A record cannot be auto-promoted from an LLM answer;
it must originate from a structured, redacted runtime observation and carry
run/event/evidence provenance.

### 6.2 Safety and lifecycle

Before memory serialization, the same sensitive-argument and redaction policy
used by the durable runtime applies. Records containing secrets, raw project
source, untrusted instruction text without a safe summary, missing provenance,
or a foreign project scope are rejected.

Human correction creates a new record which supersedes the old one; deletion
is logical status transition rather than silent mutation. Memory index entries
are derived from active, non-expired records and can be rebuilt.

### 6.3 Context assembly

`ContextAssembler` selects memory only for a matching project scope and current
task/rule/finding keys. It enforces `top_k`, character/token, and provenance
budgets; it de-duplicates records and gives current durable run state priority
over long-term summaries. Trace output stores only memory IDs, versions,
selection reasons, and budget consumption.

## 7. Integration with the durable Agent runtime

Graph/memory work is read-only enrichment. The existing durable runtime remains
the only executor of typed operations. Retrieval/memory inputs and selected
index/memory versions are appended to the tool result/evaluation trace using
the existing secret-safe event boundary. The evaluator, not an LLM, decides
whether evidence is sufficient, a graph fallback is acceptable, manual review
is required, or the loop terminates.

The first integration tool remains read-only and local. It has no network,
write, or approval capability. Index construction/rebuild is explicitly
bounded, observable, resumable, and cancellable through the same tool runtime
when exposed to an Agent loop; direct library use stays deterministic for unit
tests.

## 8. Evaluation and acceptance criteria

The offline golden suite must add fixed cases for:

1. deterministic manifest/snapshot reproducibility from identical corpus input;
2. finding/rule seed to expected source/chunk relation-path retrieval;
3. one and two-hop path precision with no fabricated edge;
4. additive `graph_hybrid` behavior and legacy mode compatibility;
5. changed source/rule updates affecting only the expected node/edge/chunk set;
6. removal/tombstone behavior and old-version replay;
7. corrupt/missing/stale graph manifest, graph budget exhaustion, and text
   fallback reason;
8. memory scope isolation, secret rejection, disabled/superseded/expired
   records, and context budget compliance;
9. trace inclusion of retrieval mode, index version, relation paths, selected
   memory IDs, fallback, and budget;
10. deterministic replay of a stored query against its recorded index version.

Published metrics with non-zero denominators are:

- `graph_seed_recall_at_k`;
- `relation_path_precision`;
- `citation_provenance_completeness`;
- `incremental_update_correctness`;
- `snapshot_reproducibility`;
- `memory_scope_isolation`;
- `fallback_correctness`;
- `context_budget_compliance`.

All must meet explicit fixture thresholds in CI. Metric output includes cases,
denominators, threshold outcome, and limitations. Fake embeddings/LLM clients
validate mechanics only; no offline score claims semantic provider quality.

## 9. Failure handling

- Invalid schema, unknown relation type, digest mismatch, duplicate stable ID,
  or unsafe memory input: reject before persistence.
- Failed index publish: preserve last verified manifest and return an explicit
  unavailable/degraded result.
- Ambiguous interrupted Agent-triggered rebuild: use the existing durable tool
  attempt/reconciliation contract; never publish a partial manifest.
- Retrieval budget exhaustion: record the stop reason and fall back exactly as
  configured; never expand traversal indefinitely.
- Evidence without required original provenance: return evidence-gap/manual
  review rather than a derived assertion.

## 10. Implementation decomposition

The implementation will be planned as independently reviewable tasks:

1. versioned relation models, builder, manifest storage, and deterministic
   initial/incremental tests;
2. graph/local and graph-hybrid retrieval integration with typed provenance,
   fallback, and existing RAG compatibility tests;
3. transparent project-memory store/index/context assembler with safety and
   lifecycle tests;
4. runtime/trace integration and offline golden eval expansion;
5. independent full-branch review, full tests, Ruff, mypy, diff check, local
   checkpoint, and optional authorized remote CI verification.

## 11. Design self-review

- No `TODO`/`TBD` placeholders remain.
- The graph is explicitly derived, versioned, bounded, and fallback-capable.
- Long-term memory is distinct from runtime recovery state and protected by the
  same secret/path controls.
- Legacy retrieval and deterministic release policy remain authoritative.
- Heavy GraphRAG, implicit network, and project writes are out of scope.
- Each required behavior has a testable acceptance condition.
