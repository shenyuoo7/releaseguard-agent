from app import release_message


def test_release_message():
    assert release_message() == "release-ready"
