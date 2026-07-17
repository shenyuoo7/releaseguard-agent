# ReleaseGuard Agent Implementation Status

Last verified: 2026-07-17 (final launcher closeout)

This page is the evidence-backed status of the local repository. A directory,
class name, roadmap item, or resume keyword is not treated as implemented
unless reachable code and tests support it.

## Current product boundary

The current product entry points are the CLI and a synchronous FastAPI app:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m releaseguard_agent.cli.main
.\.venv\Scripts\python.exe -m uvicorn releaseguard_agent.api.app:app --host 127.0.0.1 --port 8000
```

The CLI delegates its business workflow to `ReleaseReviewService`. The service
runs deterministic checkers once, builds `CheckResult` records, applies the
deterministic blocking policy, and can write reports, a release checklist,
deterministic release advice, and a run-level trace from the same scan.

Every review now attaches exact rule evidence to its structured result. The
CLI also exposes `search-rules` with exact, BM25, vector, and hybrid modes.
Vector retrieval uses a real LlamaIndex `VectorStoreIndex`; offline tests use a
fixed fake embedding, while product configuration can build an OpenAI-compatible
embedding provider. Missing embedding configuration degrades explicitly to
BM25 without a network request.

The repository also contains a provider-neutral LLM protocol,
`FakeLLMClient`, `ReleaseRiskAnalysisAgent`, an OpenAI-compatible provider
factory, and optional CLI LLM analysis. A normal CLI/API run does not resolve
provider configuration, require a key, or make an LLM network request. Only
the explicit `--llm-analysis-output-dir` path can invoke a configured client.

The `agent-review` CLI command invokes a compiled LangGraph `StateGraph` through
`ReleaseAgentWorkflowService`. Graph nodes call typed scan, retrieval, guarded
risk, and fix-plan tools. Conditional edges skip unnecessary work for clean
projects, supplement missing evidence, request manual review when evidence is
still insufficient, and route LLM failures through a deterministic fallback.

## Capability status

| Capability | Status | Current evidence and boundary |
| --- | --- | --- |
| Checker execution framework | COMPLETE | `CheckerRunner` executes the registered deterministic checkers and converts checker errors into structured results. |
| Unified review service | COMPLETE | `ReleaseReviewService.review()` validates the target, runs one checker pass, aggregates results, and coordinates all current artifact writers. The CLI calls this service. |
| Python release checks | COMPLETE | Dependency, environment example, test structure, pytest configuration, and optional pytest execution checks are registered. |
| FastAPI and Flask project checks | PARTIAL | Static dependency and AST-based framework signals are checked; this is not a ReleaseGuard FastAPI service. |
| Docker review | COMPLETE | Target Dockerfile and Compose intent are inspected statically; ReleaseGuard also has a non-root local demo API image. The image is not an untrusted-code sandbox. |
| Structured reports and checklist | COMPLETE | The CLI can write Markdown and JSON reports, a release checklist, and deterministic advice. |
| Rule evidence retrieval | COMPLETE | Structured rule chunks support exact rule ID, BM25 Top-K, real LlamaIndex vector indexing, hybrid reciprocal-rank fusion, chunk deduplication, and deterministic token-overlap reranking. Every returned evidence record carries rule/source/chunk and score provenance. |
| Embedding provider | PARTIAL | Product code can build a configurable OpenAI-compatible embedding provider, and unavailable configuration falls back to BM25. Offline fake embeddings verify mechanics, not real semantic retrieval quality; real provider interoperability remains opt-in and unverified. |
| Deterministic release decision | COMPLETE | `CheckResult.should_block_release` remains authoritative and is reused by deterministic decision code. |
| LLM abstraction and FakeLLM | COMPLETE | Provider-neutral messages/responses/client protocol and deterministic fake client have unit tests. |
| Optional LLM risk analysis | COMPLETE | CLI can enrich an existing deterministic review through `LLMReviewService`; FakeLLM covers the product path offline and deterministic facts remain authoritative. |
| OpenAI-compatible adapter | COMPLETE | Explicit provider/model/base URL/timeout environment configuration builds the lazy SDK adapter; missing key falls back to deterministic mode and errors are sanitized. Real network interoperability is optional and not asserted by offline tests. |
| Trace | COMPLETE | Existing run traces remain available; Agent and verification flows additionally record redacted node/tool/retrieval/LLM events, route history, provenance IDs, latency, optional token usage, artifacts, errors, and before/after deltas. |
| Unit and integration tests | COMPLETE | 318 unit tests and 27 CLI/API/launcher integration tests pass at final launcher closeout. |
| E2E and eval system | COMPLETE | A real Uvicorn health smoke test and a fixed offline golden-case eval cover six required metrics. FakeLLM/fixed embeddings prove repeatability and wiring, not provider or semantic quality. |
| FastAPI product API | COMPLETE | `GET /health`, `POST /reviews`, and `POST /verifications` are real synchronous routes with strict schemas, safe path policy, uniform errors, TestClient integration, and Uvicorn health smoke coverage. |
| Agent tools and LangGraph | COMPLETE | Reachable tool wrappers are called by a typed `StateGraph`; the graph has normal and conditional edges, is compiled, invoked by a service and CLI, and has four distinct tested routes. |
| Role-based multi-agent workflow | COMPLETE | Evidence, Risk, Fix Planner, and Verifier have independent input/output dataclasses, execute as distinct graph nodes, and transfer typed state. One LLM instance may be shared, but deterministic policy resolves conflicts. |
| User-applied-fix verification | COMPLETE | CLI/API call `ReleaseVerificationService`, scan separate before/after snapshots, run the Verifier Agent, report resolved/new/unchanged, and use the after scan for the final decision. No repository is modified. |
| Docker packaging | COMPLETE | The non-root demo image builds on Docker Desktop/Linux, serves all three API endpoints, runs as UID 10001, and reaches Docker health `healthy`. It remains a demo image, not an untrusted-code sandbox. |
| GitHub Actions | COMPLETE | Push run #1 for `yin/releaseguard-complete` completed successfully on Ubuntu: quality/tests/Eval and container smoke both passed. |
| Windows one-click entry | COMPLETE | Root `ReleaseGuard.bat` opens the tested Chinese PowerShell menu for review, local API docs, before/after verification, demos, and environment repair. Core operations call existing CLI/API boundaries. |

## Current main flow

```text
CLI or POST /reviews
-> ReleaseReviewService
-> default checker composition
-> CheckerRunner
-> deterministic CheckResult records
-> exact rule evidence attached to the review result
-> deterministic release policy
-> optional report/checklist/advice/trace writers
-> optional configured LLMReviewService analysis (CLI flag only)
```

The standalone `search-rules` CLI reaches exact/BM25/vector/hybrid retrieval.
The LLM risk-analysis service is still outside the default review flow.

The optional graph flow is:

```text
agent-review -> ReleaseAgentWorkflowService -> compiled StateGraph
-> scan tool
-> clean: finalize
-> blocking: evidence tool -> [supplement/manual-review | risk tool]
-> [LLM failure: deterministic fallback] -> fix-plan tool -> final state
```

## Test baseline

Static test inventory before M0 modifications:

- 43 `test_*.py` files
- 42 unit-test files
- 1 integration-test file
- 0 E2E test files

Comparable pre-change verification on Python 3.11.9:

| Suite | Result |
| --- | --- |
| LLM, Agent, and CLI focused selection | 46 passed, 0 failed, 0 skipped |
| `tests/unit` | 259 passed, 0 failed, 0 skipped |
| `tests/integration` | 13 passed, 0 failed, 0 skipped |
| Full suite | 272 passed, 0 failed, 0 skipped |

The commands set `PYTHONDONTWRITEBYTECODE=1`, point `TEMP`, `TMP`, and
`TMPDIR` to a new E-drive directory, disable pytest's cache provider, and use a
unique E-drive `--basetemp`:

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp <unique-e-drive-path> <focused-test-files>
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp <unique-e-drive-path> tests\unit
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp <unique-e-drive-path> tests\integration
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp <unique-e-drive-path>
```

