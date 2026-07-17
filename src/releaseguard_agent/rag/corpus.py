from dataclasses import dataclass
from pathlib import Path

from releaseguard_agent.rag.rule_index_retriever import RuleIndexRetriever


@dataclass(frozen=True)
class RuleChunk:
    chunk_id: str
    rule_id: str
    text: str
    source_url: str
    local_source: str
    metadata: dict[str, str]


class RuleCorpusLoader:
    """Load trusted local rule/source records into deterministic chunks."""

    @classmethod
    def from_rule_index(cls, index_path: Path) -> tuple[RuleChunk, ...]:
        retriever = RuleIndexRetriever.from_file(index_path)
        chunks: list[RuleChunk] = []
        for rule in retriever.records:
            documents = rule.source_documents or (None,)
            for index, source in enumerate(documents, start=1):
                source_url = source.source_url if source else ""
                local_source = (
                    source.knowledge_file if source else rule.knowledge_file
                )
                rule_text = source.rule_text if source else rule.rule_name
                rationale = source.rationale if source else rule.source
                chunk_id = f"{rule.rule_id}:chunk-{index:02d}"
                text = "\n".join(
                    (rule.rule_id, rule.rule_name, rule_text, rationale)
                )
                chunks.append(
                    RuleChunk(
                        chunk_id=chunk_id,
                        rule_id=rule.rule_id,
                        text=text,
                        source_url=source_url,
                        local_source=local_source,
                        metadata={
                            "rule_name": rule.rule_name,
                            "priority": rule.priority,
                            "blocking_policy": rule.blocking_policy,
                            "support_level": rule.support_level,
                        },
                    )
                )
        return tuple(chunks)


def get_default_rule_index_path() -> Path:
    return (
        Path(__file__).resolve().parents[3]
        / "knowledge_base"
        / "release_rules"
        / "rule_index.md"
    )
