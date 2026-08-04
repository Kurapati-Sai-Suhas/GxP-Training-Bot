from django.utils import timezone
from rest_framework import decorators, permissions, response, viewsets
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response

from accounts.permissions import ADMIN_GROUP, IsReviewerUser
from audit.models import log_action
from quiz.models import Question

from .models import AttemptAnswer, ChunkMastery, QuizAttempt, TopicMastery
from .serializers import AttemptAnswerSerializer, QuizAttemptSerializer
from .services import apply_elo_update, apply_elo_update_ability_only


def _is_admin(user):
    return bool(user and user.is_authenticated and (user.is_staff or user.groups.filter(name=ADMIN_GROUP).exists()))


# A learner failing the same SOP this many times is treated as a compliance signal worth
# flagging to QA/Admin, not just another automatic retraining cycle.
RETRAINING_ESCALATION_THRESHOLD = 3

# Difficulty-weighted mastery scoring (see Ye, Su & Cao, "A Stochastic Shortest Path
# Algorithm for Optimizing Spaced Repetition Scheduling", KDD 2022, and Settles & Meeder,
# "A Trainable Spaced Repetition Model for Language Learning", ACL 2016, both on why
# treating every item as equally hard understates what a learner actually knows): a hard
# question answered correctly counts for more toward the Leitner box advance than an easy
# one, and vice versa for a miss. The weight now comes from each question's live Elo
# rating (see services.py) rather than its one-time difficulty label, linearly mapped onto
# the same 1.0-2.0 range the old easy/medium/hard lookup used, and clamped so a single
# outlier rating can't dominate the pass/fail signal. ELO_WEIGHT_FLOOR/CEILING reuse
# Question's own difficulty-seed constants, so a never-yet-answered question reproduces
# the exact old weight, and only drifts once real answers move its rating.
ELO_WEIGHT_FLOOR = Question.DIFFICULTY_SEED_ELO["easy"]
ELO_WEIGHT_CEILING = Question.DIFFICULTY_SEED_ELO["hard"]
# LLM self-reported confidence is often miscalibrated (Geng et al., "A Survey of
# Confidence Estimation and Calibration in Large Language Models", NAACL 2024) but it is
# still the best signal we have that a question might be ambiguous -- a wrong answer on a
# low-confidence AI-drafted question shouldn't unfairly reset a learner's whole schedule.
CONFIDENCE_TRUST_THRESHOLD = 0.5


def _elo_weight(question):
    span = ELO_WEIGHT_CEILING - ELO_WEIGHT_FLOOR
    fraction = (question.elo_rating - ELO_WEIGHT_FLOOR) / span
    fraction = max(0.0, min(1.0, fraction))
    return 1.0 + fraction  # 1.0 at/below the easy seed .. 2.0 at/above the hard seed


