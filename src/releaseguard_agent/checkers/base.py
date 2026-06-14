from abc import ABC, abstractmethod
from pathlib import Path

from releaseguard_agent.models.check_result import CheckResult


class BaseChecker(ABC):
    """Base class for all release readiness checkers."""

    name: str
    description: str

    @abstractmethod
    def run(self, project_path: Path) -> list[CheckResult]:
        """Run the checker against a target project path."""
        raise NotImplementedError