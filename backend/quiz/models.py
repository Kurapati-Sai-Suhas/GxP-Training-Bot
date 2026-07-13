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

    sop = models.ForeignKey(SOPDocument, related_name="questions", on_delete=models.CASCADE)
    job_role = models.ForeignKey(JobRole, related_name="questions", on_delete=models.CASCADE)
    source_chunk = models.ForeignKey(SOPChunk, on_delete=models.SET_NULL, null=True, blank=True)
    question_text = models.TextField()
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default="medium")
    explanation = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.question_text[:80]


class Option(models.Model):
    question = models.ForeignKey(Question, related_name="options", on_delete=models.CASCADE)
    option_text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)

    def __str__(self):
        return self.option_text[:80]
