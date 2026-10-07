"""Actor capability constants."""

from enum import StrEnum


class Capability(StrEnum):
    KNOWLEDGE_READ = "knowledge.read"
    KNOWLEDGE_WRITE = "knowledge.write"
    ONTOLOGY_READ = "ontology.read"
    ONTOLOGY_PROPOSE = "ontology.propose"
    ONTOLOGY_APPLY = "ontology.apply"
    FEEDBACK_CREATE = "feedback.create"
    FEEDBACK_READ = "feedback.read"
    FEEDBACK_MANAGE = "feedback.manage"
    ADMIN = "admin"


DEFAULT_AGENT_CAPABILITIES: tuple[Capability, ...] = (
    Capability.KNOWLEDGE_READ,
    Capability.KNOWLEDGE_WRITE,
    Capability.ONTOLOGY_READ,
    Capability.ONTOLOGY_PROPOSE,
    Capability.ONTOLOGY_APPLY,
    Capability.FEEDBACK_CREATE,
)
