from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import JobRole
from accounts.permissions import SME_GROUP
from sops.models import SOPDocument

from .models import Option, Question


class QuestionWorkflowTests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.reviewer = get_user_model().objects.create_user(username="vikram", password="demo12345")
        self.reviewer.groups.add(Group.objects.get_or_create(name=SME_GROUP)[0])
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")

        self.role_a = JobRole.objects.create(name="Production Operator", department="Production")
        self.role_b = JobRole.objects.create(name="QA Analyst", department="Quality Assurance")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-900", version="v1.0", department="Production",
            file="sops/sop-900.txt", status="processed",
        )
        self.draft_question = self._make_question(self.role_a, "draft")
        self.approved_question = self._make_question(self.role_a, "approved")
        self._make_question(self.role_b, "approved")  # different role, should not leak into role_a filters

    def _make_question(self, role, status_value):
        question = Question.objects.create(
            sop=self.sop, job_role=role, question_text="Sample question?", explanation="Because.",
            status=status_value,
        )
        Option.objects.create(question=question, option_text="Correct", is_correct=True)
        Option.objects.create(question=question, option_text="Wrong", is_correct=False)
        return question

    def test_approve_requires_authentication(self):
        response = self.client.patch(f"/api/quiz/questions/{self.draft_question.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)
        self.draft_question.refresh_from_db()
        self.assertEqual(self.draft_question.status, "draft")

    def test_approve_rejected_for_plain_learner(self):
        """RBAC: a learner with no Admin/SME role cannot approve questions."""
        self.client.force_authenticate(user=self.learner)
        response = self.client.patch(f"/api/quiz/questions/{self.draft_question.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.draft_question.refresh_from_db()
        self.assertEqual(self.draft_question.status, "draft")

    def test_approve_succeeds_for_sme_reviewer(self):
        self.client.force_authenticate(user=self.reviewer)
        response = self.client.patch(f"/api/quiz/questions/{self.draft_question.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.draft_question.refresh_from_db()
        self.assertEqual(self.draft_question.status, "approved")

    def test_approve_succeeds_for_admin(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(f"/api/quiz/questions/{self.draft_question.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_sme_reviewer_cannot_create_raw_questions(self):
        """RBAC: approving is an SME action, but directly creating/editing a Question is Admin-only."""
        self.client.force_authenticate(user=self.reviewer)
        response = self.client.post(
            "/api/quiz/questions/",
            {
                "sop": self.sop.id, "job_role": self.role_a.id, "question_text": "New?",
                "explanation": "Because.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_question_list_filters_by_job_role_and_status(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.get(
            f"/api/quiz/questions/?job_role={self.role_a.id}&status=approved"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        returned_ids = {item["id"] for item in response.data}
        self.assertEqual(returned_ids, {self.approved_question.id})
