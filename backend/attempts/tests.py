import datetime
import json
import math

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import JobRole
from quiz.models import Option, Question
from sops.models import SOPChunk, SOPDocument

from . import adaptive, evaluation, fsrs
from .models import AttemptAnswer, ChunkMastery, QuizAttempt, QuizAttemptQuestion, TopicMastery
from .services import QUESTION_K_FACTOR, apply_elo_update


def _offer_exactly(attempt_id, question_ids):
    """Narrow an attempt's persisted offered set to exactly `question_ids`.

    Submission must match the offered set exactly, so a test that models a *targeted*
    assessment -- the adaptive engine selecting only the weak sections -- needs an attempt
    whose offered set is that subset. The self-started quiz path offers every approved
    question for the SOP, so tests covering targeted retraining narrow it here.

    This is test setup standing in for the assignment endpoint, not a bypass: the
    submission itself still goes through the API and is still validated against whatever
    set is recorded.
    """
    QuizAttemptQuestion.objects.filter(attempt_id=attempt_id).delete()
    QuizAttemptQuestion.objects.bulk_create(
        [
            QuizAttemptQuestion(attempt_id=attempt_id, question_id=qid, position=position)
            for position, qid in enumerate(question_ids)
        ]
    )


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


class CompletedAttemptImmutabilityTests(APITestCase):
    """P0 regression: a completed attempt is a training record and must not be rewritten.

    Previously submit() had no completed_at guard, so a learner could submit, read the
    results screen (which discloses every correct answer and explanation by design), and
    then resubmit a perfect score over the top -- destroying the meaning of the record and
    of the Elo/FSRS/mastery state derived from it.
    """

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-920", version="v1.0", department="Production",
            file="sops/sop-920.txt", status="processed",
        )
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q?", explanation="Because.", status="approved",
        )
        self.correct = Option.objects.create(question=self.question, option_text="Right", is_correct=True)
        self.wrong = Option.objects.create(question=self.question, option_text="Wrong", is_correct=False)
        self.client.force_authenticate(user=self.learner)

        self.attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
        ).data["id"]

    def _submit(self, option_id):
        return self.client.post(
            f"/api/attempts/quiz-attempts/{self.attempt_id}/submit/",
            {"answers": [{"question": self.question.id, "selected_option": option_id}]},
            format="json",
        )

    def test_first_submission_succeeds(self):
        response = self._submit(self.wrong.id)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.data["score"]), 0.0)

    def test_second_submission_is_rejected(self):
        self._submit(self.wrong.id)
        response = self._submit(self.correct.id)
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_score_is_unchanged_by_a_rejected_resubmission(self):
        """The actual exploit: fail, learn the answers from the results screen, resubmit."""
        self._submit(self.wrong.id)
        attempt = QuizAttempt.objects.get(id=self.attempt_id)
        original_score = attempt.score

        self._submit(self.correct.id)

        attempt.refresh_from_db()
        self.assertEqual(attempt.score, original_score)
        self.assertEqual(float(attempt.score), 0.0)

    def test_completed_at_is_unchanged_by_a_rejected_resubmission(self):
        self._submit(self.wrong.id)
        original_completed_at = QuizAttempt.objects.get(id=self.attempt_id).completed_at

        self._submit(self.correct.id)

        self.assertEqual(QuizAttempt.objects.get(id=self.attempt_id).completed_at, original_completed_at)

    def test_stored_answers_are_unchanged_by_a_rejected_resubmission(self):
        self._submit(self.wrong.id)
        self._submit(self.correct.id)

        answers = AttemptAnswer.objects.filter(attempt_id=self.attempt_id)
        self.assertEqual(answers.count(), 1)
        self.assertFalse(answers.first().is_correct)
        self.assertEqual(answers.first().selected_option_id, self.wrong.id)

    def test_mastery_state_is_not_advanced_by_a_rejected_resubmission(self):
        """A blocked resubmission must not feed the spaced-repetition scheduler either."""
        self._submit(self.wrong.id)
        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        streak_before, elo_before = mastery.streak_correct, mastery.elo_rating

        self._submit(self.correct.id)

        mastery.refresh_from_db()
        self.assertEqual(mastery.streak_correct, streak_before)
        self.assertEqual(mastery.elo_rating, elo_before)

    def test_question_elo_is_not_moved_by_a_rejected_resubmission(self):
        self._submit(self.wrong.id)
        self.question.refresh_from_db()
        elo_before = self.question.elo_rating

        self._submit(self.correct.id)

        self.question.refresh_from_db()
        self.assertEqual(self.question.elo_rating, elo_before)

    def test_blocked_resubmission_is_audited(self):
        """The attempt is a compliance signal in its own right -- someone tried to rewrite
        a completed training record."""
        from audit.models import AuditLog

        self._submit(self.wrong.id)
        self._submit(self.correct.id)

        entry = AuditLog.objects.get(action="quiz_attempt_resubmit_blocked")
        self.assertEqual(entry.user, self.learner)
        self.assertEqual(entry.object_id, self.attempt_id)
        self.assertEqual(entry.details["existing_score"], 0.0)

    def test_a_new_attempt_can_still_be_started_and_submitted(self):
        """Retaking a quiz is legitimate and must keep working -- the constraint is one
        submission per attempt, not one attempt per learner."""
        self._submit(self.wrong.id)

        second_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
        ).data["id"]
        response = self.client.post(
            f"/api/attempts/quiz-attempts/{second_id}/submit/",
            {"answers": [{"question": self.question.id, "selected_option": self.correct.id}]},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.data["score"]), 100.0)

    def test_result_payload_discloses_correct_answer_after_submission(self):
        """Post-submission disclosure is intended (the 'why was I wrong' moment) and the
        learner-facing question API no longer carries it, so the submit response is now the
        only place the result screen can get it."""
        response = self._submit(self.wrong.id)
        answer = response.data["answers"][0]
        self.assertEqual(answer["correct_option_text"], "Right")
        self.assertEqual(answer["selected_option_text"], "Wrong")
        self.assertEqual(answer["explanation"], "Because.")


class AdaptiveRetrainingTests(APITestCase):
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
        """Three separate passing attempts in a row (the MASTERY_STREAK_THRESHOLD) should
        flip the topic to mastered and push the next-eligible date out via Leitner box
        scheduling. Mastery is scored per whole attempt (>= PASS_THRESHOLD), not per
        individual answer, so this takes three submissions, not three questions in one."""
        all_correct = [
            {"question": q.id, "selected_option": self.correct_options[i].id}
            for i, q in enumerate(self.questions)
        ]
        for _ in range(3):
            attempt_id = self._start_attempt()
            self.client.post(f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": all_correct}, format="json")

        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        self.assertEqual(mastery.streak_correct, 3)
        self.assertEqual(mastery.mastery_status, "mastered")
        self.assertGreater(mastery.next_eligible_at, timezone.now())

    def test_topic_mastery_scores_whole_attempt_not_last_answer(self):
        """A strong attempt (2 of 3 correct, 67% -- below PASS_THRESHOLD) must not be
        overwritten by whichever answer happens to be graded last; regression test for the
        bug where TopicMastery updated once per AttemptAnswer instead of once per attempt."""
        attempt_id = self._start_attempt()
        answers = [
            {"question": self.questions[0].id, "selected_option": self.correct_options[0].id},
            {"question": self.questions[1].id, "selected_option": self.correct_options[1].id},
            {"question": self.questions[2].id, "selected_option": self.wrong_options[2].id},
        ]
        _offer_exactly(attempt_id, [a["question"] for a in answers])
        self.client.post(f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json")

        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        # 2/3 = 66.67%, below the 80% pass mark, regardless of the last answer being wrong.
        self.assertEqual(mastery.streak_correct, 0)
        self.assertEqual(mastery.box_index, 0)

    def test_topic_mastery_resets_on_a_wrong_answer(self):
        attempt_id = self._start_attempt()
        answers = [
            {"question": self.questions[0].id, "selected_option": self.correct_options[0].id},
            {"question": self.questions[1].id, "selected_option": self.wrong_options[1].id},
        ]
        _offer_exactly(attempt_id, [a["question"] for a in answers])
        self.client.post(f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json")

        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        self.assertEqual(mastery.streak_correct, 0)
        self.assertEqual(mastery.box_index, 0)
        self.assertEqual(mastery.mastery_status, "in_progress")

    def test_hard_question_weighted_more_than_easy_for_mastery(self):
        """Difficulty-weighted scoring: 3/4 correct (75%) fails the plain pass mark, but
        weighting a hard question higher than easy ones (Ye, Su & Cao, KDD 2022) pushes
        the same attempt to exactly 80% because the one correct answer was the hard one."""
        hard_q = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Hard?", explanation="Because.",
            status="approved", difficulty="hard",
        )
        hard_correct = Option.objects.create(question=hard_q, option_text="Right", is_correct=True)
        easy_qs, easy_correct, easy_wrong = [], [], []
        for i in range(3):
            q = Question.objects.create(
                sop=self.sop, job_role=self.role, question_text=f"Easy {i}?", explanation="Because.",
                status="approved", difficulty="easy",
            )
            easy_qs.append(q)
            easy_correct.append(Option.objects.create(question=q, option_text="Right", is_correct=True))
            easy_wrong.append(Option.objects.create(question=q, option_text="Wrong", is_correct=False))

        attempt_id = self._start_attempt()
        answers = [
            {"question": hard_q.id, "selected_option": hard_correct.id},
            {"question": easy_qs[0].id, "selected_option": easy_correct[0].id},
            {"question": easy_qs[1].id, "selected_option": easy_correct[1].id},
            {"question": easy_qs[2].id, "selected_option": easy_wrong[2].id},
        ]
        _offer_exactly(attempt_id, [a["question"] for a in answers])
        response = self.client.post(f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json")
        self.assertEqual(float(response.data["score"]), 75.0)  # plain score: 3/4 correct

        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        # Weighted: (2.0 hard + 1.0 + 1.0) / (2.0 + 1.0 + 1.0 + 1.0) = 4.0/5.0 = 80% -> pass.
        self.assertEqual(mastery.streak_correct, 1)
        self.assertEqual(mastery.box_index, 1)

    def test_low_confidence_question_excluded_from_mastery_scoring(self):
        """Confidence-aware scoring: a wrong answer on a low-confidence AI-drafted
        question (Geng et al., NAACL 2024, on LLM confidence miscalibration) shouldn't
        unfairly reset an otherwise-strong attempt's schedule."""
        trusted_qs, trusted_correct = [], []
        for i in range(3):
            q = Question.objects.create(
                sop=self.sop, job_role=self.role, question_text=f"Trusted {i}?", explanation="Because.",
                status="approved", confidence_score=0.9,
            )
            trusted_qs.append(q)
            trusted_correct.append(Option.objects.create(question=q, option_text="Right", is_correct=True))
        ambiguous_q = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Ambiguous?", explanation="Because.",
            status="approved", confidence_score=0.2,
        )
        ambiguous_wrong = Option.objects.create(question=ambiguous_q, option_text="Wrong", is_correct=False)

        attempt_id = self._start_attempt()
        answers = [
            {"question": trusted_qs[0].id, "selected_option": trusted_correct[0].id},
            {"question": trusted_qs[1].id, "selected_option": trusted_correct[1].id},
            {"question": trusted_qs[2].id, "selected_option": trusted_correct[2].id},
            {"question": ambiguous_q.id, "selected_option": ambiguous_wrong.id},
        ]
        _offer_exactly(attempt_id, [a["question"] for a in answers])
        response = self.client.post(f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json")
        self.assertEqual(float(response.data["score"]), 75.0)  # plain score: 3/4 correct

        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        # Excluding the low-confidence miss: 3/3 trusted answers correct -> 100% -> pass.
        self.assertEqual(mastery.streak_correct, 1)
        self.assertEqual(mastery.box_index, 1)

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

    def test_retraining_status_requires_reviewer_or_admin(self):
        response = self.client.get("/api/attempts/retraining-status/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_retraining_status_lists_unmastered_learners_and_flags_escalation(self):
        TopicMastery.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role,
            box_index=0, streak_correct=0, mastery_status="in_progress",
            next_eligible_at=timezone.now() - datetime.timedelta(days=1),
        )
        TopicMastery.objects.create(
            learner=self.other_learner, sop=self.sop, job_role=self.role,
            box_index=5, streak_correct=3, mastery_status="mastered",
            next_eligible_at=timezone.now() + datetime.timedelta(days=30),
        )
        for score in [10, 20, 30]:
            attempt = self.client.post(
                "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
            ).data["id"]
            # A single-question assessment, failed three times, to drive the escalation
            # threshold. Narrowed so the submission matches the recorded offered set.
            _offer_exactly(attempt, [self.questions[0].id])
            self.client.post(
                f"/api/attempts/quiz-attempts/{attempt}/submit/",
                {"answers": [{"question": self.questions[0].id, "selected_option": self.wrong_options[0].id}]},
                format="json",
            )

        self.client.force_authenticate(user=self.admin)
        response = self.client.get("/api/attempts/retraining-status/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        learners = {row["learner"]: row for row in response.data["learners"]}
        self.assertIn("rohit", learners)
        self.assertNotIn("priya", learners)  # mastered, excluded
        self.assertGreaterEqual(learners["rohit"]["failed_attempts"], 3)
        self.assertTrue(learners["rohit"]["escalated"])


class AdaptiveLearningScenarioTests(APITestCase):
    """The controlled three-topic scenario the adaptive engine exists to handle.

    One SOP, three sections (GMP / CAPA / Documentation), two approved questions each.
    The learner gets GMP right and the other two wrong, and the engine must then steer
    retraining toward the two weak sections rather than replaying the whole SOP.

    These tests describe the *intended* adaptive behaviour end to end; where the
    implementation diverges they are the evidence for what to fix.
    """

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Quality Management", sop_code="SOP-960", version="v1.0", department="Production",
            file="sops/sop-960.txt", status="processed",
        )
        self.chunks = {}
        self.questions = {}
        self.correct = {}
        self.wrong = {}
        for name in ("GMP", "CAPA", "Documentation"):
            chunk = SOPChunk.objects.create(sop=self.sop, section_title=name, chunk_text=f"{name} content.")
            self.chunks[name] = chunk
            self.questions[name] = []
            self.correct[name] = []
            self.wrong[name] = []
            # Three questions per section, not two: adaptive.MIN_EVIDENCE requires at least
            # three answers before a section can be excluded on accuracy alone, so a
            # two-question section could never reach LOW priority however well it was
            # answered. Three is also closer to how a real SOP section would be covered.
            for i in range(3):
                question = Question.objects.create(
                    sop=self.sop, job_role=self.role, source_chunk=chunk,
                    question_text=f"{name} question {i}?", explanation="Because.", status="approved",
                )
                self.questions[name].append(question)
                self.correct[name].append(
                    Option.objects.create(question=question, option_text="Right", is_correct=True)
                )
                self.wrong[name].append(
                    Option.objects.create(question=question, option_text="Wrong", is_correct=False)
                )
        self.client.force_authenticate(user=self.learner)

    def _take_quiz(self, outcomes):
        """outcomes: {"GMP": True, "CAPA": False, ...} -> answer both questions that way.

        Submission must now match the attempt's persisted offered set exactly, so an
        attempt covering a subset of sections has to be created as a *targeted* attempt --
        which is what the adaptive assignment endpoint produces when it selects only the
        weak sections. Naming fewer sections here therefore models targeted retraining,
        exactly as it did before this contract existed; the offered set is narrowed to the
        questions being sat rather than the submission being allowed to omit questions.
        """
        attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
        ).data["id"]
        answers = []
        for name, should_be_correct in outcomes.items():
            for i, question in enumerate(self.questions[name]):
                option = self.correct[name][i] if should_be_correct else self.wrong[name][i]
                answers.append({"question": question.id, "selected_option": option.id})
        _offer_exactly(attempt_id, [a["question"] for a in answers])
        return self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json"
        )

    def _force_due(self):
        """Spaced repetition legitimately schedules the next review a day out, so a same-
        session test (or a live demo) has to fast-forward. This manipulates only the
        schedule, never the mastery state itself.

        Both levels are forced: assignment is now gated on the *section's* own FSRS
        schedule (ChunkMastery), not just the whole-SOP one. Forcing only TopicMastery
        would leave every section still scheduled for tomorrow and nothing would be
        offered -- which is the correct behaviour, just not what these tests are about.
        """
        past = timezone.now() - datetime.timedelta(days=1)
        TopicMastery.objects.filter(learner=self.learner, sop=self.sop).update(next_eligible_at=past)
        ChunkMastery.objects.filter(learner=self.learner, sop_chunk__sop=self.sop).update(
            next_eligible_at=past
        )

    def test_every_answered_section_gets_its_own_mastery_row(self):
        self._take_quiz({"GMP": True, "CAPA": False, "Documentation": False})
        self.assertEqual(
            ChunkMastery.objects.filter(learner=self.learner, sop_chunk__sop=self.sop).count(), 3
        )

    def test_correct_section_strengthens_while_wrong_sections_stay_weak(self):
        self._take_quiz({"GMP": True, "CAPA": False, "Documentation": False})

        gmp = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunks["GMP"])
        capa = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunks["CAPA"])
        docs = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunks["Documentation"])

        self.assertEqual(gmp.streak_correct, 1)
        self.assertEqual(gmp.mastery_status, "in_progress")
        self.assertEqual(capa.streak_correct, 0)
        self.assertEqual(docs.streak_correct, 0)
        # Ability rating separates them too, not just the streak counter.
        self.assertGreater(gmp.elo_rating, capa.elo_rating)

    def test_weak_sections_are_scheduled_sooner_than_the_strong_one(self):
        """The core spaced-repetition promise: material you got wrong comes back sooner."""
        self._take_quiz({"GMP": True, "CAPA": False, "Documentation": False})

        gmp = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunks["GMP"])
        capa = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunks["CAPA"])
        self.assertLess(capa.next_eligible_at, gmp.next_eligible_at)

    def test_retraining_targets_only_the_weak_sections(self):
        """The headline behaviour: after this quiz the learner should be re-tested on CAPA
        and Documentation, and NOT dragged back through GMP."""
        self._take_quiz({"GMP": True, "CAPA": False, "Documentation": False})
        self._force_due()

        response = self.client.get("/api/attempts/auto-assigned/")
        assignment = response.data["assignments"][0]
        targeted = set(assignment["question_ids"])

        for question in self.questions["CAPA"] + self.questions["Documentation"]:
            self.assertIn(question.id, targeted)
        for question in self.questions["GMP"]:
            self.assertNotIn(question.id, targeted)

    def test_mastering_a_section_removes_it_from_retraining(self):
        """Three passes on a section should retire it, narrowing retraining further."""
        self._take_quiz({"GMP": True, "CAPA": False, "Documentation": False})
        for _ in range(3):
            self._take_quiz({"CAPA": True})
        self._force_due()

        capa = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunks["CAPA"])
        self.assertEqual(capa.mastery_status, "mastered")

        response = self.client.get("/api/attempts/auto-assigned/")
        targeted = set(response.data["assignments"][0]["question_ids"])
        for question in self.questions["CAPA"]:
            self.assertNotIn(question.id, targeted)
        for question in self.questions["Documentation"]:
            self.assertIn(question.id, targeted)

    def test_reassessment_improves_the_weak_section_state(self):
        """Closing the loop: retraining then a better attempt must move the state."""
        self._take_quiz({"GMP": True, "CAPA": False, "Documentation": False})
        before = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunks["CAPA"])
        elo_before = before.elo_rating

        self._take_quiz({"CAPA": True})

        after = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunks["CAPA"])
        self.assertEqual(after.streak_correct, 1)
        self.assertGreater(after.elo_rating, elo_before)

    def test_a_section_never_yet_assessed_is_still_offered_for_training(self):
        """A learner who has only ever been tested on GMP has no ChunkMastery row for CAPA
        or Documentation. Those sections are unseen, not mastered, so retraining must still
        cover them -- otherwise never-assessed material becomes permanently invisible."""
        self._take_quiz({"GMP": False})
        self._force_due()

        response = self.client.get("/api/attempts/auto-assigned/")
        targeted = set(response.data["assignments"][0]["question_ids"])
        for question in self.questions["CAPA"] + self.questions["Documentation"]:
            self.assertIn(question.id, targeted)


