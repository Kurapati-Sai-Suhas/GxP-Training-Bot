from rest_framework.routers import DefaultRouter

from .views import OptionViewSet, QuestionViewSet


router = DefaultRouter()
router.register("questions", QuestionViewSet)
router.register("options", OptionViewSet)

urlpatterns = router.urls
