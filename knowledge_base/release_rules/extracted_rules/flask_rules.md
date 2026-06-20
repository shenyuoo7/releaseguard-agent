# Flask Rules

This file groups phase-one Flask detection and release-safety rules.

## Rule Table

| rule_id | rule_name | checker | source | support_level | blocking_policy | evidence_type | phase |
|---|---|---|---|---|---|---|---|
| RG-FLASK-001 | Flask dependency is declared when Flask is used | FlaskDetector | Flask Installation; ReleaseGuard dependency policy | releaseguard-default | block | dependency_line | phase-1 |
| RG-FLASK-002 | Flask application instance is detectable | FlaskDetector | Flask Quickstart | source-backed | block | matched_lines | phase-1 |
| RG-FLASK-003 | Release startup does not rely on the Flask development server | FlaskDetector | Flask Quickstart; Flask Deploying To Production | source-backed | conditional | matched_lines | phase-1 |
| RG-SEC-002 | Flask debug mode is not explicitly enabled for release | FlaskDetector | Flask Debugging; Flask Quickstart | source-backed | block | matched_lines | phase-1 |

## Checker Guidance

- Use Python AST instead of raw substring matching.
- Detect direct and aliased Flask imports.
- Detect assignments whose value calls `Flask(...)`.
- Use `PythonDependencyScanner` for Flask dependency evidence.
- Treat explicit `debug=True` as blocking.
- Treat dynamic debug values as conditional evidence.
- Treat plain `app.run()` as conditional rather than automatically blocking.
- Ignore tests, virtual environments, caches, and generated outputs.

## Boundary Notes

- Factory-pattern support may be added after explicit instance detection.
- Do not claim that dependency-file declaration is directly required by Flask.
- Do not claim that every occurrence of `app.run()` is a production defect.
- Reports should include matched file paths, line numbers, and source lines.