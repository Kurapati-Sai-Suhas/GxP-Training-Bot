"""Explainable adaptive-learning policy: which SOP sections this learner should train next,
and — just as importantly for a controlled-training context — *why*.

Why this module exists
----------------------
ChunkMastery already tracked per-section scheduling state, but retraining selection consumed
only its binary `mastery_status`, which flips to "mastered" solely after
MASTERY_STREAK_THRESHOLD consecutive passes. Three consequences followed, all verified by
test before this module was written:

1. A section answered *correctly* was indistinguishable from one answered *incorrectly* until
   a full streak accumulated, so early retraining replayed the entire SOP -- the opposite of
   adaptive.
2. A section never yet assessed has no ChunkMastery row at all, so it was invisible to
   selection: a learner tested only on section A would be retrained only on section A forever.
3. Whole-SOP TopicMastery gated everything, so acing one section three times could mark the
   SOP "mastered" while a genuinely weak section was never revisited.

The fix is to rank sections by *observed answer accuracy* -- the signal the learner actually
generates -- rather than by a streak counter alone, and to treat "never assessed" as its own
state rather than as an absence.

Why accuracy rather than FSRS retrievability
--------------------------------------------
FSRS retrievability R(elapsed, stability) is the natural "how well is this remembered"
number, and it drives *scheduling* (see fsrs.py). It cannot drive *selection*, because
R(0, S) = 1.0 for every S: immediately after an attempt, a section just failed and a section
just passed both score 1.0. Retrievability answers "is it time to review?"; observed accuracy
answers "is this learner weak here?". This module uses accuracy for priority and leaves FSRS
in charge of timing, which is what each is actually good for.
"""

from django.utils import timezone

from quiz.models import Question

from .models import AttemptAnswer, ChunkMastery

# Below this recency-weighted accuracy a section is treated as a genuine weakness.
WEAK_ACCURACY_THRESHOLD = 60.0
# At or above the pass mark but not yet mastered: worth reinforcing, not urgent. Matches
# TopicMastery.PASS_THRESHOLD so "proficient" means the same thing here as everywhere else.
PROFICIENT_ACCURACY_THRESHOLD = 80.0
# How many of the most recent answers count as "recent" for the displayed trend figure.
RECENT_WINDOW = 5

# --- Recency weighting ----------------------------------------------------------------
# Priority is driven by an exponentially recency-weighted accuracy rather than a flat
# lifetime average, because a flat average cannot represent *change*. A learner who
# answered 0/5 and then 5/5 has plainly improved, but their lifetime accuracy is 50% --
# so under a flat average they stay flagged "weak" while the UI simultaneously displays
# "Recent: 100%". The screen contradicted itself, and the learner could not shed a weak
# label without an implausible run of successes.
#
# Weight of the i-th most recent answer (i = 0 is newest):  w_i = 0.5 ** (i / HALF_LIFE)
#
# HALF_LIFE = 5 means an answer five answers ago counts half as much as the newest one.
# Five is not arbitrary: it is the same window already used for the displayed "recent
# accuracy", so "recent" means exactly one half-life throughout the system.
#
# Worked behaviour (verified numerically):
#     0/5 then 5/5 (improving)  -> 66.7%  (lifetime 50%)  HIGH   -> MEDIUM
#     ... then 5 more correct   -> 85.7%                  MEDIUM -> LOW
#     5/5 then 0/5 (declining)  -> 33.3%  (lifetime 50%)  flagged sooner than lifetime
#
# Improvement and deterioration are both recognised, and neither instantly: a single good
# answer cannot erase a history of failure, which is the correct behaviour for a
# compliance-training record.
RECENCY_HALF_LIFE = 5.0
_RECENCY_DECAY = 0.5 ** (1.0 / RECENCY_HALF_LIFE)

# --- Evidence sufficiency -------------------------------------------------------------
# One correct answer is not the same evidence as fifty. Below this many answers a section
# cannot be classified LOW or NONE on accuracy alone -- it is capped at MEDIUM with an
# explicit "insufficient evidence" reason. Deliberately asymmetric: weak performance on a
# small sample is still treated as weak, because the cost of over-training is a few extra
# questions while the cost of under-training is an unqualified operator.
#
# Three is the smallest sample that can show a trend at all (two agreeing observations plus
# one more). It is a chosen default, not a fitted value -- with real deployment data it is
# the first constant that should be tuned.
MIN_EVIDENCE = 3

