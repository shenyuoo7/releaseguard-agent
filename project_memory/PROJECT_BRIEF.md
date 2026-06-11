# Project Brief

## Project Name

ReleaseGuard Agent

## Root Directory

`E:\A_project\Agent\ReleaseGuard_Agent`

All project content must stay under this root directory, including source code, virtual environment, configuration, knowledge base, outputs, history, logs, test sample projects, documentation, Docker files, and eval cases.

## Product Positioning

ReleaseGuard Agent is an AI Agent system for pre-release review of software projects. Before release, it should scan project structure, identify project type, run tests, inspect dependencies, Docker setup, environment variables, Git status, README quality, API health, and other release-readiness signals. It should combine these checks with a RAG release-rule knowledge base to generate rule evidence, risk analysis, repair suggestions, and final release reports.

## Long-Term Goal

Build a high-quality, extensible ReleaseGuard Agent architecture from day one. The first phase focuses on Python, FastAPI, and Flask projects, while later phases expand to Node.js, Java, Go, and other ecosystems through a plugin mechanism.

This project must not be simplified into a minimal execution demo, a single-purpose script, or a shallow scaffold. Every phase should serve an engineering-grade professional project suitable for portfolio and job-search demonstration.

## Job Target Alignment

This project should demonstrate capabilities relevant to:

- AI Agent application development
- RAG application development
- LLM application development
- Python backend development
- Agent workflow engineering
- AI tool platform development
- Agent evals and AI testing

## First-Phase Scope

Supported in phase one:

- `python-generic`
- `fastapi`
- `flask`

Not supported in phase one:

- Django
- Celery
- Node.js
- Java
- Go
- Mobile projects
- Embedded projects

## Entry Strategy

Phase one prioritizes CLI implementation, while the architecture must reserve space for a future FastAPI API layer.

Future API endpoints to reserve for:

- `GET /health`
- `POST /api/check`
- `GET /api/reports/{run_id}`
- `GET /api/history`
- `GET /api/rules/search`

## Report Strategy

Priority report formats:

1. Markdown
2. JSON

Future extensions:

- HTML
- PDF

Expected future outputs:

- `release_report.md`
- `release_checklist.md`
- `check_result.json`
- `trace.json`
