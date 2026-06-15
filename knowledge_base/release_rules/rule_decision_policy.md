# Rule Decision Policy

This file defines how ReleaseGuard classifies rule evidence and default release impact.

## Purpose

ReleaseGuard should move forward with practical checks while staying honest about source authority.

The policy separates:

- Directly source-backed requirements or behaviors.
- ReleaseGuard product policies derived from sources and engineering practice.
- Useful rules that still need stronger source mapping before audit-level claims.
- Rules that should be deferred until the architecture is mature enough.

## Support Levels

### `source-backed`

Use this when the current source directly supports the rule.

Allowed report language:

- "Source-backed by [source name]."
- "The source documents this behavior or requirement."

Examples:

- pytest default discovery of `test_*.py` and `*_test.py`.
- Dockerfile `FROM` placement rules.
- FastAPI examples using `FastAPI()` and `TestClient(app)`.

### `releaseguard-default`

Use this when the rule is ReleaseGuard's release-readiness policy, derived from source principles and mature engineering practice, but not directly mandated by the source.

Allowed report language:

- "ReleaseGuard default policy."
- "Recommended for release-readiness."

Examples:

- Requiring `.env.example` as a practical way to document environment variables.
- Recommending `/health` for web services.
- Warning on missing `WORKDIR` in Dockerfile.

### `needs-source-mapping`

Use this when the rule is valuable but needs a more exact standard, version, or framework source before strong compliance wording.

Allowed report language:

- "ReleaseGuard baseline; stronger source mapping needed."
- "Do not treat this as a completed compliance control yet."

Examples:

- Secret scanning rules before exact ASVS mapping.
- Flask `debug=True` before adding direct Flask deployment/security documentation.
- Broad CORS checks before exact CORS/security source mapping.

### `deferred`

Use this when the rule should not be implemented in phase one.

Common reasons:

- Requires container builds or real external services.
- Requires deep AST/data-flow analysis.
- Requires complete security audit scope.
- Requires mature Agent, RAG, eval, or observability infrastructure.

## Blocking Policies

### `block`

The finding should stop release.

Use for:

- Failing automated tests.
- No dependency declaration in a Python project.
- Invalid Dockerfile structure when a Dockerfile exists.
- Real or strongly suspected secrets, with redacted evidence.

### `warn`

The finding should be reported but does not block phase-one release by default.

Use for:

- Missing `tests/` directory when tests may exist elsewhere.
- Missing health-check route in phase one.
- Missing optional Dockerfile readability conventions.
- Missing production security documentation.

### `info`

The finding is informational or style-oriented.

Use for:

- Dockerfile instruction casing convention.
- Source-mapping completeness notes.

### `conditional`

The release impact depends on project context.

Use for:

- Missing Dockerfile when containerized release is not claimed.
- Missing `.env.example` when no environment-variable usage is detected.
- CORS wildcard usage when environment context is unclear.
- Web entrypoint checks in generic Python projects.

## Evidence Principles

- Prefer evidence that can be checked deterministically in phase one.
- Include paths, line numbers, command strings, exit codes, and short summaries.
- Redact secrets and sensitive values.
- Keep metadata fields stable so reports and Agent reasoning can consume them later.

## Report Guardrails

- Never describe a ReleaseGuard default policy as an official source requirement.
- Never claim full ASVS coverage in phase one.
- Never display full secret values.
- When a security rule references ASVS, include versioned requirement IDs once exact mapping exists.
