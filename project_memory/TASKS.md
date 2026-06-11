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
