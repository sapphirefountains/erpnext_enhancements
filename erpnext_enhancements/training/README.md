# Training

An in-house training system: staff and customers work through mixed-media courses
(rich text, images, PDFs, video), and the system measures whether they actually
engaged rather than clicked through. Courses are **built in the UI** — anyone with
something to teach can author one without a deploy.

Built standalone. This site has neither `hrms` (no `Training Program` / `Training
Event` / `Training Result`) nor `lms`, so nothing here wraps an upstream app. Every
DocType is prefixed `Training ` and deliberately avoids the six hrms names, so
installing hrms later cannot collide.

> **Not to be confused with `Training Insight`** in [`../ai_governance/`](../ai_governance/README.md),
> which stores *AI* training data for the Triton assistant and has nothing to do
> with human training. It is deliberately not linked from this module's workspace.

## The shape of it

**Content is versioned; progress is not.** That split is the whole design.

- A **Training Course** is the stable identity — title, slug, audience, policy,
  completion gates. It never holds content.
- A **Training Course Version** holds the content and is **submittable**:
  `docstatus 0` = draft (authors edit), `1` = published (frozen), `2` = retired.
  Publishing *is* `submit()`, so a learner can only ever read a submitted version —
  "an author saved a half-finished edit into a live course" is structurally
  impossible, not merely discouraged.
- Publishing asks the author to classify the change: **Minor Edit** keeps existing
  completions valid; **Material Change** marks them `Superseded` and raises retake
  assignments. Every completion stores the `version_number` and a `content_hash`,
  so an audit can answer "what exactly did this person pass, in 2026?".

A version contains **Chapters** (grouping labels only) and **Lessons**. Lessons are
top-level and point back with `course_version` + `chapter_key` — Frappe has no
grandchild tables, so Chapter→Lesson cannot be child-of-child. Everything joins on
stable `*_key` values rather than `idx`, which is why reordering blocks never
orphans a learner's in-flight progress.

A lesson holds **Content Blocks** (one row per renderable thing, typed by
`block_type`), an optional quiz drawn from **Training Questions**, and — for video
blocks — **Training Checkpoints** attached by `(lesson, block_key)`.

## Course weight

Each course declares itself:

- **Required** — assignable, with due dates, recertification, overdue escalation.
- **Optional** — a self-serve library; progress and scores are still recorded.

The distinction drives everything downstream. Only Required courses participate in
auto-assignment and escalation.

## Measuring attention

Three mechanisms, each a per-course toggle:

1. **End-of-lesson quiz** — passing score, attempt limits, server-side shuffling of
   both question order and answer options.
2. **In-video checkpoints** — the video pauses at an author-set timestamp and asks
   a question before it resumes.
3. **Watch telemetry** — a coverage percentage built from the seconds genuinely
   watched. Forward-seeking earns nothing.

> **Be honest about the ceiling.** Coverage measures *"they spent the time"*, never
> *"they paid attention."* A learner can start a video and walk away. That is
> precisely why all three exist together, and why the compliance artefact is always
> coverage **plus** checkpoint accuracy **plus** quiz score — never coverage alone.
> Label it "watched" wherever it appears on screen. A manager should not discover
> this nuance during a disciplinary conversation.

Grading is entirely server-side. Correct answers never reach the browser: the
learner-facing payload is materialized at publish into
`Training Lesson.published_content_json` with `is_correct` stripped, and the key
lives in `answer_key_json` at **`permlevel: 1`**. Learner roles hold no DocPerm at
all on the content doctypes, so `/api/resource/Training Question` 403s them
regardless of what any endpoint does.

## Files

- `doctype/training_course/` — the stable course identity.
- `doctype/training_course_version/` — submittable content version; the publish
  state machine lives in its controller.
- `doctype/training_chapter/` — child of Course Version; grouping label only.
- `doctype/training_lesson/` — the unit of progress; holds blocks and the quiz pool.
- `doctype/training_content_block/` — child of Lesson; one row per renderable thing.
- `doctype/training_checkpoint/` — in-video questions, keyed by `(lesson, block_key)`.
- `doctype/training_question/` — one storage doctype for both bank and inline
  questions; "inline" is the `is_bank_question` flag, not a second shape.
