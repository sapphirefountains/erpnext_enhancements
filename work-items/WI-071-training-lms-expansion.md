# WI-071: Training LMS expansion — Frappe-LMS-parity features (program plan)
**Phase:** 5   **Type:** APP_CODE   **Size:** L (umbrella; each sub-phase is its own PR)
**Blocked by:** nothing (Training Phases 1–4 are live)   **Blocks:** nothing

## Why
Nik reviewed [Frappe LMS](https://github.com/frappe/lms) and asked for its feature set in our LMS.
Our training module already **exceeds** Frappe LMS on the things that matter to a compliance
programme: submittable content **versioning** (draft→published→retired with content hashes so a typo
fix does not invalidate completions), **in-video checkpoints**, **watch-coverage telemetry** (real
seconds watched; forward-seek earns nothing), **gamification** (points/badges/streaks/leaderboard),
the **auto-assignment / due-date / recertification / escalation** engine, and **supervisor sign-off**.
Frappe LMS is a broad *public-course marketplace*; ours is a sharper *internal compliance trainer*.

The genuine gaps, filtered for internal training, are the eight sub-phases below. This item is the
plan-of-record; each sub-phase ships as its own versioned PR and (where it adds real surface) its own
WI. Tracked on ERPNext prod under **TASK-2026-01900** (child of the Training Module task).

## Native-first check (per feature — this repo prefers native ERPNext over custom code)
- **Batches/cohorts** — no native fit; Training's assignment model is per-learner. APP_CODE: new
  `Training Batch` + `Training Batch Member`. Reuse the existing `Training Assignment` engine to
  fan a batch's course set out to members rather than inventing a second assignment path.
- **Live classes** — reuse the existing Google Calendar integration (`google_calendar/`, currently
  disabled on prod per [[google-calendar-accounts-disabled]] — re-enable or post an ICS/`Event`).
  APP_CODE is a thin `Training Live Class` record + a learner "upcoming" surface; the invite/calendar
  side is native.
- **Scheduled evaluations** — extend the **existing** `Training Signoff` rather than a new parallel
  concept: add a bookable slot + auto-invite around the sign-off we already have.
- **Announcements** — native ERPNext has no per-course notice board; small `Training Announcement`
  doctype + a strip on the learner home. Notifications ride the existing training notification stack.
- **Peer discussions** — evolve the **existing** author-only Q&A (`training/qa.py`,
  `Training Question Thread`) into learner-visible threads with replies; do not add a second forum.
- **Learner work submissions** — no native fit for graded practical evidence; new
  `Training Submission` (file + status + grade), a new assessment kind alongside quiz/checkpoint.
- **Course reviews & ratings** — small `Training Review` doctype; no native equivalent worth bending.
- **Analytics dashboard** — **native-first**: Frappe Dashboard + Number/Chart cards + Query Reports
  over the data the phases above produce; write custom only where a card cannot express it.

**Explicitly out of scope** (exist in Frappe LMS, do not fit internal compliance training, and are
**not** authorized by this item): payments/monetisation, job board, programming/code-exercise lessons,
SCORM import, and a native mobile app (the player is already mobile-first web).

## Sub-phases (dependency-ordered; A first)
- **A — Batches / cohorts** *(foundation for B/C/D)*. `Training Batch` (title, course set, start/end,
  status) + `Training Batch Member` (learner, enrolled_on). Manager creates a batch and enrols; the
  assignment engine raises each member's assignments for the batch's courses with the batch due date.
  Learner sees "my cohort" on the training home. Roles: Training Manager manages; Learner reads own.
- **B — Live classes** *(needs A)*. `Training Live Class` (batch, title, starts_on, duration,
  join_url) + a Google Calendar event / ICS invite to members + an "upcoming sessions" strip in the
  player home. The player learns a session's join link; it never hosts video itself.
- **C — Scheduled evaluations** *(needs A; extends sign-off)*. A bookable evaluation slot
  (evaluator, datetime, learner or batch) with an auto-invite, feeding the existing
  `Training Signoff` competency record — formalises today's manual sign-off.
- **D — Announcements** *(small)*. `Training Announcement` (scope: course or batch, body, pinned) +
  a home surface + optional notification. Author/Manager posts.
- **E — Peer discussions** *(independent; evolves Q&A)*. Turn the author-only Q&A into threaded,
  learner-visible discussion per lesson (and optionally per batch): a learner may reply, an author is
  marked. Keep the existing visibility gate (`training/qa.py`).
- **F — Learner work submissions + grading** *(independent)*. `Training Submission` (learner, lesson
  or a new "practical" block, file, submitted_on, status, grade, feedback). A block/lesson may require
  a graded submission to finish; a Manager grades from a queue. GCS/private-file handling like the
  PDF/video path.
- **G — Course reviews & ratings** *(independent, small)*. `Training Review` (learner, course,
  rating 1–5, comment, published). Shown on the course card / catalog; a learner reviews after
  completion. Moderatable.
- **H — Manager analytics dashboard** *(last; consumes A/F)*. A Frappe Dashboard: enrolments,
  completion rate, overdue count, per-course pass rate, cohort progress, average rating — Number and
  Chart cards over the training doctypes + a couple of Query Reports where a card cannot express it.

## Sequencing & delivery
A → then B/C/D (need A) and E/F/G (independent) in any order → H last. Each sub-phase is its own PR:
new doctype(s) in the `Training` module (per `tests/test_doctype_modules.py`), whitelisted endpoints
following `add-endpoint`, learner/manager UI, bench-free tests wired into CI (`run-tests`), the
mandatory version + CHANGELOG bump, and — where it adds a real surface — its own WI (WI-072+).
Every learner-facing addition stays inside the player's no-framework / server-graded / no-CDN
envelope and honours the class-contract test.

## Acceptance criteria (program)
- Each sub-phase merges independently, green on the bench-free suites, version + changelog bumped.
- No sub-phase weakens an existing guarantee: server-side grading, the watch-coverage gate, the
  sign-off gate, content versioning, or the customer-Website-User (desk_access=0) constraint.
- The out-of-scope list above is honoured; anything on it needs a new, separately justified WI.

## Rollback
Each sub-phase is additive (new doctypes + optional surfaces) and independently revertable. Batches
enrol *through* the existing assignment engine, so removing a batch leaves the raised assignments
intact and auditable rather than orphaning progress.

## Explicitly NOT in this work item
Payments/monetisation, job board, programming/code-exercise lessons, SCORM import, native mobile app,
and any change to the existing grading, coverage, sign-off, or versioning guarantees. Each sub-phase's
own detailed scope (fields, endpoints, gates) is settled in its own PR/WI, not pre-committed here.
