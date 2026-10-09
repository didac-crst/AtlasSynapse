"""Phase C: structure-preserving extraction into knowledge_candidate rows.

Does not resolve identity, ontology, or commit. Produces staged candidates only.

Failure / retry (V1)
--------------------
- Candidate rows are persisted incrementally (no package-wide transaction).
- On failure: already-written candidates are retained for audit; ingestion
  status becomes ``failed`` with ``stats_json.extraction_error``.
- Retry is an explicit transition ``failed → extracting`` (also allowed from
  ``accepted`` / ``resolving`` / in-progress ``extracting``).
- Retry against the same immutable source revision is idempotent: identical
  semantic fingerprints are reused; changed semantics under the same
  structural ``candidate_key`` raise ``KnowledgeExtractionConflictError``.
- Candidates are never deleted automatically by extraction.
"""

from __future__ import annotations

import uuid
from collections import Counter
from typing import Any

from sqlalchemy.orm import Session

from semantic_memory.config import Settings, get_settings
from semantic_memory.exceptions import (
    InvalidStateTransitionError,
    KnowledgeExtractionConflictError,
    ValidationFailedError,
)
from semantic_memory.models.enums import (
    KnowledgeCandidateDerivation,
    KnowledgeCandidateState,
    KnowledgeIngestionStatus,
)
from semantic_memory.models.knowledge_ingestion import KnowledgeCandidate, KnowledgeIngestion
from semantic_memory.repositories.knowledge_ingestion import KnowledgeIngestionRepository
from semantic_memory.repositories.provenance import ProvenanceRepository
from semantic_memory.services.knowledge_extraction.candidate_keys import (
    build_candidate_key,
    semantic_fingerprint,
)
from semantic_memory.services.knowledge_extraction.classifier import (
    EXTRACTOR_VERSION,
    classify_fragments,
)
from semantic_memory.services.knowledge_extraction.formats import detect_package_format
from semantic_memory.services.knowledge_extraction.llm_classifier import (
    KNOWLEDGE_EXTRACTION_PROMPT_VERSION,
    OpenAIKnowledgeFragmentClassifier,
)
from semantic_memory.services.knowledge_extraction.schemas import (
    CandidateDraft,
    ExtractionStats,
    FragmentSkip,
)
from semantic_memory.services.knowledge_extraction.structural import (
    SourceFragment,
    parse_structured_source,
)
from semantic_memory.services.knowledge_ingestion import KnowledgeIngestionService
from semantic_memory.services.llm_logging import DatabaseLlmCallLogger, NoOpLlmCallLogger

_EXTRACT_RETRY_FROM = frozenset(
    {
        KnowledgeIngestionStatus.ACCEPTED.value,
        KnowledgeIngestionStatus.EXTRACTING.value,
        KnowledgeIngestionStatus.RESOLVING.value,
        KnowledgeIngestionStatus.FAILED.value,
    }
)


