# Lightweight Relation RAG and Project Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (the selected execution mode) to implement each task independently. Use `superpowers:test-driven-development` for every production change and `superpowers:verification-before-completion` before each task handoff.

**Goal:** Extend ReleaseGuard's existing deterministic, evidence-backed retrieval with a versioned lightweight relation index and transparent, bounded project memory. The implementation must remain offline-capable, provenance-preserving, replayable, and fail back to the current hybrid text retrieval path when a relation or memory artifact is unavailable or invalid.

**Architecture:** The trusted rule corpus remains the source of truth. A canonical relation snapshot is a derived, immutable artifact built from the existing rule index and chunks, with explicit nodes, edges, hashes, parent version, and tombstones. Retrieval optionally expands deterministic rule seeds through one-to-two relation hops, then fuses graph and text candidates under bounded budgets. Human-readable project-memory records are separately versioned source artifacts; their SQLite retrieval index is a rebuildable cache. Runtime calls only query these artifacts and record version/fallback/provenance in the existing durable trace.

**Tech Stack:** Python 3.11, frozen dataclasses, canonical JSON/SHA-256, existing `RuleIndexRetriever`/`RuleCorpusLoader`/`RuleRetrievalService`, SQLite runtime data under `.runtime/`, pytest, Ruff, mypy.

## Global Constraints

- Preserve deterministic checker facts and release decision authority. Relation and memory data may enrich evidence or explanation, never override `CheckResult.should_block_release`.
- Treat `knowledge_base/release_rules/` and its trusted source metadata as the only initial graph input. Do not introduce network calls, provider credentials, LLM extraction, community summaries, or auto-editing of a reviewed project.
- All generated snapshots, caches, test temp directories, and durable artifacts must be under the configured E-drive/repository runtime root. Do not read `.env` or create ReleaseGuard data on C:.
- Every content-addressed object must have a canonical preimage, version, source provenance, integrity verification, and a deterministic fail-closed read path. Atomic build/publish must never overwrite a verified earlier snapshot.
- Preserve existing `exact`, `bm25`, `vector`, and `hybrid` behavior. `local_graph` and `graph_hybrid` are additive modes; missing/corrupt/incompatible graph data falls back to the current deterministic text mode with an explicit reason.
- Persist or display no secrets, source-derived tokens, raw `.env` data, or unredacted provider errors. Reuse the runtime's pre-envelope secret rejection/redaction boundary for memory input, context, and trace attributes.
- Use only fixed offline fixtures and injected fakes in tests. Record focused RED/GREEN evidence, then run the required wider suite, Ruff, mypy, and `git diff --check` before each task handoff.

## File and Interface Map

| Area | Planned files | Responsibility |
| --- | --- | --- |
| Relation contracts | `src/releaseguard_agent/models/relation_index.py`, `src/releaseguard_agent/models/__init__.py` | Immutable nodes, edges, manifests, query budgets, canonical serialization. |
| Relation build/read | `src/releaseguard_agent/rag/relation_index.py` | Deterministic build, atomic publish, load, integrity checks, local expansion. |
| Retrieval | `src/releaseguard_agent/rag/retrieval_service.py`, `src/releaseguard_agent/models/retrieval_evidence.py` | Additive relation paths, graph modes, fusion and explicit fallback. |
| Project memory | `src/releaseguard_agent/models/project_memory.py`, `src/releaseguard_agent/rag/project_memory.py` | Versioned human-readable records, safe validation, derived SQLite cache, bounded context assembly. |
| Runtime adapters | `src/releaseguard_agent/agent_tools/release_tools.py`, `src/releaseguard_agent/agents/role_agents.py`, `src/releaseguard_agent/runtime/loop.py`, observability models as needed | Read-only artifact lookup and trace references, never graph/memory mutation during a review. |
| Offline evaluation | `src/releaseguard_agent/evaluation/runner.py`, `evals/datasets/relation_rag_memory_cases.json` | Fixed graph/memory cases and deterministic metrics. |
| Tests | `tests/unit/test_relation_index.py`, `tests/unit/test_relation_retrieval.py`, `tests/unit/test_project_memory.py`, plus focused adapter/eval tests | TDD evidence for every contract and failure boundary. |

