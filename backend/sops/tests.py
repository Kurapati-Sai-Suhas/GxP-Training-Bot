import shutil
import tempfile
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings
from rest_framework import status
from rest_framework.test import APITestCase

from .models import SOPChunk, SOPDocument
from .serializers import MAX_SOP_FILE_SIZE_BYTES
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

    def test_process_corrupted_file_marks_sop_failed(self):
        """A file with an allowed extension (.pdf) that isn't actually a valid PDF passes
        upload-time validation (extension + size only) but fails at extraction time —
        the SOP should end up 'failed', not silently stuck or crashing the request."""
        upload = SimpleUploadedFile("sop.pdf", b"not a real pdf binary", content_type="application/pdf")
        create_response = self.client.post(
            "/api/sops/documents/",
            {
                "title": "Corrupted SOP",
                "sop_code": "SOP-901",
                "version": "v1.0",
                "department": "Production",
                "file": upload,
            },
        )
        self.assertEqual(create_response.status_code, status.HTTP_201_CREATED)
        sop_id = create_response.data["id"]

        process_response = self.client.post(f"/api/sops/documents/{sop_id}/process/")
        self.assertEqual(process_response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(SOPDocument.objects.get(id=sop_id).status, "failed")

    def test_upload_rejected_for_unsupported_extension(self):
        upload = SimpleUploadedFile("sop.xyz", b"not a real document", content_type="application/octet-stream")
        response = self.client.post(
            "/api/sops/documents/",
            {
                "title": "Bad Format SOP",
                "sop_code": "SOP-902",
                "version": "v1.0",
                "department": "Production",
                "file": upload,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SOPDocument.objects.filter(sop_code="SOP-902").exists())

    def test_upload_rejected_for_oversized_file(self):
        oversized = SimpleUploadedFile(
            "sop.txt", b"a" * (MAX_SOP_FILE_SIZE_BYTES + 1), content_type="text/plain"
        )
        response = self.client.post(
            "/api/sops/documents/",
            {
                "title": "Too Big SOP",
                "sop_code": "SOP-903",
                "version": "v1.0",
                "department": "Production",
                "file": oversized,
            },
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertFalse(SOPDocument.objects.filter(sop_code="SOP-903").exists())


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SopFileAccessControlTests(APITestCase):
    """P0 regression: uploaded SOPs are controlled documents, not public files.

    Django's static() media serving has no authentication and activates whenever DEBUG is
    true -- which the Docker stack sets -- so every uploaded procedure was downloadable by
    anyone who knew or guessed the URL.
    """

    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.client.force_authenticate(user=self.admin)
        upload = SimpleUploadedFile("sop.txt", SOP_TEXT.encode("utf-8"), content_type="text/plain")
        self.sop_id = self.client.post(
            "/api/sops/documents/",
            {
                "title": "Cleanroom Entry", "sop_code": "SOP-930", "version": "v1.0",
                "department": "Production", "file": upload,
            },
        ).data["id"]

    def test_media_url_is_not_served_at_all(self):
        """The unauthenticated route must be gone, not merely unlinked from the UI."""
        self.client.force_authenticate(user=None)
        response = self.client.get("/media/sops/sop.txt")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_download_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get(f"/api/sops/documents/{self.sop_id}/download/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_authenticated_learner_can_download(self):
        """SOP text is already readable by every authenticated role via /api/sops/chunks/,
        so gating the source file more tightly than its own extracted content would be
        theatre. The control being restored here is authentication, not role separation."""
        self.client.force_authenticate(user=self.learner)
        response = self.client.get(f"/api/sops/documents/{self.sop_id}/download/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn(b"gowning", b"".join(response.streaming_content).lower())

    def test_download_of_unknown_sop_is_404(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.get("/api/sops/documents/999999/download/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_list_payload_exposes_a_download_url_not_a_raw_media_path(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.get("/api/sops/documents/")
        row = next(item for item in response.data if item["id"] == self.sop_id)
        self.assertIn(f"/api/sops/documents/{self.sop_id}/download/", row["download_url"])
        self.assertNotIn("file", row)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class SopMutationAuditTests(APITestCase):
    """P0 regression: destroying an SOP cascades away its questions, attempts, answers and
    mastery rows. That previously left no audit entry whatsoever."""

    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.client.force_authenticate(user=self.admin)
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-931", version="v1.0", department="Production",
            file="sops/sop-931.txt", status="processed",
        )
        self.chunk = SOPChunk.objects.create(sop=self.sop, section_title="S1", chunk_text="text")

    def test_deleting_an_sop_writes_an_audit_entry_with_the_cascade_impact(self):
        from accounts.models import JobRole
        from audit.models import AuditLog
        from quiz.models import Question

        role = JobRole.objects.create(name="Production Operator", department="Production")
        Question.objects.create(
            sop=self.sop, job_role=role, source_chunk=self.chunk,
            question_text="Q?", explanation="Because.", status="draft",
        )

        response = self.client.delete(f"/api/sops/documents/{self.sop.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)

        entry = AuditLog.objects.get(action="sop_deleted")
        self.assertEqual(entry.user, self.admin)
        self.assertEqual(entry.details["sop_code"], "SOP-931")
        self.assertEqual(entry.details["questions_deleted"], 1)
        self.assertEqual(entry.details["chunks_deleted"], 1)

    def test_updating_sop_metadata_writes_an_audit_entry(self):
        from audit.models import AuditLog

        self.client.patch(
            f"/api/sops/documents/{self.sop.id}/", {"title": "Renamed Procedure"}, format="json"
        )
        entry = AuditLog.objects.get(action="sop_updated")
        self.assertIn("title", entry.details["fields_changed"])
        self.assertEqual(entry.details["previous"]["title"], "Cleanroom Entry")

    def test_reprocessing_is_blocked_once_approved_questions_exist(self):
        """Reprocessing rebuilds chunks, which cascades away ChunkMastery and orphans
        approved questions from their source text. Blocking it prevents silent destruction
        of training history; versioned reprocessing is the deferred proper fix."""
        from accounts.models import JobRole
        from quiz.models import Question

        role = JobRole.objects.create(name="Production Operator", department="Production")
        Question.objects.create(
            sop=self.sop, job_role=role, source_chunk=self.chunk,
            question_text="Q?", explanation="Because.", status="approved",
        )

        response = self.client.post(f"/api/sops/documents/{self.sop.id}/process/")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        # The chunk (and therefore any ChunkMastery hanging off it) survived.
        self.assertTrue(SOPChunk.objects.filter(id=self.chunk.id).exists())

    def test_reprocessing_still_allowed_when_only_drafts_exist(self):
        """Regenerating before review is the normal workflow and must keep working."""
        from accounts.models import JobRole
        from quiz.models import Question

        role = JobRole.objects.create(name="Production Operator", department="Production")
        Question.objects.create(
            sop=self.sop, job_role=role, question_text="Q?", explanation="Because.", status="draft",
        )
        response = self.client.post(f"/api/sops/documents/{self.sop.id}/process/")
        self.assertNotEqual(response.status_code, status.HTTP_409_CONFLICT)


class ChunkTextTests(SimpleTestCase):
    def test_splits_on_detected_section_headings(self):
        chunks = chunk_text(SOP_TEXT)
        self.assertEqual([title for title, _, _ in chunks], ["Section 1: Purpose", "Section 2: Sequence"])
        self.assertIn("mandatory gowning sequence", chunks[0][1])
        self.assertIn("hair cover", chunks[1][1])
        self.assertTrue(all(strategy == "heading" for _, _, strategy in chunks))

    @mock.patch.dict("os.environ", {"NVIDIA_API_KEY": ""})
    def test_falls_back_to_length_based_split_when_no_headings_and_no_api_key(self):
        text = "Just a plain paragraph with no section headings anywhere in it at all."
        chunks = chunk_text(text, max_chars=1000)
        self.assertEqual(len(chunks), 1)
        self.assertIsNone(chunks[0][0])
        self.assertEqual(chunks[0][2], "fixed_length")

    @mock.patch("sops.services._embed_sentences")
    def test_falls_back_to_semantic_chunking_when_no_headings_but_api_key_present(self, mock_embed):
        # Two near-identical directions (dominated by the first coordinate) followed by
        # two near-identical but different directions (dominated by the second), so
        # Max-Min similarity chunking should split them into two semantic groups.
        mock_embed.return_value = [
            [1.0, 0.0], [0.95, 0.05], [0.0, 1.0], [0.05, 0.95],
        ]
        text = "First idea sentence one.\nFirst idea sentence two.\nSecond idea sentence one.\nSecond idea sentence two."
        chunks = chunk_text(text, max_chars=1000)
        self.assertEqual(len(chunks), 2)
        self.assertTrue(all(strategy == "semantic" for _, _, strategy in chunks))
        self.assertIn("First idea", chunks[0][1])
        self.assertIn("Second idea", chunks[1][1])

    def test_splits_an_overlong_section_further(self):
        # A section made of many separate lines (as a real multi-sentence SOP paragraph
        # would extract), cumulatively well over max_chars.
        sentences = "\n".join(f"This is sentence number {i} of the long section body." for i in range(20))
        text = f"Section 1: Purpose\n{sentences}"
        chunks = chunk_text(text, max_chars=200)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(title == "Section 1: Purpose" for title, _, _ in chunks))

    def test_numeric_headings_are_also_detected(self):
        text = "3.1 Cleaning Verification\nWipe the surface and inspect under UV light for residue."
        chunks = chunk_text(text)
        self.assertEqual(chunks[0][0], "3.1 Cleaning Verification")
