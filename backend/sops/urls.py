from rest_framework.routers import DefaultRouter

from .views import SOPChunkViewSet, SOPDocumentViewSet


router = DefaultRouter()
router.register("documents", SOPDocumentViewSet)
router.register("chunks", SOPChunkViewSet)

urlpatterns = router.urls