## Task 1: Define and Build Immutable Relation Snapshots

**Files:**

- Create: `src/releaseguard_agent/models/relation_index.py`
- Create: `src/releaseguard_agent/rag/relation_index.py`
- Modify: `src/releaseguard_agent/models/__init__.py`
- Modify: `src/releaseguard_agent/rag/__init__.py`
- Create: `tests/unit/test_relation_index.py`

- [ ] **Step 1: Write failing relation-contract and reproducibility tests.**

  Cover a small fixed rule corpus with nodes `RULE`, `CHECKER`, `PHASE`, `SOURCE`, and `CHUNK`; typed directed edges `RULE_CHECKED_BY`, `RULE_APPLIES_TO_PHASE`, `RULE_SOURCED_BY`, `RULE_HAS_CHUNK`; deterministic node/edge IDs; duplicate suppression; bounded source-chunk relation; and `max_hops`/node/edge budget validation. Add tests that the same source index and build config reproduce the same snapshot digest and that altered node/edge bytes, dangling edge endpoints, an unknown type, or a mismatched manifest digest is rejected before use.

- [ ] **Step 2: Run the RED tests.**

  Run:

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE = '1'
  $env:TEMP = (Resolve-Path .runtime).Path + '\\task1-temp'
  $env:TMP = $env:TEMP; $env:TMPDIR = $env:TEMP
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-relation-index tests\unit\test_relation_index.py
  ```

  Expected: tests fail because contracts and builder do not yet exist.

- [ ] **Step 3: Implement canonical relation contracts.**

  In `models/relation_index.py`, add frozen dataclasses:

  ```python
  RelationNode(node_id, node_type, canonical_name, description, source_chunk_ids)
  RelationEdge(edge_id, source_node_id, target_node_id, relation_type, source_chunk_ids)
  RelationSnapshotManifest(schema_version, index_version, source_index_sha256,
                           chunking_config, retrieval_config, created_at_utc,
                           parent_index_version, change_set, nodes_sha256, edges_sha256)
  RelationSnapshot(manifest, nodes, edges, snapshot_sha256)
  RelationQueryBudget(max_hops, max_nodes, max_edges, max_context_characters)
  ```

  Use exact closed enums/constants and canonical sorted JSON. `index_version` derives from the canonical manifest preimage; `snapshot_sha256` derives from manifest plus ordered node/edge records. Do not embed a self-hash in its own preimage.

- [ ] **Step 4: Implement deterministic build/load and incremental semantics.**

  `RelationIndexBuilder.build(rule_index_path, output_root, *, parent_snapshot=None)` must load through the existing `RuleIndexRetriever` and `RuleCorpusLoader`, construct only trusted corpus relations, sort everything deterministically, and publish `manifest.json`, `nodes.json`, and `edges.json` through a staging directory followed by an atomic replace. A changed/deleted source creates a tombstone/change-set entry and a new child `index_version`; existing versions are immutable. `RelationIndexStore.load(index_version)` verifies every digest, all node/edge constraints, and the declared parent before returning a snapshot.

- [ ] **Step 5: Run focused verification and inspect the diff.**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-relation-index tests\unit\test_relation_index.py
  .\.venv\Scripts\python.exe -m ruff check src\releaseguard_agent\models src\releaseguard_agent\rag tests\unit\test_relation_index.py
  .\.venv\Scripts\python.exe -m mypy src\releaseguard_agent\models\relation_index.py src\releaseguard_agent\rag\relation_index.py
  git diff --check
  git diff -- src/releaseguard_agent/models/relation_index.py src/releaseguard_agent/rag/relation_index.py tests/unit/test_relation_index.py
  ```

- [ ] **Step 6: Create the reviewed task checkpoint.**

  ```powershell
  git add src/releaseguard_agent/models/relation_index.py src/releaseguard_agent/rag/relation_index.py src/releaseguard_agent/models/__init__.py src/releaseguard_agent/rag/__init__.py tests/unit/test_relation_index.py
  git diff --cached --check
  git commit -m "feat: add versioned relation index"
  ```

