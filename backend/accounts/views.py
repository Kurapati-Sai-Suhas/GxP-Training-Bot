from django.contrib.auth import authenticate
from rest_framework import decorators, permissions, response, status, viewsets
from rest_framework.authtoken.models import Token

from audit.models import log_action

from .models import JobRole, LearnerProfile
from .permissions import ADMIN_GROUP, SME_GROUP, IsAdminUser
from .serializers import JobRoleSerializer, LearnerProfileSerializer
from .throttling import LoginRateThrottle


class JobRoleViewSet(viewsets.ModelViewSet):
    queryset = JobRole.objects.all().order_by("department", "name")
    serializer_class = JobRoleSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        role = serializer.save()
        log_action(
            self.request.user, "job_role_changed", role,
            summary=f"Created job role {role.name} ({role.department})",
            details={"operation": "create", "name": role.name, "department": role.department},
        )

    def perform_update(self, serializer):
        before = {"name": serializer.instance.name, "department": serializer.instance.department}
        role = serializer.save()
        log_action(
            self.request.user, "job_role_changed", role,
            summary=f"Updated job role {role.name} ({role.department})",
            details={"operation": "update", "previous": before},
        )

    def perform_destroy(self, instance):
        # Logged before the delete: afterwards the row (and its name) is gone, and the
        # cascade takes every Question and QuizAttempt attached to this role with it.
        log_action(
            self.request.user, "job_role_changed", instance,
            summary=f"Deleted job role {instance.name} ({instance.department})",
            details={"operation": "delete", "name": instance.name, "department": instance.department},
        )
        instance.delete()


class LearnerProfileViewSet(viewsets.ModelViewSet):
    queryset = LearnerProfile.objects.select_related("user", "job_role").all()
    serializer_class = LearnerProfileSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]

    def perform_create(self, serializer):
        profile = serializer.save()
        log_action(
            self.request.user, "learner_profile_changed", profile,
            summary=f"Created learner profile for {profile.user.get_username()}",
            details={
                "operation": "create",
                "learner": profile.user.get_username(),
                "job_role": profile.job_role.name if profile.job_role else None,
            },
        )

    def perform_update(self, serializer):
        # Which job role a learner holds decides which training they are shown, so a
        # change here is a training-record-relevant event, not just profile admin.
        previous_role = serializer.instance.job_role.name if serializer.instance.job_role else None
        profile = serializer.save()
        log_action(
            self.request.user, "learner_profile_changed", profile,
            summary=f"Updated learner profile for {profile.user.get_username()}",
            details={
                "operation": "update",
                "learner": profile.user.get_username(),
                "previous_job_role": previous_role,
                "job_role": profile.job_role.name if profile.job_role else None,
            },
        )

    def perform_destroy(self, instance):
        log_action(
            self.request.user, "learner_profile_changed", instance,
            summary=f"Deleted learner profile for {instance.user.get_username()}",
            details={"operation": "delete", "learner": instance.user.get_username()},
        )
        instance.delete()


def _serialize_current_user(user):
    profile = LearnerProfile.objects.select_related("job_role").filter(user=user).first()
    group_names = set(user.groups.values_list("name", flat=True))
    is_admin = user.is_staff or ADMIN_GROUP in group_names
    is_reviewer = is_admin or SME_GROUP in group_names
    return {
        "id": user.id,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "is_staff": user.is_staff,
        "roles": {"is_admin": is_admin, "is_reviewer": is_reviewer},
        "learner_profile": (
            {
                "id": profile.id,
                "employee_code": profile.employee_code,
                "job_role": (
                    {"id": profile.job_role.id, "name": profile.job_role.name}
                    if profile.job_role
                    else None
                ),
            }
            if profile
            else None
        ),
    }


@decorators.api_view(["POST"])
@decorators.permission_classes([permissions.AllowAny])
@decorators.throttle_classes([LoginRateThrottle])
def login_view(request):
    username = request.data.get("username")
    password = request.data.get("password")
    if not username or not password:
        return response.Response(
            {"error": "username and password are required"}, status=status.HTTP_400_BAD_REQUEST
        )

    user = authenticate(request, username=username, password=password)
    if user is None:
        return response.Response({"error": "Invalid username or password"}, status=status.HTTP_401_UNAUTHORIZED)

    token, _ = Token.objects.get_or_create(user=user)
    return response.Response({"token": token.key, "user": _serialize_current_user(user)})


@decorators.api_view(["POST"])
@decorators.permission_classes([permissions.IsAuthenticated])
def logout_view(request):
    Token.objects.filter(user=request.user).delete()
    return response.Response({"message": "Logged out"})


@decorators.api_view(["GET"])
@decorators.permission_classes([permissions.IsAuthenticated])
def me_view(request):
    return response.Response(_serialize_current_user(request.user))
