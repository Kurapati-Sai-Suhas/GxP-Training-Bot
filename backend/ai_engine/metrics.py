"""Inference telemetry for the AI layer.

WHY THIS EXISTS
---------------
The fallback contract means an AI outage produces no errors, no 5xx, and no user-visible
symptom -- the pipeline quietly serves deterministic offline output instead. That is correct
availability behaviour and it is also, exactly, how both configured models stayed retired
for two weeks unnoticed.

You cannot alert on an exception that is never raised. What you CAN alert on is the ratio
of calls taking the fallback path. That is the signal this module exists to produce:

    fallback_rate > 0        the live model path is degraded
    fallback_rate == 1.0     the live model path is entirely gone
    error_category           WHY, in operator-actionable terms

SCOPE, HONESTLY STATED
----------------------
These counters are in-process and reset when the process restarts. With multiple gunicorn
workers each holds its own view. That is a deliberate trade for a deployment of this size:
no new infrastructure, no new dependency, no failure mode of its own.

They are the *local* half of the story. The durable half is the structured log line emitted
alongside every record() call, which carries the same fields and is what CloudWatch Logs
Insights (or Prometheus, via an exporter) actually aggregates across workers and over time.
Scaling this up means pointing an exporter at snapshot() or querying the logs -- not
rewriting the call sites.
"""
import logging
import threading
import time

logger = logging.getLogger("ai_engine.metrics")

_lock = threading.Lock()
_STARTED_AT = time.time()

# operation -> counters. Operations are the two live-model call sites plus chunking.
_counters = {}
_recent_errors = []
MAX_RECENT_ERRORS = 20


def _blank():
    return {
        "calls": 0,
        "success": 0,
        "fallback": 0,
        "latency_ms_total": 0.0,
        "latency_ms_max": 0.0,
        "error_categories": {},
        "models_seen": {},
    }


def record(operation, model, ok, latency_ms, error_category=None, fallback_used=False):
    """Record one inference attempt.

    Called on BOTH paths -- success and fallback -- because a fallback rate is only
    meaningful against a denominator of total attempts.
    """
    with _lock:
        bucket = _counters.setdefault(operation, _blank())
        bucket["calls"] += 1
        bucket["latency_ms_total"] += latency_ms
        bucket["latency_ms_max"] = max(bucket["latency_ms_max"], latency_ms)
        bucket["models_seen"][model] = bucket["models_seen"].get(model, 0) + 1
        if ok:
            bucket["success"] += 1
        if fallback_used:
            bucket["fallback"] += 1
        if error_category:
            cats = bucket["error_categories"]
            cats[error_category] = cats.get(error_category, 0) + 1
            _recent_errors.append({
                "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "operation": operation, "model": model, "category": error_category,
            })
            del _recent_errors[:-MAX_RECENT_ERRORS]

    # The durable half. Structured extras so a log aggregator can group without regex.
    logger.info(
        "inference operation=%s model=%s ok=%s latency_ms=%.0f category=%s fallback=%s",
        operation, model, ok, latency_ms, error_category or "-", fallback_used,
        extra={"provider": "nvidia_nim", "operation": operation, "model": model,
               "ok": ok, "latency_ms": latency_ms, "error_category": error_category,
               "fallback_used": fallback_used},
    )


def snapshot():
    """Point-in-time view for /api/health/metrics/ and for an exporter to scrape."""
    with _lock:
        operations = {}
        for name, bucket in _counters.items():
            calls = bucket["calls"] or 1
            operations[name] = {
                "calls": bucket["calls"],
                "success": bucket["success"],
                "fallback": bucket["fallback"],
                "fallback_rate": round(bucket["fallback"] / calls, 4),
                "success_rate": round(bucket["success"] / calls, 4),
                "latency_ms_mean": round(bucket["latency_ms_total"] / calls, 1),
                "latency_ms_max": round(bucket["latency_ms_max"], 1),
                "error_categories": dict(bucket["error_categories"]),
                "models_seen": dict(bucket["models_seen"]),
            }
        total_calls = sum(b["calls"] for b in _counters.values())
        total_fallback = sum(b["fallback"] for b in _counters.values())
        return {
            "uptime_seconds": round(time.time() - _STARTED_AT, 1),
            "scope": "in-process; per-worker; resets on restart",
            "totals": {
                "calls": total_calls,
                "fallback": total_fallback,
                "fallback_rate": round(total_fallback / total_calls, 4) if total_calls else None,
            },
            "operations": operations,
            "recent_errors": list(_recent_errors),
        }


def reset():
    """Test-only. Counters are global module state, so a test that asserts on them must be
    able to start from a known point."""
    with _lock:
        _counters.clear()
        del _recent_errors[:]
