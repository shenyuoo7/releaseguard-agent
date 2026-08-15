# Durable Tool Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local, restartable Agent runtime that executes the existing ReleaseGuard tools through typed contracts, durable checkpoints, idempotency, bounded loops, and fail-closed recovery.

**Architecture:** Keep `ReleaseReviewService`, the existing role Agents, and the compiled LangGraph as business components. Add a small `runtime/` package with a SQLite WAL event store under the repository's E: `.runtime/agent_runs` directory, a reducer that materializes `AgentRunState`, a registry of typed tools, and a `LoopController` that commits one event before and after each tool action. The controller adapts current read-only tools first; HITL and write-capable tools remain explicit follow-up slices.

**Tech Stack:** Python 3.11 standard library (`sqlite3`, `json`, `hashlib`, `dataclasses`, `pathlib`), existing LangGraph/tool classes, pytest, Ruff, mypy. No new C: runtime data and no external workflow service.

## Global Constraints

- All runtime state, temporary data, checkpoints, and test basetemp paths must remain on E: under `.runtime/` or a unique E: directory.
- Default tool execution is read-only, offline, and restricted to the canonical project root already accepted by `ProjectPathPolicy`.
- The deterministic checker result and `CheckResult.should_block_release` remain authoritative; no planner or LLM may change them.
- A completed tool call is never executed twice for the same `(run_id, step_index, idempotency_key)`.
- Unknown event kinds, schema versions, stale compare-and-swap writes, malformed payloads, path escapes, and budget violations fail closed.
- Do not modify a reviewed project automatically; Slice 1 only adapts existing read-only tools.

---

### Task 1: Define durable runtime models and the event reducer

**Files:**
- Create: `src/releaseguard_agent/runtime/__init__.py`
- Create: `src/releaseguard_agent/runtime/models.py`
- Create: `src/releaseguard_agent/runtime/reducer.py`
- Test: `tests/unit/test_runtime_models.py`
- Test: `tests/unit/test_runtime_reducer.py`

**Interfaces:**
- `AgentRunStatus = Literal["CREATED", "RUNNING", "WAITING_HITL", "PAUSED", "RECOVERING", "FAILED", "COMPLETED", "CANCELLED"]`.
- `AgentRunState` is a frozen dataclass with `run_id`, `schema_version`, `task_kind`, `project_root`, `status`, `step_index`, `plan_digest`, `checkpoint_event_id`, `budget`, `pending_approval_id`, `memory_refs`, and `decision_digest`.
- `RunEvent` is a frozen dataclass with `event_id`, `run_id`, `sequence`, `event_kind`, `payload`, `payload_sha256`, and `created_at_utc`.
- `reduce_event(state: AgentRunState | None, event: RunEvent) -> AgentRunState` validates the allowed transition and returns the next immutable state.
- `canonical_json(value: object) -> str` uses sorted keys, compact separators, and UTF-8-safe JSON; `sha256_json(value: object) -> str` hashes those bytes.

- [ ] **Step 1: Write failing model tests**

```python
def test_run_state_round_trips_with_canonical_json():
    state = AgentRunState.created(
        run_id="run-1",
        task_kind="REVIEW",
        project_root=Path(r"E:\repo"),
        budget=RunBudget(max_steps=4, max_tool_calls=4, max_retries=1),
    )
    assert AgentRunState.from_dict(state.to_dict()) == state
    assert sha256_json(state.to_dict()) == sha256_json(state.to_dict())
```

- [ ] **Step 2: Run the focused test and confirm it fails**

Run: `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\unit\test_runtime_models.py -q`

Expected: FAIL because `releaseguard_agent.runtime` does not yet expose the model types.

- [ ] **Step 3: Implement the models and canonical serialization**

Use dataclasses with explicit `to_dict`/`from_dict` methods. Reject missing required fields, unknown status values, negative budgets, non-absolute project roots, and schema versions other than `1`.

- [ ] **Step 4: Add reducer transition tests**

Cover `RUN_CREATED -> PLAN_PROPOSED -> TOOL_REQUESTED -> TOOL_STARTED -> TOOL_COMPLETED -> EVALUATION_RECORDED -> CHECKPOINT_COMMITTED -> RUN_COMPLETED`; `TOOL_REQUESTED -> WAITING_HITL`; retryable `TOOL_FAILED -> TOOL_REQUESTED`; stale/out-of-order/duplicate/unknown events; and terminal-state mutation rejection.

- [ ] **Step 5: Implement the reducer**

Keep the transition table explicit. `CHECKPOINT_COMMITTED` may advance `step_index` only by one; `TOOL_COMPLETED` must carry the same `idempotency_key` as its preceding request; `RUN_COMPLETED` requires a deterministic decision digest.

- [ ] **Step 6: Run focused model/reducer tests**

Run: `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\unit\test_runtime_models.py tests\unit\test_runtime_reducer.py -q`

Expected: all tests pass with malformed and illegal events rejected.

---

### Task 2: Implement the SQLite WAL event store and crash-safe CAS

