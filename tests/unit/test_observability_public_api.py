import releaseguard_agent.observability as observability
from releaseguard_agent.observability import (
    EXECUTION_TRACE_SCHEMA_VERSION,
    ExecutionTraceArtifacts,
    ExecutionTracer,
    TRACE_FILE_NAME,
    TRACE_SCHEMA_VERSION,
    TraceArtifacts,
    TraceSpan,
    build_trace_payload,
    write_trace_artifact,
)


def test_observability_public_api_exports_trace_writer_helpers() -> None:
    assert TRACE_FILE_NAME == "trace.json"
    assert TRACE_SCHEMA_VERSION == "1.0"
    assert TraceArtifacts.__name__ == "TraceArtifacts"
    assert EXECUTION_TRACE_SCHEMA_VERSION == "1.0"
    assert callable(ExecutionTraceArtifacts)
    assert callable(ExecutionTracer)
    assert callable(TraceSpan)
    assert callable(build_trace_payload)
    assert callable(write_trace_artifact)


def test_observability_public_api_defines_explicit_all() -> None:
    assert observability.__all__ == [
        "TRACE_FILE_NAME",
        "TRACE_SCHEMA_VERSION",
        "EXECUTION_TRACE_SCHEMA_VERSION",
        "ExecutionTraceArtifacts",
        "ExecutionTracer",
        "TraceArtifacts",
        "TraceSpan",
        "build_trace_payload",
        "write_trace_artifact",
    ]
