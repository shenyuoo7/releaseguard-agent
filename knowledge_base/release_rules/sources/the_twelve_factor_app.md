# The Twelve-Factor App

## Source

- URL: https://12factor.net/
- Type: cloud application release-readiness principles
- Use in ReleaseGuard: baseline guidance for dependency declaration, environment-based configuration, process startup, port binding, and logs.

## Source-Backed Facts

- Twelve-Factor applies to service applications and emphasizes portability across execution environments.
- The Dependencies factor says applications should explicitly declare and isolate dependencies.
- The Config factor says configuration should be stored in the environment.
- The Build, release, run and Processes factors support clear runtime process boundaries.
- The Port binding factor supports exposing services through port binding.
- The Logs factor treats logs as event streams.

## ReleaseGuard Rule Mapping

| rule_id | ReleaseGuard rule | support_level | blocking_policy | evidence_type | boundary |
|---|---|---|---|---|---|
| RG-CONFIG-001 | Check for a root `.env.example` file | releaseguard-default | conditional | file_exists | `.env.example` is ReleaseGuard's practical convention for documenting environment variables; Twelve-Factor supports environment config but does not mandate this exact file. |
| RG-CONFIG-002 | Check for suspected hardcoded configuration | source-backed | conditional | matched_lines | Directly supported by the Config principle when the finding is truly configuration stored in code. |
| RG-DEPS-001 | Check for an explicit dependency declaration file | source-backed | block | file_exists | Directly supported by the Dependencies principle. |
| RG-LOG-001 | Check for basic logging usage traces | releaseguard-default | warn | matched_lines | Twelve-Factor supports logs as event streams; the exact static scan is a ReleaseGuard policy. |
| RG-DEPLOY-001 | Check for startup command or runtime entrypoint guidance | releaseguard-default | conditional | matched_lines | Derived from process and release-readiness concerns; exact README/script detection is ReleaseGuard policy. |
| RG-PORT-001 | Check for a web service entrypoint or port-binding signal | source-backed | conditional | matched_lines | Directly related to Port binding for service apps. |

## Implementation Notes

- Prefer evidence that can be checked without running external services: files, matched lines, command snippets, and root-level metadata.
- Do not report `.env.example` as an official Twelve-Factor requirement.
- For generic Python projects, missing web entrypoint or port binding should not block by default.
- For FastAPI or Flask projects that appear to be releaseable web services, missing startup/entrypoint evidence can become release-blocking.

## Report Language Boundary

Use wording like:

- "The Twelve-Factor App supports environment-based configuration."
- "ReleaseGuard uses `.env.example` as a default policy to make required environment variables visible."

Avoid wording like:

- "Twelve-Factor requires every project to provide `.env.example`."
- "Every Python project must expose a port."
