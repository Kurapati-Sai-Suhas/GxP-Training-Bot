# Adaptive Learning — Demonstration Scenario

One command, twelve narrated steps, real code paths throughout.

```bash
cd backend
uv run python manage.py demo_adaptive                      # full loop
uv run python manage.py demo_adaptive --stop-after-analysis # pause at the weak state
```

**Nothing is hard-coded.** Every score, mastery value, priority and selection below is produced
by the same functions the application uses. The command creates its own isolated data
(`SOP-DEMO`, `demo_sme`, `demo_learner`) and resets it on each run.

---

## The scenario

One SOP, three sections, two AI-generated questions each:

| Section | Learner performance |
|---|---|
| **Section 1 — Good Manufacturing Practice** | answers correctly |
| **Section 2 — CAPA and Root Cause Analysis** | answers incorrectly |
| **Section 3 — Documentation Practices** | answers incorrectly |

Expected outcome: GMP excluded from retraining; CAPA and Documentation selected.

---

## Verified output — live NVIDIA NIM

Abridged from an actual run.

### Steps 1–5: content pipeline

```text
[3] Process the SOP (extract text, then chunk it)
    Result: {'message': 'SOP processed', 'chunks': 3}
      - [heading] Section 1: Good Manufacturing Practice
      - [heading] Section 2: CAPA and Root Cause Analysis
      - [heading] Section 3: Documentation Practices
    3 sections created; SOP status now 'processed'.

[4] Generate role-specific questions with the AI engine
    Generated 9 draft question(s) via live NVIDIA NIM (meta/llama-3.1-8b-instruct).
    Skipped duplicates: 0
      - [draft] (Section 3: Documentation Practices) What is the consequence of backdating a record?
      - [draft] (Section 3: Documentation Practices) What is the requirement for the original entry…
      - [draft] (Section 3: Documentation Practices) What is the requirement for corrections to be…
      - [draft] (Section 2: CAPA and Root Cause Analysis) When can a CAPA record be closed?
      - [draft] (Section 2: CAPA and Root Cause Analysis) What is a necessary step before approving…
      - [draft] (Section 2: CAPA and Root Cause Analysis) What is the timeframe for recording a deviation?
      - [draft] (Section 1: Good Manufacturing Practice) What is the requirement for recording actions…
      - [draft] (Section 1: Good Manufacturing Practice) What is the purpose of verifying equipment…
      - [draft] (Section 1: Good Manufacturing Practice) What must all personnel entering a controlled…

[5] SME reviews and approves under electronic signature
    9 question(s) approved and signed by demo_sme.
    Signature binding example: content_hash=b40f4985c5d77a1e...
    Signature intact: True
```

Nine questions across three sections — three per section. That is not cosmetic: `MIN_EVIDENCE = 3`
means a section cannot be excluded from retraining on fewer than three answers, so two questions
per section could never demonstrate a section being retired.

Heading-aware chunking fired (tier 1 of 3), each question is bound to the section it came from,
and each approval is bound to a SHA-256 hash of the content signed.

### Steps 6–7: assessment and mastery

```text
[6] Learner takes the quiz - strong on GMP, weak on CAPA and Documentation
    Attempt 1: scored 33.33% (3/9 correct)

[7] Server-side grading updates per-section mastery
    Section 1: Good Manufacturing Practice   streak=1 status=in_progress elo=1545 next_review=2026-08-16
    Section 2: CAPA and Root Cause Analysis  streak=0 status=in_progress elo=1454 next_review=2026-08-14
    Section 3: Documentation Practices       streak=0 status=in_progress elo=1454 next_review=2026-08-14
```

Two things worth pointing at in a review:

- **Elo separates the sections** from one attempt: 1545 for the passed section, ~1454 for the
  failed ones, from a common 1500 start.
- **FSRS schedules failed material sooner** — 14 Aug vs 16 Aug — with no special-casing. This is
  the spaced-repetition layer doing its own job.

### Step 8: weakness analysis with evidence

```text
[8] Adaptive analysis - which sections are weak, and why
    >> Section 3: Documentation Practices
         priority : HIGH  (scheduled for later)
         measured : adaptive score 0.0% | lifetime 0.0% | 0/3 correct
         evidence : 0.0% accuracy (0/3 correct) - below the 60% weakness threshold.
    >> Section 2: CAPA and Root Cause Analysis
         priority : HIGH  (scheduled for later)
         measured : adaptive score 0.0% | lifetime 0.0% | 0/3 correct
         evidence : 0.0% accuracy (0/3 correct) - below the 60% weakness threshold.
       Section 1: Good Manufacturing Practice
         priority : LOW  (scheduled for later)
         measured : adaptive score 100.0% | lifetime 100.0% | 3/3 correct
         evidence : 100.0% accuracy (3/3 correct) - performing well, 1 of 3 passing assessments toward mastery.
```

`>>` marks selection. Three things are reported per section, deliberately:

- **adaptive score** — the recency-weighted figure the priority was decided on
- **lifetime** — the honest long-run average
- **(scheduled for later / available now)** — FSRS due-ness, which is a *separate* verdict from
  priority. A section can be HIGH and not yet due; the interface says so rather than offering a
  quiz that does not exist.

### Steps 9–11: targeted retraining and improvement

