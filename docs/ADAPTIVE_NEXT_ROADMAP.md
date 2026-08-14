# Adaptive Learning — Next Roadmap

Prioritised by **review risk per hour of work**, not by technical interest. Nothing here has
been implemented; this is the recommendation for what to do after the audit is accepted.

---

## P0 — Fix immediately (undermines the adaptive claim)

### P0-1 · Reconcile adaptive priority with FSRS scheduling
**Problem (verified):** a HIGH-priority section in a not-yet-due SOP is recommended by
My Learning Path while `auto-assigned` offers nothing. A reviewer clicking through hits a dead
end.
**Minimal fix:** surface `is_due` and `next_eligible_at` on each recommended section and label
it — *"Recommended · scheduled for 23 Aug"* vs *"Available now"*. Optionally let HIGH-priority
sections override the schedule.
**Files:** `attempts/views.py::learning_path`, `App.jsx::LearningPath`.
**Effort:** 1–2 h · **Review value:** very high · **Risk:** low — presentation-layer only.

### P0-2 · Recency-weighted accuracy
**Problem (verified):** 0/5 then 5/5 ⇒ lifetime 50% ⇒ **HIGH**, while the UI displays
"Recent: 100%" beside it. The screen contradicts itself, and a learner cannot shed a weak label
promptly.
**Minimal fix:** exponentially weight answers by recency (e.g. half-life of 10 answers) inside
`_classify()`, or use `max(lifetime, recent)` when recent is above threshold with ≥3 samples.
Keep both figures displayed.
**Files:** `attempts/adaptive.py` only.
**Effort:** 2–3 h incl. tests · **Review value:** very high · **Risk:** medium — changes
selection behaviour; existing scenario tests must be re-run and reasoned about, not forced green.

### P0-3 · Minimum-sample confidence
**Problem (verified):** 1/1 and 50/50 are treated identically.
**Minimal fix:** require `answered >= 3` before a section can be classified LOW/NONE on accuracy
alone; below that, cap at MEDIUM with reason *"only 1 answer so far — not enough evidence"*.
Wilson lower bound is the fuller answer if time allows.
**Files:** `attempts/adaptive.py`.
**Effort:** 1–2 h · **Review value:** high · **Risk:** low.

### P0-4 · Test that generation actually links `source_chunk`
**Problem:** the FK the entire adaptive claim depends on has **no test**. Every adaptive test
sets it by hand in fixtures. It could break and the suite would stay green.
**Fix:** one test asserting `generate_quiz_task` produces questions with `source_chunk`
populated, using the mocked provider already available.
**Effort:** 30 min · **Review value:** moderate (high engineering value) · **Risk:** none.

---

## P1 — Complete missing core behaviour

### P1-1 · Server-side quiz session
**Problem:** no record of which questions were *offered*; targeting is honoured by the browser;
`submit()` accepts arbitrary question ids. "Start Quiz" silently bypasses adaptation.
**Fix:** persist the offered question set on `QuizAttempt` (a `QuizAttemptQuestion` join or a
JSON id list) at creation; validate submissions against it.
**Effort:** 4–6 h + migration · **Review value:** high · **Risk:** medium — touches the
submission path; needs care around the existing atomic claim.

### P1-2 · SOP version lifecycle
**Problem:** reprocessing is blocked, so revising a procedure has no workflow.
**Fix:** `SOPVersion` entity; questions and mastery bind to a version; new version supersedes
rather than deletes.
**Effort:** 1–2 days + data migration · **Review value:** high · **Risk:** high.
**Do not start before the review.**

### P1-3 · Difficulty-aware priority
Elo already weights the pass signal; it does not weight priority, so an easy miss ≡ a hard miss.
**Effort:** 2 h · **Risk:** low.

### P1-4 · Per-section scheduling actually consumed
`ChunkMastery.next_eligible_at` is computed and never read. Either use it for section-level
due-ness or document why only `TopicMastery` gates. **Effort:** 2–3 h.

### P1-5 · Question revision history
Reject → edit → re-approve destroys the superseded wording. **Effort:** 4 h + migration.

---

## P2 — Fast improvements with real demo value

| # | Improvement | Benefit | Complexity | Review value | Risk | Time |
|---|---|---|---|---|---|---|
| P2-1 | Show mastery trend (lifetime vs recent) as a sparkline/arrow | Makes improvement visible | Low | High | Low | 2 h |
| P2-2 | Semantic duplicate detection (embedding cosine or n-gram) | Kills "these two are the same question" | Medium | High | Low | 3–4 h |
| P2-3 | Warn when a SOP chunked as `fixed_length` | Flags semantically weak chunks | Low | Medium | Low | 1 h |
| P2-4 | Reviewer note on approve | Records *what* was checked | Low | Medium | Low | 2 h |
| P2-5 | Per-section learning-gain ("CAPA 0% → 75%") | Demonstrates the loop closing | Low | High | Low | 2 h |
| P2-6 | Seed the offline fallback's RNG | Makes fallback truly reproducible | Trivial | Low | None | 15 m |
| P2-7 | Positive-path PDF/DOCX extraction tests | Covers the formats customers use | Low | Low | None | 1 h |
| P2-8 | Two-learner divergence test | Proves personalisation on paper | Low | Medium | None | 1 h |

---

## P3 — Future research / advanced scope

- Entailment verification of the correct answer against its chunk (NLI model or LLM judge)
- Item Response Theory / CAT for within-quiz adaptation — needs calibrated item parameters
- Prerequisite graph between sections
- Concept clustering across SOPs (LECTOR-style)
- Per-user FSRS parameter optimisation — correctly declined at this data scale
- Tamper-evident audit (hash chain / WORM)
- Training assignment & qualification model

---

## Recommended implementation order

```text
P0-4  (30 min, zero risk — do first, it protects everything else)
  ↓
P0-1  (1–2 h — removes the live-demo dead end)
  ↓
P0-3  (1–2 h — cheap, closes a standard objection)
  ↓
P0-2  (2–3 h — highest review value, needs careful test reasoning)
  ↓
P2-1 + P2-5  (4 h — make the loop visible in the UI)
  ↓
[review]
  ↓
P1-1 → P1-2  (after the review, not before)
```

Total before the review: **roughly one focused day** for all four P0s plus two P2s. That closes
four of the five questions most likely to hurt.
