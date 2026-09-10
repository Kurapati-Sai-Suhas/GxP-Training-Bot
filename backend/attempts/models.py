import datetime

from django.conf import settings
from django.db import models
from django.utils import timezone

from accounts.models import JobRole
from quiz.models import Option, Question
from sops.models import SOPChunk, SOPDocument

from . import fsrs


class QuizAttempt(models.Model):
    learner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="quiz_attempts", on_delete=models.CASCADE)
    job_role = models.ForeignKey(JobRole, on_delete=models.CASCADE)
    sop = models.ForeignKey(SOPDocument, on_delete=models.CASCADE)
    score = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    # Marks an attempt produced by `manage.py demo_adaptive` rather than by a person.
    #
    # The demo constructs a scripted learner to show the adaptive loop; its outcomes are decided
    # by the script, not observed. Those rows are perfectly valid for demonstrating behaviour and
    # perfectly invalid as evidence about learning, so the offline evaluation harness excludes
    # them by default (see attempts/evaluation.py). Marking them explicitly is preferable to
    # inferring "demo-ness" from the SOP code or the username, both of which are cosmetic and can
    # drift.
    #
    # Nothing in the production request path sets this to True; only the demo command does.
    is_synthetic = models.BooleanField(default=False, db_index=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.learner} - {self.sop}"


