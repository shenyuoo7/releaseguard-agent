from releaseguard_agent import api


def test_api_exports_application_factory_and_models() -> None:
    assert api.app.title == "ReleaseGuard Agent API"
    assert callable(api.create_app)
    assert api.ReviewRequest.__name__ == "ReviewRequest"
    assert api.ReviewResponse.__name__ == "ReviewResponse"
    assert api.VerificationRequest.__name__ == "VerificationRequest"
