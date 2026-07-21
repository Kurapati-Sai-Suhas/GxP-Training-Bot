import csv

from django.http import HttpResponse
from rest_framework import decorators, viewsets

from accounts.permissions import IsAdminUser

from .models import AuditLog
from .serializers import AuditLogSerializer


class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """Admin-only read access to the compliance audit trail."""

    queryset = AuditLog.objects.select_related("user").all()
    serializer_class = AuditLogSerializer
    permission_classes = [IsAdminUser]

    @decorators.action(detail=False, methods=["get"])
    def export(self, request):
        """CSV export of the audit trail, suitable to hand to an inspector — the
        read API is JSON-only otherwise."""
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = "attachment; filename=gxp-audit-log.csv"
        writer = csv.writer(response)
        writer.writerow(["Timestamp", "User", "Action", "Object Type", "Object ID", "Summary", "Details"])
        for entry in self.get_queryset():
            writer.writerow([
                entry.created_at.isoformat(),
                entry.user.get_username() if entry.user else "system",
                entry.get_action_display(),
                entry.object_type,
                entry.object_id or "",
                entry.summary,
                entry.details,
            ])
        return response
