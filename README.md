# ReleaseGuard Agent

ReleaseGuard Agent is a deterministic-first, role-based multi-agent RAG system
for pre-release software review.

It scans a project before release, runs deterministic release-readiness checks,
enriches findings with rule evidence, and writes human-readable and
machine-readable artifacts for review.

## 最简单使用方式

Windows 用户日常只需双击项目根目录的 `ReleaseGuard.bat`，然后从中文
菜单选择检查项目、启动网页、修复前后对比或自带演示。第一次使用选择
“安装或修复运行环境”。详见 [极简使用说明](docs/SIMPLE_USAGE.md)。

Phase 1 focuses on Python projects, including:

- `python-generic`
- `fastapi`
- `flask`

The Windows menu, CLI, and synchronous FastAPI app are product entry points.
They reuse the same service/workflow boundaries. An OpenAI-compatible provider
is explicit opt-in; normal reviews, graph execution, and the golden eval work
without credentials or network access.

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

命令行用户也可以从项目根目录直接传入 CLI 参数：

```powershell
.\ReleaseGuard.bat -Action Check -ProjectPath sample_projects\clean_python_project -NoPause
```

`ReleaseGuard.bat` without arguments opens the menu. Its PowerShell launcher
sets the project environment and calls the existing CLI/API entry points; it
does not duplicate release-review business logic.

The equivalent direct Python command is:

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

Run the conditional LangGraph workflow in deterministic mode:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main agent-review sample_projects\fastapi_bad_project --skip-pytest-execution
```

This command uses real graph nodes and conditional edges. `--enable-llm` is
explicit opt-in; without it, risk explanation and fix planning stay offline.
Add `--execution-trace-output-dir outputs\agent-trace` to persist the redacted
node/tool/retrieval/LLM event stream.

Compare a baseline snapshot with a user-modified snapshot:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main verify path\to\before path\to\after --skip-pytest-execution
```

ReleaseGuard never applies the change. It rescans both snapshots and reports
resolved, new, and unchanged findings.

Run the reproducible offline eval:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main evaluate
```

## Docker demo

Build and run the local demonstration API image:

```powershell
docker build --tag releaseguard-agent:local .
docker run --detach --name releaseguard-demo --publish 8000:8000 releaseguard-agent:local
.venv\Scripts\python.exe scripts\http_health_smoke.py
docker rm --force releaseguard-demo
```

The image runs as a non-root user, defaults to deterministic mode, contains
the local rule corpus and sample projects, and is intended for demonstration
and CI smoke tests. It is not a sandbox for executing untrusted repositories.

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

Run quality and type checks:

```powershell
.venv\Scripts\python.exe -m ruff check src tests scripts
.venv\Scripts\python.exe -m mypy src\releaseguard_agent
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
- [Docker demo](docs/usage/docker.md)
- [Current architecture](docs/architecture/current-architecture.md)

## Current project status

The project currently has a working CLI path for Python/FastAPI/Flask
release-readiness review, deterministic rule evidence enrichment, release
decision advice, report artifacts, checklist artifacts, and trace artifacts.
The CLI and `POST /reviews` delegate scanning to the shared
`ReleaseReviewService`. `GET /health` and synchronous `POST /verifications` are
also operational. Verification rescans caller-supplied before/after snapshots;
it never edits the reviewed repository.

Rule lookup now supports exact rule ID, BM25 Top-K, LlamaIndex in-memory vector
retrieval, and hybrid fusion with deduplication and deterministic reranking.
Vector and hybrid modes require an explicitly configured embedding provider;
without one they report the degradation and fall back to offline BM25. This is
retrieval infrastructure, not a claim that semantic quality has been validated
against a real embedding model. Agent-callable tools and a compiled LangGraph
conditional workflow are implemented. Evidence, Risk, Fix Planner, and Verifier
are separate role nodes with typed state transfer; LLM output must cite supplied
Evidence IDs and cannot override deterministic blocking facts. Execution-level
trace and a fixed offline eval suite are implemented. The non-root local Docker
demo has passed image, API, user-ID, and Docker health checks. The Ubuntu
GitHub Actions workflow covers tests, Eval, lint, types, and container smoke;
its first remote Ubuntu run passed on commit `469dc48`. The eval's fake
embeddings validate repeatability and integration mechanics, not real
semantic-search quality. See the implementation status page for the
evidence-backed boundary.
