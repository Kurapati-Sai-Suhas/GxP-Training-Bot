from unittest import mock

from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import JobRole
from quiz.models import Question
from sops.models import SOPChunk, SOPDocument


class GenerateQuizTests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.client.force_authenticate(user=self.admin)

        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry and Gowning",
            sop_code="SOP-900",
            version="v1.0",
            department="Production",
            file="sops/sop-900.txt",
            status="processed",
        )
        SOPChunk.objects.create(
            sop=self.sop,
            section_title="Auto chunk 1",
            chunk_text=(
                "Personnel must don garments in the following strict order: hair cover, face mask, "
                "sterile coverall, safety goggles, and sterile gloves last. Sterile gloves are always "
                "donned last to prevent contamination of the outer glove surface during earlier steps. "
                "A single gowning cycle inside the Grade B area is limited to a maximum of four hours "
                "before personnel must exit and re-gown regardless of task completion status."
            ),
        )

    def test_generate_rejected_for_plain_learner(self):
        """RBAC: triggering AI generation is an Admin action, not available to a plain learner."""
        self.client.force_authenticate(user=self.learner)
        response = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 1},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_generate_requires_sop_and_job_role(self):
        response = self.client.post("/api/ai_engine/generate/", {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_generate_rejects_sop_with_no_chunks(self):
        empty_sop = SOPDocument.objects.create(
            title="No Chunks Yet", sop_code="SOP-901", version="v1.0", department="Production",
            file="sops/sop-901.txt", status="uploaded",
        )
        response = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": empty_sop.id, "job_role": self.role.id, "count": 2},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @mock.patch.dict("os.environ", {"NVIDIA_API_KEY": ""})
    def test_generate_falls_back_to_mock_and_creates_draft_questions(self):
        response = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 3},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["source"], "mock")
        self.assertEqual(len(response.data["questions"]), 3)

        questions = Question.objects.filter(sop=self.sop, job_role=self.role)
        self.assertEqual(questions.count(), 3)
        for question in questions:
            self.assertEqual(question.status, "draft")
            self.assertEqual(question.options.filter(is_correct=True).count(), 1)

    @mock.patch.dict("os.environ", {"NVIDIA_API_KEY": ""})
    def test_generate_skips_duplicates_on_a_repeat_run(self):
        """Running generate twice for the same SOP+role should not create the same
        question-and-correct-answer pair twice, even though the mock generator's question
        stem is identical across calls (only the correct answer differs per fact tested)."""
        first = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 3},
            format="json",
        )
        self.assertEqual(len(first.data["questions"]), 3)
        self.assertEqual(first.data["skipped_duplicates"], 0)

        second = self.client.post(
            "/api/ai_engine/generate/",
            {"sop": self.sop.id, "job_role": self.role.id, "count": 3},
            format="json",
        )
        self.assertEqual(len(second.data["questions"]), 0)
        self.assertEqual(second.data["skipped_duplicates"], 3)
        self.assertEqual(Question.objects.filter(sop=self.sop, job_role=self.role).count(), 3)