PRIORITY_HIGH = "high"
PRIORITY_MEDIUM = "medium"
PRIORITY_LOW = "low"
PRIORITY_NONE = "none"

# Priorities that put a section into the next retraining set.
TRAINING_PRIORITIES = {PRIORITY_HIGH, PRIORITY_MEDIUM}


def _answer_history_by_chunk(learner, sop, job_role):
    """Newest-first `is_correct` sequence per source chunk, in a single query.

    Order matters now that priority is recency-weighted, so this returns the ordered
    history rather than pre-aggregated counts. Previously the counts and the "recent
    accuracy" figure were fetched separately -- one aggregate query plus one query *per
    section* -- so collecting the ordered history once is both more informative and fewer
    queries. A None key holds the unlinked-question bucket.
    """
    rows = (
        AttemptAnswer.objects.filter(
            attempt__learner=learner,
            attempt__sop=sop,
            attempt__job_role=job_role,
        )
        .order_by("-id")  # newest first; id order matches answer order within an attempt
        .values_list("question__source_chunk", "is_correct")
    )
    history = {}
    for chunk_id, is_correct in rows:
        history.setdefault(chunk_id, []).append(bool(is_correct))
    return history


def weighted_accuracy(sequence):
    """Exponentially recency-weighted accuracy over a newest-first answer sequence.

        weighted = Σ(w_i · correct_i) / Σ(w_i),   w_i = 0.5 ** (i / RECENCY_HALF_LIFE)

    Returns None for an empty sequence -- "no evidence" is not 0%, and conflating the two
    is exactly the mistake that made never-assessed sections invisible.
    """
    if not sequence:
        return None
    numerator = 0.0
    denominator = 0.0
    for index, correct in enumerate(sequence):
        weight = _RECENCY_DECAY ** index
        denominator += weight
        if correct:
            numerator += weight
    return round(numerator / denominator * 100, 1)


def _plain_accuracy(sequence):
    if not sequence:
        return None
    return round(sum(1 for c in sequence if c) / len(sequence) * 100, 1)


def _learning_gain(sequence):
    """(initial_accuracy, current_accuracy, improvement) in percentage points.

    `sequence` is newest-first, so the *oldest* half is the tail. Comparing the two halves
    is what turns "CAPA is now at 75%" into "CAPA went from 25% to 100%" -- the single most
    convincing artefact the adaptive loop can produce. Returns (None, None, None) below
    four answers, where two halves would be too small to mean anything.
    """
    if len(sequence) < 4:
        return None, None, None
    midpoint = len(sequence) // 2
    newest_half = sequence[:midpoint]
    oldest_half = sequence[midpoint:]
    initial = _plain_accuracy(oldest_half)
    current = _plain_accuracy(newest_half)
    return initial, current, round(current - initial, 1)