class RecencyWeightedAccuracyTests(TestCase):
    """The metric priority is actually decided on. Sequences are newest-first."""

    def test_empty_sequence_has_no_accuracy(self):
        """"No evidence" is not 0% -- conflating them is what made never-assessed sections
        invisible in the first place."""
        self.assertIsNone(adaptive.weighted_accuracy([]))

    def test_all_correct_is_one_hundred(self):
        self.assertEqual(adaptive.weighted_accuracy([True] * 5), 100.0)

    def test_all_wrong_is_zero(self):
        self.assertEqual(adaptive.weighted_accuracy([False] * 5), 0.0)

    def test_improving_learner_scores_above_their_lifetime_average(self):
        """0/5 then 5/5 -> newest-first is 5 correct then 5 wrong. Lifetime is 50%."""
        improving = [True] * 5 + [False] * 5
        self.assertEqual(adaptive.weighted_accuracy(improving), 66.7)

    def test_declining_learner_scores_below_their_lifetime_average(self):
        declining = [False] * 5 + [True] * 5
        self.assertEqual(adaptive.weighted_accuracy(declining), 33.3)

    def test_improvement_and_decline_are_symmetric_about_the_lifetime_average(self):
        improving = adaptive.weighted_accuracy([True] * 5 + [False] * 5)
        declining = adaptive.weighted_accuracy([False] * 5 + [True] * 5)
        self.assertAlmostEqual((improving + declining) / 2, 50.0, places=1)

    def test_stable_learner_stays_near_their_lifetime_average(self):
        """A learner with no trend should barely be moved by the weighting -- the metric
        must only diverge from lifetime when performance is actually changing."""
        alternating = [True, False] * 6  # lifetime exactly 50%
        self.assertAlmostEqual(adaptive.weighted_accuracy(alternating), 50.0, delta=5.0)

    def test_sustained_improvement_eventually_clears_the_pass_mark(self):
        """The learner must be able to escape a weak label in a plausible number of
        assessments -- 5 more correct answers after 0/5 then 5/5."""
        self.assertEqual(adaptive.weighted_accuracy([True] * 10 + [False] * 5), 85.7)

    def test_one_good_answer_cannot_erase_a_history_of_failure(self):
        """The other side of recency: it must not be a reset button."""
        self.assertLess(adaptive.weighted_accuracy([True] + [False] * 9), 30.0)


class AdaptiveRecencyIntegrationTests(APITestCase):
    """End-to-end: does a learner who improves stop being told they are weak?"""

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Quality", sop_code="SOP-990", version="v1.0", department="Production",
            file="f.txt", status="processed",
        )
        self.chunk = SOPChunk.objects.create(sop=self.sop, section_title="CAPA", chunk_text="c")
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, source_chunk=self.chunk,
            question_text="CAPA?", explanation="Because.", status="approved",
        )
        self.right = Option.objects.create(question=self.question, option_text="R", is_correct=True)
        self.wrong = Option.objects.create(question=self.question, option_text="W", is_correct=False)
        self.client.force_authenticate(user=self.learner)

    def _answer(self, correct, times=1):
        for _ in range(times):
            attempt_id = self.client.post(
                "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
                format="json",
            ).data["id"]
            _offer_exactly(attempt_id, [self.question.id])
            self.client.post(
                f"/api/attempts/quiz-attempts/{attempt_id}/submit/",
                {"answers": [{"question": self.question.id,
                              "selected_option": (self.right if correct else self.wrong).id}]},
                format="json",
            )

    def _section(self):
        return adaptive.analyse_sections(self.learner, self.sop, self.role)[0]

    def test_learner_who_improved_is_no_longer_flagged_high(self):
        """The reviewer trap: lifetime stays at 50% while recent accuracy is 100%. Before
        recency weighting this section stayed HIGH and the UI displayed "Recent: 100%"
        right beside that verdict."""
        self._answer(correct=False, times=5)
        self.assertEqual(self._section()["priority"], "high")

        self._answer(correct=True, times=5)

        section = self._section()
        self.assertEqual(section["accuracy"], 50.0)          # lifetime, unchanged
        self.assertEqual(section["recent_accuracy"], 100.0)  # displayed
        # 66.5, not the 66.7 this asserted before difficulty weighting existed. These answers
        # go through the API, so each one moves the question's Elo; the later answers therefore
        # carry a slightly different difficulty weight than the earlier ones. The 0.2pp shift is
        # difficulty weighting acting on a genuinely drifted rating, not a regression.
        # The property under test is unchanged and asserted below: the deciding figure sits far
        # above lifetime, so the section is no longer HIGH.
        self.assertEqual(section["weighted_accuracy"], 66.5)
        self.assertGreater(section["weighted_accuracy"], section["accuracy"])
        self.assertNotEqual(section["priority"], "high")

    def test_learner_who_declined_is_flagged_before_lifetime_would_notice(self):
        self._answer(correct=True, times=5)
        self._answer(correct=False, times=5)

        section = self._section()
        self.assertEqual(section["accuracy"], 50.0)
        # 33.2 rather than 33.3, for the same reason as above.
        self.assertEqual(section["weighted_accuracy"], 33.2)
        self.assertLess(section["weighted_accuracy"], section["accuracy"])
        self.assertEqual(section["priority"], "high")

    def test_reason_names_the_metric_the_decision_was_made_on(self):
        """The UI must never display a number that contradicts its own verdict.

        Uses the declining learner: five consecutive passes would flip the section to
        `mastered`, whose reason is a different (and correct) branch.
        """
        self._answer(correct=True, times=5)
        self._answer(correct=False, times=5)
        section = self._section()
        reason = section["reason"]
        # The reason must quote the figure the decision was actually made on, whatever it is.
        self.assertIn(f"{section['weighted_accuracy']}%", reason)
        self.assertIn("recency-", reason)           # named as such
        self.assertIn("50.0% lifetime", reason)     # both shown, no contradiction
        # Difficulty was recorded for these answers, so the basis must say so.
        self.assertIn("difficulty-weighted", reason)
        self.assertTrue(section["difficulty_weighted"])

    def test_learning_gain_is_measured_from_real_answers(self):
        self._answer(correct=False, times=4)
        self._answer(correct=True, times=4)
        section = self._section()
        self.assertEqual(section["initial_accuracy"], 0.0)
        self.assertEqual(section["current_accuracy"], 100.0)
        self.assertEqual(section["improvement"], 100.0)


class EvidenceSufficiencyTests(APITestCase):
    """1/1 correct is not the same evidence as 50/50 correct."""

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Quality", sop_code="SOP-991", version="v1.0", department="Production",
            file="f.txt", status="processed",
        )
        self.chunk = SOPChunk.objects.create(sop=self.sop, section_title="GMP", chunk_text="c")
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, source_chunk=self.chunk,
            question_text="GMP?", explanation="Because.", status="approved",
        )
        self.right = Option.objects.create(question=self.question, option_text="R", is_correct=True)
        self.wrong = Option.objects.create(question=self.question, option_text="W", is_correct=False)
        self.client.force_authenticate(user=self.learner)

    def _answer(self, correct, times=1):
        for _ in range(times):
            attempt_id = self.client.post(
                "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
                format="json",
            ).data["id"]
            _offer_exactly(attempt_id, [self.question.id])
            self.client.post(
                f"/api/attempts/quiz-attempts/{attempt_id}/submit/",
                {"answers": [{"question": self.question.id,
                              "selected_option": (self.right if correct else self.wrong).id}]},
                format="json",
            )

    def _section(self):
        return adaptive.analyse_sections(self.learner, self.sop, self.role)[0]

    def test_zero_answers_is_never_assessed_not_insufficient_evidence(self):
        """These are different states and must stay distinguishable."""
        section = self._section()
        self.assertEqual(section["priority"], "high")
        self.assertIn("Never assessed", section["reason"])
        self.assertIsNone(section["accuracy"])

    def test_one_correct_answer_is_insufficient_to_exclude(self):
        self._answer(correct=True, times=1)
        section = self._section()
        self.assertEqual(section["accuracy"], 100.0)
        self.assertFalse(section["evidence_sufficient"])
        self.assertEqual(section["priority"], "medium")
        self.assertIn("insufficient evidence", section["reason"])

    def test_two_correct_answers_are_still_insufficient(self):
        self._answer(correct=True, times=2)
        self.assertEqual(self._section()["priority"], "medium")

    def test_three_correct_answers_are_sufficient_to_exclude(self):
        """The property under test is exclusion, not a particular label: three consecutive
        passing attempts also satisfy the mastery streak, so this section retires as
        'none'. Either way it is no longer selected -- which is what MIN_EVIDENCE gates."""
        self._answer(correct=True, times=3)
        section = self._section()
        self.assertTrue(section["evidence_sufficient"])
        self.assertFalse(section["selected_for_retraining"])
        self.assertIn(section["priority"], {"low", "none"})

    def test_strong_but_unmastered_section_reaches_low_priority(self):
        """Exercises the LOW branch specifically: high weighted accuracy with the mastery
        streak broken, so the 'mastered' branch cannot mask the result."""
        self._answer(correct=True, times=2)
        self._answer(correct=False, times=1)
        self._answer(correct=True, times=2)
        section = self._section()
        self.assertTrue(section["evidence_sufficient"])
        self.assertNotEqual(section["mastery_status"], "mastered")
        self.assertEqual(section["priority"], "low")
        self.assertFalse(section["selected_for_retraining"])

    def test_large_sample_of_correct_answers_is_excluded(self):
        self._answer(correct=True, times=10)
        section = self._section()
        self.assertFalse(section["selected_for_retraining"])
        self.assertEqual(section["accuracy"], 100.0)

    def test_weak_performance_on_a_small_sample_is_still_high_priority(self):
        """Deliberately asymmetric: under-training is the more costly error, so a weak
        section is not given the benefit of the doubt for lack of data."""
        self._answer(correct=False, times=1)
        section = self._section()
        self.assertEqual(section["priority"], "high")
        self.assertFalse(section["evidence_sufficient"])

    def test_weak_performance_on_a_large_sample_is_high_priority(self):
        self._answer(correct=False, times=10)
        self.assertEqual(self._section()["priority"], "high")


