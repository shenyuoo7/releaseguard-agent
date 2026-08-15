# Fixture Trusted Source

## Source

- URL: https://example.com/fixed-relation-fixture
- Type: documentation

## ReleaseGuard Rule Mapping

| rule_id | ReleaseGuard rule | support_level | blocking_policy | evidence_type | boundary |
|---|---|---|---|---|---|
| RG-FIXTURE-001 | Verify fixture tests | source-backed | block | directory_exists | Fixed fixture test boundary. |
| RG-FIXTURE-002 | Verify fixture health | source-backed | warn | endpoint_exists | Fixed fixture health boundary. |
