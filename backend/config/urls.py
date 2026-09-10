from django.contrib import admin
from django.urls import include, path

from .health import ai_status, inference_metrics, liveness, readiness


urlpatterns = [
    path("admin/", admin.site.urls),

    # Operational endpoints. live/ and ready/ are unauthenticated because a load balancer
    # health check cannot hold a token; they expose no business data. ai/ and metrics/ are
    # admin-only -- they name the configured models and report call volumes.
    path("api/health/live/", liveness, name="health-live"),
    path("api/health/ready/", readiness, name="health-ready"),
    path("api/health/ai/", ai_status, name="health-ai"),
    path("api/health/metrics/", inference_metrics, name="health-metrics"),
    path("api/accounts/", include("accounts.urls")),
    path("api/sops/", include("sops.urls")),
    path("api/quiz/", include("quiz.urls")),
    path("api/attempts/", include("attempts.urls")),
    path("api/analytics/", include("analytics.urls")),
    path("api/ai_engine/", include("ai_engine.urls")),
    path("api/audit/", include("audit.urls")),
]

# MEDIA_URL is deliberately NOT served here. django.conf.urls.static.static() serves the
# media directory with no authentication whatsoever, and it activates whenever DEBUG is
# true -- which the Docker stack sets -- so every uploaded SOP was publicly downloadable by
# URL. Uploaded files are now served only through
# GET /api/sops/documents/{id}/download/, which sits behind the normal API authentication.
