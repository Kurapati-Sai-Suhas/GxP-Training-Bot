from django.conf import settings
from django.db import models


class SOPDocument(models.Model):
    STATUS_CHOICES = [
        ("uploaded", "Uploaded"),
        ("processed", "Processed"),
        ("failed", "Failed"),
    ]

    title = models.CharField(max_length=180)
    sop_code = models.CharField(max_length=80)
    version = models.CharField(max_length=40)
    department = models.CharField(max_length=120)
    file = models.FileField(upload_to="sops/")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="uploaded")
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        unique_together = ("sop_code", "version")

    def __str__(self):
        return f"{self.sop_code} v{self.version}"


class SOPChunk(models.Model):
    STRATEGY_CHOICES = [
        ("heading", "Heading-aware"),
        ("semantic", "Semantic (embeddings)"),
        ("fixed_length", "Fixed-length"),
    ]

    sop = models.ForeignKey(SOPDocument, related_name="chunks", on_delete=models.CASCADE)
    section_title = models.CharField(max_length=180, blank=True)
    page_number = models.PositiveIntegerField(null=True, blank=True)
    chunk_text = models.TextField()
    # Which splitter actually produced this chunk: heading-aware (preferred, used whenever
    # the SOP has detectable section headings), semantic embeddings-based (Max-Min
    # similarity chunking, used when no headings are found), or fixed-length (last-resort
    # fallback if NVIDIA_API_KEY is unset or the embedding call fails). Null for chunks
    # created before this field existed.
    chunking_strategy = models.CharField(max_length=20, choices=STRATEGY_CHOICES, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.sop.sop_code} chunk {self.id}"
