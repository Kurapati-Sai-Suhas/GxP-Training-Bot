from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AttemptAnswerViewSet,
    QuizAttemptViewSet,
    auto_assigned_retraining,
    learning_path,
    retraining_status,
    section_mastery_status,
)


router = DefaultRouter()
router.register("quiz-attempts", QuizAttemptViewSet)
router.register("answers", AttemptAnswerViewSet)

urlpatterns = router.urls + [
    path("auto-assigned/", auto_assigned_retraining, name="auto-assigned-retraining"),
    path("retraining-status/", retraining_status, name="retraining-status"),
    path("section-mastery/", section_mastery_status, name="section-mastery-status"),
    path("learning-path/", learning_path, name="learning-path"),
]
