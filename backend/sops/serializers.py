from rest_framework import serializers

from .models import SOPChunk, SOPDocument


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
