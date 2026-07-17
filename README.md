# ReleaseGuard Agent

ReleaseGuard Agent is currently a CLI-first pre-release review system that is
being developed toward a multi-agent RAG system for software release review.

It scans a project before release, runs deterministic release-readiness checks,
enriches findings with rule evidence, and writes human-readable and
machine-readable artifacts for review.

Phase 1 focuses on Python projects, including:

- `python-generic`
- `fastapi`
- `flask`

The CLI and synchronous FastAPI app are current product entry points. Both use
`ReleaseReviewService`. Standalone LLM analysis code and an OpenAI-compatible
provider adapter are available through an explicit optional CLI flag; normal
CLI/API reviews remain deterministic and offline.

## What it checks today

ReleaseGuard Agent currently includes checks for:

- Python dependency declarations
- `.env.example` presence
- Test directory structure
- Pytest configuration
- Optional pytest execution
- FastAPI project signals
- Flask project signals
- Dockerfile readiness
- Dockerfile style
- Rule evidence enrichment
- Deterministic release decision advice
- Trace artifact output for observability

## Generated artifacts

A full CLI run can generate the following files:

| Artifact | Purpose |
| --- | --- |
| `release_report.md` | Human-readable release readiness report. |
| `check_result.json` | Machine-readable raw check results and summary. |
| `release_checklist.md` | Operator checklist grouped by blockers, warnings, passed checks, and skipped checks. |
| `release_decision_advice.md` | Human-readable deterministic release decision advice. |
| `release_decision_advice.json` | Machine-readable release decision advice payload. |
| `trace.json` | Observability trace for the CLI run, including inputs, outputs, environment summary, and decision summary. |

## Quick start

From the repository root:

```powershell
$env:PYTHONPATH = "src"
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --output-dir outputs\demo-report --checklist-output-dir outputs\demo-checklist --agent-advice-output-dir outputs\demo-advice --trace-output-dir outputs\demo-trace
```

This command runs ReleaseGuard against the clean Python sample project and
writes all supported artifacts.

Expected output directories:

```text
outputs/
├── demo-report/
│   ├── release_report.md
│   └── check_result.json
├── demo-checklist/
│   └── release_checklist.md
├── demo-advice/
│   ├── release_decision_advice.md
│   └── release_decision_advice.json
└── demo-trace/
    └── trace.json
```

## Common CLI commands

The project does not yet have installable package metadata. Set
`$env:PYTHONPATH = "src"` once in the current PowerShell session before using
the CLI commands below.

Run checks with text output:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project
```

Run checks with JSON output:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json
```

Skip dynamic pytest execution:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --skip-pytest-execution
```

Write standard report artifacts:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --output-dir outputs\demo-report
```

Write release checklist artifacts:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --checklist-output-dir outputs\demo-checklist
```

Write Agent advice artifacts:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --agent-advice-output-dir outputs\demo-advice
```

Write trace artifacts:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --trace-output-dir outputs\demo-trace
```

List default checkers:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main list-checkers
```

Search trusted release rules offline with BM25:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main search-rules "Docker health check" --mode bm25 --top-k 5
```

`--mode vector` and `--mode hybrid` use the configured embedding provider. If
none is configured, the command returns `mode_used: bm25` with an explicit
`embedding_unavailable` degradation reason instead of contacting a provider.

## Exit codes

| Exit code | Meaning |
| ---: | --- |
| `0` | No blocking release issues were found. |
| `1` | One or more blocking release issues were found. |
| `2` | Usage error, such as an invalid project path or invalid output path. |

## Development checks

Run the full test suite:

```powershell
.venv\Scripts\python.exe -m pytest
```

Run CLI-focused tests:

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\test_cli_main.py
```

Run sample-project integration tests:

```powershell
.venv\Scripts\python.exe -m pytest tests\integration\test_cli_sample_projects.py
```

Check whitespace issues before committing:

```powershell
git diff --check
```

## Documentation

More detailed documentation is available in:

- [Current implementation status](docs/IMPLEMENTATION_STATUS.md)
- [CLI usage](docs/usage/cli.md)
- [Synchronous API usage](docs/usage/api.md)
- [Current architecture](docs/architecture/current-architecture.md)

## Current project status

The project currently has a working CLI path for Python/FastAPI/Flask
release-readiness review, deterministic rule evidence enrichment, release
decision advice, report artifacts, checklist artifacts, and trace artifacts.
The CLI and `POST /reviews` delegate scanning to the shared
`ReleaseReviewService`. `GET /health` is also operational. The verification
route remains explicitly unavailable until M6 supplies real before/after
semantics.

Rule lookup now supports exact rule ID, BM25 Top-K, LlamaIndex in-memory vector
retrieval, and hybrid fusion with deduplication and deterministic reranking.
Vector and hybrid modes require an explicitly configured embedding provider;
without one they report the degradation and fall back to offline BM25. This is
retrieval infrastructure, not a claim that semantic quality has been validated
against a real embedding model. LangGraph, role-based multi-agent orchestration,
post-fix verification semantics, Docker packaging, and GitHub Actions remain
planned work. See the implementation status page for the evidence-backed
boundary.
