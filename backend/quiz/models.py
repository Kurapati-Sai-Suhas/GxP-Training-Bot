from django.db import models

from accounts.models import JobRole
from sops.models import SOPChunk, SOPDocument


class Question(models.Model):
    DIFFICULTY_CHOICES = [
        ("easy", "Easy"),
        ("medium", "Medium"),
        ("hard", "Hard"),
    ]
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("approved", "Approved"),
        ("rejected", "Rejected"),
    ]
    GENERATION_SOURCE_CHOICES = [
        ("nvidia_nim", "NVIDIA NIM"),
        ("mock", "Offline fallback"),
        ("manual", "Manually authored"),
    ]
    # Starting point for elo_rating, keyed by the one-time difficulty label -- a brand-new
    # question has no answer history yet, so it borrows the LLM's guess as a prior. From
    # there, elo_rating drifts based on how real learners actually do on it (see
    # attempts/services.py::apply_elo_update), instead of staying pinned to that guess
    # forever. 1300/1700 are deliberately the same numbers _elo_weight() in
    # attempts/views.py treats as its floor/ceiling, so a never-yet-answered question
    # reproduces the exact same mastery-weighting behaviour this project had before Elo
    # rating existed, and only diverges once real data pulls it away from the seed.
    DIFFICULTY_SEED_ELO = {"easy": 1300, "medium": 1500, "hard": 1700}

    sop = models.ForeignKey(SOPDocument, related_name="questions", on_delete=models.CASCADE)
    job_role = models.ForeignKey(JobRole, related_name="questions", on_delete=models.CASCADE)
    source_chunk = models.ForeignKey(SOPChunk, on_delete=models.SET_NULL, null=True, blank=True)
    question_text = models.TextField()
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default="medium")
    explanation = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    # The generator's own self-reported confidence (0.0-1.0) that this question and its
    # correct answer are unambiguous and well-supported by the source SOP text. Null for
    # questions created before this field existed, or created manually via the API.
    confidence_score = models.FloatField(null=True, blank=True)
    # Which path actually produced this question: a real NVIDIA NIM completion, the
    # deterministic offline fallback (used when NVIDIA NIM is unreachable), or a
    # hand-authored seed question. Lets reviewers verify the AI-drafting claim is real,
    # not just a UI label; null for questions created before this field existed.
    generation_source = models.CharField(max_length=20, choices=GENERATION_SOURCE_CHOICES, null=True, blank=True)
    # Live difficulty rating (Elo system -- see attempts/services.py), continuously updated
    # from real learner answers. Defaults to 1500 (the "medium" seed) so any Question
    # created without going through save()'s seeding below still gets a neutral prior.
    elo_rating = models.FloatField(default=1500)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        # Seed elo_rating from the difficulty label on first creation only, and only if
        # nothing already set it explicitly -- avoids every question-creation call site
        # (AI generation, offline fallback, direct API, tests, admin) needing to remember
        # to do this itself.
        if self._state.adding and self.elo_rating == self._meta.get_field("elo_rating").get_default():
            self.elo_rating = self.DIFFICULTY_SEED_ELO.get(self.difficulty, self.elo_rating)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.question_text[:80]


class Option(models.Model):
    question = models.ForeignKey(Question, related_name="options", on_delete=models.CASCADE)
    option_text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)

    def __str__(self):
        return self.option_text[:80]