def _pass_signal_from_pairs(pairs):
    """Confidence-aware, Elo-weighted pass/fail signal from a list of (question,
    is_correct) pairs -- the shared core of both the whole-attempt (TopicMastery) and
    single-section (ChunkMastery) retraining signals. Caller guarantees pairs is
    non-empty; the "no answers at all" edge case is handled by callers, not here, since
    only the whole-attempt case has a plain attempt.score to fall back on."""
    trustworthy = [
        (q, correct) for q, correct in pairs
        if q.confidence_score is None or q.confidence_score >= CONFIDENCE_TRUST_THRESHOLD
    ]
    if len(trustworthy) < max(1, len(pairs) // 2):
        trustworthy = pairs

    total_weight = sum(_elo_weight(q) for q, _correct in trustworthy)
    if total_weight == 0:
        return False

    correct_weight = sum(_elo_weight(q) for q, correct in trustworthy if correct)
    return (correct_weight / total_weight) * 100 >= TopicMastery.PASS_THRESHOLD


def _retraining_pass_signal(attempt):
    """Whether this attempt counts as a pass for adaptive-retraining purposes: the plain
    percentage score used for the learner-facing result screen, but weighted by each
    question's live Elo difficulty and with low-confidence AI-drafted questions excluded
    (falling back to all answers if too few trustworthy ones remain to judge fairly).

    Reads elo_rating as it stands *before* this attempt's own answers can move it (Elo
    updates are applied later in submit(), after this signal is computed) -- otherwise a
    question's weight would shift mid-calculation depending on iteration order."""
    answers = list(
        AttemptAnswer.objects.filter(attempt=attempt).select_related("question")
    )
    if not answers:
        return float(attempt.score) >= TopicMastery.PASS_THRESHOLD
    return _pass_signal_from_pairs([(a.question, a.is_correct) for a in answers])


class QuizAttemptViewSet(viewsets.ModelViewSet):
    queryset = QuizAttempt.objects.select_related("learner", "job_role", "sop").prefetch_related("answers")
    serializer_class = QuizAttemptSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        if _is_admin(self.request.user):
            return queryset
        return queryset.filter(learner=self.request.user)

    def perform_create(self, serializer):
        serializer.save(learner=self.request.user)

    @decorators.action(detail=True, methods=["post"])
    def submit(self, request, pk=None):
        attempt = self.get_object()
        if attempt.learner_id != request.user.id:
            return response.Response(
                {"error": "This quiz attempt does not belong to you."},
                status=403,
            )
        submitted_answers = request.data.get("answers", [])

        AttemptAnswer.objects.filter(attempt=attempt).delete()
        correct_count = 0
        answered_questions = []  # (question, is_correct), for the Elo update below
        for item in submitted_answers:
            question_id = item.get("question")
            selected_option_id = item.get("selected_option")
            is_correct = False
            if selected_option_id:
                from quiz.models import Option

                is_correct = Option.objects.filter(id=selected_option_id, question_id=question_id, is_correct=True).exists()
            correct_count += 1 if is_correct else 0
            AttemptAnswer.objects.create(
                attempt=attempt,
                question_id=question_id,
                selected_option_id=selected_option_id,
                is_correct=is_correct,
            )
            question = Question.objects.filter(id=question_id).first()
            if question is not None:
                answered_questions.append((question, is_correct))

        total = len(submitted_answers) or 1
        attempt.score = round((correct_count / total) * 100, 2)
        attempt.completed_at = timezone.now()
        attempt.save(update_fields=["score", "completed_at"])
        log_action(
            request.user, "quiz_attempt_submitted", attempt,
            summary=f"{request.user} submitted attempt #{attempt.id} on {attempt.sop.sop_code}, score {attempt.score}%",
            details={"score": float(attempt.score), "answers": len(submitted_answers)},
        )

        # Adaptive-retraining schedule: updated once per completed attempt, from the
        # attempt's overall score, not once per individual answer (see TopicMastery
        # docstring in models.py for why that distinction matters).
        mastery, _ = TopicMastery.objects.get_or_create(
            learner=attempt.learner, sop=attempt.sop, defaults={"job_role": attempt.job_role}
        )
        mastery.job_role = attempt.job_role

        # Compute both the whole-SOP and per-section pass/fail signals *before* touching
        # any Elo rating, so the weighting above reads a stable pre-answer snapshot
        # regardless of this loop's order (answered_questions holds the same in-memory
        # Question objects _retraining_pass_signal's DB read will see, still untouched).
        pass_signal = _retraining_pass_signal(attempt)
        pairs_by_chunk = {}
        for question, is_correct in answered_questions:
            if question.source_chunk_id is None:
                continue  # no section to attribute this answer to (manually-authored question, etc.)
            pairs_by_chunk.setdefault(question.source_chunk_id, []).append((question, is_correct))
        chunk_pass_signals = {
            chunk_id: _pass_signal_from_pairs(pairs) for chunk_id, pairs in pairs_by_chunk.items()
        }

        # Elo rating update (see services.py): each answered question nudges both the
        # learner's per-SOP ability and that question's own live difficulty.
        touched_questions = [q for q, _is_correct in answered_questions]
        for question, is_correct in answered_questions:
            apply_elo_update(mastery, question, is_correct)
        if touched_questions:
            Question.objects.bulk_update(touched_questions, ["elo_rating"])

        mastery.apply_answer(pass_signal)
        mastery.save()

        # Section-level tracking (ChunkMastery): the finer-grained sibling of the
        # whole-SOP update above -- a miss in one section only resets that section's
        # schedule, not every section of the SOP. Ability-only Elo update here (see
        # services.py) since apply_elo_update() above already moved each question's own
        # difficulty rating once; doing it again per section would double-count one answer.
        for chunk_id, pairs in pairs_by_chunk.items():
            chunk_mastery, _created = ChunkMastery.objects.get_or_create(
                learner=attempt.learner, sop_chunk_id=chunk_id, defaults={"job_role": attempt.job_role}
            )
            chunk_mastery.job_role = attempt.job_role
            for question, is_correct in pairs:
                apply_elo_update_ability_only(chunk_mastery, question, is_correct)
            chunk_mastery.apply_answer(chunk_pass_signals[chunk_id])
            chunk_mastery.save()

        # get_object() prefetched `answers` before the delete/create above, so that cache is
        # stale now. Re-fetch so the serialized response reflects the answers just created.
        attempt = self.get_queryset().get(pk=attempt.pk)
        return response.Response(self.get_serializer(attempt).data)


class AttemptAnswerViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = AttemptAnswer.objects.select_related("attempt", "question", "selected_option").all()
    serializer_class = AttemptAnswerSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()
        if _is_admin(self.request.user):
            return queryset
        return queryset.filter(attempt__learner=self.request.user)


@api_view(["GET"])
@permission_classes([permissions.IsAuthenticated])
def auto_assigned_retraining(request):
    """Adaptive-retraining auto-assignment (hard): for the requesting learner, find every
    SOP whose TopicMastery schedule (Leitner-style box scheduling, see models.py) says a
    retest is now due and not yet mastered, and pre-create the QuizAttempt itself instead
    of only suggesting one -- the learner is taken straight into the quiz, not just handed
    a pre-filled dropdown. Idempotent: re-checking this endpoint (e.g. on every page load)
    reuses the same not-yet-completed auto-assigned attempt rather than spawning a new one
    each time, so retaking the SOP later still goes through the normal Start Quiz flow."""
    due = (
        TopicMastery.objects.filter(learner=request.user, next_eligible_at__lte=timezone.now())
        .exclude(mastery_status="mastered")
        .select_related("sop", "job_role")
    )

    assignments = []
    for mastery in due:
        # Elo-driven, not streak-driven: reflects this learner's actual measured ability
        # on this SOP (see services.py), so e.g. an experienced transfer who's answered a
        # few questions correctly gets suggested harder material sooner than a first-day
        # hire would, even before either has strung together a full mastery streak.
        if mastery.elo_rating < ELO_WEIGHT_FLOOR:
            suggested_difficulty = "easy"
        elif mastery.elo_rating < ELO_WEIGHT_CEILING:
            suggested_difficulty = "medium"
        else:
            suggested_difficulty = "hard"

        available_count = Question.objects.filter(
            sop=mastery.sop, job_role=mastery.job_role, status="approved"
        ).count()
        if available_count == 0:
            continue

        attempt = (
            QuizAttempt.objects.filter(
                learner=request.user, sop=mastery.sop, job_role=mastery.job_role, completed_at__isnull=True
            )
            .order_by("-started_at")
            .first()
        )
        if attempt is None:
            attempt = QuizAttempt.objects.create(learner=request.user, sop=mastery.sop, job_role=mastery.job_role)
            log_action(
                request.user, "quiz_attempt_auto_assigned", attempt,
                summary=(
                    f"Auto-assigned retraining attempt #{attempt.id} for {request.user} on "
                    f"{mastery.sop.sop_code} (box {mastery.box_index})"
                ),
                details={"box_index": mastery.box_index, "streak_correct": mastery.streak_correct},
            )

            # Compliance escalation: a learner stuck failing the same SOP repeatedly is a
            # safety/compliance signal, not just a UX detail -- flag it for QA/Admin instead
            # of letting them cycle through 1-day retests indefinitely with no one noticing.
            # Fires once per new retraining cycle (not on every page load, since this branch
            # only runs when a fresh attempt is actually created).
            failed_attempts = QuizAttempt.objects.filter(
                learner=request.user, sop=mastery.sop, job_role=mastery.job_role,
                completed_at__isnull=False, score__lt=TopicMastery.PASS_THRESHOLD,
            ).count()
            if failed_attempts >= RETRAINING_ESCALATION_THRESHOLD:
                log_action(
                    request.user, "retraining_escalation", attempt,
                    summary=(
                        f"{request.user} has failed {mastery.sop.sop_code} {failed_attempts} times -- "
                        f"flagged for QA/Admin review"
                    ),
                    details={"failed_attempts": failed_attempts, "sop_code": mastery.sop.sop_code},
                )

        # Target the retest at the specific unmastered section(s) of this SOP first (see
        # ChunkMastery) -- much more precise than "any question ever missed," since a
        # one-off fluke on an otherwise-mastered section shouldn't drag the whole SOP's
        # retest into covering material the learner has since demonstrated they know.
        unmastered_chunk_ids = list(
            ChunkMastery.objects.filter(learner=request.user, sop_chunk__sop=mastery.sop, job_role=mastery.job_role)
            .exclude(mastery_status="mastered")
            .values_list("sop_chunk_id", flat=True)
        )
        if unmastered_chunk_ids:
            weak_question_ids = list(
                Question.objects.filter(
                    sop=mastery.sop, job_role=mastery.job_role, status="approved",
                    source_chunk_id__in=unmastered_chunk_ids,
                ).values_list("id", flat=True)
            )
        else:
            # No section-level data yet (e.g. these questions predate chunk linkage) --
            # fall back to the original targeting: questions this learner has actually
            # gotten wrong before, instead of re-running the whole quiz including ones
            # they already know cold. Falls back further to the full approved set
            # (question_ids=[]) if there's no wrong-answer history yet either.
            weak_question_ids = list(
                AttemptAnswer.objects.filter(
                    attempt__learner=request.user, attempt__sop=mastery.sop, attempt__job_role=mastery.job_role,
                    is_correct=False, question__status="approved",
                )
                .values_list("question_id", flat=True)
                .distinct()
            )
        targeted = len(weak_question_ids) > 0

        assignments.append(
            {
                "sop_id": mastery.sop_id,
                "sop_code": mastery.sop.sop_code,
                "sop_title": mastery.sop.title,
                "job_role_id": mastery.job_role_id,
                "box_index": mastery.box_index,
                "streak_correct": mastery.streak_correct,
                "elo_rating": round(mastery.elo_rating),
                "memory_stability_days": (
                    round(mastery.fsrs_stability, 1) if mastery.fsrs_stability is not None else None
                ),
                "due_since": mastery.next_eligible_at.isoformat(),
                "suggested_difficulty": suggested_difficulty,
                "question_count_available": available_count,
                "attempt_id": attempt.id,
                "question_ids": weak_question_ids,
                "targeted": targeted,
                "unmastered_section_count": len(unmastered_chunk_ids),
                "reason": (
                    f"Due for a spaced retest (box {mastery.box_index}) under our adaptive-retraining "
                    f"schedule — {mastery.streak_correct} correct in a row so far on this SOP."
                    + (
                        f" Retest focuses on {len(unmastered_chunk_ids)} unmastered section(s)."
                        if unmastered_chunk_ids
                        else (
                            f" Retest focuses on the {len(weak_question_ids)} question(s) you missed before."
                            if targeted
                            else ""
                        )
                    )
                ),
            }
        )

    return Response({"assignments": assignments})


@api_view(["GET"])
@permission_classes([IsReviewerUser])
def retraining_status(request):
    """Admin/SME-facing visibility into every learner currently stuck in a retraining
    loop (not yet mastered). Closes a real gap: retraining_escalation events were already
    being written to the audit trail, but nobody could see them without digging through
    logs -- this surfaces the same signal as a live, sorted list."""
    rows = []
    for mastery in TopicMastery.objects.exclude(mastery_status="mastered").select_related("learner", "sop", "job_role"):
        failed_attempts = QuizAttempt.objects.filter(
            learner=mastery.learner, sop=mastery.sop, job_role=mastery.job_role,
            completed_at__isnull=False, score__lt=TopicMastery.PASS_THRESHOLD,
        ).count()
        rows.append(
            {
                "learner": mastery.learner.get_full_name() or mastery.learner.username,
                "sop_code": mastery.sop.sop_code,
                "sop_title": mastery.sop.title,
                "job_role": mastery.job_role.name,
                "box_index": mastery.box_index,
                "streak_correct": mastery.streak_correct,
                "elo_rating": round(mastery.elo_rating),
                "memory_stability_days": (
                    round(mastery.fsrs_stability, 1) if mastery.fsrs_stability is not None else None
                ),
                "next_eligible_at": mastery.next_eligible_at.isoformat(),
                "is_due": mastery.next_eligible_at <= timezone.now(),
                "failed_attempts": failed_attempts,
                "escalated": failed_attempts >= RETRAINING_ESCALATION_THRESHOLD,
            }
        )
    rows.sort(key=lambda r: (-r["failed_attempts"], r["next_eligible_at"]))
    return Response({"learners": rows})


@api_view(["GET"])
@permission_classes([IsReviewerUser])
def section_mastery_status(request):
    """Admin/SME-facing visibility at section granularity -- the finer-grained sibling of
    retraining_status: which specific SOP section a learner is weak in, not just which
    whole SOP. Closes the "you're solid on 9/10 chapters, weak on 1" visibility gap that
    whole-SOP TopicMastery alone can't show, since one section's miss no longer looks
    identical to a SOP-wide struggle."""
    rows = []
    for mastery in ChunkMastery.objects.exclude(mastery_status="mastered").select_related(
        "learner", "sop_chunk", "sop_chunk__sop", "job_role"
    ):
        rows.append(
            {
                "learner": mastery.learner.get_full_name() or mastery.learner.username,
                "sop_code": mastery.sop_chunk.sop.sop_code,
                "sop_title": mastery.sop_chunk.sop.title,
                "section_title": mastery.sop_chunk.section_title or "Untitled section",
                "job_role": mastery.job_role.name,
                "box_index": mastery.box_index,
                "streak_correct": mastery.streak_correct,
                "elo_rating": round(mastery.elo_rating),
                "memory_stability_days": (
                    round(mastery.fsrs_stability, 1) if mastery.fsrs_stability is not None else None
                ),
                "next_eligible_at": mastery.next_eligible_at.isoformat(),
                "is_due": mastery.next_eligible_at <= timezone.now(),
            }
        )
    rows.sort(key=lambda r: r["next_eligible_at"])
    return Response({"sections": rows})
