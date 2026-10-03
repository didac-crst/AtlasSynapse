"""Transparent LLM cost estimation from token usage and pricing snapshots."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any

from semantic_memory.models.enums import LlmCostStatus


@dataclass(frozen=True)
class PricingSnapshot:
    """Immutable pricing context used for a cost estimate."""

    version: str
    currency: str
    input_rate_per_token: Decimal | None
    output_rate_per_token: Decimal | None
    provider: str
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "currency": self.currency,
            "input_rate_per_token": (
                None if self.input_rate_per_token is None else str(self.input_rate_per_token)
            ),
            "output_rate_per_token": (
                None if self.output_rate_per_token is None else str(self.output_rate_per_token)
            ),
            "provider": self.provider,
            "model": self.model,
        }


@dataclass(frozen=True)
class CostEstimate:
    amount: Decimal | None
    currency: str | None
    status: LlmCostStatus
    pricing_version: str | None
    pricing_snapshot: dict[str, Any] | None


# Versioned default rates for mock/local estimation. Never invent zero when unknown.
_DEFAULT_PRICING: dict[tuple[str, str], PricingSnapshot] = {
    ("mock", "mock-reviewer"): PricingSnapshot(
        version="atlas-mock-pricing-2026-10",
        currency="USD",
        input_rate_per_token=Decimal("0.000001"),
        output_rate_per_token=Decimal("0.000002"),
        provider="mock",
        model="mock-reviewer",
    ),
}


def resolve_pricing(
    *,
    provider: str,
    model: str,
    pricing_version: str | None = None,
) -> PricingSnapshot | None:
    snapshot = _DEFAULT_PRICING.get((provider, model))
    if snapshot is None:
        return None
    if pricing_version is not None and snapshot.version != pricing_version:
        return None
    return snapshot


def estimate_cost(
    *,
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
    provider_reported_cost: Decimal | None = None,
    provider_reported_currency: str | None = None,
) -> CostEstimate:
    """Estimate cost from tokens, or accept a provider-reported amount.

    Missing rates or tokens yield UNKNOWN with NULL amount — never a fake zero.
    """
    if provider_reported_cost is not None:
        return CostEstimate(
            amount=provider_reported_cost,
            currency=provider_reported_currency or "USD",
            status=LlmCostStatus.PROVIDER_REPORTED,
            pricing_version=None,
            pricing_snapshot={
                "source": "provider_reported",
                "provider": provider,
                "model": model,
            },
        )

    snapshot = resolve_pricing(provider=provider, model=model)
    if (
        snapshot is None
        or snapshot.input_rate_per_token is None
        or snapshot.output_rate_per_token is None
        or input_tokens is None
        or output_tokens is None
    ):
        return CostEstimate(
            amount=None,
            currency=None if snapshot is None else snapshot.currency,
            status=LlmCostStatus.UNKNOWN,
            pricing_version=None if snapshot is None else snapshot.version,
            pricing_snapshot=None if snapshot is None else snapshot.to_dict(),
        )

    amount = (
        Decimal(input_tokens) * snapshot.input_rate_per_token
        + Decimal(output_tokens) * snapshot.output_rate_per_token
    )
    return CostEstimate(
        amount=amount,
        currency=snapshot.currency,
        status=LlmCostStatus.ESTIMATED,
        pricing_version=snapshot.version,
        pricing_snapshot=snapshot.to_dict(),
    )


def pricing_snapshot_dict(snapshot: PricingSnapshot) -> dict[str, Any]:
    return asdict(snapshot)
