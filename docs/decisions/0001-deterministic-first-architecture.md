# ADR 0001: Deterministic-First Architecture

## Status

Accepted

## Date

2026-06-25

## Context

ReleaseGuard Agent is a pre-release review system for software projects. Its
long-term goal includes AI Agent workflows, RAG, LLM-assisted analysis, Python
backend APIs, evals, observability, and extensible checker/plugin support.

The current phase-one scope supports:

- `python-generic`
- `fastapi`
- `flask`

The current entry point is CLI-first. A future FastAPI backend is reserved, but
the project currently focuses on deterministic local execution and artifact
generation.

The project already has these foundations:

- deterministic release-readiness checkers,
- source-backed local release rules,
- exact rule-ID based evidence retrieval,
- deterministic Agent-facing release decisions,
- Markdown and JSON report artifacts,
- release checklist artifact output,
- trace artifact output,
- unit and integration test coverage around the current behavior.

The project deliberately avoids live LLM calls, embeddings, vector databases,
and network retrieval in the current phase.

This is not a decision to build a deterministic-only product. ReleaseGuard
Agent is still an AI Agent project, and the long-term product must demonstrate
LLM application development, Agent workflow engineering, RAG grounding,
observability, and evals. The deterministic foundation exists so the future LLM
Agent has reliable tools, evidence, traces, and evaluation targets instead of
operating as an ungrounded chatbot.

## Decision

ReleaseGuard Agent will use a deterministic-core plus planned LLM-Agent
architecture.

In phase one, the project is deterministic-first. This means the deterministic
core is built before live LLM behavior. It does not mean the project will remain
deterministic-only.

This means the current system must first make these behaviors stable:

- scan a target project with deterministic checkers;
- produce structured `CheckResult` objects;
- map checker results to local rule evidence by stable `rule_id`;
- synthesize deterministic release decisions;
- produce deterministic Agent-readable advice;
- write stable Markdown and JSON artifacts;
- write stable trace metadata;
- keep tests reproducible without external services.

LLM calls, semantic retrieval, embeddings, vector databases, network retrieval,
and autonomous repair actions are intentionally deferred until the system has
stable tool outputs, structured context, traces, and eval cases. They are not
rejected as product capabilities.

The current "RAG" layer is exact local rule evidence retrieval. It is not yet a
semantic embedding search system.

The current "Agent" layer is deterministic decision synthesis and advice
generation. It is not yet an LLM-powered autonomous Agent.

The future LLM Agent layer is required for the project's Agent-job alignment.
It should be added as an explicit layer that consumes deterministic scan
results, rule evidence, trace context, and run history, then produces grounded
analysis, prioritized repair plans, and human-readable release guidance.

## Why deterministic-first

### 1. Stable tests

The project needs a trustworthy engineering foundation before adding
probabilistic behavior. Deterministic checkers, deterministic rule lookup, and
deterministic report generation can be tested with exact assertions.

This is important because release-readiness tools must be reliable. If the
foundation is unstable, adding an LLM would make defects harder to diagnose.

### 2. Clear release-blocking policy

Release blocking must come from explicit checker results and stable rule
metadata.

The current source of truth for release blocking is the structured result
model, especially `CheckResult.should_block_release`. Agent-facing summaries may
explain blockers, group them, and make them easier to understand, but they must
not invent a different release policy.

### 3. Explainable evidence

The project needs a clear trail from:

```text
checker result -> rule ID -> rule evidence -> source document -> report/advice
```

Exact local retrieval by `rule_id` is the simplest reliable way to build that
trail. It makes every finding auditable before semantic retrieval is introduced.

### 4. Better LLM integration later

LLMs are more valuable when they are grounded in stable structured context.

A future LLM Agent should receive:

- checker results,
- rule evidence,
- release decision summaries,
- trace metadata,
- artifact paths,
- historical runs,
- eval expectations.

If these structures are stable first, the future LLM layer can focus on
reasoning, prioritization, remediation planning, and explanation quality instead
of compensating for weak system boundaries.

### 5. Better portfolio value

