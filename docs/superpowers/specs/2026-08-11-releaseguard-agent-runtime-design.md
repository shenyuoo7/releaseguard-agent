# ReleaseGuard Durable Agent Runtime Design

**Status:** Draft for user review; no production code changes are authorized by this document alone.

**Goal:** Evolve ReleaseGuard from a conditional demo workflow into a restartable, tool-calling release-review Agent while preserving deterministic release facts, read-only defaults, and user-controlled changes.

**Scope:** The first implementation slice covers durable execution state, typed tool contracts, loop budgets, HITL approvals, guardrails, recovery, and quantitative evaluation. A later slice adds a lightweight evidence graph on top of the existing hybrid RAG index.

## Design principles

1. **Deterministic facts remain authoritative.** Checkers, scanners, path policy, release-blocking policy, and evidence provenance cannot be overridden by an LLM or an Agent plan.
2. **Read-only and offline are the defaults.** A review may inspect an explicitly allowed project root and local rule corpus. It does not write to the reviewed project, resolve provider credentials, or make network calls unless an explicit approval enables that exact action.
3. **Every action is a typed tool call.** The model proposes a tool and arguments; the runtime validates the contract, guardrails, budget, approval state, and idempotency key before execution.
4. **Every meaningful transition is durable.** A restart resumes from the last committed checkpoint and never repeats a completed side effect.
5. **Human approval is a state transition, not a comment.** Approval requests, decisions, expiry, actor, scope, and the approved argument digest are persisted and audited.
6. **Evaluation is part of the product.** Every new runtime behavior has deterministic golden cases, failure cases, and trace-replay evidence.
7. **All runtime data stays on E:.** Runtime state, checkpoints, traces, and temporary test data live under the repository's ignored `.runtime/` directory or another explicitly approved E: path. No implementation step writes C:.

## Current baseline and gap

The repository already provides `ReleaseReviewService`, typed graph-callable scan/evidence/risk/fix tools, a compiled LangGraph workflow, role Agent contracts, hybrid exact/BM25/vector retrieval, redacted execution tracing, and an offline evaluation runner. The `memory` package is reserved, the graph invocation is in-process, and no persisted Agent checkpoint or formal approval ledger exists. Existing LLM guardrail notes describe output constraints but do not gate all tool side effects.

The design therefore extends existing boundaries instead of replacing the checker or report pipeline.

## Target runtime architecture

```text
CLI/API/UI trigger
  -> AgentRunService
  -> AgentRunStore (SQLite WAL + append-only event rows under E:/.runtime)
  -> LoopController
       -> ContextAssembler (task + scoped memory + current evidence)
       -> Planner (LLM or deterministic plan)
       -> ToolPolicyGate (schema, path, network, budget, approval, idempotency)
       -> ToolExecutor (typed tool registry)
       -> ObservationRecorder (result summary, provenance, trace)
       -> Evaluator (success, evidence sufficiency, stop/retry/recover)
       -> CheckpointCommitter
  -> existing ReleaseReviewService / RAG / reports / verification
```

The runtime must be usable without an LLM. In deterministic mode the planner selects the same registered tool sequence from the current graph; an LLM can propose a plan only when explicitly configured. The final release decision always comes from deterministic `CheckResult.should_block_release` policy.

## Durable state model

`AgentRunState` is a versioned, JSON-serializable record with:

| Field | Requirement |
| --- | --- |
| `run_id` | Stable UUID-like identifier; immutable after creation. |
| `schema_version` | Explicit migration version; unknown versions fail closed. |
| `task_kind` | `REVIEW`, `VERIFICATION`, or `APPROVED_CHANGE`. |
| `project_root` | Canonical E: path already accepted by `ProjectPathPolicy`. |
| `status` | `CREATED`, `RUNNING`, `WAITING_HITL`, `PAUSED`, `RECOVERING`, `FAILED`, `COMPLETED`, or `CANCELLED`. |
| `step_index` | Monotonic loop step; a committed tool result may not be replayed under a new index. |
| `plan_digest` | Hash of the current bounded plan and tool arguments. |
| `checkpoint_event_id` | Last committed event; used as the resume anchor. |
| `budget` | Maximum steps, wall time, tool calls, retries, output bytes, and optional token/cost ceiling. |
| `pending_approval_id` | Nullable; non-null only in `WAITING_HITL`. |
| `memory_refs` | Hashes of selected session/long-term memory records, never raw secrets. |
| `decision_digest` | Hash of the deterministic release decision and final evidence set. |

The store uses SQLite in WAL mode under `.runtime/agent_runs/` with an append-only `run_events` table and a materialized `run_snapshots` table. Each event contains `event_id`, `run_id`, `sequence`, `event_kind`, canonical payload JSON, payload hash, and UTC timestamp. A compare-and-swap on `(run_id, sequence)` commits exactly one next event. A crash before commit leaves no visible side effect; a crash after commit is replayable.

