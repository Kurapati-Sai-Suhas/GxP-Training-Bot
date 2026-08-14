from rest_framework.permissions import BasePermission

ADMIN_GROUP = "Admin"
SME_GROUP = "SME"


def is_admin(user):
    """Training/QA Admin: uploads SOPs, triggers AI generation, manages roles/learners.

    Exposed as a plain function (not only as a permission class) because the same test is
    needed outside the permission pipeline -- e.g. choosing which serializer a viewset
    returns, or scoping a queryset. Keeping one definition means a role check can't drift
    between the two use sites.
    """
    return bool(
        user
        and user.is_authenticated
        and (user.is_staff or user.groups.filter(name=ADMIN_GROUP).exists())
    )


def is_reviewer(user):
    """SME/QA Reviewer or Admin: approves or rejects AI-generated questions, and is
    trusted to see reviewer-only question metadata (correct answers, explanations,
    confidence scores)."""
    return bool(
        user
        and user.is_authenticated
        and (user.is_staff or user.groups.filter(name__in=[ADMIN_GROUP, SME_GROUP]).exists())
    )


class IsAdminUser(BasePermission):
    """Training/QA Admin: uploads SOPs, triggers AI generation, manages roles/learners."""

    def has_permission(self, request, view):
        return is_admin(request.user)


class IsReviewerUser(BasePermission):
    """SME/QA Reviewer or Admin: approves or rejects AI-generated questions."""

    def has_permission(self, request, view):
        return is_reviewer(request.user)
