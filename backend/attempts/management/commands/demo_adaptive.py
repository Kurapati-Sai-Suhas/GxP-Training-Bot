"""Runs the complete SOP -> AI -> SME -> quiz -> mastery -> adaptive retraining loop and
narrates each step, so the core workflow can be demonstrated in one command.

    uv run python manage.py demo_adaptive

Why this exists: the loop's final stages are invisible in a live demo without it. Spaced
repetition legitimately schedules the next review ~1 day out, so a reviewer watching the UI
cannot see retraining trigger; and the interesting behaviour (weak sections selected, strong
ones excluded) only emerges across several attempts. This command fast-forwards the
*schedule* only -- every score, mastery update and selection below is produced by the real
application code paths, not simulated.

Works with or without NVIDIA_API_KEY: without one, generation falls back to the deterministic
offline generator and the flow is otherwise identical.
"""

import datetime

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.base import ContentFile
from django.core.management.base import BaseCommand
from django.db.models import Q
from django.utils import timezone

from accounts.models import JobRole, LearnerProfile
from accounts.permissions import SME_GROUP
from ai_engine.tasks import generate_quiz_task
from attempts import adaptive
from attempts.models import AttemptAnswer, ChunkMastery, QuizAttempt, TopicMastery
from attempts.views import _pass_signal_from_pairs
from audit.models import AuditLog
from quiz.models import Option, Question
from sops.models import SOPChunk, SOPDocument
from sops.tasks import process_sop_document_task

DEMO_SOP_CODE = "SOP-DEMO"
DEMO_USERS = ["demo_sme", "demo_learner"]

SOP_TEXT = """Section 1: Good Manufacturing Practice
All personnel entering a controlled production area must follow documented GMP requirements.
Equipment must be verified as clean and released before any batch operation begins.
Every action taken in the production area must be recorded contemporaneously.

Section 2: CAPA and Root Cause Analysis
A deviation must be recorded within one working day of discovery.
Corrective and Preventive Action requires a documented root cause investigation before any
corrective action is approved. Effectiveness of the corrective action must be verified after
implementation, and the CAPA record cannot be closed until that verification is complete.

Section 3: Documentation Practices
Records must be legible, contemporaneous, original, accurate and attributable.
Corrections must be made with a single line strike-through, initialled and dated, so that the
original entry remains readable. Backdating any record is prohibited without exception.
"""


