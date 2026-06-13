from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckStatus(str, Enum):
    """Status of a single release check."""

    PASSED = "passed"
    FAILED = "failed"
    WARNING = "warning"
    SKIPPED = "skipped"


class RiskLevel(str, Enum):
    """Risk level of a check result."""

    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CheckResult:
    """Structured result returned by every checker."""

    checker_name: str
    status: CheckStatus
    risk_level: RiskLevel
    title: str
    message: str
    evidence: list[str] = field(default_factory=list)
    recommendation: str | None = None
    rule_id: str | None = None
    rule_source: str | None = None
    file_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        """Return True if the check passed."""
        return self.status == CheckStatus.PASSED

    @property
    def failed(self) -> bool:
        """Return True if the check failed."""
        return self.status == CheckStatus.FAILED

    @property
    def should_block_release(self) -> bool:
        """Return True if this result should block release."""
        return self.status == CheckStatus.FAILED and self.risk_level in {
            RiskLevel.HIGH,
            RiskLevel.CRITICAL,
        }

    def to_dict(self) -> dict[str, Any]:
        """Convert the check result to a plain dictionary."""
        return {
            "checker_name": self.checker_name,
            "status": self.status.value,
            "risk_level": self.risk_level.value,
            "title": self.title,
            "message": self.message,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
            "rule_id": self.rule_id,
            "rule_source": self.rule_source,
            "file_path": self.file_path,
            "metadata": self.metadata,
            "passed": self.passed,
            "failed": self.failed,
            "should_block_release": self.should_block_release,
        }