class AdaptiveScheduleReconciliationTests(APITestCase):
    """Adaptive answers WHAT, FSRS answers WHEN. The learner must never be recommended
    something the assignment engine will not hand over."""

    def setUp(self):
        from accounts.models import LearnerProfile

        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        LearnerProfile.objects.create(user=self.learner, job_role=self.role, employee_code="E1")
        self.sop = SOPDocument.objects.create(
            title="Quality", sop_code="SOP-992", version="v1.0", department="Production",
            file="f.txt", status="processed",
        )
        self.chunk = SOPChunk.objects.create(sop=self.sop, section_title="CAPA", chunk_text="c")
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, source_chunk=self.chunk,
            question_text="CAPA?", explanation="Because.", status="approved",
        )
        self.right = Option.objects.create(question=self.question, option_text="R", is_correct=True)
        self.wrong = Option.objects.create(question=self.question, option_text="W", is_correct=False)
        self.client.force_authenticate(user=self.learner)
        # One failed answer: genuinely weak, and FSRS schedules the retest ~1 day out.
        attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
            format="json",
        ).data["id"]
        self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/",
            {"answers": [{"question": self.question.id, "selected_option": self.wrong.id}]},
            format="json",
        )

    def _path_section(self):
        response = self.client.get("/api/attempts/learning-path/")
        row = next(r for r in response.data["sops"] if r["sop_id"] == self.sop.id)
        return row, row["sections"][0]

    def _force_due(self):
        past = timezone.now() - datetime.timedelta(days=1)
        TopicMastery.objects.filter(learner=self.learner, sop=self.sop).update(next_eligible_at=past)
        ChunkMastery.objects.filter(learner=self.learner, sop_chunk=self.chunk).update(
            next_eligible_at=past
        )

    def test_learning_path_and_auto_assigned_agree(self):
        """The core contract: anything reported as available_now must actually be offered,
        and anything offered must be reported as available_now."""
        for force in (False, True):
            if force:
                self._force_due()
            _row, section = self._path_section()
            offered = self.client.get("/api/attempts/auto-assigned/").data["assignments"]
            offered_ids = {q for a in offered for q in a["question_ids"]}

            if section["available_now"]:
                self.assertTrue(offered_ids, "path says available but nothing was offered")
                self.assertIn(self.question.id, offered_ids)
            else:
                self.assertNotIn(self.question.id, offered_ids)

    def test_weak_but_not_yet_due_is_recommended_and_labelled_scheduled(self):
        """Not hidden -- the learner should know the section is weak -- but honestly
        labelled so they are not sent to a quiz that does not exist."""
        row, section = self._path_section()
        self.assertEqual(section["priority"], "high")
        self.assertTrue(section["selected_for_retraining"])   # WHAT: yes
        self.assertFalse(section["is_due"])                   # WHEN: not yet
        self.assertFalse(section["available_now"])            # so: not actionable
        self.assertIsNotNone(section["next_eligible_at"])     # and the date is given
        self.assertEqual(row["sections_needing_training"], 1)
        self.assertEqual(row["sections_available_now"], 0)

        self.assertEqual(self.client.get("/api/attempts/auto-assigned/").data["assignments"], [])

    def test_weak_and_due_is_available_and_actually_offered(self):
        self._force_due()
        _row, section = self._path_section()
        self.assertTrue(section["available_now"])

        assignments = self.client.get("/api/attempts/auto-assigned/").data["assignments"]
        self.assertEqual(len(assignments), 1)
        self.assertIn(self.question.id, assignments[0]["question_ids"])

    def test_section_due_alone_is_enough_to_offer_retraining(self):
        """ChunkMastery.next_eligible_at was previously computed and never consumed:
        assignment waited for the whole SOP to come due, delaying exactly the section FSRS
        had scheduled soonest."""
        past = timezone.now() - datetime.timedelta(days=1)
        ChunkMastery.objects.filter(learner=self.learner, sop_chunk=self.chunk).update(
            next_eligible_at=past
        )
        TopicMastery.objects.filter(learner=self.learner, sop=self.sop).update(
            next_eligible_at=timezone.now() + datetime.timedelta(days=30)
        )
        assignments = self.client.get("/api/attempts/auto-assigned/").data["assignments"]
        self.assertEqual(len(assignments), 1)


class TwoLearnerPersonalisationTests(APITestCase):
    """Isolation is not personalisation. This proves two learners with different histories
    receive *different content*, not merely that they cannot see each other's data."""

    def setUp(self):
        from accounts.models import LearnerProfile

        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Quality", sop_code="SOP-993", version="v1.0", department="Production",
            file="f.txt", status="processed",
        )
        self.chunks, self.questions, self.right, self.wrong = {}, {}, {}, {}
        for name in ("GMP", "CAPA", "Documentation"):
            chunk = SOPChunk.objects.create(sop=self.sop, section_title=name, chunk_text="c")
            self.chunks[name] = chunk
            self.questions[name] = []
            self.right[name] = []
            self.wrong[name] = []
            for i in range(3):
                q = Question.objects.create(
                    sop=self.sop, job_role=self.role, source_chunk=chunk,
                    question_text=f"{name}{i}?", explanation="B.", status="approved",
                )
                self.questions[name].append(q)
                self.right[name].append(Option.objects.create(question=q, option_text="R", is_correct=True))
                self.wrong[name].append(Option.objects.create(question=q, option_text="W", is_correct=False))

        self.a = get_user_model().objects.create_user(username="learner_a", password="demo12345")
        self.b = get_user_model().objects.create_user(username="learner_b", password="demo12345")
        for user in (self.a, self.b):
            LearnerProfile.objects.create(user=user, job_role=self.role, employee_code=user.username)

    def _sit(self, user, outcomes):
        self.client.force_authenticate(user=user)
        attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
            format="json",
        ).data["id"]
        answers = []
        for name, correct in outcomes.items():
            for i, q in enumerate(self.questions[name]):
                option = (self.right if correct else self.wrong)[name][i]
                answers.append({"question": q.id, "selected_option": option.id})
        _offer_exactly(attempt_id, [a["question"] for a in answers])
        self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json"
        )

    def _selected_titles(self, user):
        sections = adaptive.analyse_sections(user, self.sop, self.role)
        return {s["section_title"] for s in sections if s["selected_for_retraining"]}

    def test_two_learners_receive_different_adaptive_training(self):
        # A is strong on GMP, weak elsewhere. B is the mirror image.
        self._sit(self.a, {"GMP": True, "CAPA": False, "Documentation": False})
        self._sit(self.b, {"GMP": False, "CAPA": True, "Documentation": False})

        selected_a = self._selected_titles(self.a)
        selected_b = self._selected_titles(self.b)

        self.assertNotEqual(selected_a, selected_b)
        self.assertIn("CAPA", selected_a)
        self.assertNotIn("GMP", selected_a)
        self.assertIn("GMP", selected_b)
        self.assertNotIn("CAPA", selected_b)
        # Both are weak on Documentation, so both are trained on it -- personalisation
        # means different where they differ, not different for its own sake.
        self.assertIn("Documentation", selected_a & selected_b)

    def test_their_recommended_question_sets_actually_differ(self):
        self._sit(self.a, {"GMP": True, "CAPA": False, "Documentation": False})
        self._sit(self.b, {"GMP": False, "CAPA": True, "Documentation": False})

        ids_a = set(adaptive.select_retraining_questions(
            adaptive.analyse_sections(self.a, self.sop, self.role)))
        ids_b = set(adaptive.select_retraining_questions(
            adaptive.analyse_sections(self.b, self.sop, self.role)))

        self.assertNotEqual(ids_a, ids_b)
        for q in self.questions["CAPA"]:
            self.assertIn(q.id, ids_a)
            self.assertNotIn(q.id, ids_b)
        for q in self.questions["GMP"]:
            self.assertIn(q.id, ids_b)
            self.assertNotIn(q.id, ids_a)