- `doctype/training_answer_option/` — child, shared by Question and Checkpoint.
  Holds `is_correct`, and is never served to a learner.
- `doctype/training_quiz_question/` — child of Lesson; the quiz pool.
- `doctype/training_category/` — navigation + question-bank filtering.
- `doctype/training_audience_role/`, `doctype/training_audience_customer/` —
  children of Course; who can see it.
- `doctype/training_assignment_rule/` — child of Course; auto-assignment targeting.
- `doctype/training_assignment/` — one learner owes one course by one date.
- `doctype/training_video_asset/` — one row per video: Drive source, GCS object,
  probed duration, transcript.
- `doctype/training_settings/` — Single; the master switches.
- `assignment.py` — the rule engine and the Employee / User doc_events.
- `notifications.py` — assignment, due and escalation email (ported from
  `travel_management/notifications.py`).
- `permissions.py` — `permission_query_conditions` / `has_permission` for the
  learner-owned doctypes.
- `tasks.py` — the scheduled due-reminder and escalation jobs.
- `gcs_media.py` — signs short-lived playback URLs and copies video from Drive
  into the private bucket. See **Video** below.
- `drive_media.py` — the hourly health check behind every video asset: stats the
  GCS object, stamps `last_verified_on`, repairs `size_bytes` / `mime_type`, and
  is the only thing that ever sets `Missing`. `TrainingVideoAsset._derive_status`
  has always deferred to it by name; until v1.332.0 the module did not exist, so
  nothing moved an asset out of `Available` and a deleted video stayed green until
  a learner pressed play.
- `roles.py` — granting the Training Learner role durably. Read this before
  touching role assignment; see **Access** below.
- `setup.py` — starter Training Categories (`after_migrate`, insert-only).
- `workspace/training/` — the desk workspace.

The Course form script is [`../public/js/training/training_course.js`](../public/js/training/training_course.js),
wired via `doctype_js`.

Endpoints live in [`../api/training_author.py`](../api/README.md) (authoring,
publishing, assignment), [`../api/training.py`](../api/README.md) (the learner
runtime) and [`../api/training_ai.py`](../api/README.md) (quiz and checkpoint
drafting). All four phases are built and merged.

## Authoring a course

Open a Training Course and press **Open Builder**, or go straight to
`/app/training-builder?course=TRN-CRS-00001`.

**The builder only ever edits an open draft.** Publishing turns the draft into the
live version and leaves the course with none, so the next round of edits starts a
new draft — press **New Draft Version**. A new draft copies the live content and
keeps every lesson, block and checkpoint key, which is what lets a learner who is
part-way through stay exactly where they are across a minor edit.

Two consequences worth knowing before they surprise you:

- A published version cannot be edited in place, by design. Learners are reading
  it, and a completion records the `content_hash` of what was passed.
- `change_type` is the full string — `Minor Edit (keep completions)` or
  `Material Change (require retake)`. The parenthetical is part of the stored
  value, not a label; `publish_version` rejects anything else.

## Video

**Putting a real video in a lesson:**
[`docs/training-video-drive-runbook.md`](../../docs/training-video-drive-runbook.md).
Read it before the first one — the Drive file has to be readable by the *service
account* rather than by you, and registering the asset through the API instead of the
Desk form is what makes the watch-coverage gate measure a real duration.

Authors upload video to Google Drive; on publish it is copied into a private GCS
bucket and served to the player as a short-lived signed URL.

This indirection is not gold-plating. **A Drive preview iframe is cross-origin** —
no `timeupdate`, no `currentTime`, no `pause()` — so embedding from Drive kills
in-video checkpoints *and* coverage gating outright. A signed URL on a real
`<video>` element restores both, with correct HTTP Range/seek support, no bench
disk, no bench bandwidth, and identical behaviour for a customer who has no Google
account.

`Training Video Asset.duration_source` matters more than it looks: coverage divides
by duration, so a hand-typed 600 for a 900-second video passes an 80% gate on 53%
of a real watch. Duration is probed from Drive's `videoMediaMetadata.durationMillis`
and the field is read-only when probed; manual entry is allowed only after a probe
failure, and is flagged.

