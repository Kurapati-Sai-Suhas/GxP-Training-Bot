"""Evaluate the existing lexical retrieval against the manually curated gold set.

Read-only: no application table is written. Retrieval itself is unmodified — the production
`select_relevant_chunks` is called exactly as the chatbot calls it.

    uv run python manage.py evaluate_retrieval
    uv run python manage.py evaluate_retrieval --stdout
"""

import json
from pathlib import Path

from django.core.management.base import BaseCommand

from ai_engine import retrieval_evaluation


class Command(BaseCommand):
    help = "Measure the current lexical retrieval against the retrieval gold set."

    def add_arguments(self, parser):
        parser.add_argument("--output-dir", default="evaluation_results")
        parser.add_argument("--stdout", action="store_true", help="Print instead of writing files.")

    def handle(self, *args, **options):
        report = retrieval_evaluation.evaluate_retrieval()
        summary = retrieval_evaluation.summarise(report)
        markdown = retrieval_evaluation.to_markdown(report, summary)

        if options["stdout"]:
            self.stdout.write(markdown)
            return

        out_dir = Path(options["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "retrieval_latest.json").write_text(
            json.dumps(summary, indent=2, default=str), encoding="utf-8")
        (out_dir / "retrieval_latest.md").write_text(markdown, encoding="utf-8")

        corpus = summary["corpus"]
        m = summary["metrics"]
        self.stdout.write(self.style.MIGRATE_HEADING("\nRetrieval baseline — lexical"))
        self.stdout.write(
            f"  gold set v{summary['gold_set_version']} · {summary['queries_scored']} scored · "
            f"{summary['queries_skipped']} skipped"
        )
        if not corpus["retrieval_filters"]:
            self.stdout.write(self.style.WARNING(
                f"  NOTE: largest SOP has {corpus['max_candidate_pool']} chunks and the chatbot "
                f"requests {corpus['chatbot_max_chunks']} — retrieval never filters, it only "
                f"reorders."
            ))
        self.stdout.write(
            f"  Hit@1 {m['hit_at_1']} · Recall@1 {m['recall_at_1']} · Recall@3 {m['recall_at_3']} "
            f"· MRR {m['mrr']}"
        )
        for name, entry in sorted(summary["by_category"].items()):
            self.stdout.write(
                f"    {name:<22} n={entry['n']}  Hit@1={entry['hit_at_1_rate']}  MRR={entry['mrr']}")
        for skip in summary["skipped"]:
            self.stdout.write(self.style.WARNING(f"  skipped {skip['query_id']}: {skip['reason']}"))
        self.stdout.write(f"\n  wrote {out_dir / 'retrieval_latest.json'}")
        self.stdout.write(f"  wrote {out_dir / 'retrieval_latest.md'}")
