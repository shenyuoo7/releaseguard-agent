# ReleaseGuard Agent Implementation Status

Last verified: 2026-07-16

This page is the evidence-backed status of the local repository. A directory,
class name, roadmap item, or resume keyword is not treated as implemented
unless reachable code and tests support it.

## Current product boundary

The only current product entry point is the CLI:

```powershell
$env:PYTHONPATH = "src"
.\.venv\Scripts\python.exe -m releaseguard_agent.cli.main
```

The CLI runs deterministic checkers, builds `CheckResult` records, applies the
deterministic blocking policy, and can write reports, a release checklist,
deterministic release advice, and a run-level trace.

The repository also contains a provider-neutral LLM protocol,
`FakeLLMClient`, a standalone `ReleaseRiskAnalysisAgent` and service, and an
OpenAI-compatible provider adapter. These components are not connected to the
CLI or another product entry point. A normal CLI run does not require an API
key and must not make an LLM network request.

## Capability status

| Capability | Status | Current evidence and boundary |
| --- | --- | --- |
| Checker execution framework | COMPLETE | `CheckerRunner` executes the registered deterministic checkers and converts checker errors into structured results. |
| Python release checks | COMPLETE | Dependency, environment example, test structure, pytest configuration, and optional pytest execution checks are registered. |
| FastAPI and Flask project checks | PARTIAL | Static dependency and AST-based framework signals are checked; this is not a ReleaseGuard FastAPI service. |
| Docker review | PARTIAL | Dockerfile and Compose intent are inspected statically; ReleaseGuard itself is not containerized. |
| Structured reports and checklist | COMPLETE | The CLI can write Markdown and JSON reports, a release checklist, and deterministic advice. |
| Rule evidence retrieval | PARTIAL | Exact local `rule_id` retrieval with source provenance exists. Natural-language retrieval, BM25, vectors, fusion, and reranking do not. |
| Deterministic release decision | COMPLETE | `CheckResult.should_block_release` remains authoritative and is reused by deterministic decision code. |
| LLM abstraction and FakeLLM | COMPLETE | Provider-neutral messages/responses/client protocol and deterministic fake client have unit tests. |
| Standalone LLM risk analysis | PARTIAL | Agent, writer, and service exist and use injected clients, but no product entry point calls them. |
| OpenAI-compatible adapter | PARTIAL | SDK adapter, configurable model/base URL/timeout, sanitized project exceptions, and fake-SDK tests exist; environment configuration and product wiring do not. |
| Trace | PARTIAL | CLI run arguments, inputs, outputs, environment summary, and decision summary are recorded; graph/tool/retrieval/LLM events are not. |
| Unit and integration tests | COMPLETE | Unit tests and 13 CLI sample-project integration tests exist. |
| E2E and eval system | MISSING | `tests/e2e` and `evals` do not contain complete executable suites or quality metrics. |
| FastAPI product API | MISSING | No `FastAPI()` application or product routes exist. |
| Agent tools and LangGraph | MISSING | No tool registry/executor, graph state, `StateGraph`, nodes, edges, or compiled workflow exists. |
| Role-based multi-agent workflow | MISSING | Evidence, Risk, Fix Planner, and Verifier roles are planned only. |
| User-applied-fix verification | MISSING | No baseline run comparison or rescan verification service exists. |
| Docker packaging and GitHub Actions | MISSING | No product Dockerfile or `.github/workflows` CI exists. |

## Current main flow

```text
CLI
-> default checker composition
-> CheckerRunner
-> deterministic CheckResult records
-> deterministic release policy
-> optional report/checklist/advice/trace writers
```

Exact rule evidence is used by the deterministic advice path. The standalone
LLM risk-analysis service is outside this flow.

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

## Known risks

- The current project `.venv` does not have the newly declared `openai` SDK
  installed. M0 does not mutate the environment; fake-SDK tests validate the
  adapter offline.
- No environment-driven provider factory exists, and the adapter is not wired
  into the CLI or `ReleaseRiskAnalysisService`.
- Real OpenAI-compatible network execution remains unverified and must be
  explicit in a later milestone.
- The repository does not have installable package metadata. Direct CLI use
  currently requires `PYTHONPATH=src`.
- `TestStructureChecker` ignores any absolute path containing an `outputs`
  component. Test target projects placed below the repository's `outputs/`
  tree can therefore produce false missing-test findings.
- There is no coverage percentage, E2E suite, retrieval benchmark, Agent eval,
  Linux CI result, or container smoke test.

## Approved M0-M8 route

| Milestone | Goal | Current state |
| --- | --- | --- |
| M0 | Truthful baseline and repository hygiene | Complete locally; awaiting user review |
| M1 | Unified `ReleaseReviewService` | Not started |
| M2 | FastAPI and shared CLI/API use | Not started |
| M3 | Production OpenAI-compatible provider configuration | Not started |
| M4 | BM25, vector retrieval, fusion, and reranking | Not started |
| M5 | Agent tools and LangGraph conditional workflow | Not started |
| M6 | Role-based Agents and user-applied-fix verification | Not started |
| M7 | Execution-level trace and minimum evals | Not started |
| M8 | Docker, GitHub Actions, documentation, and interview training | Not started |

Every milestone must stop after its own implementation and verification. The
next milestone starts only after explicit user confirmation.

## Resume claim truth table

| Claim | Current status | Truthful current wording |
| --- | --- | --- |
| Python release review | COMPLETE | Deterministic Python release-readiness CLI and tests. |
| FastAPI and Flask support | PARTIAL | Static project detection and selected release checks. |
| Docker | PARTIAL | Dockerfile and Compose static inspection only. |
| Pytest | COMPLETE | Test structure/configuration checks, optional execution, and project test suite. |
| Checker framework | COMPLETE | Registered checker execution with structured results and error isolation. |
| Rule knowledge base | PARTIAL | Local rule index, trusted sources, and exact evidence lookup. |
| RAG | PARTIAL | Exact rule evidence retrieval only; not semantic or hybrid RAG. |
| LLM API | PARTIAL | Offline-tested provider adapter exists but is not configured or product-wired. |
| Agent decision | PARTIAL | Deterministic advice is product-wired; standalone single-LLM analysis is not. |
| Structured reports | COMPLETE | Report, checklist, deterministic advice, and run trace artifacts. |
| FastAPI API | MISSING | Planned synchronous `/health`, `/reviews`, and `/verifications` endpoints. |
| BM25/vector/rerank/LlamaIndex | MISSING | Planned for M4. |
| Agent tools/LangGraph | MISSING | Planned for M5. |
| Multi-agent workflow | MISSING | Planned role nodes: Evidence, Risk, Fix Planner, and Verifier. |
| Complete repair loop | MISSING | Planned manual-edit rescan and before/after comparison; no automatic edits. |
| Linux/GitHub Actions | MISSING | Planned for M8. |

Until a later milestone changes this page with code and test evidence, missing
or partial capabilities must not be described as complete.