**Files:**
- Create: `src/releaseguard_agent/runtime/store.py`
- Test: `tests/unit/test_runtime_store.py`

**Interfaces:**
- `AgentRunStore(root: Path)` creates only `root / "agent_runs.sqlite3"` and its WAL files.
- `create_run(state: AgentRunState) -> RunEvent` writes `RUN_CREATED` at sequence `0`.
- `append(run_id: str, expected_sequence: int, event_kind: str, payload: Mapping[str, object]) -> RunEvent` performs one SQLite transaction with a compare-and-swap on the latest sequence.
- `load_state(run_id: str) -> AgentRunState` replays events through `reduce_event` and rejects any invalid chain.
- `events(run_id: str) -> tuple[RunEvent, ...]` returns canonical sequence order.
- `close() -> None` closes the connection; no global connection is shared between runs.

- [ ] **Step 1: Write failing store tests**

```python
def test_store_replays_and_rejects_stale_sequence(tmp_path):
    store = AgentRunStore(tmp_path / "runtime")
    created = store.create_run(make_state())
    store.append(created.run_id, 0, "PLAN_PROPOSED", {"plan_digest": "p"})
    with pytest.raises(StaleSequenceError):
        store.append(created.run_id, 0, "TOOL_REQUESTED", {"idempotency_key": "k"})
    assert store.load_state(created.run_id).status == "RUNNING"
```

- [ ] **Step 2: Run the focused store test and confirm it fails**

Run: `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\unit\test_runtime_store.py -q`

Expected: FAIL because `AgentRunStore` does not exist.

- [ ] **Step 3: Implement schema and transactions**

Create `run_events(run_id, sequence, event_id, event_kind, payload_json, payload_sha256, created_at_utc, PRIMARY KEY(run_id, sequence))` and `run_snapshots(run_id PRIMARY KEY, sequence, state_json, state_sha256)`. Enable WAL, foreign keys, busy timeout, and deterministic row ordering. Commit event plus snapshot in one transaction; rollback on reducer or hash failure.

- [ ] **Step 4: Add crash and corruption tests**

Inject a failure before commit, after event insert but before snapshot update, after snapshot update but before commit, corrupt one payload hash, delete one event, and insert a duplicate sequence. Every case must either replay exactly or fail closed without advancing the visible state.

- [ ] **Step 5: Run the store tests**

Run: `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\unit\test_runtime_store.py -q`

Expected: all CAS, replay, rollback, and corruption tests pass; SQLite files are created only under the E: test directory.

---

### Task 3: Add the typed tool registry, budgets, and idempotency ledger

**Files:**
- Create: `src/releaseguard_agent/runtime/tools.py`
- Create: `src/releaseguard_agent/runtime/guardrails.py`
- Modify: `src/releaseguard_agent/agent_tools/release_tools.py`
- Test: `tests/unit/test_runtime_tools.py`
- Test: `tests/unit/test_runtime_guardrails.py`

**Interfaces:**
- `ToolSpec(name, version, input_schema, output_schema, side_effect, allowed_roots, network_policy, timeout_ms, max_retries, budget_cost, required_approval_scope)`.
- `ToolCall(tool_name, tool_version, canonical_args, args_sha256, run_id, step_index, idempotency_key)`.
- `ToolRegistry.register(spec, handler)`, `ToolRegistry.get(name, version)`, and `ToolRegistry.execute(call, context) -> ToolResult`.
- `ToolResult(status, output, output_sha256, idempotency_key, error_type, redacted_summary)`.
- `GuardrailDecision = ALLOW | REQUIRE_HITL | RETRY | BLOCK | QUARANTINE`.
- `GuardrailEngine.check(call, spec, context) -> GuardrailDecision`.

- [ ] **Step 1: Write failing contract tests**

Cover unknown tools, malformed arguments, path traversal, `.env` arguments, network requests in offline mode, output-schema mismatch, exceeded steps/retries/bytes, duplicate idempotency keys, and successful read-only `scan_project` execution.

- [ ] **Step 2: Run the contract tests and confirm they fail**

Run: `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\unit\test_runtime_tools.py tests\unit\test_runtime_guardrails.py -q`

Expected: FAIL because the registry and guardrail engine are not implemented.

- [ ] **Step 3: Implement contracts and the registry**

Adapt the four existing read-only tools without changing their public behavior. Canonicalize arguments before hashing; store completed idempotency results in the run store; never invoke a handler if the same key already has a committed result.

- [ ] **Step 4: Implement guardrail checks**

Use `ProjectPathPolicy` for roots, reject secrets using the existing redaction patterns, enforce network-off by default, and convert approval-required side effects to `REQUIRE_HITL` rather than silently blocking or executing.

- [ ] **Step 5: Run tool/guardrail tests and existing tool tests**

Run: `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\unit\test_runtime_tools.py tests\unit\test_runtime_guardrails.py tests\unit\test_agent_tools.py -q`

Expected: all new and existing tool tests pass; deterministic facts are unchanged.

---

### Task 4: Implement the bounded LoopController and resume path

