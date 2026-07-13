from django.contrib.auth import get_user_model
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import JobRole
from quiz.models import Option, Question
from sops.models import SOPDocument


class QuizAttemptSubmitTests(APITestCase):
    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.other_learner = get_user_model().objects.create_user(username="priya", password="demo12345")
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-900", version="v1.0", department="Production",
            file="sops/sop-900.txt", status="processed",
        )
        self.questions = []
        self.correct_options = []
        for i in range(3):
            question = Question.objects.create(
                sop=self.sop, job_role=self.role, question_text=f"Question {i}?", explanation="Because.",
                status="approved",
            )
            correct = Option.objects.create(question=question, option_text="Right", is_correct=True)
            Option.objects.create(question=question, option_text="Wrong", is_correct=False)
            self.questions.append(question)
            self.correct_options.append(correct)

    def _start_attempt(self, user):
        self.client.force_authenticate(user=user)
        response = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["learner"], user.id)
        return response.data["id"]

    def test_submit_requires_authentication(self):
        attempt_id = self._start_attempt(self.learner)
        self.client.force_authenticate(user=None)
        response = self.client.post(f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_submit_hidden_from_non_owner_learner(self):
        """RBAC: a plain learner's queryset is scoped to their own attempts, so another
        learner's attempt 404s instead of leaking a 403 that would confirm it exists."""
        attempt_id = self._start_attempt(self.learner)
        self.client.force_authenticate(user=self.other_learner)
        response = self.client.post(f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": []}, format="json")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_admin_can_view_but_not_submit_on_behalf_of_a_learner(self):
        """RBAC: Admin's queryset is unscoped (can see every attempt for compliance monitoring),
        but the explicit ownership check still blocks submitting answers for someone else."""
        attempt_id = self._start_attempt(self.learner)

        self.client.force_authenticate(user=self.admin)
        list_response = self.client.get("/api/attempts/quiz-attempts/")
        self.assertEqual(list_response.status_code, status.HTTP_200_OK)
        self.assertIn(attempt_id, [item["id"] for item in list_response.data])

        submit_response = self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": []}, format="json"
        )
        self.assertEqual(submit_response.status_code, status.HTTP_403_FORBIDDEN)

    def test_submit_scores_correctly_and_returns_full_answer_detail(self):
        attempt_id = self._start_attempt(self.learner)

        # Answer 2 of 3 correctly, leave the third unanswered.
        answers = [
            {"question": self.questions[0].id, "selected_option": self.correct_options[0].id},
            {"question": self.questions[1].id, "selected_option": self.correct_options[1].id},
            {"question": self.questions[2].id, "selected_option": None},
        ]
        response = self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertAlmostEqual(float(response.data["score"]), 66.67, places=1)
        self.assertIsNotNone(response.data["completed_at"])

        # Regression test: submit() previously returned an empty `answers` list because
        # get_object() prefetched the (then-empty) answers before the delete/create cycle.
        self.assertEqual(len(response.data["answers"]), 3)
        correctness_by_question = {a["question"]: a["is_correct"] for a in response.data["answers"]}
        self.assertTrue(correctness_by_question[self.questions[0].id])
        self.assertTrue(correctness_by_question[self.questions[1].id])
        self.assertFalse(correctness_by_question[self.questions[2].id])
