from django.db import migrations


def backfill_offered_questions(apps, schema_editor):
    """Give every *incomplete* attempt an offered-question set so it stays submittable.

    Attempts created before QuizAttemptQuestion existed have no recorded set, and
    submit() now rejects a submission that does not match one. Without this backfill a
    learner part-way through a quiz at deploy time would be unable to submit it.

    The set is reconstructed as the approved questions for the attempt's SOP and job role
    -- which is exactly what the self-started quiz path would have served. This is a
    reconstruction, not a record: it is applied only to attempts that were never
    completed, so it can never alter a historical training record.

    Completed attempts are deliberately left untouched. They are finished records, they
    cannot be resubmitted (the atomic claim rejects that with 409), and inventing an
    offered set for them would fabricate evidence about an assessment that has already
    happened. Their answers remain readable; only the offered set is unknown, and
    docs/KT_DATA_READINESS.md records that.
    """
    QuizAttempt = apps.get_model("attempts", "QuizAttempt")
    QuizAttemptQuestion = apps.get_model("attempts", "QuizAttemptQuestion")
    Question = apps.get_model("quiz", "Question")

    rows = []
    for attempt in QuizAttempt.objects.filter(completed_at__isnull=True):
        if QuizAttemptQuestion.objects.filter(attempt=attempt).exists():
            continue
        question_ids = list(
            Question.objects.filter(
                sop_id=attempt.sop_id, job_role_id=attempt.job_role_id, status="approved"
            )
            .order_by("id")
            .values_list("id", flat=True)
        )
        rows.extend(
            QuizAttemptQuestion(attempt=attempt, question_id=qid, position=position)
            for position, qid in enumerate(question_ids)
        )
    if rows:
        QuizAttemptQuestion.objects.bulk_create(rows)


def unbackfill(apps, schema_editor):
    """Reverse cleanly: drop only the rows this migration could have created."""
    QuizAttempt = apps.get_model("attempts", "QuizAttempt")
    QuizAttemptQuestion = apps.get_model("attempts", "QuizAttemptQuestion")
    QuizAttemptQuestion.objects.filter(
        attempt__in=QuizAttempt.objects.filter(completed_at__isnull=True)
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("attempts", "0006_attemptanswer_answered_at_and_more"),
        ("quiz", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(backfill_offered_questions, unbackfill),
    ]
