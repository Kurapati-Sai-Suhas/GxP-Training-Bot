from rest_framework.permissions import BasePermission

ADMIN_GROUP = "Admin"
SME_GROUP = "SME"


class IsAdminUser(BasePermission):
    """Training/QA Admin: uploads SOPs, triggers AI generation, manages roles/learners."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_staff or user.groups.filter(name=ADMIN_GROUP).exists())
        )


class IsReviewerUser(BasePermission):
    """SME/QA Reviewer or Admin: approves or rejects AI-generated questions."""

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and (user.is_staff or user.groups.filter(name__in=[ADMIN_GROUP, SME_GROUP]).exists())
        )