Required event kinds are `RUN_CREATED`, `PLAN_PROPOSED`, `TOOL_REQUESTED`, `APPROVAL_REQUESTED`, `APPROVAL_DECIDED`, `TOOL_STARTED`, `TOOL_COMPLETED`, `TOOL_FAILED`, `EVALUATION_RECORDED`, `CHECKPOINT_COMMITTED`, `RUN_PAUSED`, `RUN_RESUMED`, `RUN_RECOVERED`, `RUN_COMPLETED`, `RUN_FAILED`, and `RUN_CANCELLED`. Reducer tests must prove duplicate, out-of-order, unknown, stale-CAS, and corrupted-payload events are rejected.

Resume rules:

- `RUNNING` with a committed `TOOL_REQUESTED` but no `TOOL_COMPLETED` resumes through idempotency lookup; it never blindly executes twice.
- `WAITING_HITL` resumes only after an unexpired approval whose scope and argument digest match the pending request.
- `FAILED` is resumable only when the evaluator marks the error retryable and the budget remains; otherwise it requires a new run.
- `COMPLETED`, `CANCELLED`, and terminal `FAILED` runs are immutable.

## Tool contract and execution policy

Every tool implements a common contract:

```text
ToolSpec = {
  name, version, input_schema, output_schema,
  side_effect = READ_ONLY | CONTROL_PLANE_WRITE | PROJECT_WRITE | NETWORK,
  allowed_roots, network_policy, timeout_ms, retry_policy,
  idempotency_key_fields, required_approval_scope, budget_cost
}

ToolCall = {
  tool_name, tool_version, canonical_args, args_sha256,
  run_id, step_index, idempotency_key, requested_at_utc
}
```

The initial registry exposes existing `scan_project`, `search_rule_evidence`, `analyze_risk`, and `build_fix_plan` tools. They are explicitly `READ_ONLY`, have bounded inputs, and may run automatically. Future `apply_fix`, `run_project_tests`, `provider_connection_test`, and `publish_report` tools must declare their additional side effects and cannot be reachable without the corresponding approval scope.

The executor validates input/output JSON schemas, canonicalizes paths, enforces timeout and retry limits, records a redacted result summary, and returns a typed error envelope. Retries are allowed only for declared transient failures and reuse the same idempotency key. A tool cannot call another tool directly; nested work goes back through the LoopController so every action is traced and gated.

## HITL protocol

`ApprovalRequest` is a durable artifact:

```text
ApprovalRequest = {
  approval_id, run_id, action_kind, tool_name, tool_version,
  args_sha256, affected_paths[], requested_scopes[], risk_summary,
  created_at_utc, expires_at_utc, requester_identity, request_signature
}
```

`ApprovalDecision` contains `approval_id`, `decision=APPROVED|REJECTED|EXPIRED`, actor identity, decision reason, decided time, and a signature. The runtime accepts approval only when the request hash, tool version, canonical arguments, path set, scopes, and expiration all match. An approval for one tool call cannot be reused for another call or run.

Automatic execution is allowed only for read-only local tools under the already-approved root. The following always pause in `WAITING_HITL`: project writes, deleting or moving files, network calls, credential use, changing the allowed root, bypassing a guardrail, increasing a budget, or executing a user-proposed fix. The UI/API exposes pending approvals and a resume endpoint; CLI exposes `approve`, `reject`, `resume`, and `cancel` commands using the same service.

## Guardrails and recovery

Guardrails are a pre-tool and post-tool pipeline:

1. **Identity/scope:** canonical E: path, no symlink/reparse escape, no `.env` reads, no secrets in arguments.
2. **Capability:** tool side-effect class and required approval scope match the run policy.
3. **Budget:** steps, retries, timeout, output bytes, token/cost estimate, and cumulative tool calls remain within the run budget.
4. **Input safety:** untrusted repository text is evidence, never executable instructions; prompt-injection markers are quarantined from system policy.
5. **Output safety:** schema validation, provenance requirements, deterministic decision comparison, and secret redaction run before checkpoint commit.
6. **Recovery:** transient errors retry with bounded backoff; ambiguous side effects enter `PAUSED`/HITL instead of being repeated; corrupted state fails closed and preserves the last valid checkpoint.

Guardrail outcomes are typed: `ALLOW`, `REQUIRE_HITL`, `RETRY`, `BLOCK`, or `QUARANTINE`. Every non-allow outcome records the rule, evidence, and exact rejection reason.

## Loop controller

Each iteration follows:

```text
load checkpoint
 -> assemble bounded context
 -> produce deterministic/LLM plan
 -> validate one ToolCall
 -> execute or pause for HITL
 -> record observation
 -> evaluate evidence, decision, budget, and stop condition
 -> commit checkpoint
 -> continue, retry, recover, or complete
```

