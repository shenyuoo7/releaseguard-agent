# Flask Debugging

## Source

- URL: https://flask.palletsprojects.com/en/stable/debugging/
- Type: official Flask documentation
- Use in ReleaseGuard: direct source mapping for Flask debug-mode findings.

## Source-Backed Facts

- The development server and built-in debugger must not run in production.
- The browser debugger can execute arbitrary Python code.
- A debugger PIN must not be treated as a sufficient security control.
- Passing `debug=True` to `app.run()` enables debug mode.

## ReleaseGuard Rule Mapping

| rule_id | rule | support_level | blocking_policy | evidence_type |
|---|---|---|---|---|
| RG-SEC-002 | Explicit Flask debug mode is disabled for release | source-backed | block | matched_lines |

## Implementation Notes

Phase one should detect explicit AST evidence such as:

- `app.run(debug=True)`
- aliased Flask application variables using `debug=True`

Dynamic values such as `debug=settings.DEBUG` require conditional reporting
because static analysis may not know the release-time value.