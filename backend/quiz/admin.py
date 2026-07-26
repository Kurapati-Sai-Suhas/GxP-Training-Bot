from django.contrib import admin

from .models import Option, Question


class OptionInline(admin.TabularInline):
    model = Option
    extra = 0


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("question_text", "sop", "job_role", "difficulty", "elo_rating", "status", "created_at")
    list_filter = ("status", "difficulty", "job_role")
    search_fields = ("question_text", "explanation", "sop__sop_code")
    inlines = [OptionInline]


@admin.register(Option)
class OptionAdmin(admin.ModelAdmin):
    list_display = ("question", "option_text", "is_correct")
    list_filter = ("is_correct",)