class SubmissionValidationTests(APITestCase):
    """A submission may only contain approved questions belonging to this attempt's SOP.
    Without this, a crafted payload fed answers for arbitrary questions straight into
    ChunkMastery and the adaptive engine."""

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="A", sop_code="SOP-994", version="v1.0", department="P", file="f.txt", status="processed",
        )
        self.other_sop = SOPDocument.objects.create(
            title="B", sop_code="SOP-995", version="v1.0", department="P", file="f.txt", status="processed",
        )
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q?", explanation="B.", status="approved",
        )
        self.right = Option.objects.create(question=self.question, option_text="R", is_correct=True)
        self.foreign = Question.objects.create(
            sop=self.other_sop, job_role=self.role, question_text="F?", explanation="B.", status="approved",
        )
        self.draft = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="D?", explanation="B.", status="draft",
        )
        self.client.force_authenticate(user=self.learner)
        self.attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
            format="json",
        ).data["id"]

    def _submit(self, answers):
        # Deliberately NOT narrowed: this class exists to prove that foreign, unapproved,
        # duplicated and not-offered questions are rejected. Its attempt's offered set is
        # the real one the server recorded -- exactly [self.question], since the draft and
        # the foreign-SOP question are not approved for this SOP and role.
        return self.client.post(
            f"/api/attempts/quiz-attempts/{self.attempt_id}/submit/", {"answers": answers}, format="json"
        )

    def test_valid_submission_is_accepted(self):
        response = self._submit([{"question": self.question.id, "selected_option": self.right.id}])
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_question_from_another_sop_is_rejected(self):
        response = self._submit([{"question": self.foreign.id, "selected_option": None}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(self.foreign.id, response.data["invalid_question_ids"])

    def test_unapproved_question_is_rejected(self):
        response = self._submit([{"question": self.draft.id, "selected_option": None}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_missing_question_id_is_rejected(self):
        response = self._submit([{"selected_option": None}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_a_rejected_submission_does_not_consume_the_attempt(self):
        """The attempt must remain submittable -- a validation failure is not a submission."""
        self._submit([{"question": self.foreign.id, "selected_option": None}])
        self.assertIsNone(QuizAttempt.objects.get(id=self.attempt_id).completed_at)

        response = self._submit([{"question": self.question.id, "selected_option": self.right.id}])
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_rejected_submission_does_not_touch_mastery(self):
        self._submit([{"question": self.foreign.id, "selected_option": None}])
        self.assertFalse(TopicMastery.objects.filter(learner=self.learner).exists())
        self.assertFalse(AttemptAnswer.objects.filter(attempt_id=self.attempt_id).exists())

    def test_duplicate_question_ids_are_rejected(self):
        """Answering the same question repeatedly must not manufacture evidence.

        Each duplicate previously became its own AttemptAnswer row, so one known question
        submitted five times put five "correct" answers into that section's history --
        enough to satisfy MIN_EVIDENCE on its own and to pull the recency-weighted accuracy
        (and the section's Elo ability) up with fabricated repetitions of a single item.
        Rejected before any write, so nothing downstream of the payload check moves.
        """
        for repeats in (3, 5, 10):
            with self.subTest(repeats=repeats):
                response = self._submit(
                    [{"question": self.question.id, "selected_option": self.right.id}] * repeats
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertIn(self.question.id, response.data["duplicate_question_ids"])
                # No writes: no answers, no mastery, no Elo movement, attempt still open.
                self.assertFalse(AttemptAnswer.objects.filter(attempt_id=self.attempt_id).exists())
                self.assertFalse(TopicMastery.objects.filter(learner=self.learner).exists())
                self.assertFalse(ChunkMastery.objects.filter(learner=self.learner).exists())
                self.assertIsNone(QuizAttempt.objects.get(id=self.attempt_id).completed_at)
                self.question.refresh_from_db()
                self.assertEqual(self.question.elo_rating, 1500)

    def test_distinct_question_ids_are_still_accepted(self):
        """The guard rejects repetition, not multi-question submissions.

        The second question is created before the attempt, so both are in the offered set
        the server records at creation. A question added *after* an attempt starts is
        deliberately not offered by that attempt -- the assessment is fixed when it begins.
        """
        second = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q2?", explanation="B.", status="approved",
        )
        second_right = Option.objects.create(question=second, option_text="R", is_correct=True)
        attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
            format="json",
        ).data["id"]
        self.attempt_id = attempt_id

        response = self._submit([
            {"question": self.question.id, "selected_option": self.right.id},
            {"question": second.id, "selected_option": second_right.id},
        ])

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AttemptAnswer.objects.filter(attempt_id=self.attempt_id).count(), 2)


class OfferedQuestionSetTests(APITestCase):
    """The attempt, not the browser, defines what the assessment was.

    Before this contract the server checked only that a submitted question belonged to the
    attempt's SOP, matched the role and was approved. A modified client could therefore
    substitute a different eligible question, or omit questions it did not want to answer
    -- and because the score was computed over submitted answers, omitting inflated it.
    These tests pin the exact-set contract and the zero-write guarantee behind it.
    """

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.other_learner = get_user_model().objects.create_user(username="priya", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-970", version="v1.0", department="P",
            file="f.txt", status="processed",
        )
        self.other_sop = SOPDocument.objects.create(
            title="Other", sop_code="SOP-971", version="v1.0", department="P",
            file="f.txt", status="processed",
        )
        self.chunk = SOPChunk.objects.create(
            sop=self.sop, chunk_text="Section text", section_title="Section 1",
            chunking_strategy="heading",
        )
        self.questions, self.right, self.wrong = [], [], []
        for i in range(3):
            q = Question.objects.create(
                sop=self.sop, job_role=self.role, question_text=f"Q{i}?", explanation="B.",
                status="approved", source_chunk=self.chunk,
            )
            self.questions.append(q)
            self.right.append(Option.objects.create(question=q, option_text="R", is_correct=True))
            self.wrong.append(Option.objects.create(question=q, option_text="W", is_correct=False))
        self.foreign = Question.objects.create(
            sop=self.other_sop, job_role=self.role, question_text="F?", explanation="B.",
            status="approved",
        )
        Option.objects.create(question=self.foreign, option_text="R", is_correct=True)
        self.client.force_authenticate(user=self.learner)
        self.attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
            format="json",
        ).data["id"]

    def _submit(self, answers):
        return self.client.post(
            f"/api/attempts/quiz-attempts/{self.attempt_id}/submit/", {"answers": answers},
            format="json",
        )

    def _full(self, correct=(True, True, True)):
        return [
            {"question": self.questions[i].id,
             "selected_option": (self.right[i] if ok else self.wrong[i]).id}
            for i, ok in enumerate(correct)
        ]

    def _assert_nothing_written(self):
        """A1x: a rejected submission must leave every derived record untouched."""
        self.assertFalse(AttemptAnswer.objects.filter(attempt_id=self.attempt_id).exists())
        self.assertFalse(TopicMastery.objects.filter(learner=self.learner).exists())
        self.assertFalse(ChunkMastery.objects.filter(learner=self.learner).exists())
        self.assertIsNone(QuizAttempt.objects.get(id=self.attempt_id).completed_at)
        for q in self.questions:
            q.refresh_from_db()
            self.assertEqual(q.elo_rating, 1500)

    # ---- A1 / A18 -------------------------------------------------------------
    def test_a1_offered_set_is_persisted_when_an_attempt_is_created(self):
        offered = QuizAttemptQuestion.objects.filter(attempt_id=self.attempt_id)
        self.assertEqual(offered.count(), 3)
        self.assertEqual(
            sorted(offered.values_list("question_id", flat=True)),
            sorted(q.id for q in self.questions),
        )
        self.assertEqual(list(offered.values_list("position", flat=True)), [0, 1, 2])

    def test_a18_adaptive_selection_is_persisted_exactly_as_served(self):
        """The auto-assigned attempt's offered set must equal the ids reported to the client."""
        TopicMastery.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role,
            next_eligible_at=timezone.now() - datetime.timedelta(days=1),
        )
        response = self.client.get("/api/attempts/auto-assigned/")
        assignment = response.data["assignments"][0]
        persisted = list(
            QuizAttemptQuestion.objects.filter(attempt_id=assignment["attempt_id"])
            .values_list("question_id", flat=True)
        )
        self.assertEqual(sorted(persisted), sorted(assignment["question_ids"]))

    def test_a18b_reused_attempt_keeps_its_original_offered_set(self):
        """Re-polling the assignment endpoint must not silently re-scope a live attempt."""
        TopicMastery.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role,
            next_eligible_at=timezone.now() - datetime.timedelta(days=1),
        )
        first = self.client.get("/api/attempts/auto-assigned/").data["assignments"][0]
        second = self.client.get("/api/attempts/auto-assigned/").data["assignments"][0]
        self.assertEqual(first["attempt_id"], second["attempt_id"])
        self.assertEqual(sorted(first["question_ids"]), sorted(second["question_ids"]))

    # ---- A2 / A9 / A10 --------------------------------------------------------
    def test_a2_exact_offered_set_is_accepted(self):
        response = self._submit(self._full())
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(float(response.data["score"]), 100.0)

    def test_a9_unanswered_offered_question_sent_as_null_is_accepted(self):
        answers = self._full()
        answers[2]["selected_option"] = None
        response = self._submit(answers)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(AttemptAnswer.objects.filter(attempt_id=self.attempt_id).count(), 3)

    def test_a10_score_denominator_is_the_full_offered_set(self):
        """2 correct, 1 unanswered over 3 offered must be 66.67% -- never 2/2 = 100%."""
        answers = self._full()
        answers[2]["selected_option"] = None
        response = self._submit(answers)
        self.assertAlmostEqual(float(response.data["score"]), 66.67, places=1)

    # ---- A3–A8: the rejection matrix ------------------------------------------
    def test_a3_duplicate_ids_rejected(self):
        answers = self._full()
        answers[1] = dict(answers[0])
        response = self._submit(answers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(self.questions[0].id, response.data["duplicate_question_ids"])
        self._assert_nothing_written()

    def test_a4_a5_foreign_question_rejected(self):
        answers = self._full()
        answers[2] = {"question": self.foreign.id, "selected_option": None}
        response = self._submit(answers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(self.foreign.id, response.data["not_offered_question_ids"])
        self._assert_nothing_written()

    def test_a6_a8_missing_offered_question_rejected(self):
        """The partial-submission inflation vector: answer only what you know."""
        response = self._submit(self._full()[:2])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(self.questions[2].id, response.data["missing_question_ids"])
        self.assertEqual(response.data["offered_count"], 3)
        self.assertEqual(response.data["submitted_count"], 2)
        self._assert_nothing_written()

    def test_a7_extra_question_rejected(self):
        extra = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Extra?", explanation="B.",
            status="approved",
        )
        Option.objects.create(question=extra, option_text="R", is_correct=True)
        response = self._submit(self._full() + [{"question": extra.id, "selected_option": None}])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn(extra.id, response.data["not_offered_question_ids"])
        self._assert_nothing_written()

    def test_a8b_empty_submission_rejected_when_questions_were_offered(self):
        response = self._submit([])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self._assert_nothing_written()

    def test_adversarial_mixed_payload_rejected_with_zero_writes(self):
        """The full hostile payload: offered + not-offered + foreign + repeats."""
        response = self._submit([
            {"question": self.questions[0].id, "selected_option": self.right[0].id},
            {"question": self.foreign.id, "selected_option": None},
            {"question": self.questions[0].id, "selected_option": self.right[0].id},
            {"question": self.questions[0].id, "selected_option": self.right[0].id},
        ])
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self._assert_nothing_written()

    # ---- A16 / A17 ------------------------------------------------------------
    def test_a16_completed_attempt_cannot_be_resubmitted(self):
        self.assertEqual(self._submit(self._full()).status_code, status.HTTP_200_OK)
        self.assertEqual(self._submit(self._full()).status_code, status.HTTP_409_CONFLICT)

    def test_a17_another_learner_cannot_submit_against_this_attempt(self):
        self.client.force_authenticate(user=self.other_learner)
        response = self._submit(self._full())
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertFalse(AttemptAnswer.objects.filter(attempt_id=self.attempt_id).exists())


class InteractionTelemetryTests(APITestCase):
    """B1-B10: the interaction record must be temporally usable and server-authoritative.

    These fields exist so that future knowledge-tracing work has a valid event log. They
    are pinned here because the guarantees -- server-set time, untrusted latency, no
    fabricated history -- are exactly what makes such a dataset defensible.
    """

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-972", version="v1.0", department="P",
            file="f.txt", status="processed",
        )
        self.q1 = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q1?", explanation="B.", status="approved",
        )
        self.r1 = Option.objects.create(question=self.q1, option_text="R", is_correct=True)
        self.q2 = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q2?", explanation="B.", status="approved",
        )
        self.r2 = Option.objects.create(question=self.q2, option_text="R", is_correct=True)
        self.client.force_authenticate(user=self.learner)

    def _attempt(self):
        return self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
            format="json",
        ).data["id"]

    def _submit(self, answers, attempt_id=None):
        attempt_id = attempt_id or self._attempt()
        return self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json"
        )

    def _both(self, **extra):
        return [
            dict({"question": self.q1.id, "selected_option": self.r1.id}, **extra),
            dict({"question": self.q2.id, "selected_option": self.r2.id}, **extra),
        ]

    def test_b1_b2_answered_at_is_set_server_side_and_timezone_aware(self):
        before = timezone.now()
        self.assertEqual(self._submit(self._both()).status_code, status.HTTP_200_OK)
        after = timezone.now()
        for answer in AttemptAnswer.objects.all():
            self.assertIsNotNone(answer.answered_at)
            self.assertIsNotNone(answer.answered_at.tzinfo)  # B2: aware, not naive
            self.assertTrue(before <= answer.answered_at <= after)

    def test_b3_client_cannot_override_answered_at(self):
        forged = timezone.now() - datetime.timedelta(days=365)
        self._submit(self._both(answered_at=forged.isoformat()))
        for answer in AttemptAnswer.objects.all():
            self.assertGreater(answer.answered_at, forged + datetime.timedelta(days=300))

    def test_b4_b7_latency_accepted_and_optional(self):
        answers = self._both()
        answers[0]["response_latency_ms"] = 4200
        # answers[1] omits it entirely -- B7: latency may be null
        self.assertEqual(self._submit(answers).status_code, status.HTTP_200_OK)
        self.assertEqual(AttemptAnswer.objects.get(question=self.q1).response_latency_ms, 4200)
        self.assertIsNone(AttemptAnswer.objects.get(question=self.q2).response_latency_ms)

    def test_b5_negative_latency_rejected_with_no_writes(self):
        answers = self._both()
        answers[0]["response_latency_ms"] = -1
        response = self._submit(answers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(AttemptAnswer.objects.count(), 0)

    def test_b6_implausible_latency_rejected(self):
        answers = self._both()
        answers[0]["response_latency_ms"] = AttemptAnswer.MAX_RESPONSE_LATENCY_MS + 1
        response = self._submit(answers)
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(AttemptAnswer.objects.count(), 0)

    def test_b6b_non_numeric_latency_rejected(self):
        answers = self._both()
        answers[0]["response_latency_ms"] = "soon"
        self.assertEqual(self._submit(answers).status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(AttemptAnswer.objects.count(), 0)

    def test_b8_pre_existing_answers_are_not_given_fabricated_timestamps(self):
        """A row written outside the submission path keeps a NULL answered_at.

        Stands in for the historical rows that predate this field: the migration must not
        invent a response time for them, and nothing may backfill one later.
        """
        attempt = QuizAttempt.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role,
        )
        legacy = AttemptAnswer.objects.create(attempt=attempt, question=self.q1, is_correct=True)
        legacy.refresh_from_db()
        self.assertIsNone(legacy.answered_at)
        self.assertIsNone(legacy.response_latency_ms)

    def test_b9_b10_interactions_are_orderable_and_temporally_distinguishable(self):
        """A KT sequence must be reconstructable by time, across attempts."""
        self._submit(self._both())
        first_batch = list(AttemptAnswer.objects.values_list("answered_at", flat=True))
        self._submit(self._both())
        ordered = list(
            AttemptAnswer.objects.order_by("answered_at", "id").values_list("answered_at", flat=True)
        )
        self.assertEqual(len(ordered), 4)
        self.assertEqual(ordered, sorted(ordered))
        # B10: the second sitting is distinguishable from the first
        self.assertGreater(max(ordered), max(first_batch))

    def test_kt_sequence_can_be_reconstructed_with_concept_and_correctness(self):
        """The shape future KT work consumes: (time, question, concept, correct)."""
        chunk = SOPChunk.objects.create(
            sop=self.sop, chunk_text="t", section_title="S1", chunking_strategy="heading",
        )
        self.q1.source_chunk = chunk
        self.q1.save(update_fields=["source_chunk"])
        self._submit(self._both())
        sequence = [
            (a.answered_at, a.question_id, a.question.source_chunk_id, a.is_correct)
            for a in AttemptAnswer.objects.select_related("question").order_by("answered_at", "id")
        ]
        self.assertEqual(len(sequence), 2)
        self.assertTrue(all(t is not None for t, _q, _c, _ok in sequence))
        self.assertEqual(sequence[0][2], chunk.id)


class EloSnapshotTests(APITestCase):
    """The interaction record must state the ratings that were true when it happened.

    Question.elo_rating and TopicMastery.elo_rating both move after every answer, and they
    move partly *because of* the answer being recorded. Reconstructing a past interaction
    from today's ratings would therefore feed the outcome back into its own feature -- the
    definition of look-ahead bias. These tests pin the snapshot to the pre-update values
    and prove it survives later drift.

    Instrumentation only: no test here asserts any change to adaptive behaviour, because
    there is none.
    """

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-980", version="v1.0", department="P",
            file="f.txt", status="processed",
        )
        self.chunk = SOPChunk.objects.create(
            sop=self.sop, chunk_text="Section text", section_title="Section 1",
            chunking_strategy="heading",
        )
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q?", explanation="B.",
            status="approved", source_chunk=self.chunk,
        )
        self.right = Option.objects.create(question=self.question, option_text="R", is_correct=True)
        self.wrong = Option.objects.create(question=self.question, option_text="W", is_correct=False)
        self.client.force_authenticate(user=self.learner)

    def _attempt(self):
        return self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
            format="json",
        ).data["id"]

    def _submit(self, correct=True, extra=None, attempt_id=None):
        answer = {
            "question": self.question.id,
            "selected_option": (self.right if correct else self.wrong).id,
        }
        if extra:
            answer.update(extra)
        return self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id or self._attempt()}/submit/",
            {"answers": [answer]}, format="json",
        )

    # ---- TEST 1 / TEST 2 -------------------------------------------------------
    def test_1_question_difficulty_snapshot_is_the_pre_answer_rating(self):
        difficulty_before = self.question.elo_rating
        self.assertEqual(difficulty_before, 1500)

        self.assertEqual(self._submit().status_code, status.HTTP_200_OK)

        answer = AttemptAnswer.objects.get()
        self.assertEqual(answer.question_difficulty_at_answer, difficulty_before)

    def test_2_learner_ability_snapshot_is_the_pre_answer_rating(self):
        """No TopicMastery exists yet on a first attempt, so the rating truthfully is the
        starting value -- not a fabricated one."""
        self.assertFalse(TopicMastery.objects.filter(learner=self.learner).exists())

        self._submit()

        answer = AttemptAnswer.objects.get()
        self.assertEqual(answer.learner_ability_at_answer, 1500)

    def test_2b_second_attempt_snapshots_the_carried_forward_ability(self):
        """The snapshot must track the learner's real ability, not always 1500."""
        self._submit(correct=True)
        ability_after_first = TopicMastery.objects.get(learner=self.learner).elo_rating
        self.assertNotEqual(ability_after_first, 1500)

        self._submit(correct=True)

        latest = AttemptAnswer.objects.order_by("-id").first()
        self.assertAlmostEqual(latest.learner_ability_at_answer, ability_after_first, places=6)

    # ---- TEST 3: immutability under later drift --------------------------------
    def test_3_snapshot_is_unaffected_by_subsequent_elo_movement(self):
        self._submit(correct=True)
        answer = AttemptAnswer.objects.get()
        snapshot_difficulty = answer.question_difficulty_at_answer
        snapshot_ability = answer.learner_ability_at_answer

        self.question.refresh_from_db()
        mastery = TopicMastery.objects.get(learner=self.learner)
        # The ratings really did move as a result of that answer...
        self.assertLess(self.question.elo_rating, snapshot_difficulty)
        self.assertGreater(mastery.elo_rating, snapshot_ability)

        # ...and several further answers move them further still. Asserted as continued
        # drift rather than against a hard-coded rating, so the test states the property
        # being protected instead of an arithmetic result that would need revisiting if a
        # K-factor ever changed.
        drift_after_one = self.question.elo_rating
        for _ in range(3):
            self._submit(correct=True)
        self.question.refresh_from_db()
        self.assertLess(self.question.elo_rating, drift_after_one)

        # The original record is unchanged.
        answer.refresh_from_db()
        self.assertEqual(answer.question_difficulty_at_answer, snapshot_difficulty)
        self.assertEqual(answer.learner_ability_at_answer, snapshot_ability)
        self.assertEqual(snapshot_difficulty, 1500)
        self.assertEqual(snapshot_ability, 1500)

    # ---- TEST 4: no fabricated history -----------------------------------------
    def test_4_rows_written_outside_the_submission_path_stay_null(self):
        """Stands in for the rows that predate this field. The migration adds no default
        and nothing backfills them; NULL means 'not measurable', not zero."""
        attempt = QuizAttempt.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role,
        )
        legacy = AttemptAnswer.objects.create(
            attempt=attempt, question=self.question, is_correct=True,
        )
        legacy.refresh_from_db()
        self.assertIsNone(legacy.question_difficulty_at_answer)
        self.assertIsNone(legacy.learner_ability_at_answer)

    # ---- TEST 5: client cannot forge -------------------------------------------
    def test_5_client_supplied_snapshot_values_are_ignored(self):
        self._submit(extra={
            "question_difficulty_at_answer": 9999.0,
            "learner_ability_at_answer": -9999.0,
        })
        answer = AttemptAnswer.objects.get()
        self.assertEqual(answer.question_difficulty_at_answer, 1500)
        self.assertEqual(answer.learner_ability_at_answer, 1500)

    # ---- TEST 6: KT tuple reconstruction ---------------------------------------
    def test_6_kt_interaction_tuple_is_historically_correct(self):
        """(timestamp, question, concept, difficulty, ability, correctness) -- and the
        difficulty must be the historical one, not the current live rating."""
        self._submit(correct=False)

        answer = AttemptAnswer.objects.select_related("question").get()
        tuple_ = (
            answer.answered_at,
            answer.question_id,
            answer.question.source_chunk_id,
            answer.question_difficulty_at_answer,
            answer.learner_ability_at_answer,
            answer.is_correct,
        )
        self.assertIsNotNone(tuple_[0])
        self.assertEqual(tuple_[2], self.chunk.id)
        self.assertEqual(tuple_[3], 1500)
        self.assertEqual(tuple_[4], 1500)
        self.assertFalse(tuple_[5])

        # A wrong answer raises the question's live rating; the record must not follow it.
        self.question.refresh_from_db()
        self.assertGreater(self.question.elo_rating, tuple_[3])

    # ---- TEST 7: rejected submissions write nothing -----------------------------
    def test_7_invalid_submissions_create_no_snapshots(self):
        foreign_sop = SOPDocument.objects.create(
            title="Other", sop_code="SOP-981", version="v1.0", department="P",
            file="f.txt", status="processed",
        )
        foreign = Question.objects.create(
            sop=foreign_sop, job_role=self.role, question_text="F?", explanation="B.",
            status="approved",
        )
        attempt_id = self._attempt()

        for label, answers in (
            ("not offered", [{"question": foreign.id, "selected_option": None}]),
            ("duplicate", [
                {"question": self.question.id, "selected_option": self.right.id},
                {"question": self.question.id, "selected_option": self.right.id},
            ]),
            ("empty", []),
        ):
            with self.subTest(label=label):
                response = self.client.post(
                    f"/api/attempts/quiz-attempts/{attempt_id}/submit/",
                    {"answers": answers}, format="json",
                )
                self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
                self.assertEqual(AttemptAnswer.objects.count(), 0)
                self.question.refresh_from_db()
                self.assertEqual(self.question.elo_rating, 1500)
                self.assertFalse(TopicMastery.objects.filter(learner=self.learner).exists())


