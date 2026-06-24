from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


EXPECTED_AGENT_SOURCE_FILES = {
    "src/releaseguard_agent/agents/release_decision_advice_service.py",
    "src/releaseguard_agent/agents/release_decision_advice_writer.py",
    "src/releaseguard_agent/agents/release_decision_advisor.py",
    "src/releaseguard_agent/agents/release_decision_agent.py",
    "src/releaseguard_agent/agents/release_decision_explainer.py",
    "src/releaseguard_agent/agents/release_decision_synthesizer.py",
    "src/releaseguard_agent/agents/release_decision_workflow.py",
    "src/releaseguard_agent/rag/check_result_enricher.py",
}


EXPECTED_AGENT_TEST_FILES = {
    "tests/unit/test_agent_advice_public_contract.py",
    "tests/unit/test_agent_rag_public_contract.py",
    "tests/unit/test_agents_public_api.py",
    "tests/unit/test_check_result_enricher.py",
    "tests/unit/test_cli_agent_import_boundary.py",
    "tests/unit/test_rag_public_api.py",
    "tests/unit/test_release_decision_advice_service.py",
    "tests/unit/test_release_decision_advice_writer.py",
    "tests/unit/test_release_decision_advisor.py",
    "tests/unit/test_release_decision_agent.py",
    "tests/unit/test_release_decision_explainer.py",
    "tests/unit/test_release_decision_synthesizer.py",
    "tests/unit/test_release_decision_workflow.py",
}


def test_agent_rag_slice_source_files_exist() -> None:
    missing_files = sorted(
        path
        for path in EXPECTED_AGENT_SOURCE_FILES
        if not (PROJECT_ROOT / path).is_file()
    )

    assert missing_files == []


def test_agent_rag_slice_test_files_exist() -> None:
    missing_files = sorted(
        path
        for path in EXPECTED_AGENT_TEST_FILES
        if not (PROJECT_ROOT / path).is_file()
    )

    assert missing_files == []