Stop conditions are explicit: final deterministic decision plus required evidence, manual review required, budget exhausted, maximum iterations reached, cancellation, or unrecoverable guardrail violation. The evaluator—not the model—chooses the next control outcome. A plan may not silently change the deterministic release decision.

## Memory and context

Memory is split into:

- **Run state:** durable events/checkpoints needed to resume exactly.
- **Session memory:** bounded recent observations and decisions for one run/thread.
- **Long-term project memory:** user-approved, redacted lessons and prior release outcomes stored as append-only Markdown/JSON records under E:, each with provenance and an explicit retention policy.

The ContextAssembler selects only records relevant to the current project, rule IDs, and unresolved findings. It applies a token/byte budget, preserves source citations, and labels untrusted repository text. Memory writes are never inferred from an LLM response alone; they require a structured observation and, for user-preference or policy changes, HITL approval.

## RAG and lightweight evidence graph

The current local rule corpus remains the source of truth. The next RAG slice adds:

- versioned chunk manifests and incremental indexing by content hash;
- exact, BM25, vector, and hybrid retrieval with deterministic reranking;
- a relation index containing `rule -> checker`, `rule -> evidence kind`, `finding -> rule`, `fix step -> rule`, and `run -> evidence` edges;
- graph-assisted retrieval for multi-hop questions such as “which rule explains this finding and which fix validates it?”;
- graph/text fallback to BM25 when embeddings or graph indexes are unavailable.

The graph is a derived index. Every answer still cites the original rule chunk and local source; a graph edge cannot invent evidence. Incremental updates rebuild only affected nodes and invalidate dependent summaries by content hash.

## Quantitative evaluation contract

The offline eval expands the existing six metrics with fixed golden cases for:

| Metric | Definition | Required evidence |
| --- | --- | --- |
| Tool-call validity | valid schema, allowed tool, correct arguments / all proposed calls | trace + expected call sequence |
| Guardrail precision | correctly blocked unsafe calls / all unsafe calls | rejection reason and policy ID |
| HITL gate recall | required approvals paused / all approval-required cases | approval ledger and route |
| Resume success | runs reaching the same final state after injected crash / crash cases | event replay and final digest |
| Duplicate side effects | repeated side effects after retry | must be zero in deterministic fixtures |
| Evidence groundedness | cited claims whose rule/source/evidence IDs match expected | citation-level golden labels |
| Loop termination | runs stopping within budget without unsafe continuation | route history and budget ledger |
| Cost/latency | tool count, retry count, wall time, optional token/cost estimate | trace aggregates |
| Human correction impact | resolved/new/unchanged findings after approved changes | before/after verifier output |

Each metric has positive, negative, crash, stale-approval, prompt-injection, malformed-output, and budget-exhaustion cases. FakeLLM and fixed embeddings validate mechanics; provider quality remains explicitly opt-in and is never claimed from offline scores.

## Implementation order and acceptance gates

### Slice 1: Durable Tool Loop

Create the store, event reducer, checkpointed loop controller, tool registry, idempotency handling, and resume/corruption tests. Existing graph tools remain usable through adapters. Acceptance: an injected process crash resumes without duplicate scan/tool side effects and produces the same deterministic final digest.

### Slice 2: HITL and Guardrails

Add approval request/decision schemas, pending/resume API and CLI surfaces, pre/post policy gates, path/secret/network/budget guards, and redacted approval audit artifacts. Acceptance: every unsafe fixture pauses or blocks; no approval can be replayed with changed arguments.

### Slice 3: Eval and observability

Add the expanded golden cases, trace aggregation, deterministic replay command, and benchmark report. Acceptance: all required metrics are reported with denominators, per-case evidence, and explicit limitations.

### Slice 4: Lightweight GraphRAG and memory

Add versioned project memory, relation index, incremental graph updates, context budgets, and graph/text fallback. Acceptance: multi-hop evidence cases improve or remain equal to baseline without lowering citation accuracy or offline determinism.

## Non-goals

- No autonomous modification of a reviewed repository.
- No implicit network access or secret resolution.
- No claim of production-grade semantic quality from FakeLLM/fake embeddings.
- No external workflow/database dependency in the first slice.
- No replacement of the deterministic ReleaseGuard checker and decision core.

## Review checklist

- [ ] State transitions, event schemas, and resume invariants are accepted.
- [ ] Tool side-effect classes and approval scopes are accepted.
- [ ] Default read-only/offline boundary is accepted.
- [ ] Guardrail outcomes and fail-closed behavior are accepted.
- [ ] Evaluation denominators and failure fixtures are accepted.
- [ ] Slice 1 is approved for implementation in a separate plan.
