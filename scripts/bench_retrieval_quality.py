#!/usr/bin/env python3
"""Run retrieval gold-set against LAN search_memory (GET /v1/memory/search).

Baseline metrics before hybrid retrieval changes. Does not modify retrieval logic.

Usage:
  set -a; . /srv/satellite/secrets/atlas-synapse.secret.env; set +a
  python3 scripts/bench_retrieval_quality.py
  python3 scripts/bench_retrieval_quality.py --base http://10.10.0.12:5060 --limit 25
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES = ROOT / "benchmarks" / "retrieval" / "v1" / "cases.json"
DEFAULT_OUT = ROOT / "benchmarks" / "retrieval" / "v1" / "baseline_report.json"

# Primary tag classes used for checkpoint deltas.
_QUERY_CLASSES = (
    "diagnostic",
    "multi_token",
    "multi_concept",
    "lexical_ok",
    "temporal",
    "predicate_intent",
    "graph",
    "family",
    "historical",
    "quality",
    "entity",
    "goal",
)


@dataclass
class HitView:
    hit_type: str
    entity_id: str | None = None
    statement_id: str | None = None
    predicate_key: str | None = None
    status: str | None = None
    ranking_score: float = 0.0
    entity_name: str | None = None


@dataclass
class CaseResult:
    case_id: str
    query: str
    hit_count: int
    relevant_ranks: list[int] = field(default_factory=list)
    first_relevant_rank: int | None = None
    recall_at_1: bool = False
    recall_at_3: bool = False
    recall_at_10: bool = False
    zero_hits: bool = False
    superseded_in_topk: bool = False
    must_not_hits: list[str] = field(default_factory=list)
    top_hits: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


def _resolve(ref: str, aliases: dict[str, str]) -> str:
    return aliases.get(ref, ref)


def _load_cases(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    doc = json.loads(path.read_text())
    entities = dict(doc.get("entities") or {})
    statements = dict(doc.get("statements") or {})
    aliases = {**entities, **statements}
    return aliases, list(doc.get("cases") or [])


def _request_json(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode())


def search_memory(base: str, token: str, query: str, limit: int) -> dict[str, Any]:
    qs = urllib.parse.urlencode({"query": query, "limit": limit})
    return _request_json(f"{base.rstrip('/')}/v1/memory/search?{qs}", token)


def _parse_hits(payload: dict[str, Any]) -> list[HitView]:
    out: list[HitView] = []
    for hit in payload.get("hits") or []:
        ht = hit.get("hit_type") or ""
        score = float(hit.get("ranking_score") or 0.0)
        if ht == "entity" and hit.get("entity"):
            ent = hit["entity"]
            out.append(
                HitView(
                    hit_type="entity",
                    entity_id=str(ent["id"]),
                    ranking_score=score,
                    entity_name=ent.get("canonical_name"),
                )
            )
        elif ht == "statement" and hit.get("statement"):
            st = hit["statement"]
            out.append(
                HitView(
                    hit_type="statement",
                    statement_id=str(st["id"]),
                    entity_id=str(st["subject_entity_id"]) if st.get("subject_entity_id") else None,
                    predicate_key=st.get("predicate_key"),
                    status=st.get("status"),
                    ranking_score=score,
                )
            )
            if st.get("object_entity_id"):
                # Object entity counts as a related entity signal for anchoring cases.
                out[-1]  # keep statement row; object checked via expected entity separately
        else:
            out.append(HitView(hit_type=ht or "unknown", ranking_score=score))
    return out


def _hit_entity_ids(hits: list[HitView], raw_hits: list[dict[str, Any]]) -> set[str]:
    ids: set[str] = set()
    for h in hits:
        if h.entity_id:
            ids.add(h.entity_id)
    for hit in raw_hits:
        st = hit.get("statement") or {}
        if st.get("object_entity_id"):
            ids.add(str(st["object_entity_id"]))
        if st.get("subject_entity_id"):
            ids.add(str(st["subject_entity_id"]))
        ent = hit.get("entity") or {}
        if ent.get("id"):
            ids.add(str(ent["id"]))
    return ids


def _is_relevant(
    case: dict[str, Any],
    *,
    aliases: dict[str, str],
    hits: list[HitView],
    raw_hits: list[dict[str, Any]],
    k: int,
) -> tuple[list[int], list[str]]:
    """Return 1-based ranks of relevant hits within top-k, and must-not statement ids found."""
    top = hits[:k]
    raw_top = raw_hits[:k]
    expected_entities = {
        _resolve(x, aliases) for x in (case.get("expected_entity_ids") or [])
    }
    expected_statements = {
        _resolve(x, aliases) for x in (case.get("expected_statement_ids") or [])
    }
    expected_predicates = set(case.get("expected_predicate_keys") or [])
    name_needles = [n.casefold() for n in (case.get("accept_entity_name_contains") or [])]
    must_not = {_resolve(x, aliases) for x in (case.get("must_not_statement_ids") or [])}

    ranks: list[int] = []
    found_must_not: list[str] = []
    entity_ids_topk = _hit_entity_ids(top, raw_top)

    # If case has no concrete expectations beyond name needles / predicates /
    # entity, treat carefully.
    has_concrete = bool(expected_entities or expected_statements or name_needles)

    for idx, (h, raw) in enumerate(zip(top, raw_top), start=1):
        sid = h.statement_id
        if sid and sid in must_not:
            found_must_not.append(sid)
        relevant = False
        if sid and sid in expected_statements:
            relevant = True
        if h.entity_id and h.entity_id in expected_entities:
            relevant = True
        if h.hit_type == "statement":
            st = raw.get("statement") or {}
            if st.get("object_entity_id") and str(st["object_entity_id"]) in expected_entities:
                relevant = True
            if st.get("subject_entity_id") and str(st["subject_entity_id"]) in expected_entities:
                # subject alone is weak; only if we also wanted predicate or no statements
                if expected_predicates and st.get("predicate_key") in expected_predicates:
                    relevant = True
                elif not expected_statements and h.entity_id in expected_entities:
                    relevant = True
            if expected_predicates and st.get("predicate_key") in expected_predicates:
                # predicate match alone is weak unless entity also matches
                if entity_ids_topk & expected_entities or not expected_entities:
                    if expected_entities:
                        if entity_ids_topk & expected_entities:
                            relevant = True
                    else:
                        relevant = True
        if name_needles and h.entity_name:
            name = h.entity_name.casefold()
            if any(n in name for n in name_needles):
                relevant = True
        if name_needles:
            st = raw.get("statement") or {}
            blob = " ".join(
                str(x)
                for x in (
                    st.get("object_string"),
                    st.get("normalized_object"),
                    st.get("predicate_key"),
                )
                if x
            ).casefold()
            if any(n in blob for n in name_needles):
                relevant = True
        if relevant:
            ranks.append(idx)

    # Entity-set presence: if any expected entity appears in top-k payload, count
    # the first hit that introduced it (already covered) OR if only entities
    # expected and they appear via object ids on statements.
    if has_concrete and not ranks and expected_entities and (entity_ids_topk & expected_entities):
        # Find first raw hit that mentions an expected entity.
        for idx, raw in enumerate(raw_top, start=1):
            ent = raw.get("entity") or {}
            st = raw.get("statement") or {}
            ids = {
                str(x)
                for x in (
                    ent.get("id"),
                    st.get("subject_entity_id"),
                    st.get("object_entity_id"),
                )
                if x
            }
            if ids & expected_entities:
                ranks.append(idx)
                break

    # Predicate-only cases (e.g. former roles): first statement with that predicate.
    if not ranks and expected_predicates and not expected_statements and not expected_entities:
        for idx, raw in enumerate(raw_top, start=1):
            st = raw.get("statement") or {}
            if st.get("predicate_key") in expected_predicates:
                ranks.append(idx)
                break

    return ranks, found_must_not


def evaluate_case(
    case: dict[str, Any],
    *,
    aliases: dict[str, str],
    payload: dict[str, Any],
    k_max: int,
) -> CaseResult:
    raw_hits = list(payload.get("hits") or [])
    hits = _parse_hits(payload)
    ranks, must_not_hits = _is_relevant(
        case, aliases=aliases, hits=hits, raw_hits=raw_hits, k=k_max
    )
    first = ranks[0] if ranks else None
    top_summary = []
    for h, raw in zip(hits[:5], raw_hits[:5]):
        if h.hit_type == "entity":
            top_summary.append(
                {
                    "type": "entity",
                    "id": h.entity_id,
                    "name": h.entity_name,
                    "score": round(h.ranking_score, 4),
                }
            )
        else:
            st = raw.get("statement") or {}
            top_summary.append(
                {
                    "type": "statement",
                    "id": h.statement_id,
                    "predicate": st.get("predicate_key"),
                    "status": st.get("status"),
                    "score": round(h.ranking_score, 4),
                }
            )
    return CaseResult(
        case_id=str(case["id"]),
        query=str(case["query"]),
        hit_count=len(hits),
        relevant_ranks=ranks,
        first_relevant_rank=first,
        recall_at_1=bool(first is not None and first <= 1),
        recall_at_3=bool(first is not None and first <= 3),
        recall_at_10=bool(first is not None and first <= 10),
        zero_hits=len(hits) == 0,
        superseded_in_topk=bool(must_not_hits),
        must_not_hits=must_not_hits,
        top_hits=top_summary,
        tags=list(case.get("tags") or []),
    )


def _pct(n: int, d: int) -> float:
    return round(100.0 * n / d, 1) if d else 0.0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="http://10.10.0.12:5060")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=25)
    parser.add_argument("--token-env", default="HTTP_API_TOKEN")
    args = parser.parse_args()

    token = os.environ.get(args.token_env) or ""
    if not token:
        raise SystemExit(f"Set {args.token_env} (e.g. source atlas-synapse.secret.env)")

    aliases, cases = _load_cases(args.cases)
    results: list[CaseResult] = []
    errors: list[dict[str, str]] = []

    print(f"Retrieval quality baseline — {len(cases)} cases against {args.base}")
    print(f"limit={args.limit} cases={args.cases}")

    for case in cases:
        q = case["query"]
        try:
            payload = search_memory(args.base, token, q, args.limit)
        except urllib.error.HTTPError as exc:
            errors.append({"id": case["id"], "error": str(exc)})
            print(f"  ERR {case['id']}: {exc}")
            continue
        result = evaluate_case(case, aliases=aliases, payload=payload, k_max=args.limit)
        results.append(result)
        mark = "OK" if result.recall_at_3 else ("ZERO" if result.zero_hits else "MISS")
        rr = result.first_relevant_rank or "-"
        print(
            f"  {mark:4} R@{rr:<3} hits={result.hit_count:<3} {case['id']}: {q!r}"
        )

    n = len(results)
    r1 = sum(1 for r in results if r.recall_at_1)
    r3 = sum(1 for r in results if r.recall_at_3)
    r10 = sum(1 for r in results if r.recall_at_10)
    zeros = sum(1 for r in results if r.zero_hits)
    superseded = sum(1 for r in results if r.superseded_in_topk)
    rr_values = [
        1.0 / r.first_relevant_rank
        for r in results
        if r.first_relevant_rank is not None
    ]
    mrr = statistics.mean(rr_values) if rr_values else 0.0

    diagnostic = [r for r in results if "diagnostic" in r.tags]
    multi = [r for r in results if "multi_token" in r.tags or "multi_concept" in r.tags]
    lexical_ok = [r for r in results if "lexical_ok" in r.tags]

    by_class: dict[str, dict[str, Any]] = {}
    for tag in _QUERY_CLASSES:
        subset = [r for r in results if tag in r.tags]
        if not subset:
            continue
        sn = len(subset)
        by_class[tag] = {
            "n": sn,
            "recall_at_1": sum(1 for r in subset if r.recall_at_1),
            "recall_at_1_pct": _pct(sum(1 for r in subset if r.recall_at_1), sn),
            "recall_at_3": sum(1 for r in subset if r.recall_at_3),
            "recall_at_3_pct": _pct(sum(1 for r in subset if r.recall_at_3), sn),
            "recall_at_10": sum(1 for r in subset if r.recall_at_10),
            "recall_at_10_pct": _pct(sum(1 for r in subset if r.recall_at_10), sn),
            "zero_hit_cases": sum(1 for r in subset if r.zero_hits),
            "zero_hit_rate_pct": _pct(sum(1 for r in subset if r.zero_hits), sn),
        }

    summary = {
        "n_cases": n,
        "n_errors": len(errors),
        "limit": args.limit,
        "base": args.base,
        "recall_at_1": r1,
        "recall_at_1_pct": _pct(r1, n),
        "recall_at_3": r3,
        "recall_at_3_pct": _pct(r3, n),
        "recall_at_10": r10,
        "recall_at_10_pct": _pct(r10, n),
        "mrr": round(mrr, 4),
        "zero_hit_cases": zeros,
        "zero_hit_rate_pct": _pct(zeros, n),
        "superseded_in_topk_cases": superseded,
        "superseded_hit_rate_pct": _pct(superseded, n),
        "diagnostic_zero": sum(1 for r in diagnostic if r.zero_hits),
        "diagnostic_n": len(diagnostic),
        "multi_token_zero": sum(1 for r in multi if r.zero_hits),
        "multi_token_n": len(multi),
        "lexical_ok_recall_at_3": sum(1 for r in lexical_ok if r.recall_at_3),
        "lexical_ok_n": len(lexical_ok),
        "by_class": by_class,
    }

    print("\n=== Baseline summary ===")
    for key in (
        "n_cases",
        "recall_at_1_pct",
        "recall_at_3_pct",
        "recall_at_10_pct",
        "mrr",
        "zero_hit_rate_pct",
        "superseded_hit_rate_pct",
        "diagnostic_zero",
        "multi_token_zero",
        "lexical_ok_recall_at_3",
    ):
        print(f"  {key}: {summary[key]}")

    print("\n=== By query class ===")
    for tag, stats in by_class.items():
        print(
            f"  {tag}: n={stats['n']} "
            f"R@1={stats['recall_at_1_pct']}% "
            f"R@3={stats['recall_at_3_pct']}% "
            f"R@10={stats['recall_at_10_pct']}% "
            f"zero={stats['zero_hit_rate_pct']}%"
        )

    report = {
        "summary": summary,
        "errors": errors,
        "results": [r.__dict__ for r in results],
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nWrote {args.out}")


if __name__ == "__main__":
    main()
