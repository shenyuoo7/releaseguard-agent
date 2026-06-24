import releaseguard_agent.observability as observability
from releaseguard_agent.observability import (
    TRACE_FILE_NAME,
    TRACE_SCHEMA_VERSION,
    TraceArtifacts,
    build_trace_payload,
    write_trace_artifact,
)


def test_observability_public_api_exports_trace_writer_helpers() -> None:
    assert TRACE_FILE_NAME == "trace.json"
    assert TRACE_SCHEMA_VERSION == "1.0"
    assert TraceArtifacts.__name__ == "TraceArtifacts"
    assert callable(build_trace_payload)
    assert callable(write_trace_artifact)


def test_observability_public_api_defines_explicit_all() -> None:
    assert observability.__all__ == [
        "TRACE_FILE_NAME",
        "TRACE_SCHEMA_VERSION",
        "TraceArtifacts",
        "build_trace_payload",
        "write_trace_artifact",
    ]