For a professional portfolio project, deterministic engineering quality matters.

The deterministic-first approach demonstrates:

- Python backend engineering discipline,
- clean module boundaries,
- testable Agent/RAG architecture,
- reproducible CLI behavior,
- artifact design,
- observability design,
- future API readiness.

The final portfolio value, however, should not stop at deterministic behavior.
The project should eventually show both sides:

- deterministic engineering foundations that are reliable and testable;
- LLM Agent capabilities that are grounded, observable, evaluated, and useful.

This is stronger than a shallow demo that calls an LLM early but cannot explain
or verify its own decisions, and also stronger than a purely deterministic tool
that never demonstrates real Agent or LLM application work.

## Agent job alignment

The project must visibly support AI Agent role requirements.

A credible Agent-oriented ReleaseGuard should eventually demonstrate:

- LLM provider abstraction so model calls are isolated from business logic;
- prompt/context construction from deterministic checker results and rule
  evidence;
- tool-style boundaries around project scanning, rule lookup, report writing,
  trace reading, and run-history lookup;
- structured LLM input and output schemas;
- RAG grounding with citations to local rule evidence;
- memory or run-history context for comparing previous release checks;
- eval cases for advice quality, citation grounding, and actionability;
- guardrails that prevent the LLM from overriding deterministic release-blocking
  policy silently;
- observability for prompts, model outputs, tool inputs, tool outputs, and final
  advice artifacts;
- API integration so Agent behavior can be used outside the CLI.

The minimum credible future LLM Agent MVP should:

- accept existing checker results and rule evidence as context;
- ask an LLM for prioritized release-risk analysis and remediation planning;
- cite retrieved rule evidence where it makes claims;
- return structured JSON plus Markdown advice;
- preserve deterministic release status as the source of truth;
- be testable with a fake model provider and optionally runnable with a real
  provider when credentials are configured.

## Current deterministic components

### CLI

The CLI is the current primary user entry point.

It can:

- run the release check workflow,
- print human-readable output,
- print JSON output,
- write report artifacts,
- write checklist artifacts,
- write Agent advice artifacts,
- write trace artifacts.

The CLI must remain stable when optional artifact flags are not used.

### Checker runner

The checker runner executes a configured set of checkers and returns ordered
`CheckResult` objects.

It should remain independent from future LLM behavior. Checkers collect evidence
and produce structured results; they do not generate natural-language Agent
plans directly.

### Checkers, scanners, and detectors

Checkers are deterministic release-readiness inspections.

Scanners and detectors provide reusable project evidence, such as dependency
declarations, framework hints, Docker files, or test configuration.

The current phase-one checkers support Python, FastAPI, and Flask project
signals. The project must not claim broader framework support until additional
checker families and tests exist.

### Models

The model layer contains structured result and evidence objects used across
checkers, reports, RAG, Agent decision synthesis, and trace output.

Model objects should remain plain, serializable, and test-friendly.

### RAG / rule evidence

The current RAG layer is deterministic local retrieval.

It loads the local release rule knowledge base and retrieves rule evidence by
exact `rule_id`.

Current responsibilities:

- load rule-index records,
- reject malformed or duplicate rule IDs,
- preserve knowledge file paths and source line numbers,
- attach source-document evidence,
- enrich `CheckResult` objects without mutating them,
- represent missing evidence explicitly.

Current non-responsibilities:

- no embeddings,
- no vector database,
- no semantic search,
- no network retrieval,
- no LLM-generated evidence.

### Agent decision layer

The current Agent layer is deterministic.

It consumes raw or enriched checker results and produces:

- release status,
- blocking rule IDs,
- warning rule IDs,
- missing evidence counts,
- source URLs,
- structured findings,
- Markdown and JSON advice artifacts.

Current non-responsibilities:

- no autonomous code repair,
- no live LLM planning,
- no tool-calling loop,
- no hidden policy override,
- no alternate release-blocking logic.

### Reports and checklists

Reports and checklists turn structured results into review artifacts.

They are intentionally deterministic so a user, CI job, or future API caller
can compare outputs across runs.

