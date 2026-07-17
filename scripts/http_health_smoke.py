"""Poll a ReleaseGuard health endpoint for local and CI smoke tests."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request


def wait_for_health(url: str, *, attempts: int, delay_seconds: float) -> None:
    """Wait until the endpoint returns the exact ReleaseGuard health payload."""
    expected = {
        "status": "ok",
        "service": "releaseguard-agent",
        "deterministic_mode_available": True,
    }
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                payload = json.loads(response.read().decode("utf-8"))
                if response.status == 200 and payload == expected:
                    return
                last_error = RuntimeError(
                    f"unexpected health response: status={response.status}"
                )
        except (OSError, TimeoutError, urllib.error.URLError) as exc:
            last_error = exc
        time.sleep(delay_seconds)
    raise RuntimeError(
        f"ReleaseGuard health check did not succeed after {attempts} attempts"
    ) from last_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--url", default="http://127.0.0.1:8000/health"
    )
    parser.add_argument("--attempts", type=int, default=30)
    parser.add_argument("--delay-seconds", type=float, default=1.0)
    args = parser.parse_args()
    wait_for_health(
        args.url,
        attempts=args.attempts,
        delay_seconds=args.delay_seconds,
    )
    print("ReleaseGuard health smoke passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
