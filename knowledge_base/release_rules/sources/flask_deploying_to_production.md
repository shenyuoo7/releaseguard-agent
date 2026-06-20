# Flask Deploying To Production

## Source

- URL: https://flask.palletsprojects.com/en/stable/deploying/
- Type: official Flask documentation
- Use in ReleaseGuard: production startup and development-server risk guidance.

## Source-Backed Facts

- The built-in development server must not be used in production.
- It is intended only for local development.
- It is not designed to be secure, stable, or efficient for production.
- Production deployments should use a dedicated WSGI server or platform.

## ReleaseGuard Rule Mapping

| rule_id | rule | support_level | blocking_policy | evidence_type |
|---|---|---|---|---|
| RG-FLASK-003 | Production startup should not rely on the Flask development server | source-backed | conditional | matched_lines |
| RG-SEC-002 | Flask debugger must not be enabled for release | source-backed | block | matched_lines |

## Implementation Boundary

- Static detection of `app.run()` should be conditional.
- A source file may contain a local-only development entrypoint.
- Stronger future evidence may include Docker commands, process files,
  deployment configuration, or documented production startup commands.