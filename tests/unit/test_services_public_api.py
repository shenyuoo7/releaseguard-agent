from releaseguard_agent import services


def test_release_review_service_is_public() -> None:
    assert services.ReleaseReviewService.__name__ == "ReleaseReviewService"
    assert services.ReleaseReviewResult.__name__ == "ReleaseReviewResult"
    assert services.InvalidProjectPathError.__name__ == (
        "InvalidProjectPathError"
    )
    assert services.LLMReviewService.__name__ == "LLMReviewService"
    assert services.LLMReviewAnalysisResult.__name__ == (
        "LLMReviewAnalysisResult"
    )