class KnowledgeExtractionService:
    """Extract YAML/JSON package content into staged knowledge candidates."""

    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
    ) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._ingestions = KnowledgeIngestionService(session)
        self._repo = KnowledgeIngestionRepository(session)
        self._provenance = ProvenanceRepository(session)

    def extract(self, ingestion_id: uuid.UUID) -> ExtractionStats:
        ingestion = self._ingestions.get_ingestion(ingestion_id)
        if ingestion.source_content_revision_id is None:
            raise ValidationFailedError(
                "Extraction requires a fixed source_content_revision_id",
                details={"ingestion_id": str(ingestion_id)},
            )

        revision = self._provenance.get_content_revision(ingestion.source_content_revision_id)
        if revision is None:
            raise ValidationFailedError(
                "source_content_revision_id not found",
                details={
                    "source_content_revision_id": str(ingestion.source_content_revision_id),
                },
            )

        self._begin_extraction(ingestion)

        try:
            hint = None
            if isinstance(revision.metadata_json, dict):
                hint = revision.metadata_json.get("package_format") or revision.metadata_json.get(
                    "content_type"
                )
            package_format = detect_package_format(revision.canonical_content, hint=hint)
            parsed = parse_structured_source(
                revision.canonical_content, package_format=package_format
            )
            drafts, skips = classify_fragments(parsed.fragments)
            drafts, skips = self._maybe_enrich_with_llm(drafts, skips, parsed.fragments)

            created = 0
            reused = 0
            for draft in drafts:
                outcome = self._persist_draft(ingestion, draft)
                if outcome == "created":
                    created += 1
                else:
                    reused += 1

            skipped_by_reason: dict[str, int] = dict(Counter(str(s.reason) for s in skips))
            stats = ExtractionStats(
                fragments_seen=len(parsed.fragments),
                fragments_skipped=len(skips),
                candidates_created=created,
                candidates_reused=reused,
                inferred_candidates=sum(
                    1 for d in drafts if d.derivation == KnowledgeCandidateDerivation.INFERRED
                ),
                normalized_candidates=sum(
                    1 for d in drafts if d.derivation == KnowledgeCandidateDerivation.NORMALIZED
                ),
                explicit_candidates=sum(
                    1 for d in drafts if d.derivation == KnowledgeCandidateDerivation.EXPLICIT
                ),
                skipped_by_reason=skipped_by_reason,
                skipped=skips,
                package_format=package_format.value,
                extractor_version=EXTRACTOR_VERSION,
                classifier=self._settings.knowledge_extraction_mode,
            )
            self._finish_extraction(ingestion, stats)
            return stats
        except Exception as exc:
            # Retain any candidates already persisted; record failure for audit.
            failed = self._ingestions.get_ingestion(ingestion_id)
            error_stats = dict(failed.stats_json or {})
            prior_extraction = error_stats.get("extraction")
            error_stats["extraction_error"] = {
                "type": type(exc).__name__,
                "message": str(exc)[:1000],
                "candidates_retained": len(self._repo.list_candidates(ingestion_id)),
            }
            if prior_extraction is not None:
                error_stats["extraction_partial"] = prior_extraction
            self._repo.update_ingestion(
                failed,
                status=KnowledgeIngestionStatus.FAILED.value,
                stats_json=error_stats,
            )
            raise

    def _begin_extraction(self, ingestion: KnowledgeIngestion) -> None:
        if ingestion.status not in _EXTRACT_RETRY_FROM:
            raise InvalidStateTransitionError(
                "Extraction may only start or retry from accepted|extracting|resolving|failed",
                details={
                    "ingestion_id": str(ingestion.id),
                    "status": ingestion.status,
                },
            )
        # Explicit failed → extracting retry (and other allowed → extracting).
        self._repo.update_ingestion(
            ingestion,
            status=KnowledgeIngestionStatus.EXTRACTING.value,
            pause_reason=None,
            completed_at=None,
        )

    def _maybe_enrich_with_llm(
        self,
        drafts: list[CandidateDraft],
        skips: list[FragmentSkip],
        fragments: list[SourceFragment],
    ) -> tuple[list[CandidateDraft], list[FragmentSkip]]:
        if self._settings.knowledge_extraction_mode != "external":
            return drafts, skips

        ambiguous = [
            frag
            for frag in fragments
            if frag.structural_type == "string"
            and any(
                s.path == frag.path_list()
                and s.reason in {"unsupported_value", "unsupported_semantics"}
                for s in skips
            )
        ]
        if not ambiguous:
            return drafts, skips

        try:
            logger: Any = DatabaseLlmCallLogger(self._session)
        except Exception:  # noqa: BLE001
            logger = NoOpLlmCallLogger()
        classifier = OpenAIKnowledgeFragmentClassifier(self._settings, logger=logger)
        classifications = classifier.classify_ambiguous(ambiguous)
        if not classifications:
            # Keep deterministic skips; record that external enrichment failed closed.
            return drafts, [
                *skips,
                *[
                    FragmentSkip(
                        path=frag.path_list(),
                        reason="external_classifier_failed",
                        detail="no usable LLM classifications",
                    )
                    for frag in ambiguous[:1]  # aggregate signal once
                ],
            ]

        new_skips = [
            s
            for s in skips
            if s.reason not in {"unsupported_value", "unsupported_semantics"}
            or tuple(s.path) not in {tuple(f.path_list()) for f in ambiguous}
        ]
        for item in classifications:
            path_t = tuple(item.path)
            if item.action == "skip":
                new_skips.append(
                    FragmentSkip(
                        path=item.path,
                        reason=item.skip_reason or "unsupported_semantics",
                        detail="llm_skip",
                    )
                )
                continue
            if (
                item.kind is None
                or item.polarity is None
                or item.epistemic_status is None
                or item.derivation is None
                or not item.claim_text
            ):
                new_skips.append(
                    FragmentSkip(
                        path=item.path,
                        reason="unsupported_value",
                        detail="llm_incomplete_classification",
                    )
                )
                continue

            frag = next((f for f in ambiguous if tuple(f.path_list()) == path_t), None)
            key = build_candidate_key(source_context_path=item.path, ordinal=0)
            payload = (
                item.claim_payload.model_dump(exclude_none=True)
                if item.claim_payload is not None
                else {}
            )
            drafts.append(
                CandidateDraft(
                    candidate_key=key,
                    kind=item.kind,
                    polarity=item.polarity,
                    epistemic_status=item.epistemic_status,
                    derivation=item.derivation,
                    claim_text=item.claim_text,
                    claim_payload=payload,
                    source_span=frag.locator() if frag is not None else {"path": item.path},
                    source_context_path=item.path,
                    ordinal=0,
                    classification_basis=item.basis or ["llm"],
                )
            )
        return drafts, new_skips

    def _persist_draft(self, ingestion: KnowledgeIngestion, draft: CandidateDraft) -> str:
        existing = self._repo.find_candidate_by_key(ingestion.id, draft.candidate_key)
        incoming_fp = _draft_fingerprint(draft)
        if existing is None:
            self._ingestions.create_candidate(
                ingestion_id=ingestion.id,
                candidate_key=draft.candidate_key,
                kind=draft.kind,
                polarity=draft.polarity,
                epistemic_status=draft.epistemic_status,
                derivation=draft.derivation,
                claim_text=draft.claim_text,
                claim_payload={
                    **draft.claim_payload,
                    "classification_basis": draft.classification_basis,
                },
                source_span=draft.source_span,
                source_context_path=draft.source_context_path,
                state=KnowledgeCandidateState.EXTRACTED,
            )
            return "created"

        existing_fp = _row_fingerprint(existing)
        if existing_fp != incoming_fp:
            raise KnowledgeExtractionConflictError(
                "Re-extraction produced a different semantic fingerprint "
                "for the same candidate_key",
                details={
                    "ingestion_id": str(ingestion.id),
                    "candidate_key": draft.candidate_key,
                    "existing_fingerprint": existing_fp,
                    "incoming_fingerprint": incoming_fp,
                },
            )
        return "reused"

    def _finish_extraction(self, ingestion: KnowledgeIngestion, stats: ExtractionStats) -> None:
        row = self._ingestions.get_ingestion(ingestion.id)
        merged = dict(row.stats_json or {})
        merged["extraction"] = stats.model_dump(mode="json")
        # Clear prior failure detail on successful completion.
        merged.pop("extraction_error", None)
        merged.pop("extraction_partial", None)

        self._repo.update_ingestion(
            row,
            status=KnowledgeIngestionStatus.RESOLVING.value,
            stats_json=merged,
            completed_at=None,
        )
        if row.extraction_prompt_version is None:
            row.extraction_prompt_version = KNOWLEDGE_EXTRACTION_PROMPT_VERSION
        if row.extraction_model is None:
            row.extraction_model = (
                self._settings.knowledge_extraction_model
                if self._settings.knowledge_extraction_mode == "external"
                else "deterministic"
            )
        self._session.flush()


def _row_fingerprint(row: KnowledgeCandidate) -> str:
    return semantic_fingerprint(
        kind=row.kind,
        polarity=row.polarity,
        epistemic_status=row.epistemic_status,
        derivation=row.derivation,
        claim_text=row.claim_text or "",
        claim_payload=row.claim_payload if isinstance(row.claim_payload, dict) else {},
    )


def _draft_fingerprint(draft: CandidateDraft) -> str:
    return semantic_fingerprint(
        kind=draft.kind.value,
        polarity=draft.polarity.value,
        epistemic_status=draft.epistemic_status.value,
        derivation=draft.derivation.value,
        claim_text=draft.claim_text,
        claim_payload=draft.claim_payload,
    )
