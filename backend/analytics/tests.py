from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import JobRole
from attempts.models import AttemptAnswer, QuizAttempt
from quiz.models import Option, Question
from sops.models import SOPDocument


class DashboardSummaryTests(APITestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.client.force_authenticate(user=self.user)

        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-900", version="v1.0", department="Production",
            file="sops/sop-900.txt", status="processed",
        )
        self.hard_question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Hard question that most people miss?",
            explanation="Because.", status="approved",
        )
        self.easy_question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Easy question everyone gets right?",
            explanation="Because.", status="approved",
        )

        attempt = QuizAttempt.objects.create(
            learner=self.user, job_role=self.role, sop=self.sop, score=50, completed_at=timezone.now(),
        )
        # 3 of 4 learners get the hard question wrong; everyone gets the easy one right.
        AttemptAnswer.objects.create(attempt=attempt, question=self.hard_question, is_correct=False)
        AttemptAnswer.objects.create(attempt=attempt, question=self.hard_question, is_correct=False)
        AttemptAnswer.objects.create(attempt=attempt, question=self.hard_question, is_correct=False)
        AttemptAnswer.objects.create(attempt=attempt, question=self.hard_question, is_correct=True)
        AttemptAnswer.objects.create(attempt=attempt, question=self.easy_question, is_correct=True)

    def test_weak_topics_ranks_lowest_correct_rate_first(self):
        response = self.client.get("/api/analytics/dashboard-summary/")
        self.assertEqual(response.status_code, 200)

        weak_topics = response.data["weak_topics"]
        self.assertGreaterEqual(len(weak_topics), 1)
        top = weak_topics[0]
        self.assertEqual(top["question_id"], self.hard_question.id)
        self.assertEqual(top["attempts"], 4)
        self.assertAlmostEqual(top["correct_rate"], 25.0, places=1)
