"""Operational endpoints: liveness, readiness, AI-provider status, inference metrics.

These are deliberately NOT a Django app -- no models, no migrations, no admin. They are
four function views wired directly into the root URLconf.

WHY THE LIVE/READY SPLIT MATTERS
--------------------------------
A container orchestrator (ECS, Kubernetes, App Runner) does two different things with two
different signals, and conflating them causes outages:

  liveness  "is this process wedged?"        -> failing RESTARTS the container
  readiness "should this receive traffic?"   -> failing REMOVES IT FROM THE LOAD BALANCER

If liveness checked the database, a brief RDS failover would restart every container in the
service -- turning a recoverable dependency blip into a full outage, and losing in-flight
work. So liveness checks nothing but the process, and readiness checks dependencies.

The AI provider is checked by NEITHER. It is a degraded-capability signal, not an
availability one: the fallback contract means the application serves correctly with the
provider completely dead. Removing those containers from the load balancer would be strictly
worse than serving offline-generated content. It is reported separately, for alerting.
"""
import os
import time

from django.conf import settings
from django.db import connection
from rest_framework import status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import AllowAny, IsAdminUser
from rest_framework.response import Response

from ai_engine import metrics
from ai_engine.services import NVIDIA_NIM_BASE_URL, nim_model
from sops.services import embed_model

_STARTED_AT = time.time()

# How long an AI-provider probe result is trusted. The provider check costs a real inference
# call (measured: 30-100s on a cold NIM endpoint), so it must never run per-request -- a
# readiness probe every 10s would generate more provider load than the application itself.
AI_PROBE_CACHE_SECONDS = 300
_ai_probe_cache = {"checked_at": 0.0, "result": None}


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def liveness(request):
    """Is the process running? Nothing else. Never touches a dependency."""
    return Response({
        "status": "alive",
        "uptime_seconds": round(time.time() - _STARTED_AT, 1),
    })


@api_view(["GET"])
@authentication_classes([])
@permission_classes([AllowAny])
def readiness(request):
    """Can this instance serve requests? Checks only hard dependencies.

    Returns 503 when not ready so a load balancer drains this instance rather than sending
    it traffic it cannot serve.
    """
    checks = {}

    started = time.time()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = {"ok": True, "latency_ms": round((time.time() - started) * 1000, 1),
                              "engine": connection.vendor}
    except Exception as exc:  # noqa: BLE001 - a probe reports, it never raises
        checks["database"] = {"ok": False, "error": str(exc)[:200]}

    # Redis backs Celery. A web instance can still serve reads without it, but it cannot
    # queue document processing, so it is reported -- and counted as required, because a
    # silently non-queueing instance is worse than one taken out of rotation.
    broker = os.getenv("CELERY_BROKER_URL", "")
    if broker:
        started = time.time()
        try:
            import redis

            redis.from_url(broker, socket_connect_timeout=2).ping()
            checks["redis"] = {"ok": True, "latency_ms": round((time.time() - started) * 1000, 1)}
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = {"ok": False, "error": str(exc)[:200]}
    else:
        checks["redis"] = {"ok": True, "skipped": "CELERY_BROKER_URL unset (eager mode)"}

    ready = all(c["ok"] for c in checks.values())
    return Response(
        {"status": "ready" if ready else "not_ready", "checks": checks},
        status=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
    )


@api_view(["GET"])
@permission_classes([IsAdminUser])
def ai_status(request):
    """AI provider capability. Admin-only, and cached.

    Admin-only because the response names the configured models and the provider endpoint --
    useful reconnaissance, and of no use to a learner.

    This reports DEGRADED rather than failing: the application is fully available with the
    provider down, it just serves deterministic offline content. That distinction is the
    whole point of the fallback contract, and an operator needs to see it explicitly.
    """
    force = request.query_params.get("force") == "true"
    age = time.time() - _ai_probe_cache["checked_at"]
    if not force and _ai_probe_cache["result"] and age < AI_PROBE_CACHE_SECONDS:
        cached = dict(_ai_probe_cache["result"])
        cached["cache_age_seconds"] = round(age, 1)
        return Response(cached)

    result = {
        "provider": "nvidia_nim",
        "base_url": NVIDIA_NIM_BASE_URL,
        "chat_model": nim_model(),
        "embedding_model": embed_model(),
        "api_key_configured": bool(os.getenv("NVIDIA_API_KEY")),
        "cache_age_seconds": 0.0,
    }

    if not result["api_key_configured"]:
        result["status"] = "OFFLINE_MODE"
        result["detail"] = ("No API key. All AI calls take the deterministic offline path. "
                            "Expected in CI; a misconfiguration in production.")
    else:
        from openai import OpenAI

        from ai_engine.services import classify_llm_error
        started = time.time()
        try:
            OpenAI(api_key=os.getenv("NVIDIA_API_KEY"), base_url=NVIDIA_NIM_BASE_URL
                   ).chat.completions.create(
                model=nim_model(),
                messages=[{"role": "user", "content": "Reply with the single word OK."}],
                max_tokens=5, temperature=0)
            result["status"] = "OK"
            result["chat_latency_ms"] = round((time.time() - started) * 1000)
        except Exception as exc:  # noqa: BLE001
            category = classify_llm_error(exc)
            result["status"] = "MODEL_RETIRED" if category == "model_retired" else "DEGRADED"
            result["error_category"] = category
            result["error"] = str(exc)[:300]
            result["detail"] = ("Live model path unavailable; generation and SOP chat are "
                                "serving offline fallback content. The application is UP.")

    _ai_probe_cache["checked_at"] = time.time()
    _ai_probe_cache["result"] = result
    return Response(result)


@api_view(["GET"])
@permission_classes([IsAdminUser])
def inference_metrics(request):
    """Inference counters, including the fallback rate.

    Admin-only: call volumes and error categories are operational detail.

    fallback_rate is the number to watch. Because the fallback contract raises nothing, a
    dead provider produces no errors and no 5xx -- this ratio is the only in-band signal
    that the live model path has stopped working.
    """
    snapshot = metrics.snapshot()
    snapshot["environment"] = {
        "debug": settings.DEBUG,
        "chat_model": nim_model(),
        "embedding_model": embed_model(),
    }
    return Response(snapshot)
