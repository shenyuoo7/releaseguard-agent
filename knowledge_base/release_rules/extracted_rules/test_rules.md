# Test Rules

This file groups phase-one pytest release-readiness rules.

## Rule Table

| rule_id | rule_name | checker | source | support_level | blocking_policy | evidence_type | phase |
|---|---|---|---|---|---|---|---|
| RG-TEST-001 | Root `tests/` directory exists | TestStructureChecker | pytest Good Integration Practices; ReleaseGuard default policy | releaseguard-default | warn | directory_exists | phase-1 |
| RG-TEST-002 | Pytest-discoverable test files exist | TestStructureChecker | pytest Good Integration Practices | source-backed | block | file_glob_matches | phase-1 |
| RG-TEST-003 | Pytest can collect at least one test item | PytestExecutionChecker | pytest Good Integration Practices | source-backed | block | collected_tests | phase-1 |
| RG-TEST-004 | Pytest collect-only command succeeds | PytestExecutionChecker | pytest Good Integration Practices | source-backed | block | command_result | phase-1 |
| RG-TEST-005 | Pytest run command succeeds | PytestExecutionChecker | pytest Good Integration Practices | source-backed | block | command_result | phase-1 |
| RG-TEST-006 | Pytest configuration exists | PytestConfigChecker | pytest Good Integration Practices; ReleaseGuard default policy | releaseguard-default | warn | file_exists | phase-1 |
| RG-TEST-007 | `src` layout import behavior is reproducible | PytestConfigChecker | pytest Good Integration Practices | source-backed | conditional | config_value | phase-1 |

## Checker Guidance

- `RG-TEST-001` through `RG-TEST-005` are already implemented in the first checker milestone.
- `RG-TEST-006` can check for `pytest.ini`, `pytest.toml`, `.pytest.ini`, `pyproject.toml`, `tox.ini`, or `setup.cfg`.
- `RG-TEST-007` should apply only when a `src/` layout is detected.
- `RG-TEST-007` may accept `pythonpath = src`, `PYTHONPATH=src` documentation, or a future editable-install packaging workflow.
- Use `python -m pytest` in commands and report text.

## Boundary Notes

- Missing `tests/` is warning-level in phase one because pytest supports multiple layouts.
- Missing pytest-discoverable test files is blocking because the release check cannot verify behavior.
- `collected 0 items` should be treated as blocking for ReleaseGuard, even if pytest can exit successfully in some situations.
