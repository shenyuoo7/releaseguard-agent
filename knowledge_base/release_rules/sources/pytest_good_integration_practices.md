# pytest Good Integration Practices

## Source

- URL: https://docs.pytest.org/en/stable/explanation/goodpractices.html
- Type: official pytest documentation
- Use in ReleaseGuard: baseline guidance for Python test discovery, test layout, virtual environment use, and reproducible import configuration.

## Source-Backed Facts

- pytest recommends using virtual environments and installing project dependencies with pip.
- pytest's default discovery includes `test_*.py` and `*_test.py` files.
- pytest collects test functions and methods whose names are prefixed with `test`.
- pytest documents `src` layout considerations and supports `pythonpath = src` in pytest configuration.
- pytest documentation explains the difference between invoking `pytest` directly and `python -m pytest`.

## ReleaseGuard Rule Mapping

| rule_id | ReleaseGuard rule | support_level | blocking_policy | evidence_type | boundary |
|---|---|---|---|---|---|
| RG-TEST-001 | Check for a `tests/` directory | releaseguard-default | warn | directory_exists | pytest supports multiple layouts; ReleaseGuard treats a root `tests/` directory as a first-phase clarity convention. |
| RG-TEST-002 | Check for pytest-discoverable test files | source-backed | block | file_glob_matches | Directly based on pytest default file discovery patterns. |
| RG-TEST-003 | Check that pytest can collect at least one test item | source-backed | block | collected_tests | Directly based on pytest collection behavior. |
| RG-TEST-004 | Check that `python -m pytest --collect-only -q` succeeds | source-backed | block | command_result | Collection failure indicates test/import/config problems before release. |
| RG-TEST-005 | Check that `python -m pytest -q` succeeds | source-backed | block | command_result | A failing automated test suite should block release. |
| RG-TEST-006 | Check for pytest configuration | releaseguard-default | warn | file_exists | pytest supports configuration; requiring a config file is a ReleaseGuard reproducibility policy. |
| RG-TEST-007 | Check `src` layout import reproducibility | source-backed | conditional | config_value | pytest documents `PYTHONPATH` and `pythonpath` for `src` layout. |

## Implementation Notes

- Use `python -m pytest` in generated commands and dynamic checks.
- Treat `collected 0 items` as blocking for release checks, because it means the test command is not verifying the project.
- Missing `tests/` alone is a warning in phase one if pytest can still discover valid tests elsewhere.
- For `src` layout, accept either documented pytest import configuration or a future editable-install packaging workflow.

## Report Language Boundary

Use wording like:

- "pytest default discovery looks for `test_*.py` and `*_test.py` files."
- "ReleaseGuard recommends a visible `tests/` directory for release-readiness clarity."

Avoid wording like:

- "pytest requires every project to put tests under a root `tests/` directory."