## Task 2: Add Bounded Local-Graph and Graph-Hybrid Retrieval

**Files:**

- Modify: `src/releaseguard_agent/models/retrieval_evidence.py`
- Modify: `src/releaseguard_agent/rag/retrieval_service.py`
- Modify: `src/releaseguard_agent/agent_tools/release_tools.py`
- Create: `tests/unit/test_relation_retrieval.py`
- Modify: existing retrieval/evidence tool tests only where their public serialization expectations change additively

- [ ] **Step 1: Write failing retrieval tests.**

  Use the Task 1 fixture snapshot. Assert that `local_graph` seeded with a rule ID returns only one-to-two-hop rule/source/chunk paths with source chunk provenance; `graph_hybrid` has stable graph/text fusion order; each hit records graph path, channel, source/chunk IDs, raw/fusion/rerank scores, and `index_version`. Add negative tests for no entity seed, missing snapshot, corrupted snapshot, traversal exhaustion, and context-budget exhaustion: each must return the legacy deterministic text result and a precise `degraded_reason`, not an invented graph fact. Assert old mode outputs remain byte-compatible except for optional additive fields.

- [ ] **Step 2: Run RED.**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-relation-retrieval tests\unit\test_relation_retrieval.py tests\unit\test_retrieval_service.py
  ```

- [ ] **Step 3: Add additive retrieval contracts.**

  Add frozen `RelationPath` to `models/retrieval_evidence.py` with ordered node IDs, edge IDs, source chunk IDs, `index_version`, `hop_count`, and `path_score`. Extend `RetrievalEvidence` only with optional `relation_paths: tuple[RelationPath, ...] = ()` and `index_version: str | None = None`; retain existing constructor defaults and `to_dict()` fields. Do not change the legacy evidence identity or rule/source provenance semantics.

- [ ] **Step 4: Implement graph modes and deterministic fallback.**

  Extend `RuleRetrievalService.retrieve()` with additive `mode: Literal["exact", "bm25", "vector", "hybrid", "local_graph", "graph_hybrid"]`, optional `seed_rule_ids`, optional `RelationQueryBudget`, and optional relation snapshot reference. `local_graph` expands only trusted `RULE -> SOURCE -> CHUNK` and closely related rule/checker/phase paths; it never creates facts from query text. `graph_hybrid` runs the existing text pipeline and local expansion, then applies an explicit stable reciprocal-rank fusion with a fixed candidate cap. Route every failed relation read/budget/no-seed case to the current appropriate text mode and expose `mode_used` plus a machine-readable fallback reason.

- [ ] **Step 5: Make EvidenceSearchTool expose only read-only retrieval options.**

  Add optional `relation_index_version` and budget inputs to `EvidenceSearchTool.invoke()`. Validate them before the call. Trace only version, mode, fallback reason, hit IDs, path IDs, and counts; do not persist raw context or any graph/memory source content beyond the already redacted evidence representation.

- [ ] **Step 6: Run verification.**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-relation-retrieval tests\unit\test_relation_retrieval.py tests\unit\test_retrieval_service.py tests\unit\test_agent_tools.py
  .\.venv\Scripts\python.exe -m ruff check src\releaseguard_agent\models src\releaseguard_agent\rag src\releaseguard_agent\agent_tools tests\unit\test_relation_retrieval.py
  .\.venv\Scripts\python.exe -m mypy src\releaseguard_agent\models\retrieval_evidence.py src\releaseguard_agent\rag\retrieval_service.py src\releaseguard_agent\agent_tools\release_tools.py
  git diff --check
  ```

- [ ] **Step 7: Checkpoint the task.**

  ```powershell
  git add src/releaseguard_agent/models/retrieval_evidence.py src/releaseguard_agent/rag/retrieval_service.py src/releaseguard_agent/agent_tools/release_tools.py tests/unit/test_relation_retrieval.py
  git diff --cached --check
  git commit -m "feat: add bounded relation retrieval"
  ```

