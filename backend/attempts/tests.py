import datetime

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import JobRole
from quiz.models import Option, Question
from sops.models import SOPDocument

from .models import TopicMastery


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


class AdaptiveRetrainingTests(APITestCase):
    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.other_learner = get_user_model().objects.create_user(username="priya", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-900", version="v1.0", department="Production",
            file="sops/sop-900.txt", status="processed",
        )
        self.questions = []
        self.correct_options = []
        self.wrong_options = []
        for i in range(3):
            question = Question.objects.create(
                sop=self.sop, job_role=self.role, question_text=f"Question {i}?", explanation="Because.",
                status="approved",
            )
            correct = Option.objects.create(question=question, option_text="Right", is_correct=True)
            wrong = Option.objects.create(question=question, option_text="Wrong", is_correct=False)
            self.questions.append(question)
            self.correct_options.append(correct)
            self.wrong_options.append(wrong)
        self.client.force_authenticate(user=self.learner)

    def _start_attempt(self):
        response = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
        )
        return response.data["id"]

    def test_topic_mastery_created_and_advances_on_correct_answers(self):
        """Three correct answers in a row (the MASTERY_STREAK_THRESHOLD) should flip the
        topic to mastered and push the next-eligible date out via Leitner box scheduling."""
        attempt_id = self._start_attempt()
        answers = [
            {"question": q.id, "selected_option": self.correct_options[i].id}
            for i, q in enumerate(self.questions)
        ]
        self.client.post(f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json")

        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        self.assertEqual(mastery.streak_correct, 3)
        self.assertEqual(mastery.mastery_status, "mastered")
        self.assertGreater(mastery.next_eligible_at, timezone.now())

    def test_topic_mastery_resets_on_a_wrong_answer(self):
        attempt_id = self._start_attempt()
        answers = [
            {"question": self.questions[0].id, "selected_option": self.correct_options[0].id},
            {"question": self.questions[1].id, "selected_option": self.wrong_options[1].id},
        ]
        self.client.post(f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json")

        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        self.assertEqual(mastery.streak_correct, 0)
        self.assertEqual(mastery.box_index, 0)
        self.assertEqual(mastery.mastery_status, "in_progress")

    def test_auto_assigned_excludes_mastered_topics_even_if_due(self):
        other_sop = SOPDocument.objects.create(
            title="HPLC Calibration", sop_code="SOP-204", version="v1.0", department="QC",
            file="sops/sop-204.txt", status="processed",
        )
        Question.objects.create(
            sop=other_sop, job_role=self.role, question_text="Q?", explanation="Because.", status="approved",
        )
        TopicMastery.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role,
            box_index=0, streak_correct=1, mastery_status="in_progress",
            next_eligible_at=timezone.now() - datetime.timedelta(days=1),
        )
        TopicMastery.objects.create(
            learner=self.learner, sop=other_sop, job_role=self.role,
            box_index=5, streak_correct=3, mastery_status="mastered",
            next_eligible_at=timezone.now() - datetime.timedelta(days=1),
        )

        response = self.client.get("/api/attempts/auto-assigned/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sop_ids = {item["sop_id"] for item in response.data["assignments"]}
        self.assertIn(self.sop.id, sop_ids)
        self.assertNotIn(other_sop.id, sop_ids)

    def test_auto_assigned_excludes_topics_not_yet_due(self):
        TopicMastery.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role,
            box_index=1, streak_correct=1, mastery_status="in_progress",
            next_eligible_at=timezone.now() + datetime.timedelta(days=5),
        )
        response = self.client.get("/api/attempts/auto-assigned/")
        self.assertEqual(response.data["assignments"], [])

    def test_auto_assigned_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/attempts/auto-assigned/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_auto_assigned_scoped_to_requesting_learner(self):
        """RBAC: one learner's due assignment must not leak into another learner's list."""
        TopicMastery.objects.create(
            learner=self.other_learner, sop=self.sop, job_role=self.role,
            box_index=0, streak_correct=0, mastery_status="in_progress",
            next_eligible_at=timezone.now() - datetime.timedelta(days=1),
        )
        response = self.client.get("/api/attempts/auto-assigned/")
        self.assertEqual(response.data["assignments"], [])
