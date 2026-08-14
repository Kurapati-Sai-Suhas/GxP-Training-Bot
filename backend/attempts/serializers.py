from rest_framework import serializers

from .models import AttemptAnswer, QuizAttempt


class AttemptAnswerSerializer(serializers.ModelSerializer):
    """Post-submission view of one answer.

    This is where correctness is disclosed. The learner-facing question API deliberately
    withholds `is_correct` and `explanation` (see quiz.serializers.LearnerQuestionSerializer)
    so the assessment cannot be read ahead; an AttemptAnswer only exists once the learner
    has submitted, so revealing the correct answer and the compliance explanation here is
    the intended "why was I wrong" teaching moment rather than a leak.
    """

    explanation = serializers.CharField(source="question.explanation", read_only=True)
    question_text = serializers.CharField(source="question.question_text", read_only=True)
    selected_option_text = serializers.SerializerMethodField()
    correct_option_text = serializers.SerializerMethodField()

    class Meta:
        model = AttemptAnswer
        fields = [
            "id",
            "attempt",
            "question",
            "question_text",
            "selected_option",
            "selected_option_text",
            "correct_option_text",
            "is_correct",
            "explanation",
        ]
        read_only_fields = [
            "is_correct",
            "explanation",
            "question_text",
            "selected_option_text",
            "correct_option_text",
        ]

    def get_selected_option_text(self, obj):
        return obj.selected_option.option_text if obj.selected_option_id else None

    def get_correct_option_text(self, obj):
        correct = next((o for o in obj.question.options.all() if o.is_correct), None)
        return correct.option_text if correct else None


class QuizAttemptSerializer(serializers.ModelSerializer):
    answers = AttemptAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = QuizAttempt
        fields = ["id", "learner", "job_role", "sop", "score", "started_at", "completed_at", "answers"]
        read_only_fields = ["learner", "score", "started_at", "completed_at"]
