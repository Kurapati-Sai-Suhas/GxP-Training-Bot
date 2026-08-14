from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework.throttling import SimpleRateThrottle

from audit.models import AuditLog

from .models import JobRole, LearnerProfile
from .permissions import SME_GROUP

# Throttling is off under the test runner (see config/settings.py) so that cache-backed
# throttle history cannot make unrelated tests order-dependent. These tests re-enable it
# deliberately at a low rate.
#
# Patching THROTTLE_RATES rather than using override_settings(REST_FRAMEWORK=...):
# SimpleRateThrottle binds THROTTLE_RATES as a *class* attribute at import time, so it
# keeps pointing at the original dict even after DRF reloads api_settings. Overriding the
# setting therefore has no effect on an already-imported throttle class.
THROTTLE_TEST_RATES = {
    "login": "3/min",
    "esignature": "3/min",
    "ai_generate": "3/hour",
    "sop_chat": "3/hour",
}


class LoginFlowTests(APITestCase):
    def setUp(self):
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.user = get_user_model().objects.create_user(
            username="rohit", password="demo12345", first_name="Rohit", last_name="Mehta"
        )
        LearnerProfile.objects.create(user=self.user, job_role=self.role, employee_code="EMP-101")

    def test_login_returns_token_and_learner_profile(self):
        response = self.client.post(
            "/api/accounts/login/", {"username": "rohit", "password": "demo12345"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["token"])
        self.assertEqual(response.data["user"]["username"], "rohit")
        self.assertEqual(response.data["user"]["learner_profile"]["job_role"]["name"], "Production Operator")

    def test_login_rejects_bad_password(self):
        response = self.client.post(
            "/api/accounts/login/", {"username": "rohit", "password": "wrong"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_requires_authentication(self):
        response = self.client.get("/api/accounts/me/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_me_returns_current_user_when_authenticated(self):
        login = self.client.post(
            "/api/accounts/login/", {"username": "rohit", "password": "demo12345"}, format="json"
        )
        token = login.data["token"]
        response = self.client.get("/api/accounts/me/", HTTP_AUTHORIZATION=f"Token {token}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["username"], "rohit")

    def test_me_reports_role_tier_for_plain_learner(self):
        response = self.client.post(
            "/api/accounts/login/", {"username": "rohit", "password": "demo12345"}, format="json"
        )
        self.assertEqual(response.data["user"]["roles"], {"is_admin": False, "is_reviewer": False})

    def test_me_reports_role_tier_for_sme_reviewer(self):
        reviewer = get_user_model().objects.create_user(username="vikram", password="demo12345")
        reviewer.groups.add(Group.objects.get_or_create(name=SME_GROUP)[0])
        response = self.client.post(
            "/api/accounts/login/", {"username": "vikram", "password": "demo12345"}, format="json"
        )
        self.assertEqual(response.data["user"]["roles"], {"is_admin": False, "is_reviewer": True})

    def test_me_reports_role_tier_for_admin_staff_user(self):
        get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        response = self.client.post(
            "/api/accounts/login/", {"username": "anjali", "password": "demo12345"}, format="json"
        )
        self.assertEqual(response.data["user"]["roles"], {"is_admin": True, "is_reviewer": True})


class RoleBasedWritePermissionTests(APITestCase):
    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)

    def test_plain_learner_cannot_create_job_role(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.post(
            "/api/accounts/job-roles/", {"name": "New Role", "department": "Ops"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_can_create_job_role(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.post(
            "/api/accounts/job-roles/", {"name": "New Role", "department": "Ops"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)


class RoleAndProfileAuditTests(APITestCase):
    """Which job role a learner holds decides which training they are shown, so changing it
    is a training-record-relevant event. None of these mutations were audited."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.other_role = JobRole.objects.create(name="QA Analyst", department="Quality Assurance")
        self.client.force_authenticate(user=self.admin)

    def test_creating_a_job_role_is_audited(self):
        self.client.post("/api/accounts/job-roles/", {"name": "Warehouse", "department": "Ops"}, format="json")
        entry = AuditLog.objects.filter(action="job_role_changed").latest("created_at")
        self.assertEqual(entry.user, self.admin)
        self.assertEqual(entry.details["operation"], "create")

    def test_deleting_a_job_role_is_audited_with_its_name(self):
        """Captured before the delete -- afterwards the row and its name are gone."""
        self.client.delete(f"/api/accounts/job-roles/{self.other_role.id}/")
        entry = AuditLog.objects.filter(action="job_role_changed").latest("created_at")
        self.assertEqual(entry.details["operation"], "delete")
        self.assertEqual(entry.details["name"], "QA Analyst")

    def test_reassigning_a_learner_records_the_previous_role(self):
        profile = LearnerProfile.objects.create(user=self.learner, job_role=self.role, employee_code="EMP-1")
        self.client.patch(
            f"/api/accounts/learner-profiles/{profile.id}/", {"job_role": self.other_role.id}, format="json"
        )
        entry = AuditLog.objects.filter(action="learner_profile_changed").latest("created_at")
        self.assertEqual(entry.details["previous_job_role"], "Production Operator")
        self.assertEqual(entry.details["job_role"], "QA Analyst")


@mock.patch.dict(SimpleRateThrottle.THROTTLE_RATES, THROTTLE_TEST_RATES)
class ThrottlingTests(APITestCase):
    """Unlimited attempts against the credential and signature endpoints were the actual
    risk: both verify a password, so without a rate limit each is a guessing oracle."""

    def setUp(self):
        cache.clear()  # throttle history is cache-backed and leaks between tests otherwise
        self.reviewer = get_user_model().objects.create_user(username="vikram", password="demo12345")
        self.reviewer.groups.add(Group.objects.get_or_create(name=SME_GROUP)[0])

    def tearDown(self):
        cache.clear()

    def test_repeated_failed_logins_are_eventually_throttled(self):
        for _ in range(3):
            self.client.post(
                "/api/accounts/login/", {"username": "vikram", "password": "wrong"}, format="json"
            )
        response = self.client.post(
            "/api/accounts/login/", {"username": "vikram", "password": "wrong"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_throttle_also_blocks_a_subsequent_correct_password(self):
        """Otherwise an attacker who guesses correctly on attempt N+1 is unaffected by the
        limit, which would make it decorative."""
        for _ in range(3):
            self.client.post(
                "/api/accounts/login/", {"username": "vikram", "password": "wrong"}, format="json"
            )
        response = self.client.post(
            "/api/accounts/login/", {"username": "vikram", "password": "demo12345"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)

    def test_a_normal_login_is_not_throttled(self):
        response = self.client.post(
            "/api/accounts/login/", {"username": "vikram", "password": "demo12345"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_repeated_e_signature_attempts_are_throttled(self):
        from quiz.models import Option, Question
        from sops.models import SOPDocument

        sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-950", version="v1.0", department="Production",
            file="sops/sop-950.txt", status="processed",
        )
        role = JobRole.objects.create(name="Production Operator", department="Production")
        question = Question.objects.create(
            sop=sop, job_role=role, question_text="Q?", explanation="Because.", status="draft",
        )
        Option.objects.create(question=question, option_text="Right", is_correct=True)

        self.client.force_authenticate(user=self.reviewer)
        for _ in range(3):
            self.client.patch(
                f"/api/quiz/questions/{question.id}/approve/", {"password": "wrong"}, format="json"
            )
        response = self.client.patch(
            f"/api/quiz/questions/{question.id}/approve/", {"password": "wrong"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_429_TOO_MANY_REQUESTS)
        question.refresh_from_db()
        self.assertEqual(question.status, "draft")
