from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import JobRole
from quiz.models import Option, Question
from sops.models import SOPDocument

from .models import AuditLog


class AuditTrailTests(APITestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-900", version="v1.0", department="Production",
            file="sops/sop-900.txt", status="processed",
        )
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Sample?", explanation="Because.", status="draft",
        )
        Option.objects.create(question=self.question, option_text="Right", is_correct=True)
        Option.objects.create(question=self.question, option_text="Wrong", is_correct=False)

    def test_approve_writes_an_attributed_audit_log_entry(self):
        self.client.force_authenticate(user=self.admin)
        self.client.patch(
            f"/api/quiz/questions/{self.question.id}/approve/", {"password": "demo12345"}, format="json"
        )

        entry = AuditLog.objects.get(action="question_approved")
        self.assertEqual(entry.user, self.admin)
        self.assertEqual(entry.object_id, self.question.id)
        self.assertIn(str(self.question.id), entry.summary)
        self.assertTrue(entry.details.get("e_signature"))

    def test_audit_log_read_requires_admin(self):
        response = self.client.get("/api/audit/logs/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

        self.client.force_authenticate(user=self.learner)
        response = self.client.get("/api/audit/logs/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/audit/logs/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_audit_log_export_csv_requires_admin(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.get("/api/audit/logs/export/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_audit_log_export_csv_returns_rows_for_admin(self):
        self.client.force_authenticate(user=self.admin)
        self.client.patch(
            f"/api/quiz/questions/{self.question.id}/approve/", {"password": "demo12345"}, format="json"
        )

        response = self.client.get("/api/audit/logs/export/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode("utf-8")
        self.assertIn("Timestamp,User,Action,Object Type,Object ID,Summary,Details", body)
        self.assertIn("Question Approved", body)

    def test_audit_log_is_append_only_via_admin_site(self):
        from django.contrib import admin as django_admin

        model_admin = django_admin.site._registry[AuditLog]
        self.assertFalse(model_admin.has_add_permission(None))
        self.assertFalse(model_admin.has_change_permission(None))
        self.assertFalse(model_admin.has_delete_permission(None))
