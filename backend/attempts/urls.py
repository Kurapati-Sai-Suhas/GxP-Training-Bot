from rest_framework.routers import DefaultRouter

from .views import AttemptAnswerViewSet, QuizAttemptViewSet


router = DefaultRouter()
router.register("quiz-attempts", QuizAttemptViewSet)
router.register("answers", AttemptAnswerViewSet)

urlpatterns = router.urls