## Task 3: Implement Transparent, Bounded Project Memory

**Files:**

- Create: `src/releaseguard_agent/models/project_memory.py`
- Create: `src/releaseguard_agent/rag/project_memory.py`
- Modify: `src/releaseguard_agent/models/__init__.py`
- Create: `tests/unit/test_project_memory.py`

- [ ] **Step 1: Write failing memory lifecycle and safety tests.**

  Test a human-readable JSON/Markdown-backed record with `memory_id`, `project_id`, `kind`, `content`, `provenance`, `created_at_utc`, `updated_at_utc`, `status`, `confidence`, `supersedes`, and `memory_version`. Cover: stable canonical version hashes; project isolation; superseded/disabled/expired record exclusion; explicit human correction; missing provenance rejection; secret/token-like value rejection before serialization; a tampered derived cache; and stable bounded selection that records omitted IDs/reason. Include a test that deleting a record produces a tombstone/new memory version rather than silent overwrite.

- [ ] **Step 2: Run RED.**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-project-memory tests\unit\test_project_memory.py
  ```

- [ ] **Step 3: Implement memory source contracts.**

  Add frozen dataclasses for `ProjectMemoryRecord`, `ProjectMemoryManifest`, `MemoryProvenance`, `MemoryQueryBudget`, `MemoryContextSelection`, and `MemoryContext`. `kind` is closed to `PROJECT_FACT`, `DECISION`, `CONSTRAINT`, `EVIDENCE_GAP`, `RUN_LESSON`, and `HUMAN_CORRECTION`; `status` is closed to `ACTIVE`, `SUPERSEDED`, `DISABLED`, `EXPIRED`, and `TOMBSTONED`. Require each record to reference an existing run/event/evidence/rule source or explicit human correction identity. Normalize and pre-validate every mapping with the shared runtime secret policy before any canonical JSON/hash operation.

- [ ] **Step 4: Implement source store, rebuildable cache, and context assembler.**

  `ProjectMemoryStore` writes versioned source JSON/Markdown under the configured repository runtime root and atomically publishes manifests. `ProjectMemoryIndex` is a disposable SQLite/BM25-style derived cache keyed by `memory_version`; it must rebuild entirely from verified source records, and a corrupt cache is deleted/rebuilt rather than trusted. `MemoryContextAssembler.select()` takes project ID, query, active run references, `top_k`, and hard character/token budgets; it prioritizes exact active-run/relevant-rule evidence, deduplicates by record/canonical content, returns selected IDs plus exclusion reasons, and never injects raw conversation text or secrets.

- [ ] **Step 5: Run verification.**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-project-memory tests\unit\test_project_memory.py
  .\.venv\Scripts\python.exe -m ruff check src\releaseguard_agent\models src\releaseguard_agent\rag tests\unit\test_project_memory.py
  .\.venv\Scripts\python.exe -m mypy src\releaseguard_agent\models\project_memory.py src\releaseguard_agent\rag\project_memory.py
  git diff --check
  ```

- [ ] **Step 6: Checkpoint the task.**

  ```powershell
  git add src/releaseguard_agent/models/project_memory.py src/releaseguard_agent/rag/project_memory.py src/releaseguard_agent/models/__init__.py tests/unit/test_project_memory.py
  git diff --cached --check
  git commit -m "feat: add transparent project memory"
  ```

## Task 4: Integrate Read-Only Context into the Durable Agent and Trace

**Files:**

- Modify: `src/releaseguard_agent/agent_tools/release_tools.py`
- Modify: `src/releaseguard_agent/agents/role_agents.py`
- Modify: `src/releaseguard_agent/runtime/loop.py`
- Modify: existing observability/trace models only if a typed additive trace field is required
- Modify/Create: focused runtime, agent-tool, role-agent, and trace tests