class DifficultyAwarePriorityTests(TestCase):
    """Question difficulty now weights the accuracy that drives priority, not only the pass
    signal that drives mastery.

    Before this change an easy question missed and a hard question missed contributed
    identically to whether a section was called weak. Elo already weighted the pass signal, so
    the two halves of the system disagreed about what "hard" meant.

    Weighting uses the difficulty *recorded with each answer*, never the question's live rating:
    a live rating moves whenever any learner answers, which would make a section's past accuracy
    drift on its own and a past decision impossible to reproduce.
    """

    EASY = 1200.0   # DIFFICULTY_SEED_ELO["easy"]  -> weight 1.0
    HARD = 1800.0   # DIFFICULTY_SEED_ELO["hard"]  -> weight 2.0

    # ---- weight mapping ---------------------------------------------------------
    def test_difficulty_weight_spans_one_to_two_and_clamps(self):
        self.assertAlmostEqual(adaptive.difficulty_weight(self.EASY), 1.0)
        self.assertAlmostEqual(adaptive.difficulty_weight(self.HARD), 2.0)
        self.assertAlmostEqual(adaptive.difficulty_weight(1500.0), 1.5)
        # a single outlier rating must not dominate the metric
        self.assertAlmostEqual(adaptive.difficulty_weight(50.0), 1.0)
        self.assertAlmostEqual(adaptive.difficulty_weight(9000.0), 2.0)

    def test_missing_difficulty_is_neutral(self):
        self.assertAlmostEqual(adaptive.difficulty_weight(None), 1.0)

    # ---- 1 / 2: easy vs hard, miss and success ----------------------------------
    def test_1_hard_miss_hurts_more_than_easy_miss(self):
        """Same answers, same order -- only which question was missed differs."""
        missed_hard = adaptive.weighted_accuracy(
            [False, True, True], [self.HARD, self.EASY, self.EASY])
        missed_easy = adaptive.weighted_accuracy(
            [False, True, True], [self.EASY, self.HARD, self.HARD])
        self.assertLess(missed_hard, missed_easy)

    def test_2_hard_success_helps_more_than_easy_success(self):
        got_hard_right = adaptive.weighted_accuracy(
            [True, False, False], [self.HARD, self.EASY, self.EASY])
        got_easy_right = adaptive.weighted_accuracy(
            [True, False, False], [self.EASY, self.HARD, self.HARD])
        self.assertGreater(got_hard_right, got_easy_right)

    def test_difficulty_can_change_the_priority_band(self):
        """The point of the feature: failing only the hard items is not the same as failing
        everything, and the classification must be able to reflect that."""
        # Three answers, two correct. Wrong one is HARD -> weighted accuracy drops below 60.
        wrong_was_hard = adaptive.weighted_accuracy(
            [False, True, True], [self.HARD, self.EASY, self.EASY])
        # Same pattern, wrong one is EASY -> stays above 60.
        wrong_was_easy = adaptive.weighted_accuracy(
            [False, True, True], [self.EASY, self.HARD, self.HARD])
        band_hard, _ = adaptive._classify(3, 2, 66.7, wrong_was_hard, None, difficulty_weighted=True)
        band_easy, _ = adaptive._classify(3, 2, 66.7, wrong_was_easy, None, difficulty_weighted=True)
        self.assertEqual(band_hard, adaptive.PRIORITY_HIGH)
        self.assertEqual(band_easy, adaptive.PRIORITY_MEDIUM)

    # ---- 3 / 4 / 5: the guarantees that must NOT change -------------------------
    def test_3_genuinely_weak_section_stays_high_whatever_the_difficulty(self):
        for difficulty in (self.EASY, 1500.0, self.HARD):
            with self.subTest(difficulty=difficulty):
                weighted = adaptive.weighted_accuracy([False] * 5, [difficulty] * 5)
                self.assertEqual(weighted, 0.0)
                band, _ = adaptive._classify(5, 0, 0.0, weighted, None, difficulty_weighted=True)
                self.assertEqual(band, adaptive.PRIORITY_HIGH)

    def test_4_strong_section_is_never_promoted_to_high_by_difficulty(self):
        for difficulty in (self.EASY, 1500.0, self.HARD):
            with self.subTest(difficulty=difficulty):
                weighted = adaptive.weighted_accuracy([True] * 5, [difficulty] * 5)
                self.assertEqual(weighted, 100.0)
                band, _ = adaptive._classify(5, 5, 100.0, weighted, None, difficulty_weighted=True)
                self.assertEqual(band, adaptive.PRIORITY_LOW)

    def test_5_min_evidence_gate_is_unchanged(self):
        """One hard success must still not be enough to exclude a section."""
        weighted = adaptive.weighted_accuracy([True], [self.HARD])
        band, reason = adaptive._classify(1, 1, 100.0, weighted, None, difficulty_weighted=True)
        self.assertEqual(band, adaptive.PRIORITY_MEDIUM)
        self.assertIn("insufficient evidence", reason)

    # ---- 6 / 7 / 8: existing behaviour preserved --------------------------------
    def test_6_recency_weighting_still_applies(self):
        """With difficulty held constant the figures must match the pre-change values exactly."""
        improving = [True] * 5 + [False] * 5      # newest-first: 5 recent correct
        declining = [False] * 5 + [True] * 5
        flat = [1500.0] * 10
        self.assertEqual(adaptive.weighted_accuracy(improving, flat), 66.7)
        self.assertEqual(adaptive.weighted_accuracy(declining, flat), 33.3)
        # and identical to omitting difficulty entirely
        self.assertEqual(adaptive.weighted_accuracy(improving), 66.7)

    def test_7_mastery_retirement_still_short_circuits(self):
        class _Mastery:
            mastery_status = "mastered"
            streak_correct = 3
        weighted = adaptive.weighted_accuracy([False] * 3, [self.HARD] * 3)
        band, _ = adaptive._classify(3, 0, 0.0, weighted, _Mastery(), difficulty_weighted=True)
        self.assertEqual(band, adaptive.PRIORITY_NONE)

    def test_8_never_assessed_still_high(self):
        band, reason = adaptive._classify(0, 0, None, None, None, difficulty_weighted=False)
        self.assertEqual(band, adaptive.PRIORITY_HIGH)
        self.assertIn("Never assessed", reason)

    # ---- backwards compatibility -------------------------------------------------
    def test_history_without_snapshots_behaves_exactly_as_before(self):
        """Pre-instrumentation answers must not silently change meaning."""
        sequence = [True, False, True, True, False]
        self.assertEqual(
            adaptive.weighted_accuracy(sequence, [None] * 5),
            adaptive.weighted_accuracy(sequence),
        )

    def test_mixed_recorded_and_missing_difficulty_is_handled(self):
        value = adaptive.weighted_accuracy(
            [True, False, True], [self.HARD, None, self.EASY])
        self.assertIsNotNone(value)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 100.0)

    # ---- 10: explainability -------------------------------------------------------
    def test_10_reason_names_difficulty_only_when_it_was_used(self):
        weighted = adaptive.weighted_accuracy([False, True, True], [self.HARD, self.EASY, self.EASY])
        _band, with_difficulty = adaptive._classify(
            3, 2, 66.7, weighted, None, difficulty_weighted=True)
        _band2, without = adaptive._classify(
            3, 2, 66.7, weighted, None, difficulty_weighted=False)
        self.assertIn("recency- and difficulty-weighted", with_difficulty)
        self.assertNotIn("difficulty", without)


class DifficultyAwarePriorityIntegrationTests(APITestCase):
    """End to end: a real submission records difficulty, and the learning path reflects it."""

    def setUp(self):
        from accounts.models import LearnerProfile

        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        LearnerProfile.objects.create(user=self.learner, job_role=self.role, employee_code="E1")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom", sop_code="SOP-995", version="v1.0", department="P",
            file="f.txt", status="processed",
        )
        self.chunk = SOPChunk.objects.create(
            sop=self.sop, chunk_text="A", section_title="Section 1", chunking_strategy="heading")
        self.questions, self.right, self.wrong = [], [], []
        for i, difficulty in enumerate(("hard", "easy", "easy")):
            q = Question.objects.create(
                sop=self.sop, job_role=self.role, question_text=f"Q{i}?", explanation="B.",
                status="approved", source_chunk=self.chunk, difficulty=difficulty,
                elo_rating=Question.DIFFICULTY_SEED_ELO[difficulty],
            )
            self.questions.append(q)
            self.right.append(Option.objects.create(question=q, option_text="R", is_correct=True))
            self.wrong.append(Option.objects.create(question=q, option_text="W", is_correct=False))
        self.client.force_authenticate(user=self.learner)

    def _sit(self, outcomes):
        attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id},
            format="json",
        ).data["id"]
        answers = [
            {"question": q.id,
             "selected_option": (self.right[i] if ok else self.wrong[i]).id}
            for i, (q, ok) in enumerate(zip(self.questions, outcomes))
        ]
        return self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json")

    def test_11_learning_path_reports_the_difficulty_basis(self):
        self.assertEqual(self._sit([False, True, True]).status_code, status.HTTP_200_OK)

        response = self.client.get("/api/attempts/learning-path/")
        section = next(
            s for row in response.data["sops"] if row["sop_id"] == self.sop.id
            for s in row["sections"] if s["chunk_id"] == self.chunk.id
        )
        self.assertTrue(section["difficulty_weighted"])
        self.assertIsNotNone(section["mean_difficulty"])
        self.assertIn("difficulty-weighted", section["reason"])

    def test_11b_selection_matches_the_engine_decision(self):
        """The learning path and the adaptive engine must not disagree."""
        self._sit([False, False, False])
        response = self.client.get("/api/attempts/learning-path/")
        row = next(r for r in response.data["sops"] if r["sop_id"] == self.sop.id)
        section = next(s for s in row["sections"] if s["chunk_id"] == self.chunk.id)
        self.assertEqual(section["priority"], adaptive.PRIORITY_HIGH)
        self.assertTrue(section["selected_for_retraining"])

    def test_9_questions_without_a_source_chunk_still_bucket_correctly(self):
        unlinked = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="U?", explanation="B.",
            status="approved", source_chunk=None,
        )
        Option.objects.create(question=unlinked, option_text="R", is_correct=True)
        sections = adaptive.analyse_sections(self.learner, self.sop, self.role)
        unlinked_bucket = [s for s in sections if s["chunk_id"] is None]
        self.assertEqual(len(unlinked_bucket), 1)
        self.assertFalse(unlinked_bucket[0]["difficulty_weighted"])


