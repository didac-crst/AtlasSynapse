"""OpenAI-backed identity adjudicator (bounded evidence; fail closed)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from typing import Any

from semantic_memory.config import Settings
from semantic_memory.schemas.identity import CandidateDecision
from semantic_memory.schemas.identity_adjudication import (
    BoundedIdentityPackage,
    IdentityAdjudicationRequest,
    IdentityAdjudicationResult,
    IdentityCandidateAdjudication,
    IdentityLlmDecision,
    map_llm_decision,
)
from semantic_memory.services.identity_evidence_package import (
    package_identity_evidence,
    prompt_package_dict,
)

IDENTITY_ADJUDICATION_PROMPT_VERSION = "identity-adjudication-v1"

_SYSTEM_PROMPT = """You adjudicate entity identity for AtlasSynapse.

You receive a bounded evidence package for ONE candidate at a time.
You have NO database access. Use only the supplied evidence.

Hard rules:
1. Decide only among: SAME_ENTITY, DIFFERENT_ENTITY, UNCERTAIN.
2. If evidence is incomplete, conflicting, or insufficient, return UNCERTAIN.
3. Prefer UNCERTAIN over guessing. False merges are worse than duplicates.
4. Do NOT invent evidence. cited_evidence_ids must be a subset of the supplied
   evidence_id values for that candidate. Do not invent signals, statement IDs,
   or facts that are not in the package.
5. summary may only paraphrase or reference supplied signals/evidence_ids;
   it must not introduce new factual claims.
6. Exact name similarity alone is not enough for SAME_ENTITY.
7. Graph neighbor overlap is supporting context only; it is never decisive by itself.

