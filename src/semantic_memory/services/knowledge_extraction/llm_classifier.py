"""Optional bounded OpenAI classifier for ambiguous fragments.

Reuses the same urllib + json_schema + pydantic pattern as semantic review /
identity adjudication. Deterministic classification remains authoritative for
clear structural cases; this module only runs when mode=external and fragments
are marked ambiguous.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any

from semantic_memory.config import Settings
from semantic_memory.models.enums import LlmCallStatus
from semantic_memory.services.knowledge_extraction.schemas import (
    LlmExtractionBatchResult,
    LlmFragmentClassification,
)
from semantic_memory.services.knowledge_extraction.structural import SourceFragment
from semantic_memory.services.llm_logging import (
    LlmCallCompletion,
    LlmCallContext,
    LlmCallLogger,
    NoOpLlmCallLogger,
)

KNOWLEDGE_EXTRACTION_PROMPT_VERSION = "knowledge-extraction-v1"

_SYSTEM_PROMPT = """You classify knowledge-ingestion source fragments for AtlasSynapse.

You receive path context AND leaf values. Ancestors matter: a string under
rejected_or_weakened_hypotheses is a rejected hypothesis, not an active assertion.

Rules:
1. Return structured JSON only matching the schema.
2. Do not invent ontology classes or entity IDs.
3. Set epistemic_status explicitly (active|rejected|superseded|open|answered).
4. Rejected is NOT a polarity. Rejected hypotheses keep polarity=positive.
5. Negative recommendations use polarity=negative and epistemic_status=active.
6. Skip metadata/container/non-semantic fragments.
7. Prefer skip over guessing when meaning is unclear.
"""


class OpenAIKnowledgeFragmentClassifier:
    """Classify ambiguous fragments with strict structured output."""

    def __init__(
        self,
        settings: Settings,
        *,
        logger: LlmCallLogger | None = None,
    ) -> None:
        self._settings = settings
        self._logger = logger or NoOpLlmCallLogger()
        self.provider = settings.knowledge_extraction_provider
        self.model = settings.knowledge_extraction_model

    @property
    def _api_key(self) -> str:
        return self._settings.knowledge_extraction_api_key or self._settings.semantic_review_api_key

    @property
    def _api_base(self) -> str:
        return (
            self._settings.knowledge_extraction_api_base or self._settings.semantic_review_api_base
        )

    def classify_ambiguous(
        self, fragments: list[SourceFragment]
    ) -> list[LlmFragmentClassification]:
        if not fragments:
            return []
        if not self._api_key:
            return []

        payload = {
            "fragments": [
                {
                    "path": frag.path_list(),
                    "value": frag.raw_value,
                    "structural_type": frag.structural_type,
                    "parent_key": frag.parent_key,
                }
                for frag in fragments
            ]
        }
        row = self._logger.begin(
            LlmCallContext(
                purpose="knowledge_extraction",
                provider=self.provider,
                model=self.model,
                model_version=KNOWLEDGE_EXTRACTION_PROMPT_VERSION,
                metadata={"fragment_count": len(fragments)},
            )
        )
        started = time.perf_counter()
        try:
            parsed, usage = self._call_model(payload)
            result = LlmExtractionBatchResult.model_validate(parsed)
            self._logger.complete(
                row,
                LlmCallCompletion(
                    status=LlmCallStatus.SUCCEEDED,
                    outcome="classified",
                    input_tokens=usage.get("input_tokens"),
                    output_tokens=usage.get("output_tokens"),
                    total_tokens=usage.get("total_tokens"),
                    metadata={
                        "latency_ms": int((time.perf_counter() - started) * 1000),
                        "prompt_template_version": KNOWLEDGE_EXTRACTION_PROMPT_VERSION,
                    },
                ),
            )
            return result.classifications
        except Exception as exc:  # noqa: BLE001 — fail closed to deterministic path
            self._logger.complete(
                row,
                LlmCallCompletion(
                    status=LlmCallStatus.FAILED,
                    outcome="failed",
                    error_code="KNOWLEDGE_EXTRACTION_LLM_FAILED",
                    error_message=str(exc)[:500],
                ),
            )
            return []

    def _call_model(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        body = {
            "model": self.model,
            "max_completion_tokens": self._settings.knowledge_extraction_max_output_tokens,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "knowledge_extraction_batch",
                    "strict": True,
                    "schema": LlmExtractionBatchResult.model_json_schema(),
                },
            },
        }
        request = urllib.request.Request(
            f"{self._api_base.rstrip('/')}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(
            request, timeout=self._settings.knowledge_extraction_timeout_seconds
        ) as response:
            raw = json.loads(response.read().decode("utf-8"))
        content = raw["choices"][0]["message"]["content"]
        parsed = json.loads(content)
        usage_raw = raw.get("usage") or {}
        usage = {
            "input_tokens": usage_raw.get("prompt_tokens"),
            "output_tokens": usage_raw.get("completion_tokens"),
            "total_tokens": usage_raw.get("total_tokens"),
            "provider_raw_id": raw.get("id"),
        }
        return parsed, usage