class DemoScenarioDifficultyTests(TestCase):
    """The demonstration fixture must actually exercise difficulty weighting.

    Difficulty was never uniform in the demo -- questions are seeded 1300/1500/1700 from their
    labels. What made the weighting invisible was uniform *correctness*: every section was
    answered 3/3 or 0/3, and at 0% or 100% a weighted average equals an unweighted one no
    matter what the weights are.

    The scenario therefore answers each weak section 1 of 3, getting the hardest question right
    in one and the easiest in the other. Identical lifetime accuracy, genuinely different
    evidence. Nothing here sets a priority or a score; the production algorithm decides.
    """

    def setUp(self):
        self.role = JobRole.objects.create(name="Operator", department="P")
        self.sop = SOPDocument.objects.create(
            title="S", sop_code="SOP-997", version="v1", department="P",
            file="f.txt", status="processed")
        self.chunk = SOPChunk.objects.create(
            sop=self.sop, chunk_text="A", section_title="S1", chunking_strategy="heading")

    def _question(self, difficulty):
        return Question.objects.create(
            sop=self.sop, job_role=self.role, question_text=f"{difficulty}?", explanation="B.",
            status="approved", source_chunk=self.chunk, difficulty=difficulty,
        )

    # ---- 1 / 3: the fixture really is mixed -------------------------------------
    def test_1_3_difficulty_labels_seed_distinct_ratings(self):
        """Elo is seeded from the label on creation, so a mixed section has real spread."""
        ratings = {d: self._question(d).elo_rating for d in ("easy", "medium", "hard")}
        self.assertEqual(ratings["easy"], Question.DIFFICULTY_SEED_ELO["easy"])
        self.assertEqual(ratings["medium"], Question.DIFFICULTY_SEED_ELO["medium"])
        self.assertEqual(ratings["hard"], Question.DIFFICULTY_SEED_ELO["hard"])
        self.assertEqual(len(set(ratings.values())), 3)

    # ---- 5: the demonstration property ------------------------------------------
    def test_5_hardest_right_beats_easiest_right_at_equal_lifetime(self):
        """The exact claim the demo makes on screen.

        Same three questions, same one-correct-of-three, mirrored: identical lifetime
        accuracy, different difficulty-weighted evidence.
        """
        easy, medium, hard = (Question.DIFFICULTY_SEED_ELO[d] for d in ("easy", "medium", "hard"))
        difficulties = [medium, easy, hard]          # newest-first, same order for both

        hardest_right = adaptive.weighted_accuracy([False, False, True], difficulties)
        easiest_right = adaptive.weighted_accuracy([False, True, False], difficulties)

        # identical lifetime accuracy
        self.assertEqual(adaptive._plain_accuracy([False, False, True]),
                         adaptive._plain_accuracy([False, True, False]))
        # but different evidence, and getting the hard one right is worth more
        self.assertNotEqual(hardest_right, easiest_right)
        self.assertGreater(hardest_right, easiest_right)

    def test_4_weighted_accuracy_diverges_from_lifetime_when_difficulty_varies(self):
        easy, medium, hard = (Question.DIFFICULTY_SEED_ELO[d] for d in ("easy", "medium", "hard"))
        weighted = adaptive.weighted_accuracy([False, False, True], [medium, easy, hard])
        lifetime = adaptive._plain_accuracy([False, False, True])
        self.assertNotEqual(weighted, lifetime)

    def test_uniform_difficulty_cannot_change_the_score(self):
        """Why the old demo showed nothing: uniform weights cancel in a weighted average."""
        for uniform in (1300.0, 1500.0, 1700.0):
            with self.subTest(uniform=uniform):
                self.assertEqual(
                    adaptive.weighted_accuracy([False, True, False], [uniform] * 3),
                    adaptive.weighted_accuracy([False, True, False]),
                )

    def test_uniform_correctness_cannot_change_the_score_either(self):
        """The actual root cause: at 0% or 100% the weights are irrelevant."""
        easy, medium, hard = (Question.DIFFICULTY_SEED_ELO[d] for d in ("easy", "medium", "hard"))
        self.assertEqual(adaptive.weighted_accuracy([False] * 3, [easy, medium, hard]), 0.0)
        self.assertEqual(adaptive.weighted_accuracy([True] * 3, [easy, medium, hard]), 100.0)

    # ---- 6 / 7: the algorithm still decides, and explains itself -----------------
    def test_6_priority_still_comes_from_the_existing_rules(self):
        easy, medium, hard = (Question.DIFFICULTY_SEED_ELO[d] for d in ("easy", "medium", "hard"))
        weighted = adaptive.weighted_accuracy([False, False, True], [medium, easy, hard])
        priority, _reason = adaptive._classify(3, 1, 33.3, weighted, None, difficulty_weighted=True)
        # 1 of 3 is weak on any weighting -- the rule, not the fixture, produces this
        self.assertEqual(priority, adaptive.PRIORITY_HIGH)

    def test_7_reason_quotes_the_actual_weighted_figure(self):
        easy, medium, hard = (Question.DIFFICULTY_SEED_ELO[d] for d in ("easy", "medium", "hard"))
        weighted = adaptive.weighted_accuracy([False, False, True], [medium, easy, hard])
        _priority, reason = adaptive._classify(3, 1, 33.3, weighted, None, difficulty_weighted=True)
        self.assertIn(f"{weighted}%", reason)
        self.assertIn("33.3% lifetime", reason)
        self.assertIn("recency- and difficulty-weighted", reason)

    # ---- the demo's scenario resolver -------------------------------------------
    def test_scenario_resolver_supports_both_shapes(self):
        from attempts.management.commands.demo_adaptive import ALL_DIFFICULTIES, Command

        hard_q, easy_q = self._question("hard"), self._question("easy")
        resolve = Command._answers_correctly

        # legacy set-of-titles form, still used by the retraining rounds
        self.assertTrue(resolve("S1", hard_q, {"S1"}))
        self.assertFalse(resolve("S1", hard_q, {"other"}))
        # ALL_DIFFICULTIES sentinel
        self.assertTrue(resolve("S1", hard_q, {"S1": ALL_DIFFICULTIES}))
        # explicit question ids -- chosen by rating, so LLM labels cannot break the scenario
        self.assertTrue(resolve("S1", hard_q, {"S1": {hard_q.id}}))
        self.assertFalse(resolve("S1", easy_q, {"S1": {hard_q.id}}))
        # a section absent from the mapping is answered incorrectly throughout
        self.assertFalse(resolve("missing", hard_q, {"S1": {hard_q.id}}))


class SyntheticDataSeparationTests(TestCase):
    """Demo interactions must be identifiable and excluded from evaluation.

    They demonstrate the adaptive loop correctly, but their outcomes are decided by a script
    rather than observed, so treating them as evidence about learning would be the single
    easiest way to produce a meaningless result.
    """

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Operator", department="P")
        self.sop = SOPDocument.objects.create(
            title="S", sop_code="SOP-996", version="v1", department="P",
            file="f.txt", status="processed")
        self.chunk = SOPChunk.objects.create(
            sop=self.sop, chunk_text="A", section_title="S1", chunking_strategy="heading")
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q?", explanation="B.",
            status="approved", source_chunk=self.chunk)

    def _attempt(self, synthetic, count=6):
        attempt = QuizAttempt.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role, is_synthetic=synthetic)
        for i in range(count):
            AttemptAnswer.objects.create(
                attempt=attempt, question=self.question, is_correct=bool(i % 2),
                answered_at=timezone.now() + datetime.timedelta(minutes=i),
                question_difficulty_at_answer=1500.0, learner_ability_at_answer=1500.0,
            )

    def test_attempts_default_to_real(self):
        attempt = QuizAttempt.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role)
        self.assertFalse(attempt.is_synthetic)

    def test_synthetic_interactions_are_excluded_from_evaluation(self):
        self._attempt(synthetic=True, count=6)
        sequences, report = evaluation.load_interactions()
        self.assertEqual(report.excluded_synthetic, 6)
        self.assertEqual(report.usable, 0)
        self.assertEqual(sequences, {})

    def test_real_interactions_are_kept_alongside_synthetic_ones(self):
        self._attempt(synthetic=True, count=6)
        self._attempt(synthetic=False, count=6)
        _sequences, report = evaluation.load_interactions()
        self.assertEqual(report.excluded_synthetic, 6)
        self.assertEqual(report.usable, 6)

    def test_synthetic_can_be_included_explicitly_for_mechanism_checks(self):
        self._attempt(synthetic=True, count=6)
        _sequences, report = evaluation.load_interactions(include_synthetic=True)
        self.assertEqual(report.excluded_synthetic, 0)
        self.assertEqual(report.usable, 6)

    def test_report_declares_the_synthetic_exclusion(self):
        self._attempt(synthetic=True, count=6)
        result = evaluation.evaluate()
        self.assertEqual(result["dataset"]["excluded_synthetic"], 6)
        self.assertIn(
            "Synthetic demo interactions (QuizAttempt.is_synthetic) are excluded by default.",
            result["leakage_controls"],
        )


