# Flask Quickstart

## Source

- URL: https://flask.palletsprojects.com/en/stable/quickstart/
- Type: official Flask documentation
- Use in ReleaseGuard: application detection and development-mode risk guidance.

## Source-Backed Facts

- A minimal application imports `Flask`.
- The example creates `app = Flask(__name__)`.
- The resulting object is the WSGI application.
- The built-in server is intended for development and testing.
- The debugger must not be used in production.

## ReleaseGuard Rule Mapping

| rule_id | rule | support_level | blocking_policy | evidence_type |
|---|---|---|---|---|
| RG-FLASK-002 | Detect an explicit Flask application instance | source-backed | block | matched_lines |
| RG-FLASK-003 | Detect development-server startup code | source-backed | conditional | matched_lines |
| RG-SEC-002 | Detect explicitly enabled Flask debug mode | source-backed | block | matched_lines |

## Implementation Boundary

- Phase one should detect explicit `Flask(...)` assignments.
- Application factories may be supported later.
- `app.run()` alone does not prove production usage.
- Explicit `debug=True` is stronger release-blocking evidence.