class QuizAttemptQuestion(models.Model):
    """The exact set of questions offered to the learner for one attempt, recorded when
    the attempt is created.

    Previously the server computed which questions the adaptive engine had selected but
    never stored them, so at submission time it could only check that a submitted
    question belonged to the attempt's SOP, matched the role and was approved -- not that
    it was actually offered. A modified client could therefore substitute a different
    eligible question, or omit questions entirely; because the score was computed over
    submitted answers, omitting inflated it. Persisting the offered set is what turns the
    adaptive decision from advisory into enforced, and it is also what makes a past
    attempt reproducible: without it there is no record of what the learner was actually
    asked.

    `position` preserves the order the questions were served in, so an attempt can be
    reconstructed as it was sat rather than merely as a set.
    """

    attempt = models.ForeignKey(QuizAttempt, related_name="offered_questions", on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    position = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("attempt", "question")
        ordering = ["position", "id"]

    def __str__(self):
        return f"{self.attempt_id} - q{self.question_id} @{self.position}"


class AttemptAnswer(models.Model):
    """One learner response. This is the interaction record future knowledge-tracing work
    will consume, so it carries temporal information as well as correctness.

    `answered_at` is deliberately nullable and written explicitly by the server rather
    than declared `auto_now_add`: an auto-populated non-null column would have stamped
    every pre-existing row with the migration's run time, inventing a response history
    that never happened. Rows created before this field existed keep NULL and are
    excluded from any temporal analysis -- see docs/KT_DATA_READINESS.md.
    """

    # Client-reported latency is accepted only within this bound. A single multiple-choice
    # response taking longer than an hour is telemetry noise (tab left open), not a
    # measurement worth storing.
    MAX_RESPONSE_LATENCY_MS = 3_600_000

    attempt = models.ForeignKey(QuizAttempt, related_name="answers", on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    selected_option = models.ForeignKey(Option, on_delete=models.SET_NULL, null=True, blank=True)
    is_correct = models.BooleanField(default=False)
    # Server-authoritative. Never read from the request payload.
    answered_at = models.DateTimeField(null=True, blank=True)
    # Observational telemetry, measured by the client. NOT a trusted timestamp: it is not
    # used for grading, scheduling, mastery or the audit trail, and must not be treated as
    # compliance evidence. Present so that response-time features are available to future
    # knowledge-tracing models that can use them.
    response_latency_ms = models.PositiveIntegerField(null=True, blank=True)

    # --- Elo snapshot: the two ratings as they stood when this answer was recorded -------
    #
    # Both Question.elo_rating and TopicMastery.elo_rating are *live* and move after every
    # answer. Reconstructing a past interaction from today's ratings would therefore
    # attribute to it a difficulty it never had -- and, because the rating moved partly
    # *because of this very answer*, would leak the outcome back into its own feature.
    # That is look-ahead bias, and it silently inflates the apparent accuracy of any model
    # trained on such a dataset.
    #
    # These two columns are exactly the pair apply_elo_update() consumes (see
    # services.py), captured before it runs, so an interaction records the inputs that
    # actually produced the update rather than its aftermath.
    #
    # Nullable and never backfilled: rows written before this field existed cannot have a
    # truthful value, and inventing one would defeat the purpose of recording it. NULL
    # means "not measurable", not zero. See docs/KT_DATA_READINESS.md.
    #
    # Instrumentation only -- nothing reads these yet. They deliberately do NOT influence
    # priority, mastery or scheduling.
    question_difficulty_at_answer = models.FloatField(null=True, blank=True)
    learner_ability_at_answer = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"{self.attempt_id} - {self.question_id}"


class MasteryState(models.Model):
    """Shared FSRS/Elo/streak scheduling state and update logic, used at two different
    granularities: TopicMastery (whole SOP) and ChunkMastery (single section). Both exist
    in parallel, updated from the same completed attempt -- see submit() in views.py.

    The "should we stop reviewing this" decision is a streak-based mastery threshold
    (a discrete approximation of the Bayesian stopping rule in Sapountzi et al., EDM
    2021), deliberately chosen over Bayesian/neural knowledge tracing (Corbett & Anderson
    1994; Piech et al. 2015) because those need far more response data per skill than a
    small training-bot deployment will ever have — see Wilson, Karklin, Han & Ekanadham
    (EDM 2016), who found simpler probabilistic models match or beat neural knowledge
    tracing at this data scale.

    The separate "when should the next review happen" decision is FSRS (see fsrs.py):
    a per-(learner, SOP-or-chunk) memory model (stability, difficulty) fit on real
    answers, instead of every item and every learner sharing the same fixed
    1/2/4/7/14/30-day schedule regardless of how hard the material actually is or how
    fast this specific learner forgets it.
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

    box_index = models.PositiveSmallIntegerField(default=0)
    streak_correct = models.PositiveSmallIntegerField(default=0)
    mastery_status = models.CharField(max_length=20, choices=MASTERY_CHOICES, default="in_progress")
    next_eligible_at = models.DateTimeField(default=timezone.now)
    # Live ability rating (Elo system -- see attempts/services.py::apply_elo_update),
    # updated once per answered question. Starts at the same 1500 baseline every question
    # starts at, and moves from there based on real answers -- so a learner transferring
    # in with real prior knowledge is recognised as such after a handful of questions,
    # instead of being treated identically to a first-day hire just because both start at
    # box_index=0.
    elo_rating = models.FloatField(default=1500)
    # FSRS memory model (see fsrs.py) -- null until the first review, since FSRS computes
    # an initial value from the first grade rather than starting at a shared default the
    # way elo_rating does.
    fsrs_stability = models.FloatField(null=True, blank=True)
    fsrs_difficulty = models.FloatField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

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


class TopicMastery(MasteryState):
    """Whole-SOP adaptive-retraining state, one row per (learner, SOP). See
    MasteryState's docstring for the shared scheduling logic.

    Updated once per completed QuizAttempt (see QuizAttemptViewSet.submit in
    attempts/views.py), from the attempt's overall score against PASS_THRESHOLD, not once
    per individual answer. Scoring per-answer let the very last question graded overwrite
    the outcome of an otherwise-strong attempt; scoring the whole attempt fixes that.
    Read by GET /api/attempts/auto-assigned/.

    Deliberately NOT derived from ChunkMastery (e.g. "mastered once every chunk is
    mastered"): a SOP can have questions with no source_chunk (manually authored, or
    chunking metadata predating a given feature), which would make whole-SOP mastery
    unreachable under a chunk-derived rule. TopicMastery and ChunkMastery are two
    independent signals computed from the same attempt, not one built from the other --
    see ChunkMastery below for the finer-grained, section-level sibling.
    """

    learner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="topic_masteries", on_delete=models.CASCADE)
    sop = models.ForeignKey(SOPDocument, on_delete=models.CASCADE)
    job_role = models.ForeignKey(JobRole, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("learner", "sop")
        ordering = ["next_eligible_at"]

    def __str__(self):
        return f"{self.learner} - {self.sop} ({self.mastery_status})"


class ChunkMastery(MasteryState):
    """Single-section adaptive-retraining state, one row per (learner, SOPChunk). The
    finer-grained sibling of TopicMastery: a learner who misses one section of a
    multi-section SOP only resets that section's schedule, not the whole document's --
    closing the gap where a single miss anywhere in a 10-section SOP reset all 10.

    Updated in the same submit() call as TopicMastery, from the subset of that attempt's
    answers whose Question.source_chunk points at this chunk (see views.py). Questions
    with no source_chunk (manually authored, or predating chunk-linkage) have no
    ChunkMastery equivalent -- they only ever contribute to the whole-SOP TopicMastery.
    """

    learner = models.ForeignKey(settings.AUTH_USER_MODEL, related_name="chunk_masteries", on_delete=models.CASCADE)
    sop_chunk = models.ForeignKey(SOPChunk, related_name="masteries", on_delete=models.CASCADE)
    job_role = models.ForeignKey(JobRole, on_delete=models.CASCADE)

    class Meta:
        unique_together = ("learner", "sop_chunk")
        ordering = ["next_eligible_at"]

    def __str__(self):
        return f"{self.learner} - {self.sop_chunk} ({self.mastery_status})"
