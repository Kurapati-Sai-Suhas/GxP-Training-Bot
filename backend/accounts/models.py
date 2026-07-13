from django.conf import settings
from django.db import models


class JobRole(models.Model):
    name = models.CharField(max_length=120, unique=True)
    department = models.CharField(max_length=120)
    description = models.TextField(blank=True)

    def __str__(self):
        return f"{self.name} - {self.department}"


class LearnerProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    job_role = models.ForeignKey(JobRole, on_delete=models.SET_NULL, null=True, blank=True)
    employee_code = models.CharField(max_length=50, blank=True)

    def __str__(self):
        return self.user.get_username()