An **External Embed** block (Drive preview, unlisted YouTube) is still permitted for
low-stakes content, but the server refuses to apply a coverage gate to it and
stamps `gate_waived_reason` on the progress row. A compliance course cannot quietly
lose its teeth because someone picked the convenient block type.

### How the signing works, and why it is hand-rolled

`google-cloud-storage` is not a dependency and cannot be pip-installed on the
host — the same constraint that made `stripe_payments` talk to Stripe over plain
REST. But `google-auth` **is** already a dependency, and a service-account
credential built from it exposes an RSA signer. That is the only primitive a V4
signature needs; the rest is a documented string-to-sign. So
[`gcs_media.py`](gcs_media.py) assembles the canonical request by hand and signs
it with a library we already have. No new package, and the same shape as the
QuickBooks and Stripe clients.

The infrastructure is [`infra/storage.tf`](../../infra/storage.tf): one private
bucket (uniform access, public-access prevention *enforced*, no lifecycle
deletion) and one narrow `sa-training-media` service account with `objectAdmin`
on that bucket alone. It is gated behind `enable_training_media_bucket`, off by
default. The service-account key is deliberately **not** a Terraform resource —
`google_service_account_key` writes the private key in plaintext into the state
file, and that state lives in a bucket several people can read. It is created by
hand and pasted into Training Settings, which stores it encrypted.

Two properties worth knowing before you rely on this:

- **A signed URL cannot be revoked.** Once minted it works until it expires, even
  if the learner's access is removed a minute later. The 15-minute TTL is the
  mitigation, which is why it is a setting rather than a constant.
- **Signing must use UTC.** The timestamp and the date-scoped credential are both
  part of the signature, so signing against site-local time yields URLs that
  validate only when the site happens to be on UTC. `now_datetime()` is
  site-local — do not substitute it.

`test_training_gcs_media.py` rebuilds the string-to-sign independently from the
spec and compares, rather than asserting the implementation's own output. That
matters because a subtly wrong signature still produces a perfectly well-formed
URL whose only symptom is an opaque 403.

The full setup runbook lives on the ERPNext task **TASK-2026-01150**.

## Access

Three roles, seeded insert-only by
[`../patches/seed_training_roles.py`](../patches/seed_training_roles.py) — not by
`fixtures/role.json`, because fixtures import in alphabetical filename order and
`custom_docperm.json` lands first.

| Role | Desk access | |
|---|---|---|
| **Training Author** | yes | Create and edit draft versions. **Cannot publish.** |
| **Training Manager** | yes | Review, publish, assign, waive, revoke, report. |
| **Training Learner** | **no** | The runtime role — held by employees *and* by customer Website Users. |

**`Training Learner.desk_access` must stay 0.** Set it to 1 and every customer
contact becomes a System User, which moves the licensed-user count and the bill.
`tests/test_training_roles.py` pins it.

### Granting the role is not `add_roles`

`User.validate` calls `populate_role_profile_roles`, which — for any user holding
at least one Role Profile — rebuilds `roles` from the **union of those profiles**
on *every* save. Direct roles are dropped, not merged. So
`user.add_roles("Training Learner")` appears to work, survives until that user is
next saved for any reason, and then silently vanishes. On this site that is 11 of
15 active employees.

[`roles.py`](roles.py) has the two correct paths, and they must not be swapped:

- **Profiled user** → add the dedicated single-role `Training Learner` Role
  Profile as an *additional* profile.
- **Profile-less user** → grant directly, and **never** give them a profile.
  Setting one regenerates their `roles` from it and wipes `System Manager`,
  `PO Approver` and everything else. The four profile-less users here include the
  System Managers.

Both the seeding patch and the Employee `after_insert` hook go through that one
function, so the two cannot drift.

## The switch

**Training Settings** ships dormant: `training_enabled = 0`, `notifications_enabled
= 0`. Authoring works with the switches off; nothing is emailed and no rule
auto-assigns until they are turned on. Same staged-rollout contract as Travel
Management.