- [ ] **Step 1: Write failing integration tests at the durable boundary.**

  Cover an Evidence tool call using a fixed relation snapshot and memory version. Assert that the runtime persists only selected `index_version`, `memory_version`, graph mode/fallback, relation-path identifiers, selected memory IDs, exclusion reasons, and budget usage. Assert no source content, secret-like memory value, or raw prompt is present in events, snapshots, tool ledger, or reconstructed trace. Add restart/replay tests: the recorded versions are reloaded and verified; no index/memory build occurs during replay; missing/tampered versions cause a deterministic evidence-gap/fallback result rather than a handler retry or a decision change.

- [ ] **Step 2: Run RED.**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-rag-memory-runtime tests\unit\test_agent_tools.py tests\unit\test_runtime_loop.py tests\unit\test_execution_trace.py
  ```

- [ ] **Step 3: Add read-only adapter inputs and trace metadata.**

  Extend the evidence-facing role/tool input contract with explicit optional `relation_index_version`, `memory_version`, graph budget, and memory budget. Resolve only verified immutable artifacts. Feed the bounded `MemoryContext` and graph-backed retrieval output to the existing role contract as supporting context; keep checker findings and rule evidence authoritative. Extend the redacted trace schema additively with an `artifact_context` object containing hashes/versions/counts/IDs and fallback reason, not raw text.

- [ ] **Step 4: Preserve durable state-machine rules.**

  The loop must treat artifact lookup as part of the typed tool input/fingerprint so the same durable call is replay-safe. A missing artifact must produce the documented fallback/evidence-gap output before a tool reaches `TOOL_STARTED`; a corrupt artifact must fail closed/paused according to the existing ambiguous-failure policy. No adapter may invoke an LLM provider, mutate a graph/memory index, bypass offline guardrails, add a new approval scope, or change release policy.

- [ ] **Step 5: Run targeted integration verification.**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-rag-memory-runtime tests\unit\test_agent_tools.py tests\unit\test_runtime_loop.py tests\unit\test_execution_trace.py tests\integration\test_runtime_resume.py
  .\.venv\Scripts\python.exe -m ruff check src\releaseguard_agent\agent_tools src\releaseguard_agent\agents src\releaseguard_agent\runtime src\releaseguard_agent\observability tests\unit tests\integration
  .\.venv\Scripts\python.exe -m mypy src\releaseguard_agent\agent_tools\release_tools.py src\releaseguard_agent\agents\role_agents.py src\releaseguard_agent\runtime\loop.py
  git diff --check
  ```

- [ ] **Step 6: Checkpoint the task.**

  ```powershell
  git add src/releaseguard_agent/agent_tools src/releaseguard_agent/agents src/releaseguard_agent/runtime src/releaseguard_agent/observability tests/unit tests/integration
  git diff --cached --check
  git commit -m "feat: trace versioned rag memory context"
  ```

## Task 5: Add Offline Evaluation, Delivery Documentation, and Final Gates

**Files:**

- Create: `evals/datasets/relation_rag_memory_cases.json`
- Modify: `src/releaseguard_agent/evaluation/runner.py`
- Modify: `src/releaseguard_agent/evaluation/__init__.py`
- Create/Modify: `tests/unit/test_relation_rag_memory_evaluation.py`
- Modify: `README.md` or the existing capability/evaluation documentation only after code and tests prove the public claim

- [ ] **Step 1: Create fixed golden data and RED metric tests.**

  Include two versioned rule-corpus scenarios: baseline and an incremental update with a changed and tombstoned source. Include local one-hop and two-hop queries, graph-hybrid query, no-seed fallback, corrupt/missing graph fallback, memory budget overflow, disabled/superseded memory exclusion, provenance assertion, and replay against an older version. Tests must assert nonzero denominators and known deterministic expected values for `graph_seed_recall_at_k`, `relation_path_precision`, `citation_provenance_rate`, `incremental_correctness_rate`, `snapshot_reproducibility_rate`, `project_scope_isolation_rate`, `fallback_correctness_rate`, and `memory_context_budget_compliance_rate`.

- [ ] **Step 2: Run RED.**

  ```powershell
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-rag-memory-eval tests\unit\test_relation_rag_memory_evaluation.py
  ```

