# Flask Installation

## Source

- URL: https://flask.palletsprojects.com/en/stable/installation/
- Type: official Flask documentation
- Use in ReleaseGuard: Flask dependency and environment guidance.

## Source-Backed Facts

- Flask is installed as a Python package.
- Flask supports Python 3.9 and newer.
- The documentation recommends virtual environments.
- Installing Flask also installs its required distributions.

## ReleaseGuard Rule Mapping

| rule_id | rule | support_level | blocking_policy | evidence_type |
|---|---|---|---|---|
| RG-FLASK-001 | Declare Flask when Flask source usage is detected | releaseguard-default | block | dependency_line |

## Policy Boundary

The documentation establishes that Flask must be installed to run a Flask
application. Requiring it in a project dependency file is ReleaseGuard's
reproducible-release policy, not a direct Flask documentation mandate.

## Implementation Notes

Use `PythonDependencyScanner` to inspect supported project-root dependency
files for the normalized package name `flask`.