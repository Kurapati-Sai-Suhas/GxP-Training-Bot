"""Preflight check: are the configured AI models actually invocable right now?

WHY THIS EXISTS
---------------
This project's fallback contract deliberately swallows every provider failure so the
pipeline degrades instead of breaking. That is the right availability behaviour, and it is
also how both configured models stayed dead for two weeks without anyone noticing:

    meta/llama-3.1-8b-instruct   end of life 2026-08-26   HTTP 410 Gone
    nvidia/nv-embedqa-e5-v5      end of life 2026-08-25   HTTP 410 Gone

Every generation silently produced offline-fallback questions; every heading-less document
silently chunked by fixed length. Nothing errored, so nothing alerted.

A fallback is a safety net, not a monitor. This command is the monitor: it asserts that the
models named by NVIDIA_NIM_MODEL / NVIDIA_EMBED_MODEL can actually be called, and exits
non-zero when they cannot -- so it can gate a deploy, run on a schedule, or back a
readiness probe.

    python manage.py check_ai_provider              # human-readable
    python manage.py check_ai_provider --json       # machine-readable, for alerting
    python manage.py check_ai_provider --list       # what the account can actually invoke

Listing a model in the catalogue does NOT mean the account may invoke it -- catalogue entries
return HTTP 404 at call time when entitlement is missing. This command tests real calls.
"""
import json
import os
import time

from django.core.management.base import BaseCommand

from ai_engine.services import NVIDIA_NIM_BASE_URL, classify_llm_error, nim_model
from sops.services import embed_model

PROBE_PROMPT = "Reply with the single word OK."
PROBE_TEXT = "Gowning sequence for cleanroom entry."


class Command(BaseCommand):
    help = "Verify the configured NVIDIA NIM chat and embedding models are invocable."

    def add_arguments(self, parser):
        parser.add_argument("--json", action="store_true", dest="as_json",
                            help="Emit a machine-readable report for alerting.")
        parser.add_argument("--list", action="store_true", dest="list_models",
                            help="List catalogue models (does not prove invocability).")

    def handle(self, *args, **options):
        api_key = os.getenv("NVIDIA_API_KEY")
        report = {
            "provider": "nvidia_nim",
            "base_url": NVIDIA_NIM_BASE_URL,
            "api_key_configured": bool(api_key),
            "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "checks": {},
        }

        if not api_key:
            report["status"] = "NO_API_KEY"
            report["summary"] = (
                "NVIDIA_API_KEY is not set. Every AI call will take the deterministic "
                "offline fallback path. This is a valid configuration for CI and local "
                "development; it is a misconfiguration in production."
            )
            return self._emit(report, options, exit_code=1)

        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=NVIDIA_NIM_BASE_URL)

        if options["list_models"]:
            try:
                names = sorted(m.id for m in client.models.list().data)
                report["catalogue"] = names
                report["catalogue_count"] = len(names)
            except Exception as exc:  # noqa: BLE001 - a diagnostic command reports, never raises
                report["catalogue_error"] = str(exc)[:300]

        report["checks"]["chat"] = self._probe_chat(client)
        report["checks"]["embedding"] = self._probe_embedding(client)

        failed = [n for n, c in report["checks"].items() if not c["ok"]]
        retired = [n for n, c in report["checks"].items() if c.get("error_category") == "model_retired"]

        if not failed:
            report["status"] = "OK"
            report["summary"] = "All configured models are invocable."
            code = 0
        elif retired:
            report["status"] = "MODEL_RETIRED"
            report["summary"] = (
                f"Retired model(s): {', '.join(retired)}. This is permanent — retrying will "
                f"not help. Point the corresponding environment variable at a replacement."
            )
            code = 2
        else:
            report["status"] = "DEGRADED"
            report["summary"] = f"Unavailable: {', '.join(failed)}. Affected paths use the offline fallback."
            code = 1
        return self._emit(report, options, exit_code=code)

    def _probe_chat(self, client):
        model = nim_model()
        started = time.time()
        try:
            result = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": PROBE_PROMPT}],
                max_tokens=5, temperature=0,
            )
            return {
                "ok": True, "model": model,
                "latency_ms": round((time.time() - started) * 1000),
                "sample": (result.choices[0].message.content or "").strip()[:40],
                "impact_if_down": "Question generation and SOP chat fall back to the offline generator.",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False, "model": model,
                "latency_ms": round((time.time() - started) * 1000),
                "error_category": classify_llm_error(exc), "error": str(exc)[:300],
                "impact_if_down": "Question generation and SOP chat fall back to the offline generator.",
            }

    def _probe_embedding(self, client):
        model = embed_model()
        started = time.time()
        try:
            result = client.embeddings.create(
                model=model, input=[PROBE_TEXT],
                extra_body={"input_type": "passage", "truncate": "END"},
            )
            return {
                "ok": True, "model": model,
                "latency_ms": round((time.time() - started) * 1000),
                "dimensions": len(result.data[0].embedding),
                "impact_if_down": "Tier 2 (Max-Min semantic) chunking is skipped; heading-aware "
                                  "and fixed-length tiers still operate.",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False, "model": model,
                "latency_ms": round((time.time() - started) * 1000),
                "error_category": classify_llm_error(exc), "error": str(exc)[:300],
                "impact_if_down": "Tier 2 (Max-Min semantic) chunking is skipped; heading-aware "
                                  "and fixed-length tiers still operate.",
            }

    def _emit(self, report, options, exit_code):
        if options["as_json"]:
            self.stdout.write(json.dumps(report, indent=2))
        else:
            self._emit_human(report)
        if exit_code:
            raise SystemExit(exit_code)

    def _emit_human(self, report):
        style = {"OK": self.style.SUCCESS}.get(report["status"], self.style.ERROR)
        self.stdout.write("")
        self.stdout.write(f"AI provider: {report['provider']}  ({report['base_url']})")
        self.stdout.write(f"API key configured: {report['api_key_configured']}")
        self.stdout.write("")
        for name, check in report.get("checks", {}).items():
            mark = "  OK  " if check["ok"] else " FAIL "
            line = self.style.SUCCESS(mark) if check["ok"] else self.style.ERROR(mark)
            self.stdout.write(f"{line} {name:10s} {check['model']}")
            if check["ok"]:
                detail = f"       {check['latency_ms']} ms"
                if "dimensions" in check:
                    detail += f" · {check['dimensions']} dims"
                self.stdout.write(detail)
            else:
                self.stdout.write(self.style.WARNING(f"       category: {check['error_category']}"))
                self.stdout.write(f"       {check['error'][:160]}")
                self.stdout.write(f"       impact: {check['impact_if_down']}")
        if "catalogue_count" in report:
            self.stdout.write("")
            self.stdout.write(f"Catalogue lists {report['catalogue_count']} models "
                              f"(listing does not prove invocability).")
        self.stdout.write("")
        self.stdout.write(style(f"Status: {report['status']}"))
        self.stdout.write(f"  {report['summary']}")
        self.stdout.write("")
