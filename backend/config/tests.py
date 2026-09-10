"""Tests for the operational endpoints.

The security boundary is as important as the functionality here: two of these endpoints are
intentionally unauthenticated (a load balancer cannot present a token) and two are
intentionally admin-only (they name models and report call volumes). Both halves are pinned.
"""
from unittest import mock

from django.contrib.auth import get_user_model
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from ai_engine import metrics


class LivenessTests(APITestCase):
    def test_liveness_is_public(self):
        """A load balancer health check cannot hold a token."""
        response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "alive")

    def test_liveness_exposes_no_business_data(self):
        """It is unauthenticated, so it must leak nothing. Uptime only."""
        response = self.client.get(reverse("health-live"))
        self.assertEqual(set(response.data), {"status", "uptime_seconds"})

    def test_liveness_does_not_touch_the_database(self):
        """Liveness failure RESTARTS containers. If it checked the database, an RDS
        failover would restart every container in the service and turn a recoverable blip
        into a full outage."""
        with mock.patch("config.health.connection") as db:
            db.cursor.side_effect = AssertionError("liveness must not query the database")
            response = self.client.get(reverse("health-live"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)


class ReadinessTests(APITestCase):
    def test_readiness_reports_database(self):
        response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["checks"]["database"]["ok"])

    def test_readiness_returns_503_when_the_database_is_down(self):
        """503, not 500 -- it tells the load balancer to drain this instance rather than
        surfacing an error to a user."""
        with mock.patch("config.health.connection") as db:
            db.cursor.side_effect = Exception("could not connect to server")
            response = self.client.get(reverse("health-ready"))
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)
        self.assertEqual(response.data["status"], "not_ready")
        self.assertFalse(response.data["checks"]["database"]["ok"])

    def test_readiness_does_not_leak_internals_when_failing(self):
        """The failure path is still unauthenticated. Truncated error, no traceback."""
        with mock.patch("config.health.connection") as db:
            db.cursor.side_effect = Exception("x" * 5000)
            response = self.client.get(reverse("health-ready"))
        self.assertLessEqual(len(response.data["checks"]["database"]["error"]), 200)


class OperationalEndpointAuthorizationTests(APITestCase):
    """AI status and metrics name the configured models, the provider endpoint and call
    volumes. That is operational detail, and of no use to a learner."""

    def setUp(self):
        self.learner = get_user_model().objects.create_user(
            username="learner", password="not-a-real-password")
        self.admin = get_user_model().objects.create_user(
            username="admin", password="not-a-real-password", is_staff=True)

    def test_ai_status_rejects_anonymous(self):
        self.assertIn(self.client.get(reverse("health-ai")).status_code,
                      {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN})

    def test_ai_status_rejects_a_learner(self):
        self.client.force_authenticate(user=self.learner)
        self.assertEqual(self.client.get(reverse("health-ai")).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_metrics_rejects_a_learner(self):
        self.client.force_authenticate(user=self.learner)
        self.assertEqual(self.client.get(reverse("health-metrics")).status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_metrics_allows_an_admin(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(reverse("health-metrics"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("totals", response.data)

    @mock.patch.dict("os.environ", {}, clear=False)
    def test_ai_status_reports_offline_mode_without_a_key(self):
        import os

        os.environ.pop("NVIDIA_API_KEY", None)
        with mock.patch.dict("config.health._ai_probe_cache",
                             {"checked_at": 0.0, "result": None}):
            self.client.force_authenticate(user=self.admin)
            response = self.client.get(reverse("health-ai"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "OFFLINE_MODE")


class InferenceMetricsTests(APITestCase):
    """The fallback rate is the signal. Because the fallback contract raises nothing, a
    dead provider produces no errors and no 5xx -- this ratio is the only in-band evidence
    that the live model path has stopped working."""

    def setUp(self):
        metrics.reset()
        self.admin = get_user_model().objects.create_user(
            username="admin", password="not-a-real-password", is_staff=True)
        self.client.force_authenticate(user=self.admin)

    def tearDown(self):
        metrics.reset()

    def test_fallback_rate_is_computed_against_total_attempts(self):
        metrics.record("question_generation", "m", ok=True, latency_ms=100)
        metrics.record("question_generation", "m", ok=False, latency_ms=50,
                       error_category="model_retired", fallback_used=True)
        data = self.client.get(reverse("health-metrics")).data
        op = data["operations"]["question_generation"]
        self.assertEqual(op["calls"], 2)
        self.assertEqual(op["fallback"], 1)
        self.assertEqual(op["fallback_rate"], 0.5)

    def test_total_provider_outage_shows_a_fallback_rate_of_one(self):
        """The exact signature of the incident this instrumentation was written for."""
        for _ in range(5):
            metrics.record("question_generation", "meta/llama-3.1-8b-instruct", ok=False,
                           latency_ms=20, error_category="model_retired", fallback_used=True)
        data = self.client.get(reverse("health-metrics")).data
        self.assertEqual(data["totals"]["fallback_rate"], 1.0)
        self.assertEqual(
            data["operations"]["question_generation"]["error_categories"]["model_retired"], 5)

    def test_error_categories_are_retained_for_triage(self):
        metrics.record("sop_chat", "m", ok=False, latency_ms=10,
                       error_category="rate_limit", fallback_used=True)
        metrics.record("sop_chat", "m", ok=False, latency_ms=10,
                       error_category="model_retired", fallback_used=True)
        cats = self.client.get(reverse("health-metrics")).data["operations"]["sop_chat"]["error_categories"]
        self.assertEqual(cats, {"rate_limit": 1, "model_retired": 1})

    def test_no_traffic_reports_null_rather_than_a_misleading_zero(self):
        """A fallback rate of 0.0 means 'measured, all healthy'. No calls means 'unknown'.
        Reporting 0.0 for the latter would read as healthy on a dashboard."""
        data = self.client.get(reverse("health-metrics")).data
        self.assertEqual(data["totals"]["calls"], 0)
        self.assertIsNone(data["totals"]["fallback_rate"])
