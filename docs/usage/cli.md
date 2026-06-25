# ReleaseGuard Agent CLI Usage

This document explains how to run the current ReleaseGuard Agent CLI and
inspect generated release-readiness artifacts.

The CLI is the phase-one entry point for the project.

## Supported scope

Current phase-one project types:

- `python-generic`
- `fastapi`
- `flask`

Current phase-one entry point:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main
```

ReleaseGuard Agent does not currently claim support for Django, Celery,
Node.js, Java, Go, mobile, or embedded projects.

## Basic command

Run release-readiness checks against a project:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project
```

By default, the CLI prints a text report to the terminal.

## JSON output

Use `--format json` to print a machine-readable report payload:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json
```

The terminal JSON includes:

- tool name
- target project path
- pytest execution mode
- summary counts
- individual check results

## Generate all artifacts

Use this command from the repository root to generate all currently supported
artifacts:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --output-dir outputs\demo-report --checklist-output-dir outputs\demo-checklist --agent-advice-output-dir outputs\demo-advice --trace-output-dir outputs\demo-trace
```

Expected artifact layout:

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

## Artifact flags

### `--output-dir`

Writes the standard release report artifacts:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --output-dir outputs\demo-report
```

Generated files:

```text
outputs/demo-report/release_report.md
outputs/demo-report/check_result.json
```

Use these files when you want:

- a human-readable Markdown report
- a stable JSON payload for automation, tests, or future API integration

### `--checklist-output-dir`

Writes the release checklist artifact:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --checklist-output-dir outputs\demo-checklist
```

Generated file:

```text
outputs/demo-checklist/release_checklist.md
```

The checklist groups results into:

- blocking fixes
- warnings to review
- passed checks
- skipped checks

Use this file when you want an operator-facing checklist before release.

### `--agent-advice-output-dir`

Writes deterministic release decision advice artifacts:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --agent-advice-output-dir outputs\demo-advice
```

Generated files:

```text
outputs/demo-advice/release_decision_advice.md
outputs/demo-advice/release_decision_advice.json
```

Use these files when you want:

- a release decision summary
- blocker and warning grouping
- rule-evidence-aware advice
- a machine-readable decision payload

The current advice path is deterministic and does not require a live LLM call.

### `--trace-output-dir`

Writes an observability trace artifact:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --trace-output-dir outputs\demo-trace
```

Generated file:

```text
outputs/demo-trace/trace.json
```

Use this file when you want to inspect:

- command arguments
- target project path
- input artifact references
- output artifact references
- environment summary
- release decision summary

When report, checklist, Agent advice, and trace outputs are requested
together, `trace.json` records the generated artifact paths.

## Skip pytest execution

Some checks inspect project files only. The pytest execution checker can be
skipped:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --skip-pytest-execution
```

This is useful when:

- you want a faster static review
- dependencies for the target project are not installed
- you only want structure and configuration checks

## List available checkers

List the default Python checkers:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main list-checkers
```

List default Python checkers without the dynamic pytest execution checker:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main list-checkers --skip-pytest-execution
```

## Exit codes

| Exit code | Meaning |
| ---: | --- |
| `0` | ReleaseGuard found no blocking release issues. |
| `1` | ReleaseGuard found one or more blocking release issues. |
| `2` | CLI usage error, such as a missing project path or invalid output path. |

Example: a project with failing tests may return `1` because the failing
pytest execution result blocks release.

## Example sample projects

The repository includes sample projects under:

```text
sample_projects/
```

Useful examples:

```text
sample_projects/clean_python_project
sample_projects/failed_tests_project
sample_projects/fastapi_good_project
sample_projects/fastapi_bad_project
sample_projects/flask_good_project
sample_projects/flask_bad_project
sample_projects/docker_good_project
sample_projects/missing_docker_project
```

Run against a clean Python sample:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\clean_python_project --format json --output-dir outputs\clean-report --checklist-output-dir outputs\clean-checklist --agent-advice-output-dir outputs\clean-advice --trace-output-dir outputs\clean-trace
```

Run against a project with failing tests:

```powershell
.venv\Scripts\python.exe -m releaseguard_agent.cli.main check sample_projects\failed_tests_project --format json --output-dir outputs\failed-report --checklist-output-dir outputs\failed-checklist --agent-advice-output-dir outputs\failed-advice --trace-output-dir outputs\failed-trace
```

The failing-tests project is expected to return exit code `1`.

## Troubleshooting

### Project path does not exist

If the target path does not exist, the CLI returns usage error `2`.

Check that the path is relative to the repository root or provide an absolute
path.

### Output path is a file

Artifact output flags expect directories. If an output path already exists as
a file, the CLI returns usage error `2`.

For example, this is invalid if `outputs/demo-report` is a file:

```powershell
--output-dir outputs\demo-report
```

Delete or rename the file, or choose a different directory.

### Pytest execution is slow or fails because dependencies are missing

Use:

```powershell
--skip-pytest-execution
```

This keeps static checks enabled while skipping dynamic pytest execution.

## Validation commands for contributors

Run focused CLI tests:

```powershell
.venv\Scripts\python.exe -m pytest tests\unit\test_cli_main.py
```

Run sample-project integration tests:

```powershell
.venv\Scripts\python.exe -m pytest tests\integration\test_cli_sample_projects.py
```

Run all tests:

```powershell
.venv\Scripts\python.exe -m pytest
```

Check whitespace before commit:

```powershell
git diff --check
```
