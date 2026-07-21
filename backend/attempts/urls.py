from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import AttemptAnswerViewSet, QuizAttemptViewSet, auto_assigned_retraining


router = DefaultRouter()
router.register("quiz-attempts", QuizAttemptViewSet)
router.register("answers", AttemptAnswerViewSet)

urlpatterns = router.urls + [
    path("auto-assigned/", auto_assigned_retraining, name="auto-assigned-retraining"),
]
