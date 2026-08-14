from rest_framework import serializers

from .models import Option, Question


class OptionSerializer(serializers.ModelSerializer):
    """Reviewer/Admin view of an option -- includes the answer key (`is_correct`).

    Never serve this to a learner before they submit: see LearnerOptionSerializer.
    """

    class Meta:
        model = Option
        fields = ["id", "question", "option_text", "is_correct"]


class LearnerOptionSerializer(serializers.ModelSerializer):
    """Learner view of an option: the text to choose between, and nothing else.

    `is_correct` is deliberately absent. Exposing it here previously made every
    assessment open-book to anyone who opened browser devtools, which invalidated the
    score, the Elo/FSRS state derived from it, and the training record built on top.
    Correctness is disclosed only *after* submission, via AttemptAnswerSerializer.
    """

    class Meta:
        model = Option
        fields = ["id", "question", "option_text"]


class QuestionSerializer(serializers.ModelSerializer):
    """Reviewer/Admin view: the full record, including answer key, explanation, and the
    provenance fields a reviewer needs to judge AI-drafted content."""

    options = OptionSerializer(many=True, read_only=True)
    sop_code = serializers.CharField(source="sop.sop_code", read_only=True)
    sop_title = serializers.CharField(source="sop.title", read_only=True)
    job_role_name = serializers.CharField(source="job_role.name", read_only=True)
    source_section = serializers.CharField(source="source_chunk.section_title", read_only=True)
    # The actual source passage the question was generated from. A reviewer cannot judge
    # whether an AI-drafted question is faithful to the SOP without seeing the text it came
    # from -- a section *title* alone only says where to go looking.
    source_text = serializers.CharField(source="source_chunk.chunk_text", read_only=True)
    chunking_strategy = serializers.CharField(source="source_chunk.chunking_strategy", read_only=True)

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
            "source_text",
            "chunking_strategy",
            "question_text",
            "difficulty",
            "explanation",
            "status",
            "confidence_score",
            "elo_rating",
            "generation_source",
            "content_hash",
            "approved_by",
            "approved_at",
            "created_at",
            "options",
        ]
        read_only_fields = [
            "created_at",
            "generation_source",
            "elo_rating",
            "content_hash",
            "approved_by",
            "approved_at",
        ]


class LearnerQuestionSerializer(serializers.ModelSerializer):
    """Learner view: enough to sit the quiz, and nothing that gives the answer away.

    Excluded relative to QuestionSerializer: `explanation` (states which option is correct
    and why), `options.is_correct`, plus reviewer-only metadata (`confidence_score`,
    `elo_rating`, `generation_source`, `status`, `source_chunk`) that has no learner use.
    """

    options = LearnerOptionSerializer(many=True, read_only=True)
    sop_code = serializers.CharField(source="sop.sop_code", read_only=True)
    sop_title = serializers.CharField(source="sop.title", read_only=True)
    job_role_name = serializers.CharField(source="job_role.name", read_only=True)

    class Meta:
        model = Question
        fields = [
            "id",
            "sop",
            "sop_code",
            "sop_title",
            "job_role",
            "job_role_name",
            "question_text",
            "difficulty",
            "options",
        ]
