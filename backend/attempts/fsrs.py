"""FSRS (Free Spaced Repetition Scheduler) -- replaces the fixed Leitner interval table
(TopicMastery.BOX_INTERVAL_DAYS) with a continuously-updated per-(learner, SOP) memory
model: stability (how many days until retrievability decays to ~90%) and difficulty
(how inherently hard this SOP is for this learner, 1-10), instead of a shared 6-step
schedule everyone and everything follows identically.

Formulas and default weights are FSRS-4.5 (open-spaced-repetition project), taken as a
matched pair from https://borretti.me/article/implementing-fsrs-in-100-lines -- that
source presents the formulas as executable code alongside the exact default weight
values they were verified against, which is safer than combining a formula from one
source with weights published elsewhere. Weight indices 17-18 (same-day re-review
adjustment, in later FSRS versions) are not used: this app reviews a SOP once per
completed quiz attempt, never multiple times in one sitting.

Deliberately NOT running FSRS's own per-user parameter optimizer: that requires fitting
on a large volume of logged reviews per learner, the same data-scale reasoning that
already ruled out training a neural knowledge-tracing model elsewhere in this project
(see TopicMastery's docstring). The published default weights, fit on hundreds of
millions of real reviews, are used as-is.

This app has no "Hard" / "Easy" signal, only pass/fail per attempt (see
attempts/views.py::_retraining_pass_signal), so only two of FSRS's four grades are ever
used: AGAIN (failed) and GOOD (passed).
"""

import math

W = [
    0.40255, 1.18385, 3.173, 15.69105, 7.1949, 0.5345, 1.4604, 0.0046,
    1.54575, 0.1192, 1.01925, 1.9395, 0.11, 0.29605, 2.2698, 0.2315, 2.9898,
]

# Retrievability-curve shape constants (fixed by the algorithm, not fit weights):
# chosen so that R(t=S, S) = 90% exactly.
_F = 19.0 / 81.0
_C = -0.5

AGAIN, HARD, GOOD, EASY = 1, 2, 3, 4  # standard FSRS grade scale; this app only uses AGAIN/GOOD

DESIRED_RETENTION = 0.9  # schedule the next review for the point where recall odds drop to ~90%
MIN_INTERVAL_DAYS = 1.0  # never schedule "later today" -- matches the old scheme's 1-day floor


def _clamp_difficulty(d):
    return max(1.0, min(10.0, d))


def initial_stability(grade):
    return W[grade - 1]


def initial_difficulty(grade):
    return _clamp_difficulty(W[4] - math.exp(W[5] * (grade - 1)) + 1.0)


def retrievability(elapsed_days, stability):
    """Probability of successful recall right now, given how long it's been since the
    last review and the current memory stability. Falls to 0 for a not-yet-reviewed
    item (stability <= 0), and equals exactly 0.9 when elapsed_days == stability."""
    if stability is None or stability <= 0:
        return 0.0
    return (1.0 + _F * elapsed_days / stability) ** _C


def _next_difficulty(difficulty, grade):
    delta = -W[6] * (grade - 3)
    reverted = difficulty + delta * (10.0 - difficulty) / 9.0
    easy_anchor = initial_difficulty(EASY)
    return _clamp_difficulty(W[7] * easy_anchor + (1.0 - W[7]) * reverted)


def _stability_after_success(difficulty, stability, r):
    t_d = 11.0 - difficulty
    t_s = stability ** (-W[9])
    t_r = math.exp(W[10] * (1.0 - r)) - 1.0
    alpha = 1.0 + t_d * t_s * t_r * math.exp(W[8])
    return stability * alpha


def _stability_after_failure(difficulty, stability, r):
    d_f = difficulty ** (-W[12])
    s_f = (stability + 1.0) ** W[13] - 1.0
    r_f = math.exp(W[14] * (1.0 - r))
    return min(d_f * s_f * r_f * W[11], stability)


def review(stability, difficulty, elapsed_days, is_correct):
    """Apply one review event. stability/difficulty are None for a never-reviewed
    (learner, SOP) pair. Returns (new_stability, new_difficulty)."""
    grade = GOOD if is_correct else AGAIN
    if stability is None or difficulty is None:
        return initial_stability(grade), initial_difficulty(grade)

    r = retrievability(elapsed_days, stability)
    new_difficulty = _next_difficulty(difficulty, grade)
    if is_correct:
        new_stability = _stability_after_success(difficulty, stability, r)
    else:
        new_stability = _stability_after_failure(difficulty, stability, r)
    return new_stability, new_difficulty


def next_review_interval_days(stability, desired_retention=DESIRED_RETENTION):
    """Days until retrievability is projected to decay to desired_retention, i.e. when
    the next review should happen. Floored at MIN_INTERVAL_DAYS."""
    raw = (stability / _F) * (desired_retention ** (1.0 / _C) - 1.0)
    return max(MIN_INTERVAL_DAYS, raw)
