# 0015. An AI grades Short Answers unreviewed, and the learner disputes

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

A Short Answer quiz question was marked by an exact string comparison against a list of
accepted answers the author typed by hand. Verified against the code on 2026-09-19:
`training/grading.py::_matches_key` lowercases both sides, trims the ends and collapses
runs of internal whitespace, then asks whether the typed string is literally one of the
accepted ones. That is the entire tolerance. A trailing full stop, a hyphen, a definite
article, a singular where the key is plural, a unit written `3ppm` instead of `3 ppm`, or
a single mistyped letter all score zero.

Every one of those is a technician who knew the answer. The only defence was the author
listing every spelling in advance, which is the thing people are worst at predicting — and
on the visual canvas they could not even do that, because its accepted-answers control was
a single-line input while every reader of the field splits on newlines (fixed in the same
release).

This module already had a firm rule about AI near anything that decides a pass:
`draft_quiz_questions` may propose a question, and `training_author._unreviewed_ai_questions`
refuses to publish a course holding one no human has accepted. The tension is that the rule
was written for a model **inventing a question and its answer key** — deciding what
"correct" means — and the problem here is different: the author's accepted answers already
exist and are already approved.

Two further facts shaped the decision. The learner's raw typed text **is** stored per
attempt, so a verdict can be revisited. And nothing in the app recomputed a past attempt's
score, so any remedy had to be built rather than wired up.

## Decision

**An AI decides whether a Short Answer means the same as an answer the author accepted, with
no human sign-off in front of it.** The learner may dispute the verdict, and only a dispute
reaches a person.

Four constraints make that a narrower act than it sounds, and all four are load-bearing:

1. **The exact match runs first.** The model is consulted only on an answer the plain
   comparison has already rejected. An exact match is correct by definition and never
   reaches a model. So the AI can turn a wrong into a right and **cannot do the reverse**.
2. **The author's list remains the only standard.** The model is not asked what the right
   answer is; it is asked whether the learner's wording expresses one of the accepted ones.
   The system prompt says so in those words.
3. **Every failure falls back to the exact match.** Switch off, Vertex unreachable,
   unparseable reply, an answer too long to be a short answer — each returns no opinion and
   the comparison's verdict stands. A learner is never marked wrong *because* a model was
   unavailable, and never blocked by one mid-submission.
4. **The verdict is explained and contestable.** The AI's reasoning is shown to the learner
   next to a button that sends it to a Training Manager. Upholding re-marks the answer,
   re-scores the run, and re-drives the attempt through the ordinary completion path.

This is behind its own Training Settings switch (`ai_grade_short_answers`), separate from
`ai_assist_enabled`, and ships **off**.

## Consequences

**What it buys.** A correct answer in the wrong words stops being a failure. The author's job
becomes writing one good accepted answer rather than predicting twenty spellings. And the
app gains the two things it was missing underneath: a way to recompute a past attempt, and
the attempt reset that `start_quiz` has been promising learners since the module shipped
("A Training Manager can reset it for you") while no such function existed anywhere.

**What it costs, plainly.** A machine now decides part of whether somebody passed a
compliance course, and nobody checks the ones it gets right. The asymmetry in constraint 1
is what makes that tolerable rather than reckless — the failure mode is a learner passing on
a wording a human might have queried, not a learner failing on one — but it is a real change
in what a pass means, and it should be described that way to anyone relying on these
records.

A second cost is less obvious: because an outage silently reverts to strict matching, the
same words can be marked wrong on Monday and right on Tuesday. `ai_judged` on the answer row
is what lets anybody tell those cases apart afterwards, which is why it is recorded even
though it is redundant with `is_correct` at the moment of grading.

**Invariants a future contributor must preserve.** The ordering in
`grading._judge_text_answer` — exact match, then blank check, then the model — is the whole
safety argument, and reversing it is a two-line change that no other test would notice.
`tests/test_training_disputes.py` pins it by execution, with a judge stubbed to reject
everything, and was verified by reversing the ordering and watching five tests fail. The
fallback-to-comparison behaviour is pinned the same way. Do not make
`judge_short_answer` raise; do not let it be called before the exact match; do not let the
dispute flow build its own completion instead of re-driving `_evaluate_attempt`.

**What would make this worth revisiting.** If disputes are routinely upheld, the model is
too strict and the prompt or the model should change before the queue becomes the real
grader. If disputes are never raised at all, that is not evidence the AI is right — it is
more likely that learners cannot find the button, and the review screen should be checked
before concluding anything. And if the AI is ever asked to grade something the author has
*not* already answered — an essay, an open question with no key — this ADR does not cover
it, because constraint 2 would no longer hold.