```text
[9] Adaptive retraining selection
    (schedule fast-forwarded so the retest is due now - state unchanged)
    Summary: 2 weak section(s): Section 3: Documentation Practices, Section 2: CAPA…
    6 question(s) selected for targeted retraining.
    Excluded (already strong): Section 1: Good Manufacturing Practice

[10] Learner retakes the targeted retraining quiz
    Retraining covers: Section 3: Documentation Practices, Section 2: CAPA and Root Cause Analysis
    Retake 1: scored 100.0% (6/6 correct)
    Retake 2: scored 100.0% (6/6 correct)
    Retake 3: scored 100.0% (6/6 correct)

[11] Mastery re-evaluated after retraining - measured learning gain
    Section 1: Good Manufacturing Practice  score=100.0% lifetime=100.0% status=in_progress priority=low
    Section 3: Documentation Practices      score=87.9%  lifetime=75.0%  status=mastered    priority=none
        -> improved: 50.0% -> 100.0% (+50.0 points)
    Section 2: CAPA and Root Cause Analysis score=87.9%  lifetime=75.0%  status=mastered    priority=none
        -> improved: 50.0% -> 100.0% (+50.0 points)
    No further retraining scheduled. 2 of 3 section(s) are fully mastered; the rest are
    performing above the pass mark.
```

**6 questions, not 9.** The learner is not dragged back through material they demonstrated they
knew — GMP is excluded.

**The +50 points is the headline.** It is computed from stored answers (oldest half vs newest
half of that section's history), not fabricated: both weak sections went 50% → 100%. Note also
that the adaptive score (87.9%) sits above lifetime (75.0%) — recency weighting recognising the
improvement, which is the whole point of the metric.

### Step 12: audit

```text
[12] Audit trail for this demo
    12:26:16 SOP Processed by system
    12:26:31 Questions Generated by demo_sme
    12:26:31 Question Approved by demo_sme   (×9)
    (11 audit entries for this SOP in total)
```

---

## About the fast-forward

Step 9 moves `next_eligible_at` backwards so the retest is due immediately. FSRS legitimately
schedules the next review ~1 day out, which a live demo cannot wait for.

**Only the schedule is touched.** Accuracy, streaks, Elo, FSRS stability and every selection
decision are untouched and come from the real engine. The line is printed in the output so it is
never mistaken for a result.

---

## The same state in the UI

`--stop-after-analysis` leaves the learner in the weak state. Log in as
**demo_learner / demo12345** → **My Learning Path**.

**State 1 — weak, but FSRS has not yet scheduled the retest.** Both figures agree with the
assignment engine, which offers nothing:

```text
Recommended Next
Selected by the adaptive engine · available now

  Nothing is due right now. The sections below are flagged for review and are scheduled —
  spaced repetition holds material back until revisiting it is most effective.

Scheduled for Later
Flagged as weak, not yet due for review
  SOP-DEMO · Section 3: Documentation Practices
    Due Aug 14 — 0.0% accuracy (0/3 correct) - below the 60% weakness threshold.
  SOP-DEMO · Section 2: CAPA and Root Cause Analysis
    Due Aug 14 — 0.0% accuracy (0/3 correct) - below the 60% weakness threshold.

SOP-DEMO — Quality Management Essentials · 2 section(s) need review · 0 available now

⚠ Section 3: Documentation Practices  [Needs Review] [Priority: HIGH] [Scheduled for Aug 14]
   Adaptive score: 0%   Lifetime: 0%   Recent 3: 0%   Answered: 0/3   Ability: 1455  Memory: ~0.4d

⚠ Section 2: CAPA and Root Cause Analysis [Needs Review] [Priority: HIGH] [Scheduled for Aug 14]
   Adaptive score: 0%   Lifetime: 0%   Recent 3: 0%   Answered: 0/3   Ability: 1454  Memory: ~0.4d

✓ Section 1: Good Manufacturing Practice  [Strong]
   Adaptive score: 100%  Lifetime: 100%  Recent 3: 100%  Answered: 3/3  Ability: 1545 Memory: ~3.2d
```

**State 2 — once the schedule is due**, the same sections move up and become actionable:

```text
Recommended Next
Selected by the adaptive engine · available now
  SOP-DEMO · Section 3: Documentation Practices
  SOP-DEMO · Section 2: CAPA and Root Cause Analysis

SOP-DEMO — Quality Management Essentials · 2 section(s) need review · 2 available now
⚠ Section 3: Documentation Practices  [Needs Review] [Priority: HIGH] [Available now]
```

…and **Learner Quiz** then shows *"Continue Assigned Retraining — SOP-DEMO"*.

Both states verified in the running application, not mocked up. The point to make in a review is
that the learner is **never** shown a recommendation the quiz screen cannot fulfil: adaptive
priority says *what*, FSRS says *when*, and the interface reports both.

---

## Reviewer provenance

Log in as **demo_sme / demo12345** → **Question Review**. Each question exposes
*View source text (heading chunking)*:

> Records must be legible, contemporaneous, original, accurate and attributable.
> Corrections must be made with a single line strike-through, initialled and dated, so that the
> original entry remains readable. Backdating any record is prohibited without exception.

against the question *"What is the consequence of backdating a record?"* — so a reviewer can
answer "where did this come from?" without leaving the screen.

---

## Suggested review walkthrough (~5 minutes)

1. `uv run python manage.py demo_adaptive --stop-after-analysis` — steps 1–8 on screen.
2. Log in as **demo_sme** → Question Review → expand *View source text* — grounding and
   e-signature.
3. Log in as **demo_learner** → **My Learning Path** — two HIGH sections, one Strong, each with
   its numeric justification.
4. `uv run python manage.py demo_adaptive` — full loop, ending with weak sections mastered.
5. If asked how it is verified: `uv run python manage.py test attempts` → 63 tests.

---

## What the demo does not show

- SOP versioning — reprocessing a revised SOP is **blocked** when approved questions exist.
- Training assignment/completion — the system tracks mastery, not obligation.
- Celery in non-eager mode — the demo runs tasks inline.
- Offline fallback — this run used a live API key; without one, generation falls back to the
  deterministic offline generator and the flow is otherwise identical.
