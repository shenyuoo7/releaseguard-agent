# Check Standards

## Git And GitHub Sync Standards

Before pushing to GitHub, the user should verify:

- `git --version` works.
- `.gitignore` excludes `.env`, `.venv/`, caches, logs, and generated outputs.
- No secrets or private tokens are present in tracked files.
- `git status` shows only intentional files.
- The first commit message clearly describes the foundation state.
- The GitHub repository is created intentionally as public or private.

Reminder:

- Git does not track empty folders. Use placeholder files only when the user intentionally wants empty folders to appear on GitHub.

Placeholder file standard:

- Use `.gitkeep` only to make an otherwise empty directory visible to Git.
- `.gitkeep` should normally be empty.
- Do not add business logic inside `.gitkeep`.
- Do not use `.gitkeep` as documentation.
- Later, when a directory receives real files, `.gitkeep` can be removed if it is no longer needed.

Python package initialization standard:

- Use `__init__.py` to mark directories that are intended to be Python packages.
- Add `__init__.py` only under real importable source directories, not under `sample_projects/` unless those sample projects later need their own packages.
- Keep initial `__init__.py` files empty or very small.
- Do not put business logic inside `__init__.py`.
- After a directory has real package files, `.gitkeep` can eventually be removed in a cleanup step.

Command execution standard:

- Prefer running project commands from the project root: `E:\A_project\Agent\ReleaseGuard_Agent`.
- When running from a subdirectory, use paths relative to that subdirectory.
- For this project, Python commands should use a Python 3.11+ interpreter, preferably from the project-local `.venv`.
- Before validating Python files, run `python --version` and confirm it is 3.11 or newer.

After GitHub sync, verify:

- `git status` says the working tree is clean.
- `git remote -v` points to the intended repository.
- `git log --oneline -1` matches the pushed commit.
- The GitHub repository can display the expected files.
- If empty folders are part of the intended public project skeleton, add explicit placeholder files in a later guided step.

## Latest Teaching And File Modification Standards

- Codex may update `AGENTS.md` and Markdown memory files under `project_memory/` when memory maintenance is needed.
- Codex must not modify formal project files unless the user explicitly allows the exact change.
- Formal project files should normally be created and edited by the user personally so the user learns the project deeply.
- Codex must explain tasks patiently for a beginner, including what each folder or file means, why it exists, and how the user can verify the result.
- Codex guidance must stay step-by-step and must not assume prior programming knowledge.
- Codex must not modify files unless the user asks for the specific modification.
- When assigning a task, Codex should let the user execute filesystem changes and then review the user's output.

## Phase 1 Skeleton Acceptance

The project skeleton is accepted if it includes the planned top-level directories and internal architecture folders for CLI, API, core, scanners, detectors, checkers, plugins, reports, agents, RAG, memory, observability, models, services, utils, docs, prompts, evals, sample projects, outputs, tests, scripts, and docker.

The submitted skeleton on 2026-06-10 passed this check.

## Mandatory Project Memory Check

Every Codex turn must check the project memory before working and must end with the fixed memory update judgment.

Required ending:

```text
【记忆更新判断】

* 本轮是否需要更新项目记忆：是 / 否
* 需要更新到哪些文件：
* 建议写入内容：
* 是否已更新：是 / 否
* 下一轮开始前需要优先读取的文件：
```

## Phase 0 Acceptance Standards

The current task is accepted only if:

- `AGENTS.md` exists in the project root.
- `project_memory/` exists in the project root.
- The following memory files exist:
  - `PROJECT_BRIEF.md`
  - `COLLABORATION_RULES.md`
  - `DECISIONS.md`
  - `TASKS.md`
  - `CHECK_STANDARDS.md`
  - `CHECK_HISTORY.md`
  - `KNOWLEDGE_BASE.md`
  - `REVIEW_LOG.md`
- The mandatory project memory process is written into:
  - `COLLABORATION_RULES.md`
  - `CHECK_STANDARDS.md`
  - `DECISIONS.md`
- No formal code files or project implementation files are created.

## Mentor Review Standard

When the user sends a tree output or screenshot, Codex should verify:

- Whether all files are under `E:\A_project\Agent\ReleaseGuard_Agent`
- Whether only allowed files were created for the current phase
- Whether the directory structure matches the current task
- Whether the user can safely move to the next small phase

## Engineering-Grade Standard

Every phase must be reviewed against the long-term high-quality project goal. A solution is not acceptable if it only works as a quick demo but weakens future CLI, FastAPI, checker, plugin, RAG, Agent, memory, eval, observability, report, or history capabilities.
