from django.urls import path

from .views import dashboard_summary, recommended_refresher


urlpatterns = [
    path("dashboard-summary/", dashboard_summary, name="dashboard-summary"),
    path("recommended-refresher/", recommended_refresher, name="recommended-refresher"),
]
