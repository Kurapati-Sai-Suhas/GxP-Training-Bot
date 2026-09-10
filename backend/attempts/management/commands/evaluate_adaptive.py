"""Run the offline evaluation harness and write a machine-readable + human-readable report.

Read-only: this command never writes to application tables. It reads recorded interactions,
evaluates baselines offline, and emits a report.

    uv run python manage.py evaluate_adaptive
    uv run python manage.py evaluate_adaptive --output-dir evaluation_results
    uv run python manage.py evaluate_adaptive --stdout
"""

from pathlib import Path

from django.core.management.base import BaseCommand

from attempts import evaluation


class Command(BaseCommand):
    help = "Evaluate adaptive/predictive baselines offline on recorded learner interactions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output-dir", default="evaluation_results",
            help="Directory for latest.json / latest.md (relative to the backend directory).",
        )
        parser.add_argument(
            "--train-fraction", type=float, default=0.7,
            help="Per-learner fraction of earliest interactions used as history.",
        )
        parser.add_argument(
            "--stdout", action="store_true",
            help="Print the report instead of writing files.",
        )

    def handle(self, *args, **options):
        result = evaluation.evaluate(train_fraction=options["train_fraction"])
        markdown = evaluation.to_markdown(result)

        if options["stdout"]:
            self.stdout.write(markdown)
            return

        out_dir = Path(options["output_dir"])
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "latest.json").write_text(evaluation.to_json(result), encoding="utf-8")
        (out_dir / "latest.md").write_text(markdown, encoding="utf-8")

        d = result["dataset"]
        status = result["status"]
        style = self.style.WARNING if status == "INSUFFICIENT_DATA" else self.style.SUCCESS
        self.stdout.write(style(f"\nStatus: {status}"))
        self.stdout.write(
            f"  {d['usable']} usable of {d['total_rows']} rows "
            f"({d['excluded_synthetic']} synthetic demo, {d['excluded_no_timestamp']} no timestamp, "
            f"{d['excluded_no_snapshot']} no snapshot, {d['excluded_short_sequence']} short sequence)"
        )
        self.stdout.write(
            f"  {d['learners']} learner(s) · train {d['train_interactions']} · "
            f"eval {d['eval_interactions']} (+{d['eval_positives']} / -{d['eval_negatives']})"
        )
        for blocker in result["blockers"]:
            self.stdout.write(self.style.WARNING(f"  blocked: {blocker}"))
        if status == "INSUFFICIENT_DATA":
            self.stdout.write(self.style.WARNING(
                "\n  No valid model performance claim can currently be made.\n"
                "  The harness is verified; the dataset is not yet large enough."
            ))
        self.stdout.write(f"\n  wrote {out_dir / 'latest.json'}")
        self.stdout.write(f"  wrote {out_dir / 'latest.md'}")
