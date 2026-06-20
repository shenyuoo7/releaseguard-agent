# OWASP Application Security Verification Standard ASVS

## Source

- URL: https://owasp.org/www-project-application-security-verification-standard/
- Type: recognized application security verification standard
- Use in ReleaseGuard: high-level security baseline reference and future source mapping target for security rules.

## Source-Backed Facts

- OWASP ASVS provides a basis for testing web application technical security controls.
- ASVS also gives developers a list of secure development requirements.
- ASVS 5.0.0 is identified as the latest stable version on the OWASP project page at the time this knowledge entry was normalized.
- ASVS requirements should be referenced with a version and requirement identifier such as `v5.0.0-1.2.5`.
- The OWASP page gives OS command injection as an example requirement mapping.

## ReleaseGuard Rule Mapping

| rule_id | ReleaseGuard rule | support_level | blocking_policy | evidence_type | boundary |
|---|---|---|---|---|---|
| RG-SEC-001 | Check for suspected hardcoded sensitive values | needs-source-mapping | block | redacted_matched_lines | Useful security baseline, but needs exact ASVS or other source mapping before audit-level claims. |
| RG-SEC-003 | Check for overly broad CORS configuration | needs-source-mapping | conditional | matched_lines | Useful baseline, but needs exact CORS/security source mapping. |
| RG-SEC-004 | Check for risky command execution patterns | source-backed | conditional | matched_lines | ASVS page includes OS command injection requirement reference format and example. |
| RG-SEC-005 | Check for production security configuration documentation | releaseguard-default | warn | doc_section_match | ReleaseGuard handoff policy; not directly mandated by ASVS. |
| RG-SEC-006 | Check whether security reports preserve ASVS version and requirement IDs | source-backed | info | rule_metadata | Directly supported by ASVS reference-format guidance. |

## Implementation Notes

- Phase one can do lightweight text scanning only.
- Evidence for secrets must be redacted and must not display full secret values.
- Do not claim full ASVS coverage in phase one.
- Security rules that are not mapped to exact requirements should be reported as ReleaseGuard baseline findings or `needs-source-mapping`.

## Report Language Boundary

Use wording like:

- "This finding is inspired by ReleaseGuard's security baseline and should later be mapped to a precise ASVS requirement."
- "For ASVS-backed claims, include the ASVS version and requirement identifier."

Avoid wording like:

- "ReleaseGuard has completed an ASVS audit."
- "Every security baseline rule is already fully ASVS-compliant."
