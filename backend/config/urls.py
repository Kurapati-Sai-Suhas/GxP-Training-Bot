from django.contrib import admin
from django.urls import include, path


urlpatterns = [
    path("admin/", admin.site.urls),
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