Post-change M0 verification on the same interpreter and equivalent selections:

| Suite | Result |
| --- | --- |
| LLM, Agent, and CLI focused selection | 50 passed, 0 failed, 0 skipped |
| `tests/unit` | 263 passed, 0 failed, 0 skipped |
| `tests/integration` | 13 passed, 0 failed, 0 skipped |
| Full suite | 276 passed, 0 failed, 0 skipped |

The four additional passing tests cover explicit falsy SDK injection,
base-URL/timeout construction, missing-SDK handling, and sanitized SDK request
errors.

M1 verification added five tests and established lint/type-check commands:

| Suite/check | Result |
| --- | --- |
| M1 focused service/CLI/integration selection | 36 passed |
| `tests/unit` | 268 passed |
| `tests/integration` | 13 passed |
| Full suite | 281 passed |
| Ruff (`src tests`) | Passed |
| Mypy (`src/releaseguard_agent`) | 57 source files, no issues |

M2 verification:

| Suite/check | Result |
| --- | --- |
| API focused including real Uvicorn smoke | 14 passed |
| `tests/unit` | 273 passed |
| `tests/integration` | 21 passed |
| `tests/e2e` | 1 passed |
| Full suite | 295 passed |
| Ruff (`src tests`) | Passed |
| Mypy (`src/releaseguard_agent`) | 61 source files, no issues |