- [ ] **Step 3: Implement deterministic evaluation dispatch and reporting.**

  Add a dedicated dataset type/runner dispatch instead of weakening existing evaluation semantics. The evaluator must use only the fixed fixtures and fake/unconfigured embedding path, emit numerator/denominator plus failed case IDs, and reject a malformed dataset, unverified snapshot, unknown mode, missing provenance, or zero denominator. It must never call a real LLM or network provider.

- [ ] **Step 4: Document only verified capabilities.**

  Update public documentation to state that relation expansion is lightweight and trusted-corpus-derived, project memory is transparent/local/versioned, graph/memory modes are optional with deterministic text fallback, and heavy GraphRAG/community summaries/LLM extraction/external sync are intentionally out of scope. Include a reproducible offline command and do not claim production semantic quality from fake embeddings.

- [ ] **Step 5: Run all delivery gates from an E-drive runtime root.**

  ```powershell
  $env:PYTHONDONTWRITEBYTECODE = '1'
  $env:TEMP = (Resolve-Path .runtime).Path + '\\final-rag-memory-temp'
  $env:TMP = $env:TEMP; $env:TMPDIR = $env:TEMP
  $env:RELEASEGUARD_RUNTIME_ROOT = (Resolve-Path .runtime).Path
  .\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp .runtime\pytest-final-rag-memory tests\unit tests\integration tests\e2e
  .\.venv\Scripts\python.exe -m ruff check src tests scripts
  .\.venv\Scripts\python.exe -m mypy
  git diff --check
  git status --short
  git diff --stat
  git diff
  ```

  Expected: all existing and new tests pass, no lint/type/diff errors, and no accidental runtime artifacts are tracked.

- [ ] **Step 6: Perform independent final code review and create the milestone checkpoint.**

  A separate reviewer must inspect all five task diffs for canonical-hash cycles, unbounded traversal/context, source-provenance gaps, secret persistence, cross-project memory leaks, legacy retrieval regressions, and false public claims. Address any Critical/Important finding with a new failure-first regression. When clean:

  ```powershell
  git add src evals tests README.md docs
  git diff --cached --check
  git commit -m "feat: add lightweight relation rag memory"
  ```

## Cross-Task Acceptance Checklist

- [ ] A relation snapshot is reproducible from the same corpus/configuration and carries source/chunk/node/edge provenance, parent version, and tombstone/change-set data.
- [ ] Local graph traversal is bounded to two hops and declared node/edge/context budgets; every graph-derived hit supplies a full evidence path or falls back explicitly.
- [ ] Existing exact/BM25/vector/hybrid behavior remains supported, and no embedding configuration still yields deterministic BM25/text behavior.
- [ ] Memory records are human-readable, versioned, attributable, safe before serialization, project-scoped, lifecycle-aware, and never the sole truth source.
- [ ] The derived memory cache can be destroyed/rebuilt from verified source records; context injection is budgeted and traceable by IDs/versions only.
- [ ] Durable runs/replays reference immutable artifact versions and never rebuild, mutate, network-fetch, or leak raw graph/memory content.
- [ ] Offline golden evals report all eight metrics with nonzero denominators and exercise incremental update, deletion/tombstone, fallback, path provenance, memory scope, and budgets.

## Plan Self-Review

- The plan covers every approved design section: versioned relation snapshots, local/graph-hybrid retrieval, provenance/fallback, transparent memory source/cache/context, durable runtime tracing, offline eval, and truthful public documentation.
- All new interfaces name concrete owning files and remain additive to current retrieval/runtime contracts. No task requires a provider credential, internet access, C-drive storage, or modification of a reviewed repository.
- Tasks use test-first behavior, explicit failure modes, exact verification commands, independent review, and per-task Git checkpoints. There are no placeholder implementation steps or open-ended acceptance criteria.

## Execution Handoff

The user selected **Subagent-Driven** execution. Implement in the listed order, assigning one implementation task at a time, reviewing it independently before starting the next task because Tasks 2–5 consume prior immutable contracts.
