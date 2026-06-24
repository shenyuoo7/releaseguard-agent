from dataclasses import dataclass


@dataclass(frozen=True)
class RuleEvidence:
    """Structured evidence loaded from the release rule index."""

    rule_id: str
    rule_name: str
    checker: str
    source: str
    support_level: str
    priority: str
    blocking_policy: str
    evidence_type: str
    phase: str
    knowledge_file: str
    line_number: int

    def to_dict(self) -> dict[str, object]:
        """Convert the rule evidence to a plain dictionary."""
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "checker": self.checker,
            "source": self.source,
            "support_level": self.support_level,
            "priority": self.priority,
            "blocking_policy": self.blocking_policy,
            "evidence_type": self.evidence_type,
            "phase": self.phase,
            "knowledge_file": self.knowledge_file,
            "line_number": self.line_number,
        }
