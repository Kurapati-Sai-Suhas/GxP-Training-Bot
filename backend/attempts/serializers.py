from rest_framework import serializers

from .models import AttemptAnswer, QuizAttempt


class AttemptAnswerSerializer(serializers.ModelSerializer):
    explanation = serializers.CharField(source="question.explanation", read_only=True)
    question_text = serializers.CharField(source="question.question_text", read_only=True)

    class Meta:
        model = AttemptAnswer
        fields = ["id", "attempt", "question", "question_text", "selected_option", "is_correct", "explanation"]
        read_only_fields = ["is_correct", "explanation", "question_text"]


class QuizAttemptSerializer(serializers.ModelSerializer):
    answers = AttemptAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = QuizAttempt
        fields = ["id", "learner", "job_role", "sop", "score", "started_at", "completed_at", "answers"]
        read_only_fields = ["learner", "score", "started_at", "completed_at"]
