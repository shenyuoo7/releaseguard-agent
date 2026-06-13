# Tasks

## Phase 3 Update

Current phase: Git and GitHub onboarding.

Current task: Teach the user how to initialize Git, create a GitHub repository, commit the current project foundation, and push it to GitHub.

Status: completed. The user successfully pushed the first foundation commit to GitHub.

Remote repository:

- `https://github.com/shenyuoo7/releaseguard-agent.git`

Verified commit:

- `79a08e1 chore: initialize ReleaseGuard Agent project foundation`

Next recommended phase:

- Make the architecture skeleton visible and trackable in Git by intentionally adding placeholder files such as `.gitkeep` to empty directories, or by creating package marker files only where appropriate in later Python package steps.

Phase 4 preparation:

- User confirmed GitHub cannot currently show `src/`, `sample_projects/`, or `outputs/` because those directories are empty.
- User confirmed `project_memory/` should remain public in the GitHub repository.
- Next task should teach the user to add `.gitkeep` placeholder files to selected empty directories so the architecture skeleton becomes visible on GitHub.

Phase 4 progress:

- First `.gitkeep` batch completed and pushed.
- Verified commit: `1940f8a chore: track project skeleton directories`
- `config/`, `docker/`, `docs/`, `evals/`, `knowledge_base/`, `outputs/`, `prompts/`, `scripts/`, and `tests/` now contain placeholder files.
- `src/releaseguard_agent/` and `sample_projects/` still need placeholder files or future real files to become visible on GitHub.

Phase 4 completion:

- Second `.gitkeep` batch completed and pushed.
- Verified commit: `05f8019 chore: track source and sample project skeletons`
- `src/releaseguard_agent/` and its architecture subdirectories now contain `.gitkeep` placeholders.
- All planned `sample_projects/` subdirectories now contain `.gitkeep` placeholders.
- Working tree is clean and synchronized with `origin/main`.

Next recommended phase:

- Teach Python package initialization by adding `__init__.py` files to the real importable package directories under `src/releaseguard_agent/`.

Phase 5 completion:

- Python package initialization completed and pushed.
- Verified commit: `ae52c88 chore: initialize Python package structure`
- `__init__.py` files were added under `src/releaseguard_agent/` and its real importable source subdirectories.
- `sample_projects/` was correctly left without `__init__.py`.
- Working tree is clean and synchronized with `origin/main`.

Next recommended phase:

- Clean redundant `.gitkeep` files from source package directories that now contain `__init__.py`, while keeping `.gitkeep` files in directories that are still intentionally empty.

Phase 6 completion:

- Redundant `.gitkeep` files under `src/releaseguard_agent/` were removed and pushed.
- Verified commit: `b18be4a chore: remove redundant source placeholders`
- `src/releaseguard_agent/` and all source subdirectories now contain `__init__.py` only.
- Working tree is clean and synchronized with `origin/main`.

Next recommended phase:

- Begin the first real engineering code step: design the core check result data model under `src/releaseguard_agent/models/`.

Current blocker:

- The user created `src/releaseguard_agent/models/check_result.py` but hit a path error when compiling from the wrong current directory.
- The user's current Python is Anaconda Python 3.9.7, while the project requires Python 3.11+.

Immediate next task:

- Fix command location understanding and configure the correct Python 3.11+ interpreter / project virtual environment before continuing model review.

Codex permissions for this phase:

- May read all files under `E:\A_project\Agent\ReleaseGuard_Agent`.
- May update Markdown memory files under `project_memory/`.
- Must not modify formal project files unless the user explicitly allows the exact change.
- Must not run Git commands that change repository state on behalf of the user unless the user explicitly asks.

Important note:

- Git does not track empty folders by default. If the user wants all empty architecture folders visible on GitHub, a later task should teach the user to add placeholder files such as `.gitkeep` intentionally.

## Phase 2 Update

Current phase: Engineering foundation file planning.

Current task: Teach the user how to create the first formal project foundation files without writing business code.

Phase 1 result: The user created the empty engineering-grade directory skeleton and submitted `tree /F`. The structure was reviewed and accepted.

Allowed Codex changes:

- `AGENTS.md`
- Markdown files in `project_memory/`

User-owned files for the next phase:

- `README.md`
- `.gitignore`
- `requirements.txt`
- `requirements-dev.txt`
- `.env.example`

Review requirement: The user should create these files personally and send their contents or `tree /F` plus file snippets for review.

## Phase 1 Update

Current phase: Project skeleton planning.

Current task: Guide the user to create the first engineering-grade empty directory skeleton for ReleaseGuard Agent.

Allowed user-created content for this phase:

- Empty folders
- No business code
- No checker implementation
- No RAG implementation
- No Agent implementation

Review requirement: The user should send `tree /F` after creating the skeleton. Codex will check whether the structure is correct before moving to the next phase.

## Current Phase

Phase 0: Project collaboration and memory foundation.

## Current Task

Create the project-level collaboration rule file and project memory folder.

Allowed changes for this task:

- `AGENTS.md`
- `project_memory/` Markdown files

Forbidden changes for this task:

- Formal source code
- `src/`
- `tests/`
- `README`
- `pyproject.toml`
- `.env.example`
- Business logic

## Task Status

- Requirements alignment: completed
- `AGENTS.md`: created
- `project_memory/`: created
- Initial memory files: created

## Next Planned Phase

After the user confirms this memory foundation is correct, the next phase is to guide the user through project root setup and high-level empty folder skeleton planning.
