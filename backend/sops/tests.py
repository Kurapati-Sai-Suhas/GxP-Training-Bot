import shutil
import tempfile

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .models import SOPChunk, SOPDocument
from .services import chunk_text

SOP_TEXT = (
    "Section 1: Purpose\n\n"
    "This SOP defines the mandatory gowning sequence for personnel entering a Grade B cleanroom "
    "in the production area. Failure to follow this sequence is a critical deviation.\n\n"
    "Section 2: Sequence\n\n"
    "Personnel must don garments in the following order: hair cover, face mask, sterile coverall, "
    "safety goggles, and sterile gloves last, to prevent contamination of the outer glove surface."
)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SopProcessTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.get_media_root(), ignore_errors=True)
        super().tearDownClass()

    @classmethod
    def get_media_root(cls):
        from django.conf import settings

        return settings.MEDIA_ROOT

    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.client.force_authenticate(user=self.admin)

    def test_upload_rejected_for_plain_learner(self):
        """RBAC: uploading an SOP is an Admin action, not available to a plain learner."""
        self.client.force_authenticate(user=self.learner)
        upload = SimpleUploadedFile("sop.txt", SOP_TEXT.encode("utf-8"), content_type="text/plain")
        response = self.client.post(
            "/api/sops/documents/",
            {
                "title": "Cleanroom Entry and Gowning",
                "sop_code": "SOP-902",
                "version": "v1.0",
                "department": "Production",
                "file": upload,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_upload_then_process_extracts_and_persists_chunks(self):
        upload = SimpleUploadedFile("sop.txt", SOP_TEXT.encode("utf-8"), content_type="text/plain")
        create_response = self.client.post(
            "/api/sops/documents/",
            {
                "title": "Cleanroom Entry and Gowning",
                "sop_code": "SOP-900",
                "version": "v1.0",
                "department": "Production",
                "file": upload,
            },
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        sop_id = create_response.data["id"]
        self.assertEqual(create_response.data["status"], "uploaded")

        process_response = self.client.post(f"/api/sops/documents/{sop_id}/process/")
        self.assertEqual(process_response.status_code, status.HTTP_200_OK)

        # Regression test: process() previously reported chunks=0 due to a stale
        # Django prefetch_related cache on the SOPDocumentViewSet queryset.
        self.assertGreater(process_response.data["chunks"], 0)

        sop = SOPDocument.objects.get(id=sop_id)
        self.assertEqual(sop.status, "processed")
        self.assertEqual(SOPChunk.objects.filter(sop=sop).count(), process_response.data["chunks"])

        # Heading-aware chunking: the detected section heading becomes the chunk's
        # section_title instead of a generic "Auto chunk N".
        titles = set(SOPChunk.objects.filter(sop=sop).values_list("section_title", flat=True))
        self.assertIn("Section 1: Purpose", titles)
        self.assertIn("Section 2: Sequence", titles)

    def test_process_unsupported_file_type_marks_sop_failed(self):
        upload = SimpleUploadedFile("sop.xyz", b"not a real document", content_type="application/octet-stream")
        create_response = self.client.post(
            "/api/sops/documents/",
            {
                "title": "Bad Format SOP",
                "sop_code": "SOP-901",
                "version": "v1.0",
                "department": "Production",
                "file": upload,
            },
        )
        sop_id = create_response.data["id"]

        process_response = self.client.post(f"/api/sops/documents/{sop_id}/process/")
        self.assertEqual(process_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SOPDocument.objects.get(id=sop_id).status, "failed")


class ChunkTextTests(SimpleTestCase):
    def test_splits_on_detected_section_headings(self):
        chunks = chunk_text(SOP_TEXT)
        self.assertEqual([title for title, _ in chunks], ["Section 1: Purpose", "Section 2: Sequence"])
        self.assertIn("mandatory gowning sequence", chunks[0][1])
        self.assertIn("hair cover", chunks[1][1])

    def test_falls_back_to_length_based_split_when_no_headings(self):
        text = "Just a plain paragraph with no section headings anywhere in it at all."
        chunks = chunk_text(text, max_chars=1000)
        self.assertEqual(len(chunks), 1)
        self.assertIsNone(chunks[0][0])

    def test_splits_an_overlong_section_further(self):
        # A section made of many separate lines (as a real multi-sentence SOP paragraph
        # would extract), cumulatively well over max_chars.
        sentences = "\n".join(f"This is sentence number {i} of the long section body." for i in range(20))
        text = f"Section 1: Purpose\n{sentences}"
        chunks = chunk_text(text, max_chars=200)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(title == "Section 1: Purpose" for title, _ in chunks))

    def test_numeric_headings_are_also_detected(self):
        text = "3.1 Cleaning Verification\nWipe the surface and inspect under UV light for residue."
        chunks = chunk_text(text)
        self.assertEqual(chunks[0][0], "3.1 Cleaning Verification")