def _classify(answered, correct, lifetime, weighted, mastery):
    """Map observed performance onto a priority plus the sentence explaining it.

    The decision metric is `weighted` (recency-weighted accuracy). `lifetime` is carried
    only so the explanation can show both figures when they differ -- the reason string
    must always name the number the decision was actually made on, or the UI ends up
    displaying a metric that contradicts its own verdict.

    Check order is deliberate:
      1. never assessed  -- absence of data must never read as competence
      2. mastered        -- a demonstrated streak retires the section
      3. insufficient evidence -- high accuracy on a tiny sample cannot exclude
      4. weakness / proficiency thresholds

    Why the thresholds are applied to the *rounded* figure
    -----------------------------------------------------
    `weighted` arrives already rounded to one decimal place (see weighted_accuracy), and
    that is the value compared against the thresholds. The alternative -- deciding on the
    full-precision value -- was considered and rejected: a section whose true accuracy is
    59.973% displays as "60.0%", so classifying it HIGH would render the reason string
    "60.0% accuracy - below the 60% weakness threshold", which reads as a bug to a learner
    and to an auditor. Deciding on the number actually shown keeps the verdict and its
    stated evidence consistent, which in a controlled-training context matters more than
    the ~0.05pp band where the two approaches disagree. The band is reachable but narrow:
    an exhaustive search over every answer sequence of length 3-14 found 47 such sequences,
    the shortest being 8 answers. Documented rather than "fixed" -- it is a deliberate
    trade-off, not an oversight.
    """
    if answered == 0:
        return (
            PRIORITY_HIGH,
            "Never assessed - no completed attempt has covered this section yet.",
        )

    if mastery is not None and mastery.mastery_status == "mastered":
        return (
            PRIORITY_NONE,
            f"Mastered - {mastery.streak_correct} passing assessments in a row "
            f"({lifetime}% lifetime accuracy across {answered} answers).",
        )

    # How the two accuracy figures are described, so the reason never looks like a typo
    # when they diverge.
    if abs(weighted - lifetime) >= 0.1:
        measure = (
            f"{weighted}% recency-weighted accuracy "
            f"({correct}/{answered} correct overall = {lifetime}% lifetime)"
        )
    else:
        measure = f"{weighted}% accuracy ({correct}/{answered} correct)"

    if answered < MIN_EVIDENCE and weighted >= WEAK_ACCURACY_THRESHOLD:
        # Performing well, but on too little evidence to justify excluding the section.
        # Weak performance on a small sample deliberately falls through to the thresholds
        # below and stays HIGH -- under-training is the more costly error here.
        return (
            PRIORITY_MEDIUM,
            f"{measure}, but only {answered} assessment(s) so far - insufficient evidence "
            f"to rule this section out (needs {MIN_EVIDENCE}).",
        )

    if weighted < WEAK_ACCURACY_THRESHOLD:
        return (
            PRIORITY_HIGH,
            f"{measure} - below the {WEAK_ACCURACY_THRESHOLD:.0f}% weakness threshold.",
        )

    if weighted < PROFICIENT_ACCURACY_THRESHOLD:
        return (
            PRIORITY_MEDIUM,
            f"{measure} - below the {PROFICIENT_ACCURACY_THRESHOLD:.0f}% pass mark.",
        )

    streak = mastery.streak_correct if mastery else 0
    return (
        PRIORITY_LOW,
        f"{measure} - performing well, {streak} of 3 passing assessments toward mastery.",
    )


