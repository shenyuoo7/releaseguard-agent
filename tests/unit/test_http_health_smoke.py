import json
from email.message import Message
from unittest.mock import patch

from scripts.http_health_smoke import wait_for_health


class _FakeResponse:
    status = 200

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(
            {
                "status": "ok",
                "service": "releaseguard-agent",
                "deterministic_mode_available": True,
            }
        ).encode()

    def info(self) -> Message:
        return Message()


def test_health_smoke_accepts_only_the_releaseguard_payload() -> None:
    with patch("urllib.request.urlopen", return_value=_FakeResponse()):
        wait_for_health(
            "http://127.0.0.1:8000/health",
            attempts=1,
            delay_seconds=0,
        )
