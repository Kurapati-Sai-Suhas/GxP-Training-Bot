from django.contrib import admin

from .models import JobRole, LearnerProfile


@admin.register(JobRole)
class JobRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "department")
    search_fields = ("name", "department")


@admin.register(LearnerProfile)
class LearnerProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "job_role", "employee_code")
    search_fields = ("user__username", "employee_code")
