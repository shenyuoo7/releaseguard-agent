# Check History

## 2026-06-11

Git and GitHub onboarding guidance started.

Observed current project files:

- `.env.example`
- `.gitignore`
- `AGENTS.md`
- `README.md`
- `requirements.txt`
- `requirements-dev.txt`
- Markdown files under `project_memory/`

Observed `.gitignore` already ignores:

- `.venv/`
- `.env`
- Python caches
- pytest and ruff caches
- generated outputs under `outputs/`

Known environment issue:

- The Codex shell environment could not run `git` because the command was not recognized. The user should verify Git availability in their own PowerShell with `git --version`.

Important Git note:

- Empty folders are not tracked by Git. The current empty architecture folders will not appear on GitHub unless placeholder files are added later.

## 2026-06-10

Phase 1 project skeleton review completed.

User submitted `tree /F` showing the empty engineering-grade directory skeleton under:

`E:\A_project\Agent\ReleaseGuard_Agent`

Review result:

- Accepted.
- All expected major directories exist.
- Source architecture placeholder exists under `src/releaseguard_agent/`.
- CLI, API, core, scanners, detectors, checkers, plugins, reports, agents, RAG, memory, observability, models, services, and utils are reserved.
- Documentation, prompts, evals, sample projects, outputs, tests, scripts, and docker directories are reserved.
- No business code was created.

Collaboration boundary reaffirmed:

- Codex may update `AGENTS.md` and `project_memory/*.md`.
- Other project files must be created or edited by the user unless specifically permitted.

## 2026-06-07 Additional Collaboration Requirements

- Codex must not modify files unless the user asks for the specific modification.
- Codex must act as a patient teacher for a beginner with no programming foundation.
- Codex must explain tasks while assigning them.

## 2026-06-07

Initial project memory foundation created.

Created:

- `AGENTS.md`
- `project_memory/PROJECT_BRIEF.md`
- `project_memory/COLLABORATION_RULES.md`
- `project_memory/DECISIONS.md`
- `project_memory/TASKS.md`
- `project_memory/CHECK_STANDARDS.md`
- `project_memory/CHECK_HISTORY.md`
- `project_memory/KNOWLEDGE_BASE.md`
- `project_memory/REVIEW_LOG.md`

Scope control:

- No source code created.
- No formal project implementation files created.
- No business logic added.

Additional long-term requirement recorded:

- The project must not be simplified into a minimal demo or simple script.
- All later guidance should preserve engineering-grade professional architecture and portfolio value.