### Observability

Trace output records command context, artifact references, execution metadata,
and decision summaries.

This trace foundation prepares the project for future API runs, run history,
evals, debugging, and Agent behavior inspection.

## Deferred alternatives

### Alternative 1: Add an LLM immediately

Rejected as the first implementation step, not rejected as the product
direction.

Adding an LLM before deterministic evidence, decisions, reports, and traces are
stable would create a more impressive demo but a weaker engineering system.

LLM integration should be implemented after:

- deterministic Agent advice is stable,
- report and trace artifacts are stable,
- evaluation cases exist,
- prompt inputs and expected outputs can be tested.

### Alternative 2: Add embeddings and a vector database immediately

Rejected for phase one.

The current rule knowledge base is small and structured enough for exact
`rule_id` lookup. Semantic retrieval would add operational complexity before it
is necessary.

This alternative can be revisited when:

- the rule base grows substantially,
- multiple ecosystems are supported,
- users need natural-language rule search,
- evals can measure retrieval quality.

### Alternative 3: Build the API service first

Deferred, not rejected.

A future FastAPI backend is important, but the CLI has been the fastest way to
stabilize the core release-check workflow.

The API layer should wrap stable services rather than become the place where
core release logic lives.

Future API endpoints are reserved for:

- `GET /health`
- `POST /api/check`
- `GET /api/reports/{run_id}`
- `GET /api/history`
- `GET /api/rules/search`

### Alternative 4: Build plugin support first

Deferred, not rejected.

Plugin expansion will matter when the project grows beyond phase-one Python,
FastAPI, and Flask support. For now, the priority is making the current checker,
rule, report, Agent, and trace contracts stable.

## Consequences

### Positive consequences

- The system can be tested without external services.
- Release-blocking behavior remains explainable.
- Reports and traces are reproducible.
- Agent and RAG boundaries are clearer.
- Future API work can reuse stable services.
- Future LLM work can be grounded in structured evidence.

### Negative consequences

- The current system is less conversational than a full LLM Agent.
- The current RAG layer cannot answer arbitrary natural-language rule queries.
- The current Agent cannot perform autonomous remediation.
- Some advanced AI features remain invisible until later phases.

These trade-offs are accepted because the current phase prioritizes correctness,
traceability, and maintainable architecture.

## Future direction

The planned evolution is:

1. Keep deterministic CLI, checker, report, checklist, advice, and trace
   behavior stable.
2. Document the current architecture and decisions clearly.
3. Add a thin FastAPI backend around the existing core services.
4. Add run history and artifact retrieval.
5. Add eval cases for Agent advice quality and retrieval quality.
6. Add a model-provider abstraction and fake-model test harness.
7. Add LLM-assisted Agent behavior once deterministic payloads and evals can
   constrain and verify it.
8. Add semantic RAG after exact evidence retrieval is reliable and retrieval
   evals exist.
9. Add plugins for additional ecosystems after phase-one Python/FastAPI/Flask
   support remains stable.

## Guardrails

Future LLM and semantic RAG work must follow these guardrails:

- do not replace deterministic release-blocking policy with hidden LLM judgment;
- do not claim unsupported framework coverage;
- do not cite sources that are not present in the knowledge base or retrieved
  evidence;
- do not expose secrets in prompts, reports, traces, or advice artifacts;
- keep LLM outputs explainable through evidence references;
- add evals before treating LLM recommendations as product behavior;
- preserve non-LLM execution paths for CI and offline usage.

## Summary

ReleaseGuard Agent chooses deterministic-first architecture because the project
is a release-readiness tool, not just a chatbot demo.

The current RAG layer is exact local rule evidence retrieval.

The current Agent layer is deterministic release decision and advice synthesis.

Semantic RAG, embeddings, vector databases, LLM Agents, backend API service,
run history, and plugin expansion are future phases built on top of this stable
foundation.

The project should connect to an LLM in a later phase. The architecture should
not continue indefinitely as deterministic-only, because that would underserve
the project's AI Agent, RAG, and LLM job-alignment goals.
