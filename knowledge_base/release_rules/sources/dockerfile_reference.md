# Dockerfile Reference

## Source

- URL: https://docs.docker.com/reference/dockerfile/
- Type: official Docker documentation
- Use in ReleaseGuard: baseline guidance for Dockerfile existence, basic Dockerfile structure, and instruction-level evidence.

## Source-Backed Facts

- Dockerfile instructions are not case-sensitive, but uppercase instructions are the convention.
- Docker runs Dockerfile instructions in order.
- A Dockerfile must begin with a `FROM` instruction, after optional parser directives, comments, and globally scoped `ARG` instructions.
- `FROM` initializes a build stage and sets the base image.
- Dockerfile reference defines common instructions such as `FROM`, `WORKDIR`, `COPY`, `ADD`, `RUN`, `CMD`, and `ENTRYPOINT`.

## ReleaseGuard Rule Mapping

| rule_id | ReleaseGuard rule | support_level | blocking_policy | evidence_type | boundary |
|---|---|---|---|---|---|
| RG-DOCKER-001 | Check whether a Dockerfile exists | releaseguard-default | conditional | file_exists | Docker docs define Dockerfile behavior; requiring one depends on whether the project claims containerized release. |
| RG-DOCKER-002 | Check whether Dockerfile contains `FROM` | source-backed | block | docker_instruction | Directly supported by Dockerfile reference. |
| RG-DOCKER-003 | Check whether `FROM` appears in a valid position | source-backed | block | docker_instruction_order | Directly supported by Dockerfile reference, allowing parser directives, comments, and global `ARG`. |
| RG-DOCKER-004 | Check whether Dockerfile contains `WORKDIR` | releaseguard-default | warn | docker_instruction | `WORKDIR` is defined by Docker; requiring it is a ReleaseGuard readability/path-stability policy. |
| RG-DOCKER-005 | Check whether Dockerfile contains `COPY` or `ADD` | releaseguard-default | warn | docker_instruction | `COPY` and `ADD` are defined by Docker; requiring them depends on build style. |
| RG-DOCKER-006 | Check whether Dockerfile includes dependency installation steps | releaseguard-default | warn | docker_instruction | Python dependency-install detection is a ReleaseGuard policy derived from reproducible release needs. |
| RG-DOCKER-007 | Check whether Dockerfile contains `CMD` or `ENTRYPOINT` | releaseguard-default | conditional | docker_instruction | Runnable service images should expose a default start command; special base/build images may differ. |
| RG-DOCKER-008 | Check Dockerfile instruction casing consistency | source-backed | info | docker_instruction_style | Docker documents uppercase as convention, not validity requirement. |

## Implementation Notes

- Phase one should parse text lines, not build images.
- Detect root `Dockerfile` first; `docker/Dockerfile` can be considered later.
- Treat missing Dockerfile as conditional: block only when containerized release is claimed or inferred with high confidence.
- Do not perform image security scanning in phase one.

## Report Language Boundary

Use wording like:

- "Dockerfile reference requires a valid Dockerfile to start with `FROM`, with documented exceptions before it."
- "ReleaseGuard recommends `WORKDIR` for readable and stable paths."

Avoid wording like:

- "Docker requires every project to have a Dockerfile."
