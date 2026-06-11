# Decisions

## Latest Collaboration Decisions

Decision: Codex is allowed to maintain `AGENTS.md` and Markdown memory files under `project_memory/`.

Decision: Codex must not modify any other project file unless the user explicitly grants permission for that specific change. The user should personally create and edit formal project files as part of learning.

Decision: Codex must not modify project files unless the user asks for that specific modification.

Decision: The default collaboration mode is teacher-guided implementation. Codex explains, assigns small tasks, gives examples and acceptance standards, then reviews the user's result.

Decision: Codex must teach as if the user is a beginner with no programming foundation, while still preserving engineering-grade professional standards.

## Mandatory Project Memory Process

Decision: Every turn must execute the project memory process.

Details:

- Read project memory before starting.
- Refer to project goals, collaboration rules, technical decisions, task progress, and check standards before answering.
- End every response with a memory update judgment.
- Update project memory when new long-term information appears.

## Project Architecture Direction

Decision: ReleaseGuard Agent will be built as a high-quality staged architecture, not as a minimal demo or simple script.

Decision: The project must remain engineering-grade and professional in every phase. Step-by-step delivery is allowed, but simplification that damages architecture, extensibility, portfolio value, or future Agent/RAG/Evals/Observability integration is not allowed.

## Phase-One Project Types

Decision: Phase one supports:

- `python-generic`
- `fastapi`
- `flask`

Decision: Phase one does not support:

- Django
- Celery
- Node.js
- Java
- Go
- Mobile projects
- Embedded projects

## Entry Point Strategy

Decision: Phase one implements CLI first, while reserving architecture for FastAPI.

## Package Management

Decision: Phase one uses:

- `.venv`
- `requirements.txt`
- `requirements-dev.txt`

Future migration may consider:

- `pyproject.toml`
- `uv`
- `poetry`

## Python Version

Decision: Use Python 3.11+.

Recommended project metadata later:

```text
requires-python = ">=3.11"
```

## Report Format

Decision: Markdown is first priority, JSON is second priority.

Future extensions may include HTML and PDF.

## Reserved Advanced Capabilities

Decision: The directory structure must reserve space for:

- Agent workflows
- RAG
- Prompts
- Memory
- Evals
- Observability
- Knowledge base
- Reports
- History
- Traces
- Plugin expansion
