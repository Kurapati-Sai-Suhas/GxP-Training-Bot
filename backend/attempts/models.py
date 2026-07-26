import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import JobRole
from quiz.models import Option, Question
from sops.models import SOPDocument

from . import fsrs


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

    The "should we stop reviewing this SOP" decision is a streak-based mastery threshold
    (a discrete approximation of the Bayesian stopping rule in Sapountzi et al., EDM
    2021), deliberately chosen over Bayesian/neural knowledge tracing (Corbett & Anderson
    1994; Piech et al. 2015) because those need far more response data per skill than a
    small training-bot deployment will ever have — see Wilson, Karklin, Han & Ekanadham
    (EDM 2016), who found simpler probabilistic models match or beat neural knowledge
    tracing at this data scale.

    The separate "when should the next review happen" decision is FSRS (see fsrs.py):
    a per-(learner, SOP) memory model (stability, difficulty) fit on real answers,
    instead of every SOP and every learner sharing the same fixed 1/2/4/7/14/30-day
    schedule regardless of how hard the material actually is or how fast this specific
    learner forgets it.

    Updated once per completed QuizAttempt (see QuizAttemptViewSet.submit in
    attempts/views.py), from the attempt's overall score against PASS_THRESHOLD, not once
    per individual answer. Scoring per-answer let the very last question graded overwrite
    the outcome of an otherwise-strong attempt; scoring the whole attempt fixes that.
    Read by GET /api/attempts/auto-assigned/ — nothing else in the app writes to or
    depends on this model.
    """

    MASTERY_CHOICES = [
        ("in_progress", "In Progress"),
        ("mastered", "Mastered"),
    ]
    # Correct-in-a-row counter, capped at this many tiers -- kept as a simple, always-
    # visible display/audit signal (box_index appears in the audit log and the Analytics
    # UI) even though it no longer drives the review schedule itself; see next_eligible_at
    # below, now computed by FSRS instead of a BOX_INTERVAL_DAYS lookup.
    BOX_INTERVAL_DAYS = [1, 2, 4, 7, 14, 30]
    MASTERY_STREAK_THRESHOLD = 3
    # Whole-attempt pass mark used to decide box movement (see apply_answer below).
    # Matches the "Passed" threshold used elsewhere (analytics dashboard, learner progress).
    PASS_THRESHOLD = 80

    learner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="topic_masteries", on_delete=models.CASCADE)
    sop = models.ForeignKey(SOPDocument, on_delete=models.CASCADE)
    job_role = models.ForeignKey(JobRole, on_delete=models.CASCADE)
    box_index = models.PositiveSmallIntegerField(default=0)
    streak_correct = models.PositiveSmallIntegerField(default=0)
    mastery_status = models.CharField(max_length=20, choices=MASTERY_CHOICES, default="in_progress")
    next_eligible_at = models.DateTimeField(default=timezone.now)
    # Live ability rating for this learner on this SOP (Elo system -- see
    # attempts/services.py::apply_elo_update), updated once per answered question. Starts
    # at the same 1500 baseline every question starts at, and moves from there based on
    # real answers -- so a learner transferring in with real prior knowledge is recognised
    # as such after a handful of questions, instead of being treated identically to a
    # first-day hire just because both start at box_index=0.
    elo_rating = models.FloatField(default=1500)
    # FSRS memory model (see fsrs.py) -- null until the first review, since FSRS computes
    # an initial value from the first grade rather than starting at a shared default the
    # way elo_rating does.
    fsrs_stability = models.FloatField(null=True, blank=True)
    fsrs_difficulty = models.FloatField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("learner", "sop")
        ordering = ["next_eligible_at"]

    def __str__(self):
        return f"{self.learner} - {self.sop} ({self.mastery_status})"

    def apply_answer(self, is_correct):
        """Update box/streak/mastery/FSRS state/schedule from one new completed-attempt
        outcome. self.updated_at (auto_now) still holds the *previous* save's timestamp
        at this point -- read here, before save() -- so it's used as "time of last
        review" for FSRS's elapsed-days calculation."""
        elapsed_days = 0.0
        if self.fsrs_stability is not None:
            elapsed_days = max(0.0, (timezone.now() - self.updated_at).total_seconds() / 86400.0)
        self.fsrs_stability, self.fsrs_difficulty = fsrs.review(
            self.fsrs_stability, self.fsrs_difficulty, elapsed_days, is_correct
        )

        if is_correct:
            self.streak_correct += 1
            self.box_index = min(self.box_index + 1, len(self.BOX_INTERVAL_DAYS) - 1)
            if self.streak_correct >= self.MASTERY_STREAK_THRESHOLD:
                self.mastery_status = "mastered"
        else:
            self.streak_correct = 0
            self.box_index = 0
            self.mastery_status = "in_progress"

        offset_days = fsrs.next_review_interval_days(self.fsrs_stability)
        self.next_eligible_at = timezone.now() + datetime.timedelta(days=offset_days)