**Files:**
- Create: `src/releaseguard_agent/runtime/loop.py`
- Modify: `src/releaseguard_agent/services/agent_workflow_service.py`
- Modify: `src/releaseguard_agent/workflows/release_graph.py`
- Test: `tests/unit/test_loop_controller.py`
- Test: `tests/integration/test_agent_resume.py`

**Interfaces:**
- `LoopController(store, registry, evaluator, tracer).run(request) -> AgentRunResult`.
- `LoopController.resume(run_id, approval=None) -> AgentRunResult`.
- `LoopRequest(project_path, task_kind, budget, force_ai_review=False, baseline_review=None)`.
- `AgentRunResult(run_id, status, state, review, route_history, metrics, trace_path)`.

- [ ] **Step 1: Write failing loop tests**

Add deterministic cases for clean review, blocking review, evidence-gap/manual route, one transient tool retry, injected crash after `TOOL_REQUESTED`, resume from checkpoint, budget exhaustion, and terminal run immutability.

- [ ] **Step 2: Run the loop tests and confirm they fail**

Run: `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\unit\test_loop_controller.py tests\integration\test_agent_resume.py -q`

Expected: FAIL because no durable controller or resume API exists.

- [ ] **Step 3: Implement one-step commit ordering**

For every iteration append `TOOL_REQUESTED`, apply the guardrail, append `TOOL_STARTED`, execute or pause, append `TOOL_COMPLETED`/`TOOL_FAILED`, run the deterministic evaluator, and commit the next checkpoint. Use the existing graph only as an adapter for role execution; route history remains an observation, not the source of truth.

- [ ] **Step 4: Add resume and recovery**

On startup, load the event chain, reconcile incomplete tool requests through the idempotency ledger, mark the run `RECOVERING`, and continue only when the evaluator marks the failure retryable. Emit `RUN_RECOVERED` with the previous checkpoint digest.

- [ ] **Step 5: Run loop, resume, and existing graph tests**

Run: `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\unit\test_loop_controller.py tests\integration\test_agent_resume.py tests\unit\test_langgraph_workflow.py -q`

Expected: injected crashes produce the same final deterministic decision and no duplicate tool execution.

---

### Task 5: Add runtime evaluation metrics and replay artifacts

**Files:**
- Modify: `src/releaseguard_agent/evaluation/runner.py`
- Modify: `src/releaseguard_agent/observability/execution_trace.py`
- Create: `evals/datasets/agent_runtime_cases.json`
- Test: `tests/unit/test_agent_runtime_evaluation.py`

**Interfaces:**
- `EvaluationRunner.run()` adds `tool_call_validity`, `guardrail_precision`, `resume_success_rate`, `duplicate_side_effect_rate`, `hitl_gate_recall`, `loop_termination_rate`, `average_tool_calls`, and `average_retries`.
- `ExecutionTracer` records `run_id`, `step_index`, `idempotency_key`, `guardrail_decision`, `approval_id`, checkpoint sequence, and redacted cost/latency fields.
- `agent_runtime_cases.json` contains positive, malformed, crash, stale-approval, prompt-injection, retry, and budget-exhaustion cases with expected events and final status.

- [ ] **Step 1: Write failing metric tests**

Assert denominators are non-zero for the fixture set, all required metrics are present, duplicate side effects equal zero in deterministic cases, and replay produces the same final digest.

- [ ] **Step 2: Run the metric tests and confirm they fail**

Run: `.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\unit\test_agent_runtime_evaluation.py -q`

Expected: FAIL because the runtime metrics and fixture dataset do not exist.

- [ ] **Step 3: Implement metrics from traces and event rows**

Compute each metric from explicit case denominators, not from absence of errors. Include per-case event evidence and limitations for FakeLLM/fake embeddings.

- [ ] **Step 4: Add replay command/service coverage**

Replay a stored run from its event log and compare `decision_digest`, `route_history`, and tool idempotency outcomes. Reject a modified payload hash.

- [ ] **Step 5: Run focused eval, full tests, lint, type checks, and diff checks**

Run with unique E: paths:

```powershell
$env:PYTHONDONTWRITEBYTECODE = "1"
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp E:\ReleaseGuard_runtime_test tests\unit\test_agent_runtime_evaluation.py tests\unit\test_loop_controller.py tests\unit\test_runtime_store.py
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp E:\ReleaseGuard_runtime_test tests\unit
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp E:\ReleaseGuard_runtime_test tests\integration
.\.venv\Scripts\python.exe -m ruff check src tests
.\.venv\Scripts\python.exe -m mypy
git diff --check
```

Expected: focused and existing suites pass; metrics report explicit values; no command creates files on C:.

---

## Self-review checklist

- The plan preserves the existing deterministic checker and LangGraph business paths.
- Every new interface has a concrete file and test owner.
- Crash, stale-CAS, duplicate, malformed, budget, prompt-injection, and approval-replay failures have explicit tests.
- Slice 1 does not add project-write or network tools, so HITL enforcement can be tested before risky capabilities exist.
- Runtime files and pytest basetemp are explicitly E:-scoped.
- No task claims provider semantic quality from FakeLLM or fixed embeddings.
