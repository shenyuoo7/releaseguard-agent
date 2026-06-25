# ReleaseGuard Agent Phase Roadmap

This document defines the corrected project roadmap for ReleaseGuard Agent.

ReleaseGuard Agent must not stop as a deterministic release checker. The final
target is a multi-agent RAG system for software release review.

The current deterministic checker, rule evidence, report, release checklist,
and trace layers are still valuable. They are the tool layer, fact layer, and
evidence layer that future LLM Agents will use.

## Corrected product direction

The corrected direction is:

```text
deterministic core
-> LLM Agent layer
-> RAG knowledge enhancement
-> multi-agent orchestration
-> evals and backend delivery
```

The project should demonstrate capabilities relevant to mainstream Agent, RAG,
and LLM application development roles:

- LLM Agent development;
- RAG grounding and retrieval quality;
- workflow orchestration;
- multi-agent role design;
- tool calling over real engineering signals;
- prompt and structured-output design;
- evals and benchmark construction;
- observability for Agent behavior;
- FastAPI backend service delivery;
- Linux and CI engineering;
- data governance from historical runs and feedback.

## Current foundation

Current foundation status:

- Deterministic release-readiness checkers exist.
- Local rule evidence exists.
- Exact `rule_id` retrieval exists.
- Check-result enrichment exists.
- Deterministic release decision advice exists.
- Markdown and JSON report artifacts exist.
- Release checklist artifact output exists.
- Trace artifact output exists.
- CLI documentation exists.
- Current architecture documentation exists.
- The first deterministic-first ADR exists.

This foundation is enough to start the first LLM Agent layer. The project
should not keep adding checker families as the default next direction.

## Progress estimates

These estimates separate the deterministic foundation from the final job-aligned
Agent/RAG product.

| Area | Estimated progress | Notes |
| --- | ---: | --- |
| Deterministic CLI/checker foundation | 93-94% | Strong enough to support Agent work. |
| Current exact rule evidence layer | 70-75% | Good first RAG foundation, not semantic RAG yet. |
| Deterministic Agent advice layer | 65-70% | Useful as pre-LLM structured context. |
| LLM Agent capability | 5-10% | Architecture reserved, real provider layer not built yet. |
| LangGraph / workflow capability | 0-5% | Not implemented yet. |
| Multi-agent capability | 0-5% | Roles planned, not implemented yet. |
| Hybrid RAG / rerank capability | 0-5% | Exact retrieval exists, hybrid retrieval not built yet. |
| Evals / benchmark capability | 5-10% | Tests exist, Agent quality benchmark not built yet. |
| FastAPI backend capability | 0-5% | API package reserved, service not implemented yet. |
| Linux / CI capability | 0-5% | Local Windows validation exists, CI not implemented yet. |
| Final portfolio/job-aligned product | 35-40% | Strong base, but Agent/RAG/LLM/backend/evals remain. |

## Recommended build order

The preferred build order is:

```text
current deterministic foundation
-> LLM provider abstraction + FakeLLMClient tests
-> ReleaseRiskAnalysisAgent MVP
-> LangGraph multi-agent workflow
-> RAG upgrade to hybrid retrieval / rerank
-> FastAPI backend
-> evals / benchmark / feedback / Linux CI
```

This order avoids two mistakes:

- continuing to stack deterministic checkers until the project looks like only
  a release-checking utility;
- building a complex backend before the Agent/RAG capability is visible.

## Phase 1: Deterministic core

Status: mostly complete.

Purpose:

- collect engineering facts from a target project;
- produce structured release-readiness results;
- generate deterministic artifacts;
- provide reliable context for future LLM Agents.

Already implemented:

- checker abstraction;
- checker runner;
- default checker composition;
- Python/FastAPI/Flask/Docker/test/dependency/env checks;
- dependency scanners;
- framework detectors;
- report writer;
- release checklist writer;
- trace writer;
- CLI artifact flags;
- focused unit and integration tests.

Future work in this phase should be limited. New deterministic checkers should
be added only when they directly support an Agent eval case, backend workflow,
or clearly prioritized product requirement.

Acceptance standard:

- existing tests continue to pass;
- deterministic release status remains stable;
- checkers do not require LLM calls or network access;
- checker outputs remain structured and useful as Agent tool outputs.

## Phase 2: LLM provider abstraction

Status: next implementation phase.

Purpose:

- introduce real LLM application architecture without hard-coding a vendor;
- make Agent workflows testable without live credentials;
- create a seam for OpenAI or other providers later.

Recommended production modules:

```text
src/releaseguard_agent/llm/__init__.py
src/releaseguard_agent/llm/client.py
src/releaseguard_agent/llm/fake_client.py
```

Recommended tests:

```text
tests/unit/test_llm_client.py
tests/unit/test_fake_llm_client.py
```

Required concepts:

- `LLMClient` protocol or abstract interface;
- `LLMMessage` data structure;
- `LLMResponse` data structure;
- `FakeLLMClient` for deterministic tests;
- recorded prompts/responses for assertions;
- explicit errors for missing fake responses;
- no real API key required for unit tests.

Acceptance standard:

- fake model tests pass without network access;
- no secrets are required;
- no provider-specific SDK is required in the first slice;
- future real-provider integration can implement the same interface;
- the rest of the project does not import provider-specific code directly.

Why this helps Agent job alignment:

- shows LLM application architecture;
- demonstrates testable model boundaries;
- prepares for prompt and structured-output workflows;
- avoids building an untestable LLM demo.

## Phase 3: First LLM Agent MVP

Status: planned immediately after LLM provider abstraction.

Recommended Agent:

```text
ReleaseRiskAnalysisAgent
```

Purpose:

Use an LLM to analyze release risk from real engineering evidence, not from a
generic chat prompt.

Inputs:

- check results;
- enriched rule evidence;
- trace metadata;
- release report content;
- release checklist content;
- deterministic release decision advice.

Outputs:

```text
agent_risk_analysis.md
agent_decision.json
agent_fix_plan.md
```

Recommended production modules:

```text
src/releaseguard_agent/agents/llm_release_risk_analysis_agent.py
src/releaseguard_agent/agents/llm_release_risk_analysis_writer.py
src/releaseguard_agent/prompts/release_risk_analysis_prompt.md
```

Recommended tests:

```text
tests/unit/test_llm_release_risk_analysis_agent.py
tests/unit/test_llm_release_risk_analysis_writer.py
```

Responsibilities:

- build grounded prompt context from existing artifacts;
- ask the configured `LLMClient` for analysis;
- require structured output;
- preserve deterministic release status as the source of truth;
- cite rule evidence where possible;
- produce actionable repair planning;
- expose raw model output for traceability.

Non-responsibilities:

- no autonomous file edits;
- no hidden release-status override;
- no production dependency on a real provider;
- no multi-agent orchestration yet.

Acceptance standard:

- tests run with `FakeLLMClient`;
- prompt context includes check results and rule evidence;
- output can be parsed into stable fields;
- Markdown artifacts are deterministic for a given fake response;
- hallucination guard fields are present;
- missing evidence is surfaced rather than hidden.

Why this helps Agent job alignment:

- shows a real LLM Agent using project tools and evidence;
- demonstrates prompt construction and structured outputs;
- creates interview-ready discussion material around grounding and guardrails.

## Phase 4: Agent tools

Status: planned.

Purpose:

Expose deterministic project capabilities as tools that future Agents can call.

Candidate tools:

- run release checkers;
- run pytest;
- inspect git diff;
- parse Dockerfile;
- scan dependencies;
- retrieve rule evidence;
- read release report;
- read release checklist;
- read trace;
- summarize previous run history.

Recommended package:

```text
src/releaseguard_agent/tools/
```

Suggested modules:

```text
src/releaseguard_agent/tools/checker_tools.py
src/releaseguard_agent/tools/report_tools.py
src/releaseguard_agent/tools/rule_tools.py
src/releaseguard_agent/tools/trace_tools.py
src/releaseguard_agent/tools/git_tools.py
```

Acceptance standard:

- each tool has explicit input and output schemas;
- tools do not expose secrets;
- tools return structured data rather than only text;
- tools can be tested without an LLM;
- tools reuse existing core/checker/report/rag/trace modules.

Why this helps Agent job alignment:

- demonstrates tool-use architecture;
- allows LLM decisions to be grounded in real project signals;
- prepares for LangGraph nodes.

## Phase 5: LangGraph workflow

Status: planned after first LLM Agent MVP and tool boundaries.

Purpose:

Use LangGraph or an equivalent workflow engine to coordinate planning, tool
calling, analysis, verification, and report generation.

First workflow shape:

```text
collect_context
-> analyze_rules
-> judge_risk
-> plan_fixes
-> write_report
-> verify_output
```

Recommended package:

```text
src/releaseguard_agent/workflows/
```

Suggested module:

```text
src/releaseguard_agent/workflows/release_review_graph.py
```

Acceptance standard:

- each node has a clear responsibility;
- graph state is typed or schema-like;
- fake-model tests cover the graph;
- failures are represented explicitly;
- verifier output can force revision or mark the result as needing review.

Why this helps Agent job alignment:

- demonstrates workflow orchestration;
- shows task decomposition and execution control;
- creates a clear story for LangGraph experience.

## Phase 6: Multi-agent design

Status: planned.

Purpose:

Split the release-review workflow into multiple specialized Agents.

Planned Agents:

| Agent | Responsibility |
| --- | --- |
| `RuleAdvisor` | Explain rule basis and cite evidence. |
| `RiskJudge` | Judge release risk and explain release decision. |
| `FixPlanner` | Generate prioritized remediation plan. |
| `ReportWriter` | Produce final human-readable report. |
| `Verifier` | Check for missing evidence, unsupported claims, and hallucinations. |

Acceptance standard:

- each Agent has a distinct prompt and structured output;
- Agents share state through workflow state, not hidden globals;
- `Verifier` can flag unsupported claims;
- final output includes evidence references;
- deterministic release status remains visible and unchanged unless an explicit
  future product decision changes this policy.

Why this helps Agent job alignment:

- demonstrates multi-agent system design;
- shows role separation;
- provides interview-ready examples of Agent collaboration and verification.

## Phase 7: RAG upgrade

Status: planned after exact evidence and first Agent workflow are useful.

Purpose:

Move from exact rule evidence lookup to a broader knowledge system.

Current RAG foundation:

- exact local `rule_id` lookup;
- source-document evidence;
- line-number provenance;
- check-result enrichment.

Future knowledge sources:

- rule library;
- historical release reports;
- best-practice documents;
- ADRs;
- check history;
- human feedback;
- benchmark labels.

Future retrieval capabilities:

- BM25 retrieval;
- vector retrieval;
- hybrid retrieval;
- reranking;
- citation validation;
- retrieval evals.

Possible technologies:

- LlamaIndex or equivalent indexing framework;
- local vector database;
- BM25 implementation;
- reranker model or service.

Acceptance standard:

- exact rule lookup remains available;
- semantic retrieval does not replace rule-ID evidence for checker findings;
- retrieval outputs include source references;
- retrieval quality can be evaluated;
- Agent claims cite retrieved evidence.

Why this helps Agent job alignment:

- demonstrates RAG application development;
- shows hybrid retrieval design;
- creates measurable retrieval quality work.

## Phase 8: FastAPI backend

Status: planned after LLM Agent MVP or after first workflow slice.

Purpose:

Expose ReleaseGuard Agent as a service without moving release logic into the API
layer.

Recommended endpoints:

```text
GET /health
POST /api/check
POST /api/release-review
GET /api/runs/{run_id}
GET /api/reports/{run_id}
GET /api/traces/{run_id}
POST /api/runs/{run_id}/feedback
GET /api/rules/search
```

Recommended package:

```text
src/releaseguard_agent/api/
```

Acceptance standard:

- API is a thin service wrapper;
- core checker and Agent logic stays outside the API layer;
- endpoints return structured JSON;
- reports and traces can be retrieved by run ID;
- tests use FastAPI test client;
- no real LLM credential is needed for API tests.

Why this helps job alignment:

- demonstrates Python backend engineering;
- shows service delivery for Agent capabilities;
- prepares for UI, CI integration, or external automation.

## Phase 9: Evals and benchmark

Status: planned, but should start small before relying on real LLM output.

Purpose:

Measure Agent output quality with repeatable cases.

Benchmark cases:

- clean release-ready project;
- missing dependency declaration;
- missing tests;
- failing pytest;
- missing environment example;
- Docker release intent without valid Dockerfile;
- FastAPI project with missing test coverage;
- Flask project with dependency/config issues.

Metrics:

- blocking rule recall;
- warning precision;
- evidence citation rate;
- fix plan accuracy;
- hallucination rate;
- decision consistency;
- unsupported claim rate;
- remediation actionability.

Possible future evaluation:

- deterministic assertions;
- rubric-based checks;
- LLM-as-a-Judge as a secondary signal;
- human feedback comparison.

Acceptance standard:

- benchmark data is versioned;
- fake model tests remain deterministic;
- real-provider evals are optional and separated from unit tests;
- scores are explainable.

Why this helps Agent job alignment:

- demonstrates Agent evals;
- avoids subjective-only quality claims;
- supports future model and prompt iteration.

## Phase 10: Observability and trace expansion

Status: foundation exists; expansion planned.

Current trace:

- command metadata;
- environment summary;
- input references;
- output artifact references;
- decision summary.

Future trace should include:

- prompt references or prompt hashes;
- model provider metadata;
- model response metadata;
- tool call inputs and outputs;
- graph node execution;
- verifier results;
- retrieval sources;
- eval case IDs.

Acceptance standard:

- trace does not leak secrets;
- trace can connect final Agent output to evidence and tool calls;
- trace is useful for debugging and evals.

Why this helps Agent job alignment:

- demonstrates observability for AI systems;
- supports debugging hallucinations and unsupported claims.

## Phase 11: Linux and CI

Status: planned.

Purpose:

Prove the project works outside the local Windows development environment.

Recommended CI:

```text
.github/workflows/ci.yml
```

CI should run:

- Python setup;
- dependency installation;
- unit tests;
- integration tests where safe;
- `git diff --check`;
- sample CLI command;
- artifact upload for generated reports/traces.

Acceptance standard:

- CI runs on Linux;
- tests pass without real LLM credentials;
- generated artifacts are uploaded for inspection;
- provider-backed tests are skipped unless secrets are configured.

Why this helps job alignment:

- demonstrates real engineering delivery;
- makes the Agent project credible beyond local demos.

## Phase 12: Data governance and feedback

Status: planned.

Purpose:

Turn historical outputs and human feedback into benchmark and improvement data.

Data sources:

- check results;
- rule evidence;
- trace artifacts;
- Agent outputs;
- human feedback;
- standard answers;
- eval scores.

Uses:

- false-positive analysis;
- false-negative analysis;
- automatic label generation;
- prompt improvement;
- retrieval improvement;
- model comparison.

Acceptance standard:

- feedback schema is explicit;
- private data and secrets are not stored in unsafe artifacts;
- benchmark labels are reviewable;
- eval cases can be reproduced.

Why this helps Agent job alignment:

- demonstrates lifecycle thinking for AI systems;
- connects Agent outputs to quality improvement.

## Immediate next implementation slice

The next code slice should be:

```text
LLM provider abstraction + FakeLLMClient tests
```

Recommended files:

```text
src/releaseguard_agent/llm/__init__.py
src/releaseguard_agent/llm/client.py
src/releaseguard_agent/llm/fake_client.py
tests/unit/test_fake_llm_client.py
tests/unit/test_llm_public_api.py
```

This slice should not call a real provider yet.

It should provide:

- stable model input/output data structures;
- a testable client protocol;
- deterministic fake responses;
- call recording for prompt assertions;
- explicit missing-response errors.

Recommended commit after this roadmap is reviewed:

```text
docs: add Agent/RAG phase roadmap
```

## Summary

ReleaseGuard Agent's current deterministic foundation is strong, but it is not
the final destination.

The next meaningful step is to introduce a testable LLM boundary, then build
`ReleaseRiskAnalysisAgent` on top of the existing check results, rule evidence,
reports, release checklist, and trace artifacts.

That path keeps the engineering foundation strong while making the project
visibly aligned with Agent, RAG, LLM, backend, evals, observability, CI, and
data-governance job requirements.
