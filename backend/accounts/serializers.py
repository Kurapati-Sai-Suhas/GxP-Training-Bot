from rest_framework import serializers

from .models import JobRole, LearnerProfile


class JobRoleSerializer(serializers.ModelSerializer):
    class Meta:
        model = JobRole
        fields = ["id", "name", "department", "description"]


class LearnerProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)
    first_name = serializers.CharField(source="user.first_name", read_only=True)
    last_name = serializers.CharField(source="user.last_name", read_only=True)
    email = serializers.CharField(source="user.email", read_only=True)
    job_role_name = serializers.CharField(source="job_role.name", read_only=True)

    class Meta:
        model = LearnerProfile
        fields = [
            "id",
            "user",
            "username",
            "first_name",
            "last_name",
            "email",
            "job_role",
            "job_role_name",
            "employee_code",
        ]
