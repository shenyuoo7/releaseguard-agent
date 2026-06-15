# Docker Rules

This file groups phase-one Dockerfile release-readiness rules.

## Rule Table

| rule_id | rule_name | checker | source | support_level | blocking_policy | evidence_type | phase |
|---|---|---|---|---|---|---|---|
| RG-DOCKER-001 | Dockerfile exists when containerized release is expected | DockerChecker | Dockerfile reference; ReleaseGuard container policy | releaseguard-default | conditional | file_exists | phase-1 |
| RG-DOCKER-002 | Dockerfile contains `FROM` | DockerChecker | Dockerfile reference | source-backed | block | docker_instruction | phase-1 |
| RG-DOCKER-003 | `FROM` appears in a valid position | DockerChecker | Dockerfile reference | source-backed | block | docker_instruction_order | phase-1 |
| RG-DOCKER-004 | Dockerfile contains `WORKDIR` | DockerChecker | Dockerfile reference; ReleaseGuard default policy | releaseguard-default | warn | docker_instruction | phase-1 |
| RG-DOCKER-005 | Dockerfile contains `COPY` or `ADD` | DockerChecker | Dockerfile reference; ReleaseGuard default policy | releaseguard-default | warn | docker_instruction | phase-1 |
| RG-DOCKER-006 | Dockerfile contains dependency installation steps | DockerChecker | Dockerfile reference; ReleaseGuard Python image policy | releaseguard-default | warn | docker_instruction | phase-1 |
| RG-DOCKER-007 | Dockerfile contains `CMD` or `ENTRYPOINT` | DockerChecker | Dockerfile reference; ReleaseGuard runnable image policy | releaseguard-default | conditional | docker_instruction | phase-1 |
| RG-DOCKER-008 | Dockerfile instruction casing follows uppercase convention | DockerStyleChecker | Dockerfile reference | source-backed | info | docker_instruction_style | phase-1 |

## Checker Guidance

- Start with root-level `Dockerfile`.
- Parse Dockerfile text; do not build images in phase one.
- `RG-DOCKER-003` should allow parser directives, comments, and global `ARG` before `FROM`.
- `RG-DOCKER-006` can start with Python-focused matches such as `pip install`, `python -m pip install`, `uv sync`, or dependency file copy/install patterns.
- `RG-DOCKER-008` should never block release because Docker instructions are case-insensitive.

## Boundary Notes

- Missing Dockerfile is conditional, not universally blocking.
- Do not claim Docker image security scanning coverage.
- Do not require `COPY` or `ADD` for every possible Dockerfile; report it as a warning unless context proves it is necessary.
