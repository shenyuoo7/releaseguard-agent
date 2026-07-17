from collections.abc import Iterable
from pathlib import Path


class ProjectPathNotAllowedError(ValueError):
    """Raised when an API target escapes every configured review root."""


class ProjectPathPolicy:
    """Resolve API project paths and constrain them to trusted local roots."""

    def __init__(self, allowed_roots: Iterable[Path]) -> None:
        roots = tuple(
            dict.fromkeys(Path(root).expanduser().resolve() for root in allowed_roots)
        )
        if not roots:
            raise ValueError("At least one allowed project root is required.")
        self._allowed_roots = roots

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        return self._allowed_roots

    def resolve_allowed(self, raw_path: str) -> Path:
        """Return a normalized path only when it stays inside an allowed root."""
        candidate = Path(raw_path).expanduser().resolve()
        if any(
            candidate == root or candidate.is_relative_to(root)
            for root in self._allowed_roots
        ):
            return candidate
        raise ProjectPathNotAllowedError(
            "The requested project path is outside the configured review roots."
        )