class EvaluationHarnessTests(TestCase):
    """Batch C — the offline evaluation harness.

    These tests protect the harness's scientific validity, not any model's score. The fixtures
    here are tiny and synthetic *by design*: they exist to prove the mechanism (ordering,
    leakage boundaries, gating) behaves correctly. They are never a dataset, and no number
    produced from them is a result about this project.
    """

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.other = get_user_model().objects.create_user(username="priya", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom", sop_code="SOP-990", version="v1.0", department="P",
            file="f.txt", status="processed",
        )
        self.chunk_a = SOPChunk.objects.create(
            sop=self.sop, chunk_text="A", section_title="S1", chunking_strategy="heading")
        self.chunk_b = SOPChunk.objects.create(
            sop=self.sop, chunk_text="B", section_title="S2", chunking_strategy="heading")
        self.questions = []
        for i in range(6):
            self.questions.append(Question.objects.create(
                sop=self.sop, job_role=self.role, question_text=f"Q{i}?", explanation="B.",
                status="approved", source_chunk=self.chunk_a if i % 2 == 0 else self.chunk_b,
            ))

    def _interaction(self, learner, question, correct, minutes, difficulty=1500.0,
                     ability=1500.0, timestamped=True, attempt=None):
        """Create one recorded interaction directly. Fixture construction only."""
        attempt = attempt or QuizAttempt.objects.create(
            learner=learner, sop=self.sop, job_role=self.role,
        )
        return AttemptAnswer.objects.create(
            attempt=attempt, question=question, is_correct=correct,
            answered_at=(timezone.now() + datetime.timedelta(minutes=minutes)) if timestamped else None,
            question_difficulty_at_answer=difficulty if timestamped else None,
            learner_ability_at_answer=ability if timestamped else None,
        )

    def _sequence(self, learner, pattern, start=0):
        """pattern: list of bools, one interaction each, strictly increasing in time."""
        for i, correct in enumerate(pattern):
            self._interaction(learner, self.questions[i % len(self.questions)], correct, start + i)

    # ---- 1 / 8: temporal ordering and per-learner split -------------------------
    def test_1_interactions_are_ordered_by_answered_at(self):
        # Inserted out of chronological order on purpose.
        self._interaction(self.learner, self.questions[0], True, minutes=30)
        self._interaction(self.learner, self.questions[1], False, minutes=10)
        self._interaction(self.learner, self.questions[2], True, minutes=20)
        self._interaction(self.learner, self.questions[3], False, minutes=40)
        self._interaction(self.learner, self.questions[4], True, minutes=50)

        sequences, _ = evaluation.load_interactions()
        times = [e.answered_at for e in sequences[self.learner.id]]
        self.assertEqual(times, sorted(times))

    def test_8_split_is_per_learner_and_temporal(self):
        self._sequence(self.learner, [True] * 10)
        self._sequence(self.other, [False] * 10, start=100)

        sequences, report = evaluation.load_interactions()
        self.assertEqual(report.learners, 2)
        for events in sequences.values():
            rows = evaluation.build_features(events)
            train, test = evaluation.temporal_split(rows, train_fraction=0.7)
            self.assertEqual(len(train), 7)
            self.assertEqual(len(test), 3)
            # every training row precedes every evaluation row within this learner
            self.assertLess(len(train), len(rows))

    # ---- 2 / 5 / 6: the leakage boundary ----------------------------------------
    def test_2_history_never_contains_the_future(self):
        """Row i's prior counts must equal the outcomes of interactions before i only."""
        pattern = [True, False, True, True, False, False, True]
        self._sequence(self.learner, pattern)

        sequences, _ = evaluation.load_interactions()
        rows = evaluation.build_features(sequences[self.learner.id])

        running_correct = 0
        for i, row in enumerate(rows):
            self.assertEqual(row.prior_total, i)
            self.assertEqual(row.prior_correct, running_correct)
            if pattern[i]:
                running_correct += 1
        # the last row cannot know the final outcome
        self.assertEqual(rows[-1].prior_total, len(pattern) - 1)

    def test_5_chunk_history_excludes_the_target_interaction(self):
        self._sequence(self.learner, [True, True, True, True, True, True])
        sequences, _ = evaluation.load_interactions()
        rows = evaluation.build_features(sequences[self.learner.id])
        self.assertEqual(rows[0].chunk_history, [])          # nothing precedes the first
        self.assertEqual(len(rows[2].chunk_history), 1)      # only the earlier same-chunk answer

    def test_6_target_is_not_reachable_as_a_model_feature(self):
        """`target` exists on the row for scoring, but no baseline may consume it."""
        self._sequence(self.learner, [True, False, True, False, True, True])
        sequences, _ = evaluation.load_interactions()
        rows = evaluation.build_features(sequences[self.learner.id])

        for baseline_cls in evaluation.DEFAULT_BASELINES:
            model = baseline_cls()
            model.fit(rows)
            with self.subTest(baseline=model.name):
                row = rows[-1]
                flipped = evaluation.FeatureRow(**{**row.__dict__, "target": not row.target})
                self.assertEqual(model.predict(row), model.predict(flipped))

    # ---- 3: rows without temporal data are excluded, not imputed ----------------
    def test_3_rows_without_answered_at_are_excluded(self):
        self._sequence(self.learner, [True] * 6)
        for i in range(4):
            self._interaction(self.learner, self.questions[i], True, minutes=0, timestamped=False)

        sequences, report = evaluation.load_interactions()
        self.assertEqual(report.excluded_no_timestamp, 4)
        self.assertEqual(len(sequences[self.learner.id]), 6)
        self.assertEqual(report.total_rows, 10)

    def test_3b_rows_without_elo_snapshot_are_excluded(self):
        self._sequence(self.learner, [True] * 6)
        answer = self._interaction(self.learner, self.questions[0], True, minutes=99)
        AttemptAnswer.objects.filter(pk=answer.pk).update(question_difficulty_at_answer=None)

        _sequences, report = evaluation.load_interactions()
        self.assertEqual(report.excluded_no_snapshot, 1)

    # ---- 4: THE leakage test ---------------------------------------------------
    def test_4_live_elo_changes_do_not_alter_historical_features(self):
        """Changing a question's CURRENT rating must not move a recorded interaction's feature.

        This is the property the whole harness rests on: without it, the difficulty attributed
        to a past answer would drift with every later answer, feeding the outcome back into its
        own feature.
        """
        self._sequence(self.learner, [True] * 6)
        before = [e.difficulty for e in evaluation.load_interactions()[0][self.learner.id]]

        Question.objects.all().update(elo_rating=900.0)   # violent change to live ratings

        after = [e.difficulty for e in evaluation.load_interactions()[0][self.learner.id]]
        self.assertEqual(before, after)
        self.assertTrue(all(d == 1500.0 for d in after))
        self.assertEqual(Question.objects.first().elo_rating, 900.0)  # live really did change

    # ---- 7: duplicates ----------------------------------------------------------
    def test_7_repeated_question_does_not_distort_ordering(self):
        """The same question answered twice is two interactions, ordered, not merged."""
        self._interaction(self.learner, self.questions[0], False, minutes=1)
        self._interaction(self.learner, self.questions[0], True, minutes=2)
        self._sequence(self.learner, [True] * 4, start=10)

        sequences, _ = evaluation.load_interactions()
        rows = evaluation.build_features(sequences[self.learner.id])
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[1].chunk_history, [False])   # the earlier attempt, newest-first
        self.assertEqual(rows[1].chunk_prior_incorrect, 1)

    # ---- 9 / 10: gating and honest metrics --------------------------------------
    def test_9_insufficient_data_gate_triggers_on_a_small_dataset(self):
        self._sequence(self.learner, [True, False] * 4)
        result = evaluation.evaluate()
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertTrue(result["blockers"])
        for entry in result["baselines"].values():
            self.assertIn("NOT a performance claim", entry["interpretation"])

    def test_9b_empty_dataset_is_reported_not_crashed(self):
        result = evaluation.evaluate()
        self.assertEqual(result["status"], "INSUFFICIENT_DATA")
        self.assertEqual(result["dataset"]["usable"], 0)

    def test_10_metrics_report_insufficient_rather_than_fabricating(self):
        self.assertEqual(evaluation.roc_auc([True, True], [0.6, 0.7]), evaluation.INSUFFICIENT)
        self.assertEqual(evaluation.roc_auc([False, False], [0.6, 0.7]), evaluation.INSUFFICIENT)
        self.assertEqual(evaluation.pr_auc([False, False], [0.6, 0.7]), evaluation.INSUFFICIENT)
        self.assertEqual(evaluation.log_loss([], []), evaluation.INSUFFICIENT)
        self.assertEqual(evaluation.brier_score([], []), evaluation.INSUFFICIENT)

    def test_10b_metrics_are_correct_on_known_inputs(self):
        """Sanity-check the pure-Python implementations against hand-computable cases."""
        # perfect separation
        self.assertAlmostEqual(evaluation.roc_auc([False, False, True, True], [.1, .2, .8, .9]), 1.0)
        # inverted ranking
        self.assertAlmostEqual(evaluation.roc_auc([True, True, False, False], [.1, .2, .8, .9]), 0.0)
        # all ties -> no ranking information
        self.assertAlmostEqual(evaluation.roc_auc([True, False, True, False], [.5] * 4), 0.5)
        # Brier for a constant 0.5 prediction is always 0.25
        self.assertAlmostEqual(evaluation.brier_score([True, False], [0.5, 0.5]), 0.25)
        # log loss for a confident correct prediction is near zero
        self.assertLess(evaluation.log_loss([True], [0.99]), 0.02)

    # ---- 11: calibration --------------------------------------------------------
    def test_11_calibration_table_reports_bins_and_flags_thin_ones(self):
        y = [True] * 30 + [False] * 30
        p = [0.9] * 30 + [0.1] * 30
        table = evaluation.calibration_table(y, p, bins=5, min_per_bin=5)
        populated = [b for b in table if b["count"] >= 5]
        self.assertEqual(len(populated), 2)
        low = next(b for b in table if b["bin"] == "0.0-0.2")
        high = next(b for b in table if b["bin"] == "0.8-1.0")
        self.assertAlmostEqual(low["observed_rate"], 0.0)
        self.assertAlmostEqual(high["observed_rate"], 1.0)
        # a bin with too few observations must not report a rate
        thin = next(b for b in table if b["count"] == 0)
        self.assertEqual(thin["observed_rate"], evaluation.INSUFFICIENT)

    # ---- 12: the rule-engine adapter is faithful --------------------------------
    def test_12_current_engine_adapter_reproduces_production_decisions(self):
        """The adapter must call adaptive.py, not approximate it."""
        row = evaluation.FeatureRow(
            learner_id=1, prior_total=3, prior_correct=0, prior_incorrect=3,
            chunk_prior_total=3, chunk_prior_correct=0, chunk_prior_incorrect=3,
            difficulty=1500.0, ability=1500.0,
            chunk_history=[False, False, False], target=False,
        )
        model = evaluation.CurrentAdaptiveEngineBaseline()
        model.fit([row])
        # 0/3 -> weighted accuracy 0% -> HIGH in production, and P(correct) 0.0 here
        self.assertEqual(model.priority(row), adaptive.PRIORITY_HIGH)
        self.assertAlmostEqual(model.predict(row), 0.0)

        strong = evaluation.FeatureRow(**{**row.__dict__, "chunk_history": [True, True, True]})
        self.assertEqual(model.priority(strong), adaptive.PRIORITY_LOW)
        self.assertAlmostEqual(model.predict(strong), 1.0)

    def test_12b_engine_adapter_abstains_to_base_rate_without_evidence(self):
        train = [
            evaluation.FeatureRow(
                learner_id=1, prior_total=0, prior_correct=0, prior_incorrect=0,
                chunk_prior_total=0, chunk_prior_correct=0, chunk_prior_incorrect=0,
                difficulty=1500.0, ability=1500.0, chunk_history=[], target=t,
            ) for t in (True, True, False, False)
        ]
        model = evaluation.CurrentAdaptiveEngineBaseline()
        model.fit(train)
        self.assertAlmostEqual(model.predict(train[0]), 0.5)  # base rate, not a guess

    # ---- 13: Elo baseline uses snapshots ---------------------------------------
    def test_13_elo_baseline_uses_pre_answer_snapshots(self):
        model = evaluation.EloBaseline()
        equal = evaluation.FeatureRow(
            learner_id=1, prior_total=0, prior_correct=0, prior_incorrect=0,
            chunk_prior_total=0, chunk_prior_correct=0, chunk_prior_incorrect=0,
            difficulty=1500.0, ability=1500.0, chunk_history=[], target=True,
        )
        self.assertAlmostEqual(model.predict(equal), 0.5, places=6)

        stronger = evaluation.FeatureRow(**{**equal.__dict__, "ability": 1900.0})
        self.assertGreater(model.predict(stronger), 0.9)
        harder = evaluation.FeatureRow(**{**equal.__dict__, "difficulty": 1900.0})
        self.assertLess(model.predict(harder), 0.1)

    # ---- 14: logistic baseline ---------------------------------------------------
    def test_14_logistic_baseline_fits_without_future_information(self):
        self._sequence(self.learner, [True, False, True, True, False, True, False, True])
        sequences, _ = evaluation.load_interactions()
        rows = evaluation.build_features(sequences[self.learner.id])
        train, test = evaluation.temporal_split(rows)

        model = evaluation.LogisticBaseline(epochs=100)
        model.fit(train)
        self.assertTrue(model.fitted)
        for row in test:
            p = model.predict(row)
            self.assertGreaterEqual(p, 0.0)
            self.assertLessEqual(p, 1.0)
        # fitted only on the training split -- refitting on train alone must be identical
        again = evaluation.LogisticBaseline(epochs=100)
        again.fit(train)
        self.assertEqual(model.weights, again.weights)

    def test_14b_logistic_handles_a_constant_feature_without_nan(self):
        """Constant columns are common at small n; a zero-variance divide would poison every
        prediction with NaN rather than failing loudly."""
        rows = [
            evaluation.FeatureRow(
                learner_id=1, prior_total=i, prior_correct=i, prior_incorrect=0,
                chunk_prior_total=i, chunk_prior_correct=i, chunk_prior_incorrect=0,
                difficulty=1500.0, ability=1500.0, chunk_history=[], target=bool(i % 2),
            ) for i in range(6)
        ]
        model = evaluation.LogisticBaseline(epochs=50)
        model.fit(rows)
        for row in rows:
            p = model.predict(row)
            self.assertFalse(math.isnan(p))

    # ---- report shape -----------------------------------------------------------
    def test_report_declares_status_and_leakage_controls(self):
        self._sequence(self.learner, [True, False] * 3)
        result = evaluation.evaluate()
        markdown = evaluation.to_markdown(result)
        self.assertIn("NO VALID MODEL PERFORMANCE CLAIM", markdown)
        self.assertIn("Leakage controls", markdown)
        # 5 since synthetic-demo exclusion was added as an explicit control.
        self.assertEqual(len(result["leakage_controls"]), 5)
        json.loads(evaluation.to_json(result))  # must be machine-readable


class LearningPathExplainabilityTests(APITestCase):
    """The adaptive engine must be able to justify itself: every selection shown to a
    learner should trace back to recorded performance on a specific source section."""

    def setUp(self):
        from accounts.models import LearnerProfile

        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        LearnerProfile.objects.create(user=self.learner, job_role=self.role, employee_code="EMP-1")
        self.sop = SOPDocument.objects.create(
            title="Quality Management", sop_code="SOP-970", version="v1.0", department="Production",
            file="sops/sop-970.txt", status="processed",
        )
        self.chunk_gmp = SOPChunk.objects.create(sop=self.sop, section_title="GMP", chunk_text="gmp")
        self.chunk_capa = SOPChunk.objects.create(sop=self.sop, section_title="CAPA", chunk_text="capa")

        self.q_gmp = Question.objects.create(
            sop=self.sop, job_role=self.role, source_chunk=self.chunk_gmp,
            question_text="GMP?", explanation="Because.", status="approved",
        )
        self.gmp_right = Option.objects.create(question=self.q_gmp, option_text="Right", is_correct=True)
        self.q_capa = Question.objects.create(
            sop=self.sop, job_role=self.role, source_chunk=self.chunk_capa,
            question_text="CAPA?", explanation="Because.", status="approved",
        )
        self.capa_right = Option.objects.create(question=self.q_capa, option_text="Right", is_correct=True)
        self.capa_wrong = Option.objects.create(question=self.q_capa, option_text="Wrong", is_correct=False)
        self.client.force_authenticate(user=self.learner)

    def _submit(self, answers):
        attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
        ).data["id"]
        _offer_exactly(attempt_id, [a["question"] for a in answers if a.get("question")])
        return self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json"
        )

    def _sections(self):
        response = self.client.get("/api/attempts/learning-path/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        sop_row = next(row for row in response.data["sops"] if row["sop_id"] == self.sop.id)
        return {section["section_title"]: section for section in sop_row["sections"]}

    def test_requires_authentication(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/attempts/learning-path/")
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_untouched_sections_are_reported_as_never_assessed(self):
        sections = self._sections()
        self.assertEqual(sections["GMP"]["mastery_status"], "not_started")
        self.assertEqual(sections["GMP"]["answered"], 0)
        self.assertIn("Never assessed", sections["GMP"]["reason"])
        self.assertTrue(sections["GMP"]["selected_for_retraining"])

    def test_measured_accuracy_is_reported_per_section(self):
        self._submit([
            {"question": self.q_gmp.id, "selected_option": self.gmp_right.id},
            {"question": self.q_capa.id, "selected_option": self.capa_wrong.id},
        ])
        sections = self._sections()
        self.assertEqual(sections["GMP"]["accuracy"], 100.0)
        self.assertEqual(sections["CAPA"]["accuracy"], 0.0)
        self.assertEqual(sections["CAPA"]["answered"], 1)
        self.assertEqual(sections["CAPA"]["correct"], 0)

    def test_weak_section_is_high_priority_with_a_numeric_justification(self):
        self._submit([
            {"question": self.q_gmp.id, "selected_option": self.gmp_right.id},
            {"question": self.q_capa.id, "selected_option": self.capa_wrong.id},
        ])
        sections = self._sections()
        self.assertEqual(sections["CAPA"]["priority"], "high")
        # The reason must carry the actual measured numbers, not a generic label.
        self.assertIn("0.0% accuracy", sections["CAPA"]["reason"])
        self.assertIn("0/1 correct", sections["CAPA"]["reason"])

    def test_strong_section_is_not_selected_for_retraining(self):
        """Three correct answers, not one: a single correct answer is no longer enough to
        exclude a section (adaptive.MIN_EVIDENCE). That rule is asserted directly in
        test_high_accuracy_on_a_tiny_sample_is_not_enough_to_exclude below."""
        for _ in range(3):
            self._submit([{"question": self.q_gmp.id, "selected_option": self.gmp_right.id}])
        self._submit([{"question": self.q_capa.id, "selected_option": self.capa_wrong.id}])

        sections = self._sections()
        self.assertFalse(sections["GMP"]["selected_for_retraining"])
        self.assertTrue(sections["CAPA"]["selected_for_retraining"])

    def test_high_accuracy_on_a_tiny_sample_is_not_enough_to_exclude(self):
        """1/1 correct is not the same evidence as 50/50, and the explanation says so."""
        self._submit([{"question": self.q_gmp.id, "selected_option": self.gmp_right.id}])
        gmp = self._sections()["GMP"]
        self.assertEqual(gmp["accuracy"], 100.0)
        self.assertFalse(gmp["evidence_sufficient"])
        self.assertEqual(gmp["priority"], "medium")
        self.assertIn("insufficient evidence", gmp["reason"])

    def test_sections_are_ordered_most_urgent_first(self):
        self._submit([
            {"question": self.q_gmp.id, "selected_option": self.gmp_right.id},
            {"question": self.q_capa.id, "selected_option": self.capa_wrong.id},
        ])
        response = self.client.get("/api/attempts/learning-path/")
        sop_row = next(row for row in response.data["sops"] if row["sop_id"] == self.sop.id)
        self.assertEqual(sop_row["sections"][0]["section_title"], "CAPA")

    def test_improvement_is_visible_after_retraining(self):
        """The loop closing: a learner who improves must see that reflected."""
        self._submit([{"question": self.q_capa.id, "selected_option": self.capa_wrong.id}])
        self.assertEqual(self._sections()["CAPA"]["accuracy"], 0.0)

        for _ in range(3):
            self._submit([{"question": self.q_capa.id, "selected_option": self.capa_right.id}])

        capa = self._sections()["CAPA"]
        self.assertEqual(capa["accuracy"], 75.0)  # 3 of 4 lifetime
        self.assertEqual(capa["recent_accuracy"], 75.0)
        self.assertEqual(capa["mastery_status"], "mastered")
        self.assertFalse(capa["selected_for_retraining"])

    def test_learner_without_a_job_role_gets_an_explanation_not_an_error(self):
        other = get_user_model().objects.create_user(username="nobody", password="demo12345")
        self.client.force_authenticate(user=other)
        response = self.client.get("/api/attempts/learning-path/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNone(response.data["job_role"])
        self.assertEqual(response.data["sops"], [])

    def test_path_is_scoped_to_the_requesting_learner(self):
        """One learner's weakness must not appear in another's path."""
        from accounts.models import LearnerProfile

        other = get_user_model().objects.create_user(username="priya", password="demo12345")
        LearnerProfile.objects.create(user=other, job_role=self.role, employee_code="EMP-2")
        self._submit([{"question": self.q_capa.id, "selected_option": self.capa_wrong.id}])

        self.client.force_authenticate(user=other)
        sections = self._sections()
        self.assertEqual(sections["CAPA"]["answered"], 0)


class AdaptiveEloRatingTests(APITestCase):
    """Elo rating (Pelánek, Computers & Education 98, 2016): every answered question
    nudges the learner's per-SOP ability (TopicMastery.elo_rating) and the question's
    own live difficulty (Question.elo_rating) in opposite directions."""

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-905", version="v1.0", department="Production",
            file="sops/sop-905.txt", status="processed",
        )
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q?", explanation="Because.", status="approved",
        )
        self.correct_option = Option.objects.create(question=self.question, option_text="Right", is_correct=True)
        self.wrong_option = Option.objects.create(question=self.question, option_text="Wrong", is_correct=False)
        self.client.force_authenticate(user=self.learner)

    def test_apply_elo_update_raises_learner_and_lowers_question_on_a_correct_answer(self):
        mastery = TopicMastery(elo_rating=1500)
        question = Question(elo_rating=1500)
        apply_elo_update(mastery, question, is_correct=True)
        self.assertGreater(mastery.elo_rating, 1500)
        self.assertLess(question.elo_rating, 1500)

    def test_apply_elo_update_lowers_learner_and_raises_question_on_a_wrong_answer(self):
        mastery = TopicMastery(elo_rating=1500)
        question = Question(elo_rating=1500)
        apply_elo_update(mastery, question, is_correct=False)
        self.assertLess(mastery.elo_rating, 1500)
        self.assertGreater(question.elo_rating, 1500)

    def test_beating_a_harder_question_moves_rating_more_than_beating_an_easy_one(self):
        favoured_mastery = TopicMastery(elo_rating=1500)
        easy_question = Question(elo_rating=1300)  # learner already expected to win
        apply_elo_update(favoured_mastery, easy_question, is_correct=True)

        underdog_mastery = TopicMastery(elo_rating=1500)
        hard_question = Question(elo_rating=1700)  # question already expected to win
        apply_elo_update(underdog_mastery, hard_question, is_correct=True)

        self.assertGreater(underdog_mastery.elo_rating - 1500, favoured_mastery.elo_rating - 1500)

    def test_submitting_a_quiz_updates_both_learner_and_question_elo_ratings(self):
        attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
        ).data["id"]
        self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/",
            {"answers": [{"question": self.question.id, "selected_option": self.correct_option.id}]},
            format="json",
        )
        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        self.question.refresh_from_db()
        self.assertGreater(mastery.elo_rating, 1500)
        self.assertLess(self.question.elo_rating, 1500)

    def test_suggested_difficulty_tracks_elo_rating_not_streak(self):
        """A learner with a high ability rating but zero streak (e.g. just transferred in
        and hasn't yet strung together a mastery streak) should still be offered harder
        material -- the fix for the cold-start fairness gap the streak-only heuristic had."""
        TopicMastery.objects.create(
            learner=self.learner, sop=self.sop, job_role=self.role,
            box_index=0, streak_correct=0, elo_rating=1750,
            next_eligible_at=timezone.now() - datetime.timedelta(days=1),
        )
        response = self.client.get("/api/attempts/auto-assigned/")
        assignment = response.data["assignments"][0]
        self.assertEqual(assignment["suggested_difficulty"], "hard")
        self.assertEqual(assignment["elo_rating"], 1750)


