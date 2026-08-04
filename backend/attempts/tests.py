import datetime

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import JobRole
from quiz.models import Option, Question
from sops.models import SOPChunk, SOPDocument

from . import fsrs
from .models import ChunkMastery, TopicMastery
from .services import QUESTION_K_FACTOR, apply_elo_update


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
        # directly rather than fighting that legitimate behavior.
        TopicMastery.objects.filter(learner=self.learner, sop=self.sop).update(
            next_eligible_at=timezone.now() - datetime.timedelta(days=1)
        )

        response = self.client.get("/api/attempts/auto-assigned/")
        assignments = [a for a in response.data["assignments"] if a["sop_id"] == self.sop.id]
        self.assertEqual(len(assignments), 1)
        assignment = assignments[0]
        self.assertEqual(assignment["unmastered_section_count"], 1)
        self.assertIn(self.question_b.id, assignment["question_ids"])
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
