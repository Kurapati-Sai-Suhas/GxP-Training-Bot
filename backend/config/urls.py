from django.conf import settings
from django.conf.urls.static import static
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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