def analyse_sections(learner, sop, job_role):
    """Per-section adaptive analysis for one learner on one SOP.

    Returns a list of dicts ordered most-urgent first. Only sections that actually have
    approved questions are considered -- a section with nothing to ask about cannot be
    trained on, and listing it would just be noise in the explanation.
    """
    history = _answer_history_by_chunk(learner, sop, job_role)
    now = timezone.now()
    masteries = {
        m.sop_chunk_id: m
        for m in ChunkMastery.objects.filter(
            learner=learner, sop_chunk__sop=sop, job_role=job_role
        ).select_related("sop_chunk")
    }

    question_rows = (
        Question.objects.filter(sop=sop, job_role=job_role, status="approved")
        .select_related("source_chunk")
        .values("id", "source_chunk_id", "source_chunk__section_title")
    )

    questions_by_chunk = {}
    titles = {}
    for row in question_rows:
        # source_chunk_id is None for manually-authored questions, and for AI-drafted ones
        # whose chunk was later removed (the FK is SET_NULL). They are collected into a
        # single unlinked bucket rather than dropped: excluding them would make an entire
        # SOP invisible to retraining whenever none of its questions carry chunk linkage.
        chunk_id = row["source_chunk_id"]
        questions_by_chunk.setdefault(chunk_id, []).append(row["id"])
        titles[chunk_id] = (
            row["source_chunk__section_title"] or "Untitled section"
            if chunk_id is not None
            else "Questions not linked to a section"
        )

    sections = []
    for chunk_id, question_ids in questions_by_chunk.items():
        sequence = history.get(chunk_id, [])  # newest-first
        answered = len(sequence)
        correct = sum(1 for c in sequence if c)
        lifetime = _plain_accuracy(sequence)
        weighted = weighted_accuracy(sequence)
        mastery = masteries.get(chunk_id)
        priority, reason = _classify(answered, correct, lifetime, weighted, mastery)
        initial, current, improvement = _learning_gain(sequence)

        # Section-level due-ness, from this section's own FSRS schedule. A section with no
        # mastery row has never been assessed, so nothing has been scheduled for it and it
        # is available immediately. This is what makes ChunkMastery.next_eligible_at
        # actually consumed rather than merely computed.
        is_due = True if mastery is None else mastery.next_eligible_at <= now
        selected = priority in TRAINING_PRIORITIES

        sections.append(
            {
                "chunk_id": chunk_id,
                "section_title": titles[chunk_id],
                "answered": answered,
                "correct": correct,
                # `accuracy` remains the plain lifetime figure (unchanged meaning for any
                # existing consumer); `weighted_accuracy` is what the decision used.
                "accuracy": lifetime,
                "weighted_accuracy": weighted,
                "recent_accuracy": _plain_accuracy(sequence[:RECENT_WINDOW]),
                "evidence_sufficient": answered >= MIN_EVIDENCE,
                "initial_accuracy": initial,
                "current_accuracy": current,
                "improvement": improvement,
                "mastery_status": mastery.mastery_status if mastery else "not_started",
                "streak_correct": mastery.streak_correct if mastery else 0,
                "elo_rating": round(mastery.elo_rating) if mastery else None,
                "memory_stability_days": (
                    round(mastery.fsrs_stability, 1)
                    if mastery and mastery.fsrs_stability is not None
                    else None
                ),
                "next_eligible_at": mastery.next_eligible_at.isoformat() if mastery else None,
                "is_due": is_due,
                "priority": priority,
                "reason": reason,
                "question_ids": sorted(question_ids),
                # WHAT the engine wants trained...
                "selected_for_retraining": selected,
                # ...and WHEN FSRS permits it. The UI must distinguish these two, or it
                # promises training the assignment engine will not hand over.
                "available_now": selected and is_due,
            }
        )

    order = {PRIORITY_HIGH: 0, PRIORITY_MEDIUM: 1, PRIORITY_LOW: 2, PRIORITY_NONE: 3}
    # Within a priority band, weakest measured accuracy first; never-assessed sections sort
    # ahead of measured ones (accuracy None -> -1) since we have no evidence of competence.
    sections.sort(key=lambda s: (order[s["priority"]], s["accuracy"] if s["accuracy"] is not None else -1))
    return sections


def training_sections(sections, topic_mastered=False, only_available=False):
    """Sections the policy wants trained.

    `topic_mastered` reflects whole-SOP TopicMastery. When the learner has demonstrated
    SOP-level competence, that judgement is trusted unless there is *measured* evidence
    against it: a section with recorded wrong answers still reopens the topic, but a
    never-assessed section on its own does not. Without this asymmetry, any mastered SOP
    containing a section the learner happened never to be asked about would be permanently
    re-offered, and mastery would never actually retire anything.

    `only_available` additionally requires the section's own FSRS schedule to be due. The
    assignment engine passes True (it can only hand over training that is due); the
    learning path passes False and labels each section with `available_now`, so the learner
    sees "scheduled for <date>" instead of a recommendation that leads nowhere.
    """
    selected = [s for s in sections if s["selected_for_retraining"]]
    if topic_mastered:
        selected = [s for s in selected if s["answered"] > 0]
    if only_available:
        selected = [s for s in selected if s["is_due"]]
    return selected


def select_retraining_questions(sections, topic_mastered=False, only_available=False):
    """Question ids for every section the policy wants trained, most urgent first."""
    selected = []
    for section in training_sections(sections, topic_mastered, only_available):
        selected.extend(section["question_ids"])
    return selected


def needs_training(sections, topic_mastered=False, only_available=False):
    return bool(training_sections(sections, topic_mastered, only_available))


def summarise(sections):
    """One-line explanation of the selection, for the audit trail and the learner-facing
    reason string."""
    high = [s for s in sections if s["priority"] == PRIORITY_HIGH]
    medium = [s for s in sections if s["priority"] == PRIORITY_MEDIUM]
    mastered = [s for s in sections if s["priority"] == PRIORITY_NONE]

    parts = []
    if high:
        parts.append(f"{len(high)} weak section(s): {', '.join(s['section_title'] for s in high)}")
    if medium:
        parts.append(f"{len(medium)} section(s) below the pass mark")
    if mastered:
        parts.append(f"{len(mastered)} mastered and excluded")
    return "; ".join(parts) if parts else "No sections currently need retraining."
