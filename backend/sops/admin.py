from django.contrib import admin

from .models import SOPChunk, SOPDocument


class SOPChunkInline(admin.TabularInline):
    model = SOPChunk
    extra = 0
    fields = ("section_title", "page_number", "chunk_text")


@admin.register(SOPDocument)
class SOPDocumentAdmin(admin.ModelAdmin):
    list_display = ("sop_code", "title", "version", "department", "status", "created_at")
    list_filter = ("department", "status")
    search_fields = ("title", "sop_code")
    inlines = [SOPChunkInline]


@admin.register(SOPChunk)
class SOPChunkAdmin(admin.ModelAdmin):
    list_display = ("sop", "section_title", "page_number", "created_at")
    search_fields = ("sop__sop_code", "section_title", "chunk_text")
