# Security Baseline Rules

This file groups phase-one security baseline rules. These rules are intentionally conservative because phase one is not a full security audit.

## Rule Table

| rule_id | rule_name | checker | source | support_level | blocking_policy | evidence_type | phase |
|---|---|---|---|---|---|---|---|
| RG-SEC-001 | Suspected hardcoded sensitive values are detected | SecurityBaselineChecker | OWASP ASVS general basis; exact mapping needed | needs-source-mapping | block | redacted_matched_lines | phase-1 |
| RG-SEC-002 | Flask debug mode is not enabled for release | SecurityBaselineChecker | ReleaseGuard security baseline; Flask source needed | needs-source-mapping | block | matched_lines | phase-1 |
| RG-SEC-003 | Overly broad CORS configuration is detected | SecurityBaselineChecker | OWASP ASVS general basis; exact mapping needed | needs-source-mapping | conditional | matched_lines | phase-1 |
| RG-SEC-004 | Risky command execution patterns are detected | SecurityBaselineChecker | OWASP ASVS command-injection reference | source-backed | conditional | matched_lines | phase-1 |
| RG-SEC-005 | Production security configuration guidance exists | SecurityBaselineChecker | ReleaseGuard default policy | releaseguard-default | warn | doc_section_match | phase-1 |
| RG-SEC-006 | Security report records ASVS version and requirement IDs | SecurityReportChecker | OWASP ASVS | source-backed | info | rule_metadata | phase-2 |

## Checker Guidance

- Phase one should use lightweight static scanning only.
- Secret evidence must be redacted. Never print full token, password, API key, or private credential values.
- `RG-SEC-004` can begin with patterns such as `os.system`, `subprocess` with `shell=True`, and command strings that include untrusted input.
- `RG-SEC-002` should not be implemented as a strong source-backed rule until Flask deployment/security documentation is added.
- `RG-SEC-006` belongs to report quality and phase two unless security report generation is introduced earlier.

## Boundary Notes

- Do not claim full ASVS audit coverage.
- `needs-source-mapping` findings can still be useful, but report language must be careful.
- When exact ASVS IDs are available, include the versioned format such as `v5.0.0-1.2.5`.