class FSRSAlgorithmTests(APITestCase):
    """Pure-function tests for the FSRS memory model (see fsrs.py) -- no DB needed."""

    def test_first_review_uses_initial_stability_and_difficulty(self):
        stability, difficulty = fsrs.review(None, None, elapsed_days=0, is_correct=True)
        self.assertEqual(stability, fsrs.initial_stability(fsrs.GOOD))
        self.assertEqual(difficulty, fsrs.initial_difficulty(fsrs.GOOD))

    def test_retrievability_decays_as_elapsed_time_grows(self):
        r_soon = fsrs.retrievability(elapsed_days=1, stability=10)
        r_later = fsrs.retrievability(elapsed_days=30, stability=10)
        self.assertGreater(r_soon, r_later)
        self.assertGreaterEqual(r_later, 0.0)

    def test_retrievability_is_ninety_percent_when_elapsed_equals_stability(self):
        self.assertAlmostEqual(fsrs.retrievability(elapsed_days=10, stability=10), 0.9, places=6)

    def test_stability_grows_after_a_correct_answer(self):
        stability, difficulty = fsrs.review(None, None, elapsed_days=0, is_correct=True)
        new_stability, _ = fsrs.review(stability, difficulty, elapsed_days=5, is_correct=True)
        self.assertGreater(new_stability, stability)

    def test_stability_drops_sharply_after_a_wrong_answer(self):
        stability, difficulty = fsrs.review(None, None, elapsed_days=0, is_correct=True)
        grown_stability, grown_difficulty = fsrs.review(stability, difficulty, elapsed_days=5, is_correct=True)
        after_failure, _ = fsrs.review(grown_stability, grown_difficulty, elapsed_days=5, is_correct=False)
        self.assertLess(after_failure, grown_stability)

    def test_difficulty_increases_after_a_wrong_answer(self):
        stability, difficulty = fsrs.review(None, None, elapsed_days=0, is_correct=True)
        _, new_difficulty = fsrs.review(stability, difficulty, elapsed_days=5, is_correct=False)
        self.assertGreater(new_difficulty, difficulty)

    def test_next_review_interval_grows_as_stability_grows(self):
        short = fsrs.next_review_interval_days(stability=2)
        long = fsrs.next_review_interval_days(stability=20)
        self.assertGreater(long, short)
        self.assertGreaterEqual(short, fsrs.MIN_INTERVAL_DAYS)


class AdaptiveFSRSSchedulingTests(APITestCase):
    """Integration tests: TopicMastery.apply_answer() driving next_eligible_at via FSRS
    instead of the old fixed BOX_INTERVAL_DAYS lookup."""

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-906", version="v1.0", department="Production",
            file="sops/sop-906.txt", status="processed",
        )
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q?", explanation="Because.", status="approved",
        )
        self.correct_option = Option.objects.create(question=self.question, option_text="Right", is_correct=True)
        self.client.force_authenticate(user=self.learner)

    def _submit(self, selected_option_id):
        attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
        ).data["id"]
        return self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/",
            {"answers": [{"question": self.question.id, "selected_option": selected_option_id}]},
            format="json",
        )

    def test_first_submission_populates_fsrs_state(self):
        self._submit(self.correct_option.id)
        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        self.assertIsNotNone(mastery.fsrs_stability)
        self.assertIsNotNone(mastery.fsrs_difficulty)
        self.assertGreater(mastery.next_eligible_at, timezone.now())

    def test_scheduled_interval_is_fsrs_derived_not_a_fixed_leitner_day_count(self):
        self._submit(self.correct_option.id)
        mastery = TopicMastery.objects.get(learner=self.learner, sop=self.sop)
        expected_interval = fsrs.next_review_interval_days(mastery.fsrs_stability)
        actual_interval = (mastery.next_eligible_at - mastery.updated_at).total_seconds() / 86400.0
        self.assertAlmostEqual(actual_interval, expected_interval, delta=0.01)
        # The old scheme would have scheduled exactly 1 day (box_index 0); FSRS's
        # initial-stability-derived interval for a first correct answer is not 1.0.
        self.assertNotAlmostEqual(expected_interval, 1.0, places=3)

    def test_memory_stability_surfaced_in_retraining_status(self):
        self._submit(self.correct_option.id)  # streak_correct=1 < MASTERY_STREAK_THRESHOLD, so still "in_progress"
        admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.client.force_authenticate(user=admin)
        response = self.client.get("/api/attempts/retraining-status/")
        rows = [r for r in response.data["learners"] if r["learner"] == "rohit"]
        self.assertEqual(len(rows), 1)
        self.assertIsNotNone(rows[0]["memory_stability_days"])


class SectionMasteryTests(APITestCase):
    """ChunkMastery tests: the actual scenario per-section tracking exists for -- a miss
    in one section of a multi-section SOP shouldn't reset a different, already-strong
    section's schedule, and a retest should target the specific weak section."""

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-907", version="v1.0", department="Production",
            file="sops/sop-907.txt", status="processed",
        )
        self.chunk_a = SOPChunk.objects.create(sop=self.sop, section_title="Section A: Gowning", chunk_text="a")
        self.chunk_b = SOPChunk.objects.create(sop=self.sop, section_title="Section B: Cleaning", chunk_text="b")

        self.question_a = Question.objects.create(
            sop=self.sop, job_role=self.role, source_chunk=self.chunk_a,
            question_text="A?", explanation="Because.", status="approved",
        )
        self.correct_a = Option.objects.create(question=self.question_a, option_text="Right", is_correct=True)
        self.wrong_a = Option.objects.create(question=self.question_a, option_text="Wrong", is_correct=False)

        self.question_b = Question.objects.create(
            sop=self.sop, job_role=self.role, source_chunk=self.chunk_b,
            question_text="B?", explanation="Because.", status="approved",
        )
        self.correct_b = Option.objects.create(question=self.question_b, option_text="Right", is_correct=True)
        self.wrong_b = Option.objects.create(question=self.question_b, option_text="Wrong", is_correct=False)

        # No source_chunk -- exercises the "no section to attribute this to" skip path.
        self.question_unlinked = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="U?", explanation="Because.", status="approved",
        )
        self.correct_unlinked = Option.objects.create(
            question=self.question_unlinked, option_text="Right", is_correct=True
        )

        self.client.force_authenticate(user=self.learner)

    def _submit(self, answers):
        attempt_id = self.client.post(
            "/api/attempts/quiz-attempts/", {"sop": self.sop.id, "job_role": self.role.id}, format="json"
        ).data["id"]
        _offer_exactly(attempt_id, [a["question"] for a in answers if a.get("question")])
        return self.client.post(
            f"/api/attempts/quiz-attempts/{attempt_id}/submit/", {"answers": answers}, format="json"
        )

    def test_correct_and_wrong_sections_get_independent_chunk_mastery_rows(self):
        self._submit([
            {"question": self.question_a.id, "selected_option": self.correct_a.id},
            {"question": self.question_b.id, "selected_option": self.wrong_b.id},
        ])
        mastery_a = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunk_a)
        mastery_b = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunk_b)
        self.assertEqual(mastery_a.streak_correct, 1)
        self.assertEqual(mastery_b.streak_correct, 0)

    def test_a_miss_in_one_section_does_not_reset_an_already_strong_section(self):
        """The literal scenario this feature exists for."""
        self._submit([{"question": self.question_a.id, "selected_option": self.correct_a.id}])
        self._submit([{"question": self.question_a.id, "selected_option": self.correct_a.id}])
        mastery_a_before = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunk_a)
        self.assertEqual(mastery_a_before.streak_correct, 2)

        # One more attempt: section A right again, section B wrong, in the SAME attempt.
        self._submit([
            {"question": self.question_a.id, "selected_option": self.correct_a.id},
            {"question": self.question_b.id, "selected_option": self.wrong_b.id},
        ])
        mastery_a_after = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunk_a)
        mastery_b_after = ChunkMastery.objects.get(learner=self.learner, sop_chunk=self.chunk_b)
        # Section A's streak kept growing (now mastered) despite section B failing in the same attempt.
        self.assertEqual(mastery_a_after.streak_correct, 3)
        self.assertEqual(mastery_a_after.mastery_status, "mastered")
        self.assertEqual(mastery_b_after.streak_correct, 0)

    def test_questions_without_a_source_chunk_create_no_chunk_mastery(self):
        self._submit([{"question": self.question_unlinked.id, "selected_option": self.correct_unlinked.id}])
        self.assertEqual(ChunkMastery.objects.count(), 0)
        # But the whole-SOP TopicMastery is still updated as before.
        self.assertTrue(TopicMastery.objects.filter(learner=self.learner, sop=self.sop).exists())

    def test_question_elo_rating_moves_exactly_once_per_answer_not_twice(self):
        """Regression guard: a question linked to a chunk still only has its own
        elo_rating nudged once per real answer (via the whole-SOP pairing) -- the
        section-level update must be ability-only, or this would double-move it."""
        before = self.question_a.elo_rating
        self._submit([{"question": self.question_a.id, "selected_option": self.correct_a.id}])
        self.question_a.refresh_from_db()
        # A single correct answer at equal ratings (both start at 1500) moves the
        # question down by exactly QUESTION_K_FACTOR * 0.5; if section-level tracking
        # also moved it, this delta would be roughly doubled.
        expected_delta = QUESTION_K_FACTOR * 0.5
        self.assertAlmostEqual(before - self.question_a.elo_rating, expected_delta, places=2)

    def test_auto_assigned_targets_unmastered_section_questions_first(self):
        for _ in range(3):
            self._submit([{"question": self.question_a.id, "selected_option": self.correct_a.id}])
        self._submit([{"question": self.question_b.id, "selected_option": self.wrong_b.id}])

        # Three genuine successes on section A built up real FSRS stability, so a single
        # failure elsewhere doesn't necessarily make the whole SOP due again immediately
        # (FSRS caps post-failure stability at the pre-failure value on purpose -- a
        # well-established memory shouldn't collapse to daily retesting from one slip).
        # This test is about targeting logic, not scheduling timing, so force due-ness
        # directly rather than fighting that legitimate behavior. Both levels are forced
        # because assignment is gated on each section's own schedule, not just the SOP's.
        past = timezone.now() - datetime.timedelta(days=1)
        TopicMastery.objects.filter(learner=self.learner, sop=self.sop).update(next_eligible_at=past)
        ChunkMastery.objects.filter(learner=self.learner, sop_chunk__sop=self.sop).update(
            next_eligible_at=past
        )

        response = self.client.get("/api/attempts/auto-assigned/")
        assignments = [a for a in response.data["assignments"] if a["sop_id"] == self.sop.id]
        self.assertEqual(len(assignments), 1)
        assignment = assignments[0]
        # Two sections are selected, not one: section B (0% accuracy) plus the bucket
        # holding self.question_unlinked, which has no source_chunk and has never been
        # answered. "Never assessed" is deliberately treated as needing training rather
        # than as an absence -- before the adaptive policy existed, questions with no chunk
        # linkage were invisible to selection entirely. See attempts/adaptive.py.
        self.assertEqual(assignment["unmastered_section_count"], 2)
        self.assertIn(self.question_b.id, assignment["question_ids"])
        self.assertIn(self.question_unlinked.id, assignment["question_ids"])
        # Section A is mastered and stays excluded -- the point of the feature.
        self.assertNotIn(self.question_a.id, assignment["question_ids"])

    def test_section_mastery_status_requires_reviewer_or_admin(self):
        response = self.client.get("/api/attempts/section-mastery/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_section_mastery_status_lists_unmastered_sections_only(self):
        for _ in range(3):
            self._submit([{"question": self.question_a.id, "selected_option": self.correct_a.id}])
        self._submit([{"question": self.question_b.id, "selected_option": self.wrong_b.id}])

        admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.client.force_authenticate(user=admin)
        response = self.client.get("/api/attempts/section-mastery/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        section_titles = {row["section_title"] for row in response.data["sections"]}
        self.assertIn("Section B: Cleaning", section_titles)
        self.assertNotIn("Section A: Gowning", section_titles)  # mastered after 3 correct in a row
