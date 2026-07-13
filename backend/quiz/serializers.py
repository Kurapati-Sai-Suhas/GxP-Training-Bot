from rest_framework import serializers

from .models import Option, Question


class OptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Option
        fields = ["id", "question", "option_text", "is_correct"]


class QuestionSerializer(serializers.ModelSerializer):
    options = OptionSerializer(many=True, read_only=True)
    sop_code = serializers.CharField(source="sop.sop_code", read_only=True)
    sop_title = serializers.CharField(source="sop.title", read_only=True)
    job_role_name = serializers.CharField(source="job_role.name", read_only=True)
    source_section = serializers.CharField(source="source_chunk.section_title", read_only=True)

    class Meta:
        model = Question
        fields = [
            "id",
            "sop",
            "sop_code",
            "sop_title",
            "job_role",
            "job_role_name",
            "source_chunk",
            "source_section",
            "question_text",
            "difficulty",
            "explanation",
            "status",
            "created_at",
            "options",
        ]
        read_only_fields = ["created_at"]
