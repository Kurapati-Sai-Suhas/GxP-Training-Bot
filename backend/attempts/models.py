import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import JobRole
from quiz.models import Option, Question
from sops.models import SOPDocument


class QuizAttempt(models.Model):
    learner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="quiz_attempts", on_delete=models.CASCADE)
    job_role = models.ForeignKey(JobRole, on_delete=models.CASCADE)
    sop = models.ForeignKey(SOPDocument, on_delete=models.CASCADE)
    score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.learner} - {self.sop}"


class AttemptAnswer(models.Model):
    attempt = models.ForeignKey(QuizAttempt, related_name="answers", on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_option = models.ForeignKey(Option, on_delete=models.SET_NULL, null=True, blank=True)
    is_correct = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.attempt_id} - {self.question_id}"


class TopicMastery(models.Model):
    """Adaptive-retraining scheduling state, one row per (learner, SOP).

    Leitner-style box scheduling (Leitner, 1972) plus a streak-based mastery threshold,
    deliberately chosen over Bayesian/neural knowledge tracing (Corbett & Anderson 1994;
    Piech et al. 2015) because those need far more response data per skill than a small
    training-bot deployment will ever have — see Wilson, Karklin, Han & Ekanadham (EDM
    2016), who found simpler probabilistic models match or beat neural knowledge tracing
    at this data scale. Kept updated by a signal on AttemptAnswer (see signals.py), and
    read by GET /api/attempts/auto-assigned/ — nothing else in the app writes to or
    depends on this model.
    """

    MASTERY_CHOICES = [
        ("in_progress", "In Progress"),
        ("mastered", "Mastered"),
    ]
    # Expanding review intervals in days, indexed by box_index (Leitner system).
    BOX_INTERVAL_DAYS = [1, 2, 4, 7, 14, 30]
    MASTERY_STREAK_THRESHOLD = 3

    learner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="topic_masteries", on_delete=models.CASCADE)
    sop = models.ForeignKey(SOPDocument, on_delete=models.CASCADE)
    job_role = models.ForeignKey(JobRole, on_delete=models.CASCADE)
    box_index = models.PositiveSmallIntegerField(default=0)
    streak_correct = models.PositiveSmallIntegerField(default=0)
    mastery_status = models.CharField(max_length=20, choices=MASTERY_CHOICES, default="in_progress")
    next_eligible_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("learner", "sop")
        ordering = ["next_eligible_at"]

    def __str__(self):
        return f"{self.learner} - {self.sop} ({self.mastery_status})"

    def apply_answer(self, is_correct):
        """Update box/streak/mastery/schedule from one new AttemptAnswer outcome."""
        if is_correct:
            self.streak_correct += 1
            self.box_index = min(self.box_index + 1, len(self.BOX_INTERVAL_DAYS) - 1)
            if self.streak_correct >= self.MASTERY_STREAK_THRESHOLD:
                self.mastery_status = "mastered"
        else:
            self.streak_correct = 0
            self.box_index = 0
            self.mastery_status = "in_progress"
        offset_days = self.BOX_INTERVAL_DAYS[self.box_index]
        self.next_eligible_at = timezone.now() + datetime.timedelta(days=offset_days)