M3 verification:

| Suite/check | Result |
| --- | --- |
| LLM factory/adapter/service/CLI focused | 29 passed |
| `tests/unit` | 285 passed |
| `tests/integration` | 21 passed |
| `tests/e2e` | 1 passed |
| Full suite | 307 passed |
| Ruff | Passed |
| Mypy | 63 source files, no issues |

M4 verification:

| Suite/check | Result |
| --- | --- |
| RAG/service/API focused selection | 38 passed |
| `tests/unit` | 293 passed |
| `tests/integration` | 21 passed |
| `tests/e2e` | 1 passed |
| Full suite | 315 passed |
| Ruff | Passed |
| Mypy | 69 source files, no issues |

M5 verification:

| Suite/check | Result |
| --- | --- |
| Agent-tool/LangGraph/CLI focused selection | 8 passed |
| Existing CLI/Agent/service boundary selection | 29 passed |
| `tests/unit` | 301 passed |
| `tests/integration` | 21 passed |
| `tests/e2e` | 1 passed |
| Full suite | 323 passed |
| Ruff | Passed |
| Mypy | 74 source files, no issues |

M6 verification:

| Suite/check | Result |
| --- | --- |
| Role/verification/LLM/API focused selection | 39 passed |
| Additional role delta selection | 3 passed |
| `tests/unit` | 307 passed |
| `tests/integration` | 21 passed |
| `tests/e2e` | 1 passed |
| Full suite | 329 passed |
| Ruff | Passed |
| Mypy | 76 source files, no issues |

M7 verification:

| Suite/check | Result |
| --- | --- |
| Trace/eval/workflow focused selection | 16 passed |
| `tests/unit` | 312 passed |
| `tests/integration` | 21 passed |
| `tests/e2e` | 1 passed |
| Full suite | 334 passed |
| Offline golden eval | Six required metrics = 1.0 on fixed cases |
| Ruff | Passed |
| Mypy | 79 source files, no issues |

M8 verification:

| Suite/check | Result |
| --- | --- |
| Docker/CI/smoke focused selection | 4 passed |
| `tests/unit` | 316 passed |
| `tests/integration` | 21 passed |
| `tests/e2e` | 1 passed |
| Full suite | 338 passed |
| Offline golden eval | Six required metrics = 1.0 on fixed cases |
| Ruff | Passed |
| Mypy | 79 source files, no issues |
| Docker build/health | Passed: image built, API health/review/verification passed, UID 10001, Docker health `healthy` |
| Remote GitHub Actions | Later verified by successful push run #1 on commit `469dc48` |

Final one-click launcher closeout verification:

| Suite/check | Result |
| --- | --- |
| Launcher focused | 11 passed |
| `tests/unit` | 318 passed |
| `tests/integration` | 27 passed |
| `tests/e2e` | 1 passed |
| Full suite | 346 passed |
| Environment repair health | Python 3.11.9, 8 checkers, dependencies already satisfied |
| Docker build/runtime | Passed: Linux engine, health `healthy`, UID 10001, clean HTTP review allowed |
| Remote GitHub Actions baseline | Run #1 on commit `469dc48` succeeded; both jobs green |

## Known risks

- The declared OpenAI SDK is installed in the project `.venv`, but installing
  the adapter dependency does not mean provider configuration or product
  integration is complete.
- The API defaults to reviewing paths under the repository root. Other trusted
  roots require explicit `create_app(allowed_project_roots=...)` configuration.
- Verification is synchronous and stateless: callers must preserve and submit
  both baseline and modified snapshots; ReleaseGuard does not persist runs.
- Real OpenAI-compatible network execution remains unverified and must be
  explicit and opt-in; offline FakeLLM/fake-SDK tests do not establish provider
  availability or semantic answer quality.
- The repository does not have installable package metadata. Direct CLI use
  currently requires `PYTHONPATH=src`.
- `TestStructureChecker` ignores any absolute path containing an `outputs`
  component. Test target projects placed below the repository's `outputs/`
  tree can therefore produce false missing-test findings.
