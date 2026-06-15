# Config, Dependency, Logging, And Release Rules

This file groups phase-one rules derived from The Twelve-Factor App plus ReleaseGuard default release-readiness policy.

## Rule Table

| rule_id | rule_name | checker | source | support_level | blocking_policy | evidence_type | phase |
|---|---|---|---|---|---|---|---|
| RG-CONFIG-001 | Root environment example file exists | EnvExampleChecker | The Twelve-Factor App - Config; ReleaseGuard default policy | releaseguard-default | conditional | file_exists | phase-1 |
| RG-CONFIG-002 | Suspected hardcoded configuration is detected | ConfigHardcodeChecker | The Twelve-Factor App - Config | source-backed | conditional | matched_lines | phase-1 |
| RG-DEPS-001 | Explicit dependency declaration exists | DependencyChecker | The Twelve-Factor App - Dependencies | source-backed | block | file_exists | phase-1 |
| RG-LOG-001 | Basic logging usage traces exist | LoggingChecker | The Twelve-Factor App - Logs; ReleaseGuard default policy | releaseguard-default | warn | matched_lines | phase-1 |
| RG-DEPLOY-001 | Startup command or runtime entrypoint guidance exists | DeploymentChecker | The Twelve-Factor App - Processes; ReleaseGuard default policy | releaseguard-default | conditional | matched_lines | phase-1 |
| RG-PORT-001 | Web project has a service entrypoint or port-binding signal | WebEntryChecker | The Twelve-Factor App - Port binding | source-backed | conditional | matched_lines | phase-1 |

## Checker Guidance

- `RG-DEPS-001` and `RG-CONFIG-001` are already implemented in the first checker milestone.
- `RG-CONFIG-002` should avoid printing full secret values. Evidence should include file path, line number, variable/key name, and a redacted snippet.
- `RG-LOG-001` should start as a lightweight text scan for `logging`, `structlog`, `loguru`, or similar usage.
- `RG-DEPLOY-001` should inspect README, Dockerfile, scripts, and framework entrypoint hints.
- `RG-PORT-001` applies mainly to FastAPI and Flask service projects, not generic scripts.

## Boundary Notes

- Do not claim that The Twelve-Factor App requires `.env.example`.
- Do not block all projects for missing port binding; only service projects need that signal.
- For ReleaseGuard default policies, reports should say "ReleaseGuard default policy" instead of "official source requirement".
