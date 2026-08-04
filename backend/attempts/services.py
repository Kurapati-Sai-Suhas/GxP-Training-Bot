"""Elo rating for adaptive question difficulty and learner ability.

Same rating math chess uses to rank players, applied to quiz answers per Pelánek,
"Applications of the Elo rating system in adaptive educational systems," Computers &
Education 98 (2016): every answered question is treated as a one-round match between the
learner's current ability rating (TopicMastery.elo_rating) and the question's current
difficulty rating (Question.elo_rating). A correct answer is a "win" for the learner --
their rating rises and the question's falls -- and a wrong answer is the reverse. The size
of each move depends on how surprising the outcome was: beating a much-harder-rated
question moves you more than beating an easy one.

Two different K-factors (how fast each rating adapts per answer) are used on purpose:

- A learner's rating needs to adapt quickly from very few answers -- this is the direct
  fix for the cold-start problem, where a learner transferring in with real prior
  knowledge should be recognised as such within a handful of questions, not treated as a
  blank slate for weeks.
- A question's rating should move more slowly, since it's shared across every learner who
  ever answers it -- one answer shouldn't swing a question's difficulty wildly.
"""

LEARNER_K_FACTOR = 32
QUESTION_K_FACTOR = 16


def _expected_score(rating_a, rating_b):
    """Standard Elo expected-score formula: rating_a's probability of "beating" rating_b."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def _learner_rating_after(learner_rating, question_rating, is_correct):
    expected_learner = _expected_score(learner_rating, question_rating)
    actual_learner = 1.0 if is_correct else 0.0
    return learner_rating + LEARNER_K_FACTOR * (actual_learner - expected_learner)


def apply_elo_update(mastery, question, is_correct):
    """Update mastery.elo_rating (learner/section ability) and Question.elo_rating
    (question difficulty) in place from one answer -- the full, symmetric update.

    Mutates both objects but does not save() either one -- the caller controls when
    writes happen, so a multi-question submission can batch its saves.
    """
    learner_rating = mastery.elo_rating
    question_rating = question.elo_rating
    expected_learner = _expected_score(learner_rating, question_rating)
    actual_learner = 1.0 if is_correct else 0.0

    mastery.elo_rating = _learner_rating_after(learner_rating, question_rating, is_correct)
    # Zero-sum against the learner: the question "wins" (is judged harder) exactly when
    # the learner loses, and vice versa.
    question.elo_rating = question_rating + QUESTION_K_FACTOR * (expected_learner - actual_learner)


def apply_elo_update_ability_only(mastery, question, is_correct):
    """Update only mastery.elo_rating, treating question.elo_rating as a fixed opponent
    rating -- does not also move the question's own difficulty.

    One real-world answer can feed more than one ability estimate at once (e.g. both a
    section-level ChunkMastery and the whole-SOP TopicMastery). apply_elo_update() is
    called once, for the primary (whole-SOP) pairing, so the question's difficulty moves
    exactly once per answer; every additional ability estimate fed by the same answer
    should use this one-sided variant instead, or the question's rating would get nudged
    twice for a single real event.
    """
    mastery.elo_rating = _learner_rating_after(mastery.elo_rating, question.elo_rating, is_correct)