- Reviewing this repository root as one target sees framework imports inside
  bundled `sample_projects/`; the Flask detector then reports a false missing
  root dependency. Review individual sample/project roots instead. Project
  boundary detection for monorepos remains future work.
- Fake embedding tests establish deterministic integration behavior, not the
  semantic quality or availability of a real embedding endpoint.
- The current reranker is transparent deterministic token overlap rather than
  a learned cross-encoder.
- The golden eval is intentionally small. A perfect score on its fixed cases
  does not establish broad retrieval, LLM, or production embedding quality.
- There is no coverage percentage or remote Linux CI result.
- Docker has been validated locally on Docker Desktop/Linux. This does not
  establish behavior on every Docker Engine version or provide untrusted-code
  isolation.

## Approved M0-M8 route

| Milestone | Goal | Current state |
| --- | --- | --- |
| M0 | Truthful baseline and repository hygiene | Complete; checkpoint `f0402cb` |
| M1 | Unified `ReleaseReviewService` | Complete and verified locally |
| M2 | FastAPI and shared CLI/API use | Complete and verified locally; verification semantics deferred to M6 |
| M3 | Production OpenAI-compatible provider configuration | Complete and offline verified |
| M4 | BM25, vector retrieval, fusion, and reranking | Complete and verified locally |
| M5 | Agent tools and LangGraph conditional workflow | Complete and verified locally |
| M6 | Role-based Agents and user-applied-fix verification | Complete and verified locally |
| M7 | Execution-level trace and minimum evals | Complete and verified locally |
| M8 | Docker, GitHub Actions, documentation, and interview training | Complete; local Docker and remote Ubuntu Actions verified |

Every milestone is independently implemented, verified, reviewed, documented,
and saved as a local checkpoint. The approved completion run continues to the
next milestone automatically and never pushes to a remote.

## Resume claim truth table

| Claim | Current status | Truthful current wording |
| --- | --- | --- |
| Python release review | COMPLETE | Deterministic Python release-readiness CLI and tests. |
| FastAPI and Flask support | PARTIAL | Static project detection and selected release checks. |
| Docker | COMPLETE | Target-project inspection and a locally validated non-root ReleaseGuard demo image are implemented; it is not a production sandbox. |
| Pytest | COMPLETE | Test structure/configuration checks, optional execution, and project test suite. |
| Checker framework | COMPLETE | Registered checker execution with structured results and error isolation. |
| Rule knowledge base | COMPLETE | The current release-rule scope has structured rule metadata, local trusted-source documents, and provenance-preserving chunks. |
| RAG | COMPLETE | Exact/BM25/LlamaIndex vector/hybrid retrieval with provenance, graceful fallback, and deterministic reranking; real semantic quality remains unbenchmarked. |
| LLM API | COMPLETE | Configurable optional CLI path and safe no-key deterministic fallback are code/test backed; real provider calls remain opt-in and environment-dependent. |
| Agent decision | COMPLETE | Deterministic policy is product-wired and authoritative; optional evidence-citing LLM analysis is reachable through CLI/graph paths. |
| Structured reports | COMPLETE | Report, checklist, deterministic advice, and run trace artifacts. |
| FastAPI API | COMPLETE | Health, review, and before/after verification are synchronous, reachable, path-checked, and covered by TestClient; health also has a real Uvicorn smoke test. |
| BM25/vector/rerank/LlamaIndex | COMPLETE | Reachable exact/BM25/vector/hybrid service and CLI; LlamaIndex owns vector indexing, candidates are fused/deduplicated, and a deterministic reranker produces final scores. |
| Agent tools/LangGraph | COMPLETE | Real tool invocations and a compiled `StateGraph` with normal/conditional edges and tested clean, blocking, evidence-gap, and LLM-fallback routes. |
| Multi-agent workflow | COMPLETE | Four role Agents have separate contracts and graph nodes; state and Evidence IDs are explicit, and deterministic policy wins conflicts. |
| Complete repair loop | COMPLETE | Scan, evidence, decision, fix plan, user manual edit, rescan, Verifier before/after delta, and final deterministic decision are reachable; no automatic edits. |
| Linux/GitHub Actions | COMPLETE | Ubuntu push run #1 executed quality, unit/integration/E2E, Eval, Docker build, and health smoke successfully on commit `469dc48`. |

Until a later milestone changes this page with code and test evidence, missing
or partial capabilities must not be described as complete.
