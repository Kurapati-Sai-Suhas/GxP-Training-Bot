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
        response = self.client.patch(
            f"/api/quiz/questions/{self.draft_question.id}/approve/", {"password": "demo12345"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.draft_question.refresh_from_db()
        self.assertEqual(self.draft_question.status, "approved")

    def test_approve_succeeds_for_admin(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.patch(
            f"/api/quiz/questions/{self.draft_question.id}/approve/", {"password": "demo12345"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_approve_rejected_without_e_signature_password(self):
        """21 CFR Part 11: approving requires re-confirming the reviewer's own password,
        not just an already-authenticated session/token."""
        self.client.force_authenticate(user=self.reviewer)
        response = self.client.patch(f"/api/quiz/questions/{self.draft_question.id}/approve/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.draft_question.refresh_from_db()
        self.assertEqual(self.draft_question.status, "draft")

    def test_approve_rejected_for_wrong_e_signature_password(self):
        self.client.force_authenticate(user=self.reviewer)
        response = self.client.patch(
            f"/api/quiz/questions/{self.draft_question.id}/approve/", {"password": "wrong-password"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.draft_question.refresh_from_db()
        self.assertEqual(self.draft_question.status, "draft")

    def test_reject_succeeds_with_e_signature_password(self):
        self.client.force_authenticate(user=self.reviewer)
        response = self.client.patch(
            f"/api/quiz/questions/{self.approved_question.id}/reject/", {"password": "demo12345"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.approved_question.refresh_from_db()
        self.assertEqual(self.approved_question.status, "rejected")

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


class QuestionEloSeedTests(APITestCase):
    """A brand-new question has no answer history yet, so elo_rating (see
    attempts/services.py) borrows the LLM's one-time difficulty label as a starting
    point, then drifts from there as real learners answer it."""

    def setUp(self):
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-901", version="v1.0", department="Production",
            file="sops/sop-901.txt", status="processed",
        )

    def test_new_question_seeds_elo_from_difficulty_label(self):
        for difficulty, expected_elo in Question.DIFFICULTY_SEED_ELO.items():
            question = Question.objects.create(
                sop=self.sop, job_role=self.role, question_text=f"{difficulty}?", explanation="Because.",
                difficulty=difficulty,
            )
            self.assertEqual(question.elo_rating, expected_elo)

    def test_explicit_elo_rating_is_not_overridden_by_seeding(self):
        question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Q?", explanation="Because.",
            difficulty="hard", elo_rating=1650,
        )
        self.assertEqual(question.elo_rating, 1650)


class AnswerKeyConfidentialityTests(APITestCase):
    """P0 regression: the learner-facing question API must never disclose which option is
    correct. Exposing `is_correct` made every assessment open-book via devtools, which
    invalidated the score and everything derived from it (Elo, FSRS, mastery, the training
    record). Correctness is disclosed only after submission."""

    def setUp(self):
        self.learner = get_user_model().objects.create_user(username="rohit", password="demo12345")
        self.reviewer = get_user_model().objects.create_user(username="vikram", password="demo12345")
        self.reviewer.groups.add(Group.objects.get_or_create(name=SME_GROUP)[0])
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)

        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-910", version="v1.0", department="Production",
            file="sops/sop-910.txt", status="processed",
        )
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Which order?",
            explanation="Gloves last, because the outer surface would otherwise be contaminated.",
            status="approved", confidence_score=0.9,
        )
        Option.objects.create(question=self.question, option_text="Gloves last", is_correct=True)
        Option.objects.create(question=self.question, option_text="Gloves first", is_correct=False)

        self.draft = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Draft question?",
            explanation="Because.", status="draft",
        )
        Option.objects.create(question=self.draft, option_text="Right", is_correct=True)

    def test_learner_list_never_includes_is_correct(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.get(f"/api/quiz/questions/?job_role={self.role.id}&status=approved")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data)
        for item in response.data:
            for option in item["options"]:
                self.assertNotIn("is_correct", option)

    def test_learner_list_never_includes_the_explanation(self):
        """The explanation states which answer is right and why, so it is as revealing as
        the answer key itself."""
        self.client.force_authenticate(user=self.learner)
        response = self.client.get(f"/api/quiz/questions/?job_role={self.role.id}&status=approved")
        for item in response.data:
            self.assertNotIn("explanation", item)

    def test_learner_detail_never_includes_is_correct(self):
        """Guards the detail route separately -- a fix applied only to list() would leave
        /questions/{id}/ as an equivalent side channel."""
        self.client.force_authenticate(user=self.learner)
        response = self.client.get(f"/api/quiz/questions/{self.question.id}/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertNotIn("explanation", response.data)
        for option in response.data["options"]:
            self.assertNotIn("is_correct", option)

    def test_raw_response_body_contains_no_correctness_marker(self):
        """Belt-and-braces on the serialised bytes, not just the parsed structure: catches
        a correctness signal leaking through some other field name or nesting."""
        self.client.force_authenticate(user=self.learner)
        response = self.client.get(f"/api/quiz/questions/?job_role={self.role.id}&status=approved")
        body = response.content.decode("utf-8")
        self.assertNotIn("is_correct", body)
        self.assertNotIn("Gloves last, because", body)  # the explanation text

    def test_reviewer_still_receives_the_full_record(self):
        """The reviewer cannot do their job without the answer key -- this must not regress."""
        self.client.force_authenticate(user=self.reviewer)
        response = self.client.get(f"/api/quiz/questions/{self.question.id}/")
        self.assertIn("explanation", response.data)
        self.assertTrue(any(o["is_correct"] for o in response.data["options"]))

    def test_admin_still_receives_the_full_record(self):
        self.client.force_authenticate(user=self.admin)
        response = self.client.get(f"/api/quiz/questions/{self.question.id}/")
        self.assertTrue(any(o["is_correct"] for o in response.data["options"]))

    def test_learner_cannot_read_the_options_endpoint(self):
        """/api/quiz/options/ serialises is_correct directly, so leaving it open to all
        authenticated users was a trivial way around the learner question serializer."""
        self.client.force_authenticate(user=self.learner)
        response = self.client.get("/api/quiz/options/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    def test_reviewer_can_still_read_the_options_endpoint(self):
        self.client.force_authenticate(user=self.reviewer)
        response = self.client.get("/api/quiz/options/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)

    def test_learner_only_ever_sees_approved_questions(self):
        """Previously 'learners only see approved content' relied on the client passing
        ?status=approved; omitting it returned unreviewed drafts."""
        self.client.force_authenticate(user=self.learner)
        response = self.client.get("/api/quiz/questions/")
        returned = {item["id"] for item in response.data}
        self.assertIn(self.question.id, returned)
        self.assertNotIn(self.draft.id, returned)

    def test_learner_cannot_request_drafts_explicitly(self):
        self.client.force_authenticate(user=self.learner)
        response = self.client.get("/api/quiz/questions/?status=draft")
        self.assertEqual(list(response.data), [])

    def test_reviewer_can_still_see_drafts(self):
        """The review queue depends on this."""
        self.client.force_authenticate(user=self.reviewer)
        response = self.client.get("/api/quiz/questions/?status=draft")
        self.assertIn(self.draft.id, {item["id"] for item in response.data})


class ApprovedContentImmutabilityTests(APITestCase):
    """P0 regression: an approved question carries an electronic signature bound to its
    exact wording. Editing it in place would leave that signature vouching for content the
    reviewer never saw; deleting it would remove the record attempts were scored against."""

    def setUp(self):
        self.admin = get_user_model().objects.create_user(username="anjali", password="demo12345", is_staff=True)
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-911", version="v1.0", department="Production",
            file="sops/sop-911.txt", status="processed",
        )
        self.draft = self._make("draft")
        self.approved = self._make("approved")
        self.client.force_authenticate(user=self.admin)

    def _make(self, status_value):
        question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Original wording?",
            explanation="Original explanation.", status=status_value,
        )
        Option.objects.create(question=question, option_text="Right", is_correct=True)
        Option.objects.create(question=question, option_text="Wrong", is_correct=False)
        return question

    def test_approved_question_cannot_be_patched(self):
        response = self.client.patch(
            f"/api/quiz/questions/{self.approved.id}/", {"question_text": "Tampered?"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.approved.refresh_from_db()
        self.assertEqual(self.approved.question_text, "Original wording?")

    def test_approved_question_cannot_be_put(self):
        """PUT is a separate viewset method from PATCH -- blocking only one leaves the
        other as an equivalent bypass."""
        response = self.client.put(
            f"/api/quiz/questions/{self.approved.id}/",
            {
                "sop": self.sop.id, "job_role": self.role.id,
                "question_text": "Tampered?", "explanation": "Tampered.",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.approved.refresh_from_db()
        self.assertEqual(self.approved.question_text, "Original wording?")

    def test_approved_question_cannot_be_deleted(self):
        response = self.client.delete(f"/api/quiz/questions/{self.approved.id}/")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(Question.objects.filter(id=self.approved.id).exists())

    def test_draft_question_can_still_be_edited(self):
        """The lock must not block the normal pre-approval editing workflow."""
        response = self.client.patch(
            f"/api/quiz/questions/{self.draft.id}/", {"question_text": "Improved wording?"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.draft.refresh_from_db()
        self.assertEqual(self.draft.question_text, "Improved wording?")

    def test_draft_question_can_still_be_deleted(self):
        response = self.client.delete(f"/api/quiz/questions/{self.draft.id}/")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(Question.objects.filter(id=self.draft.id).exists())

    def test_rejecting_an_approved_question_reopens_it_for_editing(self):
        """The intended correction path: reject back to draft, edit, re-approve -- rather
        than silently overwriting signed content."""
        self.client.patch(
            f"/api/quiz/questions/{self.approved.id}/reject/", {"password": "demo12345"}, format="json"
        )
        response = self.client.patch(
            f"/api/quiz/questions/{self.approved.id}/", {"question_text": "Corrected wording?"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.approved.refresh_from_db()
        self.assertEqual(self.approved.question_text, "Corrected wording?")


class ElectronicSignatureBindingTests(APITestCase):
    """The signature must identify who signed, when, what object, and *what exact content*.
    Recording only a boolean left it unable to detect that the content later diverged."""

    def setUp(self):
        self.reviewer = get_user_model().objects.create_user(username="vikram", password="demo12345")
        self.reviewer.groups.add(Group.objects.get_or_create(name=SME_GROUP)[0])
        self.role = JobRole.objects.create(name="Production Operator", department="Production")
        self.sop = SOPDocument.objects.create(
            title="Cleanroom Entry", sop_code="SOP-912", version="v1.0", department="Production",
            file="sops/sop-912.txt", status="processed",
        )
        self.question = Question.objects.create(
            sop=self.sop, job_role=self.role, question_text="Which order?",
            explanation="Gloves last.", status="draft",
        )
        Option.objects.create(question=self.question, option_text="Gloves last", is_correct=True)
        Option.objects.create(question=self.question, option_text="Gloves first", is_correct=False)
        self.client.force_authenticate(user=self.reviewer)

    def test_approval_records_signer_timestamp_and_content_hash(self):
        response = self.client.patch(
            f"/api/quiz/questions/{self.question.id}/approve/", {"password": "demo12345"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.question.refresh_from_db()
        self.assertEqual(self.question.approved_by, self.reviewer)
        self.assertIsNotNone(self.question.approved_at)
        self.assertEqual(len(self.question.content_hash), 64)  # sha256 hex

    def test_content_hash_is_recorded_in_the_audit_trail(self):
        from audit.models import AuditLog

        self.client.patch(
            f"/api/quiz/questions/{self.question.id}/approve/", {"password": "demo12345"}, format="json"
        )
        self.question.refresh_from_db()
        entry = AuditLog.objects.get(action="question_approved")
        self.assertEqual(entry.details["content_hash"], self.question.content_hash)
        self.assertEqual(entry.details["signed_by"], "vikram")
        self.assertTrue(entry.details["e_signature"])

    def test_signature_is_intact_immediately_after_approval(self):
        self.client.patch(
            f"/api/quiz/questions/{self.question.id}/approve/", {"password": "demo12345"}, format="json"
        )
        self.question.refresh_from_db()
        self.assertIs(self.question.signature_is_intact(), True)

    def test_signature_detects_content_changed_behind_the_api(self):
        """The edit lock blocks this through the API; this proves a change made by any
        other route (ORM, admin site, migration) is still *detectable* afterwards."""
        self.client.patch(
            f"/api/quiz/questions/{self.question.id}/approve/", {"password": "demo12345"}, format="json"
        )
        self.question.refresh_from_db()
        Question.objects.filter(pk=self.question.pk).update(question_text="Silently changed?")
        self.question.refresh_from_db()
        self.assertIs(self.question.signature_is_intact(), False)

    def test_signature_detects_a_changed_answer_key(self):
        """Flipping which option is correct must invalidate the signature even though the
        question wording is untouched."""
        self.client.patch(
            f"/api/quiz/questions/{self.question.id}/approve/", {"password": "demo12345"}, format="json"
        )
        self.question.refresh_from_db()
        Option.objects.filter(question=self.question).update(is_correct=False)
        self.assertIs(self.question.signature_is_intact(), False)

    def test_content_hash_is_stable_across_recomputation(self):
        first = self.question.compute_content_hash()
        second = self.question.compute_content_hash()
        self.assertEqual(first, second)

    def test_unapproved_question_has_no_signature_to_verify(self):
        self.assertIsNone(self.question.signature_is_intact())

    def test_rejecting_clears_the_signature_binding(self):
        """A rejected question is no longer vouched for; leaving a stale hash and approver
        would misrepresent it as still-signed content."""
        self.client.patch(
            f"/api/quiz/questions/{self.question.id}/approve/", {"password": "demo12345"}, format="json"
        )
        self.client.patch(
            f"/api/quiz/questions/{self.question.id}/reject/", {"password": "demo12345"}, format="json"
        )
        self.question.refresh_from_db()
        self.assertIsNone(self.question.content_hash)
        self.assertIsNone(self.question.approved_by)
        self.assertIsNone(self.question.approved_at)
