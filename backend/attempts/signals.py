from django.db.models.signals import post_save
from django.dispatch import receiver

from .models import AttemptAnswer, TopicMastery


@receiver(post_save, sender=AttemptAnswer)
def update_topic_mastery(sender, instance, created, **kwargs):
    """Advance the learner's adaptive-retraining schedule for this SOP whenever a new
    answer is recorded. Purely additive: nothing else reads or writes AttemptAnswer
    differently because this receiver exists, and a failure here never blocks the
    submit() response since Django signals run in-process, synchronously, after the
    triggering save already succeeded."""
    if not created:
        return

    attempt = instance.attempt
    mastery, _ = TopicMastery.objects.get_or_create(
        learner=attempt.learner,
        sop=attempt.sop,
        defaults={"job_role": attempt.job_role},
    )
    mastery.job_role = attempt.job_role
    mastery.apply_answer(instance.is_correct)
    mastery.save()
