"""Offline evaluation of the existing lexical retrieval, against a manually curated gold set.

Why this exists
---------------
The project retrieves SOP chunks for the chatbot by lexical word overlap. Whether that is good
enough has never been measured, so any proposal to replace it (embeddings, pgvector, hybrid
retrieval, reranking) would be adopted on assumption rather than evidence. This module measures
the current behaviour first, so a later change can be shown to help rather than merely assumed to.

It does not modify retrieval. `select_relevant_chunks` is imported and called exactly as the
chatbot calls it.

The finding that shapes the metrics
-----------------------------------
Retrieval runs over a *single SOP's* chunks, and the chatbot requests `max_chunks=6`. Every SOP in
this corpus has at most five chunks. **The chatbot's retrieval therefore never filters anything —
it returns the whole document, reordered.**

That makes recall-style metrics at K >= corpus size degenerate: Recall@6 is 1.0 by construction,
and reporting it as a success would be meaningless. What retrieval actually does today is *rank*,
so the honest metrics are the rank-sensitive ones: Recall@1, Hit@1, MRR, and the rank of the first
relevant chunk. Those are reported. Recall@5 is reported too, flagged as degenerate, precisely so
the degeneracy is visible rather than hidden.

Precision@K is reported only at K=1. With one relevant chunk per query, Precision@3 cannot exceed
0.33 however good the ranking is, so comparing it across systems would measure the gold set's
shape rather than the retriever's quality.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from sops.models import SOPChunk, SOPDocument

from .services import select_relevant_chunks

GOLD_SET_PATH = Path(__file__).resolve().parent / "retrieval_gold_set.json"

# The chatbot's own value (ai_engine/services.py). Held here so the evaluation cannot silently
# drift from what production actually requests.
CHATBOT_MAX_CHUNKS = 6


@dataclass
class QueryResult:
    query_id: str
    query: str
    sop_code: str
    category: str
    expected_sections: list[str]
    expected_chunk_ids: list[int]
    retrieved_chunk_ids: list[int]
    retrieved_sections: list[str]
    candidate_pool: int
    first_relevant_rank: int | None   # 1-based; None when no relevant chunk was retrieved
    notes: str = ""

    @property
    def hit_at_1(self) -> bool:
        return self.first_relevant_rank == 1

    def recall_at(self, k: int) -> float | None:
        """Fraction of relevant chunks appearing in the top k. None when nothing is relevant."""
        if not self.expected_chunk_ids:
            return None
        top = set(self.retrieved_chunk_ids[:k])
        found = len(top & set(self.expected_chunk_ids))
        return found / len(self.expected_chunk_ids)

    def precision_at(self, k: int) -> float | None:
        if not self.expected_chunk_ids:
            return None
        top = self.retrieved_chunk_ids[:k]
        if not top:
            return 0.0
        return len(set(top) & set(self.expected_chunk_ids)) / len(top)

    @property
    def reciprocal_rank(self) -> float | None:
        if not self.expected_chunk_ids:
            return None
        return 0.0 if self.first_relevant_rank is None else 1.0 / self.first_relevant_rank


@dataclass
class EvaluationReport:
    gold_set_version: str
    results: list[QueryResult] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)

    @property
    def scored(self) -> list[QueryResult]:
        """Queries with at least one relevant chunk. The irrelevant-query case is reported
        separately because recall over an empty relevant set is undefined, not zero."""
        return [r for r in self.results if r.expected_chunk_ids]


def load_gold_set(path: Path | None = None) -> dict:
    return json.loads((path or GOLD_SET_PATH).read_text(encoding="utf-8"))


def _resolve_chunks(sop_code: str, section_titles: list[str]):
    """Map (sop_code, section_title) onto live chunk ids.

    Keying the gold set on titles rather than primary keys is what lets it survive a reseed;
    resolution failures are surfaced as skips rather than silently scoring zero.
    """
    sop = SOPDocument.objects.filter(sop_code=sop_code).first()
    if sop is None:
        return None, None, f"SOP {sop_code} not present in this database"
    chunks = list(SOPChunk.objects.filter(sop=sop).order_by("id"))
    if not chunks:
        return None, None, f"SOP {sop_code} has no chunks"
    by_title = {c.section_title: c for c in chunks}
    resolved = []
    for title in section_titles:
        chunk = by_title.get(title)
        if chunk is None:
            return None, None, f"section '{title}' not found in {sop_code}"
        resolved.append(chunk.id)
    return chunks, resolved, None


def evaluate_retrieval(path: Path | None = None, max_chunks: int = CHATBOT_MAX_CHUNKS):
    """Run every gold-set query through the production retrieval function."""
    gold = load_gold_set(path)
    report = EvaluationReport(gold_set_version=gold["version"])

    for case in gold["queries"]:
        chunks, expected_ids, error = _resolve_chunks(case["sop_code"], case["relevant_sections"])
        if error:
            report.skipped.append({"query_id": case["id"], "reason": error})
            continue

        retrieved = select_relevant_chunks(case["query"], chunks, max_chunks=max_chunks)
        retrieved_ids = [c.id for c in retrieved]

        first_rank = None
        for position, chunk_id in enumerate(retrieved_ids, start=1):
            if chunk_id in expected_ids:
                first_rank = position
                break

        report.results.append(QueryResult(
            query_id=case["id"],
            query=case["query"],
            sop_code=case["sop_code"],
            category=case["category"],
            expected_sections=case["relevant_sections"],
            expected_chunk_ids=expected_ids,
            retrieved_chunk_ids=retrieved_ids,
            retrieved_sections=[c.section_title for c in retrieved],
            candidate_pool=len(chunks),
            first_relevant_rank=first_rank,
            notes=case.get("notes", ""),
        ))
    return report


def _mean(values):
    values = [v for v in values if v is not None]
    return round(sum(values) / len(values), 4) if values else None


def summarise(report: EvaluationReport) -> dict:
    scored = report.scored
    pools = {r.candidate_pool for r in report.results}
    max_pool = max(pools) if pools else 0

    by_category: dict[str, dict] = {}
    for result in scored:
        entry = by_category.setdefault(result.category, {"n": 0, "hit_at_1": 0, "rr": []})
        entry["n"] += 1
        entry["hit_at_1"] += 1 if result.hit_at_1 else 0
        entry["rr"].append(result.reciprocal_rank)
    for entry in by_category.values():
        entry["hit_at_1_rate"] = round(entry["hit_at_1"] / entry["n"], 4)
        entry["mrr"] = _mean(entry.pop("rr"))

    return {
        "gold_set_version": report.gold_set_version,
        "queries_total": len(report.results),
        "queries_scored": len(scored),
        "queries_skipped": len(report.skipped),
        "skipped": report.skipped,
        "corpus": {
            "max_candidate_pool": max_pool,
            "chatbot_max_chunks": CHATBOT_MAX_CHUNKS,
            "retrieval_filters": max_pool > CHATBOT_MAX_CHUNKS,
        },
        "metrics": {
            "hit_at_1": _mean([1.0 if r.hit_at_1 else 0.0 for r in scored]),
            "recall_at_1": _mean([r.recall_at(1) for r in scored]),
            "recall_at_3": _mean([r.recall_at(3) for r in scored]),
            "recall_at_5": _mean([r.recall_at(5) for r in scored]),
            "precision_at_1": _mean([r.precision_at(1) for r in scored]),
            "mrr": _mean([r.reciprocal_rank for r in scored]),
        },
        "metric_caveats": {
            "recall_at_5": (
                "DEGENERATE. Every SOP here has at most "
                f"{max_pool} chunks and the chatbot requests {CHATBOT_MAX_CHUNKS}, so retrieval "
                "returns the whole document. A value of 1.0 reflects the corpus size, not "
                "retrieval quality."
            ),
            "precision_at_k": (
                "Reported at K=1 only. Most queries have a single relevant chunk, so Precision@3 "
                "is capped at 0.33 regardless of ranking quality."
            ),
            "sample_size": (
                f"{len(scored)} scored queries. Auditable, not statistically representative. "
                "No confidence intervals are computed and no result here is significance-tested."
            ),
        },
        "by_category": by_category,
    }


def to_markdown(report: EvaluationReport, summary: dict) -> str:
    m = summary["metrics"]
    lines = [
        "# Retrieval Baseline — lexical",
        "",
        f"Gold set `v{summary['gold_set_version']}` · {summary['queries_scored']} scored queries "
        f"· {summary['queries_skipped']} skipped",
        "",
        "## The headline finding",
        "",
        f"Largest candidate pool is **{summary['corpus']['max_candidate_pool']} chunks**; the "
        f"chatbot requests **{summary['corpus']['chatbot_max_chunks']}**. Retrieval therefore "
        f"**{'filters' if summary['corpus']['retrieval_filters'] else 'never filters — it returns the whole document, reordered'}**.",
        "",
        "## Metrics",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Hit@1 | {m['hit_at_1']} |",
        f"| Recall@1 | {m['recall_at_1']} |",
        f"| Recall@3 | {m['recall_at_3']} |",
        f"| Recall@5 | {m['recall_at_5']} (degenerate) |",
        f"| Precision@1 | {m['precision_at_1']} |",
        f"| MRR | {m['mrr']} |",
        "",
        "## By query category",
        "",
        "| Category | n | Hit@1 | MRR |",
        "|---|---|---|---|",
    ]
    for name, entry in sorted(summary["by_category"].items()):
        lines.append(f"| {name} | {entry['n']} | {entry['hit_at_1_rate']} | {entry['mrr']} |")

    lines += ["", "## Per-query", "", "| ID | Category | Rank of first relevant | Expected | Top-1 retrieved |", "|---|---|---|---|---|"]
    for r in report.results:
        rank = r.first_relevant_rank if r.expected_chunk_ids else "n/a (no relevant chunk)"
        top1 = r.retrieved_sections[0] if r.retrieved_sections else "—"
        lines.append(
            f"| {r.query_id} | {r.category} | {rank} | "
            f"{', '.join(r.expected_sections) or '(none)'} | {top1} |"
        )

    lines += ["", "## Caveats", ""]
    for key, text in summary["metric_caveats"].items():
        lines.append(f"- **{key}** — {text}")
    return "\n".join(lines)
