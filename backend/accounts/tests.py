from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from rest_framework import status
from rest_framework.test import APITestCase

from .models import JobRole, LearnerProfile
from .permissions import SME_GROUP


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
