import os

from rest_framework import serializers

from .models import SOPChunk, SOPDocument

ALLOWED_SOP_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}
MAX_SOP_FILE_SIZE_BYTES = 20 * 1024 * 1024  # 20 MB


class SOPChunkSerializer(serializers.ModelSerializer):
    class Meta:
        model = SOPChunk
        fields = ["id", "sop", "section_title", "page_number", "chunk_text", "created_at"]
        read_only_fields = ["created_at"]


class SOPDocumentSerializer(serializers.ModelSerializer):
    chunks_count = serializers.IntegerField(source="chunks.count", read_only=True)
    status_label = serializers.CharField(source="get_status_display", read_only=True)

    class Meta:
        model = SOPDocument
        fields = [
            "id",
            "title",
            "sop_code",
            "version",
            "department",
            "file",
            "status",
            "status_label",
            "uploaded_by",
            "created_at",
            "chunks_count",
        ]
        read_only_fields = ["created_at", "status"]

    def validate_file(self, value):
        # Server-side enforcement: previously only the frontend's <input accept=...>
        # constrained this, so a direct API call could upload anything.
        extension = os.path.splitext(value.name)[1].lower()
        if extension not in ALLOWED_SOP_EXTENSIONS:
            raise serializers.ValidationError(
                f"Unsupported file type '{extension or 'unknown'}'. "
                f"Allowed types: {', '.join(sorted(ALLOWED_SOP_EXTENSIONS))}."
            )
        if value.size > MAX_SOP_FILE_SIZE_BYTES:
            raise serializers.ValidationError(
                f"File is too large ({value.size / (1024 * 1024):.1f} MB). "
                f"Maximum allowed size is {MAX_SOP_FILE_SIZE_BYTES // (1024 * 1024)} MB."
            )
        return value