class Command(BaseCommand):
    help = "Demonstrate the full adaptive-learning loop end to end."

    def add_arguments(self, parser):
        parser.add_argument(
            "--stop-after-analysis",
            action="store_true",
            help=(
                "Stop after the weakness analysis (step 8), leaving demo_learner in the "
                "weak state. Use this to demonstrate the 'My Learning Path' screen showing "
                "HIGH-priority sections before retraining resolves them."
            ),
        )

    # -- presentation helpers ----------------------------------------------------------
    def step(self, number, title):
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING(f"[{number}] {title}"))

    def detail(self, text):
        self.stdout.write(f"    {text}")

    def ok(self, text):
        self.stdout.write(self.style.SUCCESS(f"    {text}"))

    def warn(self, text):
        self.stdout.write(self.style.WARNING(f"    {text}"))

    # -- the demo ----------------------------------------------------------------------
    def handle(self, *args, **options):
        self._reset()

        sme, learner, role = self.step_1_actors()
        sop = self.step_2_upload()
        self.step_3_process(sop)
        self.step_4_generate(sop, role, sme)
        approved = self.step_5_review_and_sign(sop, role, sme)
        if not approved:
            self.warn("No questions were approved; cannot continue the demo.")
            return

        by_section = self._questions_by_section(sop, role)
        self.step_6_first_attempt(learner, sop, role, by_section)
        self.step_7_show_mastery(learner, sop)
        self.step_8_show_adaptive_analysis(learner, sop, role)

        if options["stop_after_analysis"]:
            self.stdout.write("")
            self.ok(
                "Stopped after weakness analysis. Log in as demo_learner / demo12345 and open "
                "'My Learning Path' to see the weak sections flagged HIGH before retraining."
            )
            return

        targeted = self.step_9_retraining_selection(learner, sop, role)
        self.step_10_retake(learner, sop, role, targeted, by_section)
        self.step_11_show_improvement(learner, sop, role)
        self.step_12_audit(sop)

    # ---------------------------------------------------------------------------------
    def _reset(self):
        SOPDocument.objects.filter(sop_code=DEMO_SOP_CODE).delete()
        get_user_model().objects.filter(username__in=DEMO_USERS).delete()
        JobRole.objects.filter(name="Demo Production Operator").delete()

    def step_1_actors(self):
        self.step(1, "Actors")
        role = JobRole.objects.create(
            name="Demo Production Operator", department="Production",
            description="Demo role for the adaptive-learning walkthrough.",
        )
        User = get_user_model()
        sme = User.objects.create_user(
            username="demo_sme", password="demo12345", first_name="Vikram", last_name="Desai"
        )
        sme.groups.add(Group.objects.get_or_create(name=SME_GROUP)[0])
        learner = User.objects.create_user(
            username="demo_learner", password="demo12345", first_name="Rohit", last_name="Mehta"
        )
        LearnerProfile.objects.create(user=learner, job_role=role, employee_code="EMP-DEMO")
        self.detail(f"SME reviewer : {sme.get_full_name()} ({sme.username})")
        self.detail(f"Learner      : {learner.get_full_name()} ({learner.username})")
        self.detail(f"Job role     : {role.name}")
        return sme, learner, role

    def step_2_upload(self):
        self.step(2, "Upload a controlled SOP")
        sop = SOPDocument.objects.create(
            title="Quality Management Essentials", sop_code=DEMO_SOP_CODE, version="v1.0",
            department="Production", status="uploaded",
        )
        sop.file.save(f"{DEMO_SOP_CODE.lower()}.txt", ContentFile(SOP_TEXT.encode("utf-8")), save=True)
        self.detail(f"{sop.sop_code} v{sop.version} - {sop.title}")
        self.detail(f"Status: {sop.status}")
        return sop

    def step_3_process(self, sop):
        self.step(3, "Process the SOP (extract text, then chunk it)")
        result = process_sop_document_task(sop.id)
        sop.refresh_from_db()
        self.detail(f"Result: {result}")
        for chunk in sop.chunks.all():
            self.detail(f"  - [{chunk.chunking_strategy}] {chunk.section_title}")
        self.ok(f"{sop.chunks.count()} sections created; SOP status now '{sop.status}'.")

    def step_4_generate(self, sop, role, sme):
        self.step(4, "Generate role-specific questions with the AI engine")
        # Nine questions across three sections = three per section. adaptive.MIN_EVIDENCE
        # requires at least three answers before a section can be excluded on accuracy
        # alone, so two per section could never demonstrate a section being retired.
        payload = generate_quiz_task(sop.id, role.id, count=9, user_id=sme.id)
        source = payload["source"]
        label = {
            "nvidia_nim": "live NVIDIA NIM (meta/llama-3.1-8b-instruct)",
            "mock": "deterministic offline fallback (no API key, or the call failed)",
            "mixed": "partly live, partly offline",
        }.get(source, source)
        self.detail(f"Generated {len(payload['questions'])} draft question(s) via {label}.")
        self.detail(f"Skipped duplicates: {payload['skipped_duplicates']}")
        for question in Question.objects.filter(sop=sop, job_role=role):
            section = question.source_chunk.section_title if question.source_chunk else "unlinked"
            self.detail(f"  - [{question.status}] ({section}) {question.question_text[:70]}")

    def step_5_review_and_sign(self, sop, role, sme):
        self.step(5, "SME reviews and approves under electronic signature")
        approved = 0
        for question in Question.objects.filter(sop=sop, job_role=role, status="draft"):
            # Exactly what the approve endpoint does: bind the signature to a hash of the
            # content being approved, then record who signed and when.
            question.content_hash = question.compute_content_hash()
            question.status = "approved"
            question.approved_by = sme
            question.approved_at = timezone.now()
            question.save(update_fields=["status", "content_hash", "approved_by", "approved_at"])
            AuditLog.objects.create(
                user=sme, action="question_approved", object_type="Question", object_id=question.id,
                summary=f"Approved question #{question.id}",
                details={"e_signature": True, "content_hash": question.content_hash},
            )
            approved += 1
        self.ok(f"{approved} question(s) approved and signed by {sme.username}.")
        sample = Question.objects.filter(sop=sop, status="approved").first()
        if sample:
            self.detail(f"Signature binding example: content_hash={sample.content_hash[:16]}...")
            self.detail(f"Signature intact: {sample.signature_is_intact()}")
        return approved

    def _questions_by_section(self, sop, role):
        by_section = {}
        for question in Question.objects.filter(
            sop=sop, job_role=role, status="approved"
        ).select_related("source_chunk").prefetch_related("options"):
            title = question.source_chunk.section_title if question.source_chunk else "Unlinked"
            by_section.setdefault(title, []).append(question)
        return by_section

    def _answer(self, question, correctly):
        options = list(question.options.all())
        correct = next((o for o in options if o.is_correct), None)
        if correctly:
            return correct
        return next((o for o in options if not o.is_correct), correct)

    def _take_quiz(self, learner, sop, role, questions, correct_sections, label):
        """Runs the real grading and mastery pipeline (mirrors QuizAttemptViewSet.submit)."""
        attempt = QuizAttempt.objects.create(learner=learner, sop=sop, job_role=role)
        answered = []
        correct_count = 0
        for section_title, section_questions in questions.items():
            answer_correctly = section_title in correct_sections
            for question in section_questions:
                option = self._answer(question, answer_correctly)
                is_correct = bool(option and option.is_correct)
                AttemptAnswer.objects.create(
                    attempt=attempt, question=question, selected_option=option, is_correct=is_correct,
                )
                answered.append((question, is_correct))
                correct_count += 1 if is_correct else 0

        total = len(answered) or 1
        attempt.score = round(correct_count / total * 100, 2)
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["score", "completed_at"])

        mastery, _ = TopicMastery.objects.get_or_create(
            learner=learner, sop=sop, defaults={"job_role": role}
        )
        pass_signal = _pass_signal_from_pairs(answered)

        pairs_by_chunk = {}
        for question, is_correct in answered:
            if question.source_chunk_id is None:
                continue
            pairs_by_chunk.setdefault(question.source_chunk_id, []).append((question, is_correct))

        from attempts.services import apply_elo_update, apply_elo_update_ability_only

        for question, is_correct in answered:
            apply_elo_update(mastery, question, is_correct)
        Question.objects.bulk_update([q for q, _ in answered], ["elo_rating"])
        mastery.apply_answer(pass_signal)
        mastery.save()

        for chunk_id, pairs in pairs_by_chunk.items():
            chunk_mastery, _ = ChunkMastery.objects.get_or_create(
                learner=learner, sop_chunk_id=chunk_id, defaults={"job_role": role}
            )
            for question, is_correct in pairs:
                apply_elo_update_ability_only(chunk_mastery, question, is_correct)
            chunk_mastery.apply_answer(_pass_signal_from_pairs(pairs))
            chunk_mastery.save()

        self.detail(f"{label}: scored {attempt.score}% ({correct_count}/{total} correct)")
        return attempt

    def step_6_first_attempt(self, learner, sop, role, by_section):
        self.step(6, "Learner takes the quiz - strong on GMP, weak on CAPA and Documentation")
        strong = [t for t in by_section if "Good Manufacturing" in t or "GMP" in t]
        self.detail(f"Answering correctly only in: {strong or '(none detected)'}")
        self._take_quiz(learner, sop, role, by_section, set(strong), "Attempt 1")

    def step_7_show_mastery(self, learner, sop):
        self.step(7, "Server-side grading updates per-section mastery")
        for mastery in ChunkMastery.objects.filter(
            learner=learner, sop_chunk__sop=sop
        ).select_related("sop_chunk").order_by("sop_chunk__id"):
            self.detail(
                f"{mastery.sop_chunk.section_title[:40]:<42} "
                f"streak={mastery.streak_correct} status={mastery.mastery_status} "
                f"elo={round(mastery.elo_rating)} "
                f"next_review={mastery.next_eligible_at:%Y-%m-%d}"
            )

    def step_8_show_adaptive_analysis(self, learner, sop, role):
        self.step(8, "Adaptive analysis - which sections are weak, and why")
        for section in adaptive.analyse_sections(learner, sop, role):
            marker = ">>" if section["selected_for_retraining"] else "  "
            when = "available now" if section["is_due"] else "scheduled for later"
            self.detail(f"{marker} {section['section_title']}")
            self.detail(f"     priority : {section['priority'].upper()}  ({when})")
            self.detail(
                f"     measured : adaptive score {section['weighted_accuracy']}% "
                f"| lifetime {section['accuracy']}% | {section['correct']}/{section['answered']} correct"
            )
            self.detail(f"     evidence : {section['reason']}")

    def step_9_retraining_selection(self, learner, sop, role):
        self.step(9, "Adaptive retraining selection")
        # Fast-forward the schedule only: FSRS put the next review ~1 day out, which a live
        # demo cannot wait for. Mastery state itself is untouched. Both levels are moved --
        # assignment is gated on each section's own FSRS schedule, not just the SOP's.
        past = timezone.now() - datetime.timedelta(days=1)
        TopicMastery.objects.filter(learner=learner, sop=sop).update(next_eligible_at=past)
        ChunkMastery.objects.filter(learner=learner, sop_chunk__sop=sop).update(next_eligible_at=past)
        self.warn("(schedule fast-forwarded so the retest is due now - state unchanged)")

        sections = adaptive.analyse_sections(learner, sop, role)
        targeted = adaptive.select_retraining_questions(sections, only_available=True)
        self.detail(f"Summary: {adaptive.summarise(sections)}")
        self.ok(f"{len(targeted)} question(s) selected for targeted retraining.")
        excluded = [s["section_title"] for s in sections if not s["selected_for_retraining"]]
        if excluded:
            self.detail(f"Excluded (already strong): {', '.join(excluded)}")
        return targeted

    def step_10_retake(self, learner, sop, role, targeted, by_section):
        self.step(10, "Learner retakes the targeted retraining quiz - this time answering correctly")
        targeted_by_section = {
            title: [q for q in questions if q.id in set(targeted)]
            for title, questions in by_section.items()
        }
        targeted_by_section = {t: qs for t, qs in targeted_by_section.items() if qs}
        self.detail(f"Retraining covers: {', '.join(targeted_by_section)}")
        for round_number in range(1, 4):
            self._take_quiz(
                learner, sop, role, targeted_by_section, set(targeted_by_section),
                f"Retake {round_number}",
            )

    def step_11_show_improvement(self, learner, sop, role):
        self.step(11, "Mastery re-evaluated after retraining - measured learning gain")
        for section in adaptive.analyse_sections(learner, sop, role):
            self.detail(
                f"{section['section_title'][:38]:<40} "
                f"score={section['weighted_accuracy']}% "
                f"lifetime={section['accuracy']}% "
                f"status={section['mastery_status']} "
                f"priority={section['priority']}"
            )
            # Learning gain, computed from stored answers (oldest half vs newest half) --
            # this is the proof the loop closed, not just that it ran.
            if section["improvement"] is not None and section["improvement"] != 0:
                arrow = "improved" if section["improvement"] > 0 else "declined"
                sign = "+" if section["improvement"] > 0 else ""
                self.ok(
                    f"    -> {arrow}: {section['initial_accuracy']}% -> "
                    f"{section['current_accuracy']}% ({sign}{section['improvement']} points)"
                )
        sections = adaptive.analyse_sections(learner, sop, role)
        remaining = adaptive.select_retraining_questions(sections)
        mastered = [s["section_title"] for s in sections if s["mastery_status"] == "mastered"]
        if remaining:
            self.warn(f"{len(remaining)} question(s) still selected for further retraining.")
        else:
            self.ok(
                f"No further retraining scheduled. {len(mastered)} of {len(sections)} section(s) "
                f"are fully mastered; the rest are performing above the pass mark."
            )

    def step_12_audit(self, sop):
        self.step(12, "Audit trail for this demo")
        # Scoped to this SOP and to the questions generated from it -- AuditLog holds a
        # loose object_type/object_id pair rather than a foreign key, so the question ids
        # have to be gathered first or every historical Question entry would be listed.
        question_ids = list(Question.objects.filter(sop=sop).values_list("id", flat=True))
        entries = AuditLog.objects.filter(
            Q(object_type="SOPDocument", object_id=sop.id)
            | Q(object_type="Question", object_id__in=question_ids)
        ).order_by("created_at")
        for entry in entries[:12]:
            self.detail(f"{entry.created_at:%H:%M:%S} {entry.get_action_display()} by {entry.user or 'system'}")
        self.detail(f"({entries.count()} audit entries for this SOP in total)")
        self.stdout.write("")
        self.ok("Demo complete: SOP -> AI -> SME signature -> quiz -> mastery -> adaptive retraining -> improvement.")