Return structured JSON only."""


class OpenAIIdentityAdjudicator:
    """Call OpenAI per candidate with a hard-capped evidence package."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self.provider = settings.identity_review_provider
        self.model = settings.identity_review_model

    @property
    def _api_key(self) -> str:
        return self._settings.identity_review_api_key or self._settings.semantic_review_api_key

    @property
    def _api_base(self) -> str:
        return self._settings.identity_review_api_base or self._settings.semantic_review_api_base

    def adjudicate(self, request: IdentityAdjudicationRequest) -> IdentityAdjudicationResult:
        if not self._api_key:
            return IdentityAdjudicationResult(
                provider=self.provider,
                model=self.model,
                prompt_template_version=IDENTITY_ADJUDICATION_PROMPT_VERSION,
                metadata={"error": "missing_api_key", "fail_closed": True},
            )

        package = package_identity_evidence(
            incoming_canonical_name=request.incoming_canonical_name,
            namespace_key=request.namespace_key,
            class_key=request.class_key,
            candidates=request.candidates,
            settings=self._settings,
        )

        decisions: list[IdentityCandidateAdjudication] = []
        call_metas: list[dict[str, Any]] = []
        total_input = 0
        total_output = 0
        latency_ms = 0

        for candidate in package.candidates:
            single = BoundedIdentityPackage(
                incoming_canonical_name=package.incoming_canonical_name,
                namespace_key=package.namespace_key,
                class_key=package.class_key,
                candidates=[candidate],
                truncated_candidates=package.truncated_candidates,
                truncated_evidence=package.truncated_evidence,
            )
            allowed_ids = {item.evidence_id for item in candidate.evidence}
            started = time.perf_counter()
            try:
                parsed, usage = self._call_model(single)
                latency_ms += int((time.perf_counter() - started) * 1000)
                decision_row = self._parse_candidate_decision(
                    parsed,
                    entity_id=candidate.entity_id,
                    candidate_id=candidate.candidate_id,
                    allowed_evidence_ids=allowed_ids,
                )
                decisions.append(decision_row)
                call_metas.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "entity_id": str(candidate.entity_id),
                        "ok": True,
                        "usage": usage,
                    }
                )
                total_input += int(usage.get("input_tokens") or 0)
                total_output += int(usage.get("output_tokens") or 0)
            except Exception as exc:  # noqa: BLE001 — fail closed per candidate
                latency_ms += int((time.perf_counter() - started) * 1000)
                decisions.append(
                    IdentityCandidateAdjudication(
                        entity_id=candidate.entity_id,
                        candidate_id=candidate.candidate_id,
                        decision=CandidateDecision.UNCERTAIN,
                        llm_decision=IdentityLlmDecision.UNCERTAIN,
                        summary=None,
                    )
                )
                call_metas.append(
                    {
                        "candidate_id": candidate.candidate_id,
                        "entity_id": str(candidate.entity_id),
                        "ok": False,
                        "error": str(exc)[:300],
                        "fail_closed": True,
                    }
                )

        return IdentityAdjudicationResult(
            candidate_decisions=decisions,
            provider=self.provider,
            model=self.model,
            prompt_template_version=IDENTITY_ADJUDICATION_PROMPT_VERSION,
            metadata={
                "calls": call_metas,
                "candidate_ids": [c.candidate_id for c in package.candidates],
                "entity_ids": [str(c.entity_id) for c in package.candidates],
                "input_tokens": total_input or None,
                "output_tokens": total_output or None,
                "latency_ms": latency_ms,
                "truncated_candidates": package.truncated_candidates,
                "truncated_evidence": package.truncated_evidence,
                "prompt_template_version": IDENTITY_ADJUDICATION_PROMPT_VERSION,
            },
        )

    def _call_model(self, package: BoundedIdentityPackage) -> tuple[dict[str, Any], dict[str, Any]]:
        user_prompt = self._build_user_prompt(package)
        body: dict[str, Any] = {
            "model": self.model,
            "max_completion_tokens": self._settings.identity_review_max_output_tokens,
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "identity_adjudication_result",
                    "strict": True,
                    "schema": _json_schema(),
                },
            },
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self._settings.identity_review_reasoning_effort:
            body["reasoning_effort"] = self._settings.identity_review_reasoning_effort

        payload = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            url=f"{self._api_base.rstrip('/')}/chat/completions",
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self._settings.identity_review_timeout_seconds
            ) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"openai_http_{exc.code}:{detail[:300]}") from exc
        except TimeoutError as exc:
            raise RuntimeError("openai_timeout") from exc
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"openai_unavailable:{type(exc).__name__}") from exc

        try:
            content = raw["choices"][0]["message"]["content"]
            parsed = json.loads(content) if isinstance(content, str) else content
            if not isinstance(parsed, dict):
                raise ValueError("response_not_object")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError("identity_adjudicator_malformed_response") from exc

        usage = raw.get("usage") or {}
        meta = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "provider_raw_id": raw.get("id"),
        }
        return parsed, meta

    def _build_user_prompt(self, package: BoundedIdentityPackage) -> str:
        return "\n".join(
            [
                "INCOMING ENTITY:",
                json.dumps(
                    {
                        "canonical_name": package.incoming_canonical_name,
                        "namespace_key": package.namespace_key,
                        "class_key": package.class_key,
                    },
                    sort_keys=True,
                ),
                "",
                "CANDIDATE EVIDENCE PACKAGE:",
                json.dumps(prompt_package_dict(package), sort_keys=True, default=str),
                "",
                "TASK:",
                "Adjudicate whether the candidate is the same real-world entity.",
                "If uncertain, return UNCERTAIN.",
                "Cite only evidence_id values present in the package.",
            ]
        )

    def _parse_candidate_decision(
        self,
        parsed: dict[str, Any],
        *,
        entity_id: uuid.UUID,
        candidate_id: str,
        allowed_evidence_ids: set[str],
    ) -> IdentityCandidateAdjudication:
        rows = parsed.get("candidates")
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("identity_adjudicator_missing_candidates")
        row = rows[0]
        if not isinstance(row, dict):
            raise RuntimeError("identity_adjudicator_bad_candidate_row")

        raw_decision = str(row.get("decision") or "")
        try:
            llm_decision = IdentityLlmDecision(raw_decision)
        except ValueError as exc:
            raise RuntimeError(f"identity_adjudicator_bad_decision:{raw_decision}") from exc

        cited_raw = row.get("cited_evidence_ids") or []
        if not isinstance(cited_raw, list):
            cited_raw = []
        cited = [str(item) for item in cited_raw if item]
        valid = [item for item in cited if item in allowed_evidence_ids]
        invented = [item for item in cited if item not in allowed_evidence_ids]
        summary = row.get("summary")
        if summary is not None:
            summary = str(summary)[:500]

        return IdentityCandidateAdjudication(
            entity_id=entity_id,
            candidate_id=candidate_id,
            decision=map_llm_decision(llm_decision),
            llm_decision=llm_decision,
            summary=summary,
            cited_evidence_ids=valid,
            rejected_invented_evidence_ids=invented,
        )


def _json_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "candidates": {
                "type": "array",
                "minItems": 1,
                "maxItems": 1,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "candidate_id": {"type": "string"},
                        "decision": {
                            "type": "string",
                            "enum": [d.value for d in IdentityLlmDecision],
                        },
                        "summary": {"type": "string"},
                        "cited_evidence_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                    },
                    "required": [
                        "candidate_id",
                        "decision",
                        "summary",
                        "cited_evidence_ids",
                    ],
                },
            }
        },
        "required": ["candidates"],
    }
