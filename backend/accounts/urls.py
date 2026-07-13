from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import JobRoleViewSet, LearnerProfileViewSet, login_view, logout_view, me_view


router = DefaultRouter()
router.register("job-roles", JobRoleViewSet)
router.register("learner-profiles", LearnerProfileViewSet)

urlpatterns = [
    path("login/", login_view, name="login"),
    path("logout/", logout_view, name="logout"),
    path("me/", me_view, name="me"),
] + router.urls
