from django.contrib.auth import authenticate
from rest_framework import decorators, permissions, response, status, viewsets
from rest_framework.authtoken.models import Token

from .models import JobRole, LearnerProfile
from .permissions import ADMIN_GROUP, SME_GROUP, IsAdminUser
from .serializers import JobRoleSerializer, LearnerProfileSerializer


class JobRoleViewSet(viewsets.ModelViewSet):
    queryset = JobRole.objects.all().order_by("department", "name")
    serializer_class = JobRoleSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]


class LearnerProfileViewSet(viewsets.ModelViewSet):
    queryset = LearnerProfile.objects.select_related("user", "job_role").all()
    serializer_class = LearnerProfileSerializer

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [permissions.IsAuthenticated()]


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
