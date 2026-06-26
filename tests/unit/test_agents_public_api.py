from releaseguard_agent import agents
from releaseguard_agent.agents import (
    ADVICE_SCHEMA_VERSION,
    RELEASE_RISK_ANALYSIS_ARTIFACT_SCHEMA_VERSION,
    RELEASE_RISK_ANALYSIS_SCHEMA_VERSION,
    ReleaseDecision,
    ReleaseDecisionAdviceArtifacts,
    ReleaseDecisionAdviceResult,
    ReleaseDecisionAdviceService,
    ReleaseDecisionAdviceServiceResult,
    ReleaseDecisionAdvisor,
    ReleaseDecisionAgent,
    ReleaseDecisionExplainer,
    ReleaseDecisionExplanation,
    ReleaseDecisionFinding,
    ReleaseDecisionStatus,
    ReleaseDecisionSynthesizer,
    ReleaseDecisionWorkflow,
    ReleaseDecisionWorkflowResult,
    ReleaseRiskAnalysis,
    ReleaseRiskAnalysisAgent,
    ReleaseRiskAnalysisArtifacts,
    ReleaseRiskAnalysisContext,
    ReleaseRiskAnalysisParseError,
    ReleaseRiskAnalysisResult,
    build_advice_payload,
    build_release_risk_analysis_payload,
    get_default_rule_index_path,
    render_advice_markdown,
    render_release_fix_plan_markdown,
    render_release_risk_analysis_markdown,
    write_advice_artifacts,
    write_release_risk_analysis_artifacts,
)


EXPECTED_PUBLIC_API = {
    "ADVICE_SCHEMA_VERSION",
    "RELEASE_RISK_ANALYSIS_ARTIFACT_SCHEMA_VERSION",
    "RELEASE_RISK_ANALYSIS_SCHEMA_VERSION",
    "ReleaseDecision",
    "ReleaseDecisionAdviceArtifacts",
    "ReleaseDecisionAdviceResult",
    "ReleaseDecisionAdviceService",
    "ReleaseDecisionAdviceServiceResult",
    "ReleaseDecisionAdvisor",
    "ReleaseDecisionAgent",
    "ReleaseDecisionExplainer",
    "ReleaseDecisionExplanation",
    "ReleaseDecisionFinding",
    "ReleaseDecisionStatus",
    "ReleaseDecisionSynthesizer",
    "ReleaseDecisionWorkflow",
    "ReleaseDecisionWorkflowResult",
    "ReleaseRiskAnalysis",
    "ReleaseRiskAnalysisAgent",
    "ReleaseRiskAnalysisArtifacts",
    "ReleaseRiskAnalysisContext",
    "ReleaseRiskAnalysisParseError",
    "ReleaseRiskAnalysisResult",
    "build_advice_payload",
    "build_release_risk_analysis_payload",
    "get_default_rule_index_path",
    "render_advice_markdown",
    "render_release_fix_plan_markdown",
    "render_release_risk_analysis_markdown",
    "write_advice_artifacts",
    "write_release_risk_analysis_artifacts",
}


def test_agents_public_api_exports_expected_names() -> None:
    assert set(agents.__all__) == EXPECTED_PUBLIC_API


def test_agents_public_api_imports_representative_types() -> None:
    assert ADVICE_SCHEMA_VERSION == "1.0"
    assert RELEASE_RISK_ANALYSIS_ARTIFACT_SCHEMA_VERSION == "1.0"
    assert RELEASE_RISK_ANALYSIS_SCHEMA_VERSION == "1.0"
    assert ReleaseDecisionStatus.READY.value == "ready"

    assert callable(ReleaseDecision)
    assert callable(ReleaseDecisionAdviceArtifacts)
    assert callable(ReleaseDecisionAdviceResult)
    assert callable(ReleaseDecisionAdviceService)
    assert callable(ReleaseDecisionAdviceServiceResult)
    assert callable(ReleaseDecisionAdvisor)
    assert callable(ReleaseDecisionAgent)
    assert callable(ReleaseDecisionExplainer)
    assert callable(ReleaseDecisionExplanation)
    assert callable(ReleaseDecisionFinding)
    assert callable(ReleaseDecisionSynthesizer)
    assert callable(ReleaseDecisionWorkflow)
    assert callable(ReleaseDecisionWorkflowResult)
    assert callable(ReleaseRiskAnalysis)
    assert callable(ReleaseRiskAnalysisAgent)
    assert callable(ReleaseRiskAnalysisArtifacts)
    assert callable(ReleaseRiskAnalysisContext)
    assert callable(ReleaseRiskAnalysisParseError)
    assert callable(ReleaseRiskAnalysisResult)

    assert callable(build_advice_payload)
    assert callable(build_release_risk_analysis_payload)
    assert callable(get_default_rule_index_path)
    assert callable(render_advice_markdown)
    assert callable(render_release_fix_plan_markdown)
    assert callable(render_release_risk_analysis_markdown)
    assert callable(write_advice_artifacts)
    assert callable(write_release_risk_analysis_artifacts)
