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

Grading is entirely server-side. The learner-facing payload is materialized at
publish into `Training Lesson.published_content_json` with `is_correct` stripped,
and the key lives in `answer_key_json` at **`permlevel: 1`**. Learner roles hold no
DocPerm at all on the content doctypes, so `/api/resource/Training Question` 403s
them regardless of what any endpoint does.

**The key is disclosed in exactly one place: `grade_quiz`, on every graded run.**
The review screen names the correct options (`per_question[].correct_option_keys`,
or `accepted_text` for Short Answer) and carries the explanation, whether the
learner passed, failed, or left the question blank. Nothing else discloses — the
drawn quiz, the public lesson, the checkpoints and the gates are all still
answer-free, and `test_training_grading` walks every one of them for the sentinels.

Two things stay behind the line even there, and `is_correct` /
`correct_text_answers` / the per-**option** explanations are treated as leak markers
by name for it: the raw child row, and the text saying why each *individual* wrong
option is wrong. A learner is told which option was right, never walked through the
others.

This is a product decision (v1.445.0), taken knowingly. The review screen is the
teaching moment — the one time somebody is looking at a wrong answer of their own
and asking why — so gating it behind "you passed" or "you have no attempts left"
sends people back into a retake no better informed. The cost is that a learner can
spend one of their attempts reading the answers and return with them. `best` keeps
the higher score, so that is a real hole; it was judged smaller than a crew member
who has failed a confined-space quiz three times and still does not know when a
space is permit-required. If that trade ever needs revisiting, the gate is one
condition in `grade_quiz` and the endpoint has `max_attempts` and the run count to
feed it.

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
- `quality_course_specs.py` — the four WI-075 Quality course drafts, as data.
- `technician_program/` — the ten Technician Program course drafts, one module file each,
  plus the badge and assignment tables the seeding patch reads.
- `setup.py` — starter Training Categories and badges (`after_migrate`, insert-only).
- `workspace/training/` — the desk workspace.

The learner runtime's front end lives in [`../public/js/training/`](../public/js/training/):
`player.js` (shell, routing, the twelve views), `video.js` (watch telemetry and
in-video checkpoints), `quiz.js`, `blocks.js` (one renderer per content block) and
`transport.js` — the HTTP surface, and the **one** place the endpoint names appear.
`transport.js` left `www/training.html` in v1.428.1, when the portal page stopped
being the only host; all five are bound by the same rule, asserted in
[`../tests/test_training_phase3_contracts.py`](../tests/test_training_phase3_contracts.py):
**no `frappe.*`, ever**. A learner may be a Website User with `desk_access = 0`, who
never loads the desk bundle — so `frappe.call`, `frappe.msgprint` and `__()` all work
perfectly while a developer tests logged in as themselves, and throw a
`ReferenceError` for every customer.

`desk_assets.js` is the exception and is **not** part of that set: it is desk-only by
definition (`TR.loadAssets`, the one versioned `/assets` loader the authoring canvas
and the learner Desk Page share), and it is imported by the global desk bundle rather
than by any page, because a Desk Page cannot load a helper before the helper exists.

The Course form script is [`../public/js/training/training_course.js`](../public/js/training/training_course.js),
wired via `doctype_js`.

## Where a learner takes a course

**`/desk/learn`** — a Desk Page ([`page/learn/`](page/learn/)) that mounts the *same*
`TR.Player` the portal used to. It is a host, not a second player: it builds the mount,
loads the runtime through `TR.loadAssets`, dials `get_learner_bootstrap` through the
shared transport, and constructs `TR.Player(rootEl, boot, transport)`. Every pixel below
the page head comes from the four player files.

It moved there in v1.429.0 for a measured reason. **All fifteen Training Learner holders
are System Users**, all 26 Training Assignments belong to System Users, and the only
Website Users on the site are service accounts — so the population `/training` was built
desk-free for was zero, while the people who did hold the role had no desk door at all and
reached their courses only through a link in an email. 20 of 26 assignments sat at
`Not Started`.

**The route is `learn`, and it cannot be `training`.** `frappe.router` resolves the first
path segment against `frappe.workspaces` *before* doctypes and before the page loader, and
discards every segment after it; `desk.js` keys that map by `slug(page.name)`, and the
Training **workspace** is named "Training". A page named `training` would never render —
and because `allowed_workspaces` is permission-filtered, that one URL would be the
workspace for the fifteen learners and the page for anybody who cannot see it. One URL,
two destinations, no error in either. [`../tests/test_workspaces.py`](../tests/test_workspaces.py)
fails the build on any Page docname that shadows a workspace slug.

Deep links work — `/desk/learn/<COURSE>/<LESSON>` — in both directions. The page passes
`history: false`, so the player never *reads* the address bar (the Desk router owns Back,
and the page answers it in `on_page_show`), and it passes a **router adapter**, through
which the player *writes* it. Reading and writing are two jobs; conflating them into one
flag is how the Desk would have ended up answering browser Back by running
`queryParam("course")` against a route that has no query string. The preview harness passes
`history: false` and no adapter, which is the third arrangement of the same two switches.

`/training` is still a route and always will be: six senders have emailed it since
v1.208.0 and those messages are still in inboxes. It redirects (`www/training.py`), and
renders a sentence for anyone without desk access rather than bouncing them to a login
page for a Desk they cannot enter.

**Two workspaces, not one.** `workspace/training/` is the authoring and reporting console;
`workspace/my_training/` is the learner's. The split is not cosmetic: `training.json`
carries `roles: []`, which does **not** mean "nobody" — it means no restriction beyond the
module gate, and every learner holds read DocPerms on fourteen Training doctypes. So the
authoring console had been sitting in all fifteen sidebars with no way to start a course
from it.

**`/desk/training-insights`** ([`page/training_insights/`](page/training_insights/)) is the
manager's console, and its numbers are clickable through to the lists they count.

### The rail

Both Desk pages are two-column, and the left column is the same nav on each:
[`../public/js/training/desk_nav.js`](../public/js/training/desk_nav.js) +
[`../public/css/training/desk_nav.css`](../public/css/training/desk_nav.css), mounted with
`TR.deskNav({page, active})` and loaded through `TR.loadAssets` on its own chain — chrome
and page fail independently, so a rail that will not load costs the sidebar and not the
lesson.

It carries **My Trainings** (the courses assigned to *you*, with due date and progress,
each opening at `/desk/learn/<COURSE>`), the learner views, and a role-gated **Manage**
section. Three things about it are load-bearing:

- **It names no endpoint.** The list is the `assigned` array from the
  `get_learner_bootstrap` payload the player already fetched, handed over by `setLearner`.
  A `frappe.db.get_list` for open assignments would be a second, client-side answer to
  "which courses are mine" — and "open" and "overdue" are *predicates* defined in
  `api/training._open_assignments`, which is exactly the kind of filter that once turned
  "no expiry" into "expired" here. `/desk/training-insights` holds no learner payload, so
  its rail offers My Trainings as a **door** rather than a list; `null` and `[]` are
  deliberately different states.
- **Two gates, because there are two questions.** A *section* is drawn on role —
  `MANAGER_ROLES` must equal `training-insights.json`'s roles and `AUTHOR_ROLES`
  `training-canvas.json`'s, asserted in
  [`../tests/test_training_desk_nav.py`](../tests/test_training_desk_nav.py) — and a learner
  gets no Manage heading at all rather than an empty one. But every *destination* is then
  checked against what this person can actually open: documents through
  `frappe.model.can_read`, Pages through `frappe.boot.allowed_pages`, which desk.js builds
  from the server's own permission-filtered `page_info`.

  Role alone is wrong in **both** directions, and the hole was real: `HR Manager` is in
  `MANAGER_ROLES` because it is on `training-insights.json`, but it is *not* on
  `learn.json` — so somebody holding HR Manager and nothing else could open the dashboard
  and be offered three links into the learner page, all of which answer "Not permitted".
  Meanwhile a Training Learner holds read DocPerms on fourteen Training doctypes and must
  still see no Manage section. A link offered to somebody the Page refuses reads as the
  feature being broken rather than as not being theirs.
- **It stacks under 992px.** frappe lays `.layout-main` out as `display: flex;
  flex-direction: row` at *every* width — it does not stack on its own — so without the
  media block the rail would sit beside the player on a phone, which is the device
  `player.css` says the learner surface was built for. Below that breakpoint the rail is a
  closed `<details>` costing one line, and its `open` follows the viewport rather than
  remembering a choice.

  Two details in that block are load-bearing and neither is obvious. The stylesheet must
  say **`991.98px`**, frappe's own `media-breakpoint-down` value, against the script's
  `min-width: 992px`: written as `991` the pair leaves a gap at every fractional width
  between them — which browser zoom produces routinely — and in it the rail is an empty
  232px column whose only control is still hidden by the desktop rule. And the block must
  reset **`align-self: stretch`**, because `align-self` is a *cross*-axis property: the
  desktop rule sets `flex-start` to stop the rail matching the height of a long lesson, and
  once the container turns to `column` that same declaration stops it matching the *width*
  of the page, shrink-to-fitting to about 100px.

The Desk's own left sidebar could not do this job: it lists **workspaces**, so it can offer
"My Training" as a destination and can never show the three courses one person owes.

### The five CSS prefixes

`tr-` is the learner render ([`../public/css/training/player.css`](../public/css/training/player.css)),
`tc-` the authoring canvas, `tl-` the learner Desk host, `ti-` the insights page, `tn-` the
shared rail. One grep trap in that last one: an unanchored search for `tn-` also matches
every Bootstrap `btn-` in the repo, so anchor on `tn-` or on the leading dot.
**`--tr-*` is declared in `player.css` and nowhere else** (plus `quiz.js`'s injected
fallback sheet), which is what lets the canvas inherit a palette fix for free — and is
pinned by [`../tests/test_training_desk_theme.py`](../tests/test_training_desk_theme.py).
The palette is switched three ways, because the two hosts disagree about what "dark" means:
a website page carries no `data-theme` and follows `prefers-color-scheme`, while the Desk
always stamps `data-theme`, having already resolved its own "automatic" mode.

Endpoints live in [`../api/training_author.py`](../api/README.md) (authoring,
publishing, assignment), [`../api/training.py`](../api/README.md) (the learner
runtime), [`../api/training_ai.py`](../api/README.md) (quiz and checkpoint
drafting) and [`../api/training_course_authoring.py`](../api/README.md) (building a
whole course from an AI-drafted Course Spec — see **Drafting a whole course with
AI** below). All four phases are built and merged.

**"Built and merged" was doing a lot of work in that sentence until v1.334.0.**
Six whitelisted functions across [`signoff.py`](signoff.py) and
[`portal.py`](portal.py) shipped complete in v1.215.0 with **no caller of any
kind** — no supervisor was ever notified, `Training Assignment.status` was never
set to `Awaiting Sign-off` by any code path, and a client contact could only be
put on the portal by building the User by hand. `tests/test_training_endpoint_surface.py`
now fails the build for any whitelisted function in either module that nothing
calls, and it walks the AST rather than grepping, so a comment *about* an
endpoint does not count as a caller.

## Authoring a course

Open a Training Course and press **Edit Visually**, or go straight to
`/app/training-canvas?course=TRN-CRS-00001`. That is the authoring surface.

The classic builder is **gone**. It was retired in stages, and the order mattered:

| | |
|---|---|
| R1 (v1.416.0) | Removed its buttons. Nothing in the app linked to it. |
| v1.417.0 | Ported the last capability that existed only there — **registering a video from Drive**. |
| R2 (v1.418.0) | A Training Settings flag, shipped off. |
| R3 (v1.422.0) | Deleted the page, the flag, and the 50-test suite that guarded its internals. |

R2 shipped a switch that was never thrown, on purpose: until v1.417.0 landed, ticking it
would have removed the only way to register a video from Drive. That is why the port came
first and the flag second.

The **API** the page used is untouched — `register_video_asset`, `retry_video_copy`,
`_builder_video_assets` and `_probe_drive_video` all stay, because the canvas calls them.
Deleting a page is not deleting an API.

Triton authoring is reachable from the **Training Course form** only; the canvas has no
Triton button.

**Authoring only ever edits an open draft.** Publishing turns the draft into the
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

### Editing on the canvas (WYSIWYG)

`/app/training-canvas?course=…` ([`page/training_canvas/`](page/training_canvas/)) is a
**full-bleed WYSIWYG builder** alongside the classic one. It renders every block with the
learner's own renderer (`public/js/training/blocks.js` → `TR.renderBlock`) and edits it
**on that render** — the thing you edit is the thing a learner sees, in the learner
stylesheet (`player.css`, Aurora). It reuses the exact data path: `get_builder_bootstrap`
to load, `save_draft_version` to autosave, the version's `modified` as the optimistic
lock, whole block table sent with every `block_key` carried.

A left **rail** lists lessons grouped by chapter and adds / reorders (drag) / deletes them; a
**⚙ Lesson** panel edits the lesson settings (summary, chapter, estimated minutes, learner
questions, work-submission gate, and the end-of-lesson quiz settings); and the draft
**lifecycle** — new draft version, submit for review, publish (with the full `change_type`
strings the DocType stores) — lives in the page menu. A new lesson carries a `temp_id` the save
maps back to the server-minted name.

It authors content completely: **Rich Text** and **Callout** are edited in place with a
formatting toolbar (bold, italic, headings, lists, link); **Checklist / Flashcards /
Accordion** have inline structured editors beside their live preview; **External Embed**
takes a URL. Blocks can be **added** (a `+` between blocks opens a type menu), **reordered**
and **removed** on the canvas, and each has a **settings** row (caption, "required to
finish", and Callout tone; headings are edited on the render itself). **Media** is authored
here too: **Image / PDF / Downloadable File** attach a private file (the classic builder's
`/api/method/upload_file` idiom) and preview it; **Video** picks a registered Training Video
Asset with poster and coverage gate; **Image Hotspots** attaches a diagram and places pins.
Every one of the twelve block types can be added from the `+` menu. **In-video checkpoints**
are placed here too, on a timeline under the video block, with a pin inspector for the
question and its options (v1.413.0) — and a draft can be **previewed as a learner** through
the real player (v1.415.0). The preview's quiz is the **draft's own**, drawn server-side by
`grading.draw_from_quiz` and graded against the draft's key (v1.445.0); before that,
`startQuiz`/`submitQuiz` were the two transport methods that never switched out of the
canned fixture, so every previewed course asked the same three questions about draining a
basin and marked them against an answer key belonging to a different course.

**Video is authored end to end here as of v1.417.0.** **Add a video from Drive…** registers a
Training Video Asset through `register_video_asset`, which **probes** the real length from
`videoMediaMetadata.durationMillis` — the denominator watch coverage is measured against. A
panel under the block then says what is wrong with the asset: a failed Drive→GCS copy with
its error and a **Retry the copy** button (Drive answers 404 for a file that is merely
*unshared*, so that case is translated rather than shown raw), a copy that has not run yet,
and — the quiet one — a duration that was typed rather than probed.

That last warning sits directly under the Coverage field on purpose. A `Manual` duration makes
`evaluate_gates` **waive** the coverage gate entirely, so without it the number an author just
typed reads as a setting that is being applied when it is not.

Making the record by hand in the Desk is **not** an equivalent and never was: `duration_source`
is `read_only`, so a hand-made row cannot be corrected to `Manual` afterwards — and until
v1.416.0 it also carried `"default": "Probed"`, which made `grading._duration_is_verified`
*enforce* the gate against a number nobody measured. The design is to waive on an unverified
duration rather than run on a guess; that default silently inverted it.

One classic-only convenience is **not** ported: `upload_video`, which attaches a raw file for a
local preview. Its own message said a Training Video Asset still has to be registered for
coverage gating, so it was never the authoritative path — and the authoritative path is here.

**It forced a real round-trip fix that also helped the classic builder.**
`get_builder_bootstrap` returned each block's edit shape but *omitted* `data` (the
interactive list JSON) and `callout_tone`, while a save re-sends the whole block table by
position (`_apply_blocks`). So after a reload an interactive block loaded with neither,
and the next save blanked its stored list. `_builder_lesson` now returns both;
[`tests/test_training_canvas.py`](../tests/test_training_canvas.py) guards the round-trip
and the canvas's use of the real endpoints.

### Drafting a whole course with AI

On a Training Course form, authors get a **Draft a course with Triton AI** button
(the Triton assistant, opened via the global widget's `window.SapphireTriton.ask`).
Describe the course; Triton drafts it and ERPNext builds it. The split is the safety
story, and it is deliberate:

- **The model proposes a *Course Spec*, it never writes records.** `draft_course_spec`
  (an `assistant_tools/` read tool) turns a brief into a validated
  [`course_spec.py`](course_spec.py) — a course, lessons, blocks and quizzes — using
  ERPNext's own Vertex client, and returns a preview + a one-hour token. The spec's
  option bounds are imported from the Training Question controller (a spec that
  validates also survives `insert()`), and it only permits block types a model can
  honestly author — rich text, callouts, dividers, checklists, flashcards, accordions;
  nothing that needs an uploaded image, PDF or registered video.
- **ERPNext builds it deterministically.** `author_training_course` (a gated write
  tool) hands the spec to [`../api/training_course_authoring.py`](../api/README.md)
  `author_course_from_spec`, which maps it onto the **existing** engine
  (`create_draft_version` + `save_draft_version`) and nothing else — so an AI-seeded
  course comes out in the identical shape and flow, and never touches the published
  payloads (the answer-key-stays-server-side guarantee is inherited).
- **A guessed answer key never grades anyone unreviewed.** Every question the builder
  creates is stamped `ai_generated` with **no** reviewer — the pair
  `_unreviewed_ai_questions` blocks publication on — so the course lands as an
  unpublished **Draft** and cannot go live until a person opens the builder and accepts
  each question. Seeding is not shipping.

The whole feature lives in `erpnext_enhancements`; Triton is a pure MCP client that
discovers and calls the two tools (per its `docs/convergence.md`, one owner per
cross-repo overlap). Bench-free coverage is in
[`../tests/test_training_course_authoring.py`](../tests/test_training_course_authoring.py).

### The four Quality course drafts (WI-075)

[`quality_course_specs.py`](quality_course_specs.py) holds four Course Specs as data, seeded by
`patches/seed_quality_training_courses` **through the same `author_course_from_spec` path** — so
every rule above applies to them, including that each question is stamped `ai_generated` with no
reviewer and the publish gate holds. They were written after the features they teach shipped: a
course about a screen nobody can open is worthless, and one written from a plan teaches the plan.

Two things about them are worth not undoing.

**They are created `Draft`, and that is now load-bearing in a way the plan did not anticipate.**
`assignment.py` selects on `{"status": "Published", "weight": "Required", "auto_assign": 1}` —
all three. WI-075 assumed the module was dormant (`training_enabled = 0`), so four Required
courses would "assign nothing and mail nobody". Measured on prod 2026-09-14 that is **no longer
true**: `training_enabled`, `auto_assign_enabled` and `portal_enabled` are all 1, against 14 live
courses. Publishing one of these will assign and email real staff on the next sweep. Publishing is
the act of adopting a course.

**They teach how the software behaves and do not assert Sapphire policy.** How soon an inspection
must happen, who signs what off, what rework rate is acceptable — inventing those is the same
error as inventing a checklist. Where a course reaches that edge it says *your supervisor
decides*, and `tests/test_quality_training_courses.py` fails the build on any invented deadline of
the form "within N days".

### The ten Technician Program drafts

[`technician_program/`](technician_program/) holds ten more Course Specs, seeded by
`patches/seed_technician_training_program` **through the same `author_course_from_spec` path** — so
every rule above applies to them too, including that each question is stamped `ai_generated` with no
reviewer and the publish gate holds. Sapphire supplied a ten-module outline with seventy-two numbered
topics under it; one module is one course, one topic is one lesson, every lesson carries a quiz, and
each module has its own **Training Badge**.

They are Required, they carry an assignment rule aimed at the `Technician` job-family Position, and
they are created **Draft** — which is the only one of `assignment.py`'s three conditions this branch
does not supply. Publishing one is the act of adopting it.

Two things about them are different from the Quality four, and both are worth not undoing.

**A trade course cannot be written from the code, and that changes what it must refuse.** The Quality
drafts teach how this software behaves, which is knowable by reading it. These teach solvent welding,
chemical handling, confined space entry and anchor setting. The dangerous failure is no longer a
wrong claim about a screen — it is a **plausible number**: a cure time, a torque figure, a dose rate,
a service interval. A technician reads it here, does not check the label, and makes a joint that
fails under a slab in three years. So every such figure is replaced by the principle plus an
`ask_block` naming where the real one lives, and `tests/test_technician_training_program.py` fails
the build on an invented deadline *or* an invented frequency. Numbers that are genuinely universal —
water at 8.34 lb/gal, a Class A GFCI at 4–6 mA, 512 DMX channels to a universe, 19.5–23.5% oxygen —
are stated plainly, because hedging a fact teaches nothing either.

**The badges are inert, and it is worth following the chain rather than assuming it.**
`gamification_enabled` is **1** on production (measured 2026-09-15, along with `training_enabled = 1`,
`notifications_enabled = 1`, `auto_assign_enabled = 0`, `portal_enabled = 0` — note that
`auto_assign_enabled` read **1** a day earlier, so it is a checkbox somebody ticks, not a guardrail).
So awarding is live. The ten badges are nonetheless unearnable: a `Course Completed` badge is earned
when `gamification._badge_is_earned` finds its `criteria_course` among a learner's completions, a
completion is a submitted `Training Completion`, and nobody completes a course that was never
published. **One decision — adopting the course — turns on its assignment, its certificate and its
badge together.**

`Course Completed` is also the *only* criterion that means "this specific course"; a count-based one
would be satisfied by any other course on the site, which would turn "a badge for each module" into
"a badge for finishing anything". An unrecognised `criteria_type` awards nothing at all, so a drifted
literal would be ten badges nobody can ever earn with nothing on screen to say so — the test checks
the literal against `training_badge.json` rather than against a memory of it.

The artwork is ten SVGs in
[`../public/images/training/badges/`](../public/images/training/badges/), written straight into
`Training Badge.image` as an `/assets` path. They are **static app assets, not uploaded Files** —
that field is an `Attach Image` and stores a URL, so there is no `File` record to create and nothing
to re-parent. Two things follow. A raw `/assets` path is served **immutable for a year with no
content hash**, so a badge that is redrawn needs a *new filename* rather than an edit in place, or
the change never reaches a browser that already cached it. And an `Attach Image` pointing at nothing
renders as an empty box — no broken-image icon, no error, nothing in the log — so the test asserts
every one of the ten files is actually on disk, and that none of the SVGs carries a script, an
external reference or an embedded raster.

There is deliberately **no capstone badge** for finishing all ten. The mechanism that would carry it,
`Category Completed`, means *every published course in the category*, and these ten share their
categories with courses already on the site — so its meaning would change whenever somebody adds a
course. A criterion that drifts is worse than no badge.

### Reviewing AI-drafted questions

**`/desk/training-review`** ([`page/training_review/`](page/training_review/), served by
[`review.py`](review.py)) is where the publish gate is actually satisfied. Every question an AI path
creates is stamped `ai_generated` with **no** reviewer, and `_unreviewed_ai_questions` is what
`submit_for_review` and `publish_version` both refuse on. That gate has worked since Phase 4; what
never existed was anywhere to do the reviewing. Measured on production 2026-09-15 it was holding
**128 questions across all 11 Draft courses**, against 14 ever reviewed.

**The lesson is the unit of review.** A reviewer cannot answer *"could a learner have got this from
the lesson?"* from a list of questions, so `get_review_lesson` returns a lesson's content and its
pending questions in one reply and the page puts them side by side. Accept, edit-and-accept, or
reject, from the keyboard.

Three properties are load-bearing:

- **The reviewer comes from the session, never from the payload.** Until this page the only writer of
  `ai_reviewed_by` on a persisted question was the browser, through core `frappe.client.save` — so
  the field recording *who vouched for this answer key* was set by the client that wanted it set.
  `accept_question` has no reviewer parameter at all, which a test pins by asserting `TypeError`.
- **Reject means the question leaves the course**, because that is the only thing that clears the
  gate: there is nowhere on `Training Question` to record a verdict, so a rejected question left in
  the pool would block publication for ever and one marked reviewed would go live. The reason is
  written to the course version's timeline, which is the surviving record of a question about to be
  deleted. Rejecting a lesson's **last** question is refused unless the reviewer confirms — with
  `has_quiz` ticked and an empty pool, `TrainingLesson._validate_quiz` makes that lesson unsaveable
  by anybody.
- **There is no bulk accept and one must not be added.** A button that clears a course in one click
  turns the gate into theatre, and the gate is the only thing between a machine-written answer key
  and somebody's compliance record. The endpoint set is pinned by set equality so adding one fails
  the build first. Making the work fast is the goal; making it skippable is not.

### Batches (cohorts)

A **Training Batch** is a cohort — a set of learners moving through a set of courses
together (`Training Batch` + its `Training Batch Member` / `Training Batch Course`
child tables; WI-071 Phase A). It is deliberately thin: when a batch goes **Active**
it raises an assignment for every member × every course **through the existing
Training Assignment engine**, never a second assignment path — so a cohort cannot
drift out of step with the individual-assignment model.

The fan-out ([`batch.py`](batch.py) `sync_batch`, enqueued after commit by the
controller's `on_update`) is **idempotent**: every raise is guarded on open status,
so a re-save, a newly-added member, or a re-drive after a deploy FLUSHDB completes
what was missed rather than double-assigning. Due dates prefer the cohort's end
date, then its start date, then the course default. `enrolled_on` is stamped once
per member. A member's `Employee` is derived from their User (`user_id` match), not
fetched from the login id. The learner sees their active cohorts as a "Your cohorts"
strip on `/training` (`get_learner_bootstrap` → `batches`). Bench-free coverage:
[`../tests/test_training_batch.py`](../tests/test_training_batch.py).

### Live classes (WI-071 Phase B)

A **Training Live Class** is one scheduled synchronous session for a batch — a title,
`starts_on`, a duration, a host, and the **join link** members click. It is
deliberately thin: **the player never hosts video itself**, it hands the learner the
link. Members of the session's batch see upcoming and in-progress sessions as an
"Upcoming live sessions" strip on `/training` (`get_learner_bootstrap` → `live_classes`,
from `_learner_live_classes`, scoped strictly to the learner's own membership and to
`Scheduled`/`Live`). The join URL is **http(s)-only, enforced on save and at render** —
it is served as an anchor and clicked, so a `javascript:` scheme is refused both
places. The calendar-invite side (an ICS / Google Calendar event to members) is the
*native* half and is deliberately deferred: the Google Calendar accounts are disabled
on prod (Task→calendar sync was removed in v1.346.0). Bench-free coverage:
[`../tests/test_training_live_classes.py`](../tests/test_training_live_classes.py).

### Scheduled evaluations (WI-071 Phase C)

Some competencies are not proved by a quiz, so a course can require a supervisor
**sign-off** ([`signoff.py`](signoff.py)). A **Training Evaluation** *books* that
sign-off: an evaluator, a time, a learner, a place, and an auto-invite to both. The
learner sees booked evaluations on `/training` (`get_learner_bootstrap` →
`evaluations`). When the evaluator (or a Manager — never the learner) presses
**Record Outcome** on the evaluation form, [`evaluations.py`](evaluations.py)
`record_evaluation` **creates and submits a real `Training Signoff` through the
existing engine** (`signoff.record_signoff`), links it, and marks the evaluation
Completed — so the completion gate is satisfied by the *same* attestation a manual
sign-off produces. **There is no second way to sign anything off**: the evaluation is
the scheduling wrapper, the sign-off is the evidence. The calendar invite is the
deferred native half. Bench-free coverage:
[`../tests/test_training_evaluations.py`](../tests/test_training_evaluations.py).

### Announcements (WI-071 Phase D)

A **Training Announcement** is a short, plain-text notice an author or manager posts
to **all** learners, or scoped to one **course** or one **batch**, optionally pinned
and with an expiry. Learners see the relevant ones at the top of `/training`
(`get_learner_bootstrap` → `announcements`), pinned first. Relevance is the union of
**three separate scoped reads** — All Learners, the learner's own courses, their own
batches — so a course or batch announcement never leaks outside it, and only
`published`, unexpired ones are sent. The body is plain text, rendered with
`textContent` (never `innerHTML`). Deliberately small: no rich text, no threads; the
"email everyone it reaches" notification is a deferred follow-up. Bench-free coverage:
[`../tests/test_training_announcements.py`](../tests/test_training_announcements.py).

### Work submissions and grading (WI-071 Phase F)

Some lessons cannot be assessed by a quiz — the learner has to *do* something and hand
in the result. A **Training Lesson** with **Ask for a Work Submission**
(`requires_submission`) set shows the learner a submit box in the player; they attach
one file and an optional note, and a **Training Manager** grades it **Passed**, **Needs
Rework**, or claims it **Under Review** from a queue ([`submissions.py`](submissions.py)).
The learner sees their own submissions and any feedback on the `/training` home
(`get_learner_bootstrap` → `submissions`) and inline in the lesson. Grading is
manager-only and re-checked on the server; a `Needs Rework` verdict must carry feedback.

The one thing worth knowing before touching this: **the submitted file is private and
follows the record.** The player uploads it through Frappe's own `upload_file` (a
private `File`), then `submit_work` re-parents that file onto the new **Training
Submission** (`attached_to_doctype` / `attached_to_name`, `is_private=1`). Access is
then Frappe's standard private-file check — read permission on the submission — so the
learner opens their own file, a grader (a manager, unscoped in
[`permissions.py`](permissions.py)) opens all, and there is **no bespoke serving route**
like the author-uploaded lesson video needs (that is shipped *to* a learner who does not
own it; this is a file the learner already owns). A learner cannot attach a file they do
not own. Row scoping keys on the `user` column, same as every learner-owned doctype.
*Deferred:* completion-gating — a lesson **blocking** on a `Passed` submission before it
can finish; v1 collects, grades and shows status but does not yet stop completion.
Bench-free coverage: [`../tests/test_training_submissions.py`](../tests/test_training_submissions.py).

### Manager analytics (WI-071 Phase H)

A read-only dashboard at **`/desk/training-insights`**
([`page/training_insights/`](page/training_insights/)) for training managers: org-wide completion / overdue / awaiting-sign-off,
the Phase-F grading backlog, a by-course table with completion bars and average
score, active-cohort progress, and recent completions. One whitelisted read,
[`analytics.py`](analytics.py) `get_training_analytics`, is the single source; the
page renders the dict it returns and **computes nothing of its own** — "overdue" is a
predicate, and a second implementation in JavaScript would be a second chance to get it
wrong. It moved off `/training_analytics` in v1.431.0 (that route is now a redirect);
it was a website page whose entire audience was desk users. **Manager-only** — the {System Manager, Training Manager, HR Manager} set
that is unscoped in [`permissions.py`](permissions.py), because it reports across
every learner; a non-manager gets a 404. The rollup is Python over guarded `get_all`
reads, not SQL, on purpose: the *overdue* rule is a predicate (a `<`-on-a-nullable-date
filter silently sweeps in NULLs), a Cancelled assignment is out of the completion-rate
denominator, and average score comes from real completions. Bench-free coverage:
[`../tests/test_training_analytics.py`](../tests/test_training_analytics.py).

### Building a course with Triton (reuse the real assistant)

Course authoring goes through the **real** Triton assistant — the global desk chat
bubble — not a second widget. The **Training Course** form and the **builder**
(a "🔱 Triton" button in its top bar) both open it via the widget's sanctioned
public opener `window.SapphireTriton.ask(prompt, context)`, which pops the bubble
with a training prompt **prefilled** (never sent — the author edits first) and the
current course **pinned as context**. Triton then proposes a Course Spec and calls
the `author_training_course` FAC tool, which materialises an **unpublished,
review-gated** draft via `author_course_from_spec` and **never publishes** — the
one authoring path, behind the review gate.

An earlier iteration (v1.373.0) shipped a self-contained "Create a course with
Triton" trident on both the builder and the learner `/training` player, with its own
`draft_course_with_triton` endpoint. That duplicated the real bubble and was removed
in v1.374.0 (the learner player carries no Triton at all; `/training` is a learner
surface). Guard: [`../tests/test_training_triton_authoring.py`](../tests/test_training_triton_authoring.py).

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

That is why the role alone does not open `/desk/learn`: desk access comes from being a
System User, which every learner on this site already is by virtue of their other roles.
The role decides whether the page is *offered* — `learn.json`'s `roles` list — and the
endpoints re-check for themselves, because a Desk Page has no server controller and its
role list is show/hide, never a permission boundary.

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

**The learner runtime follows `training_enabled` alone.** It used to read
`training_enabled AND portal_enabled`, back when `/training` was the only learner surface
and "the portal" and "the runtime" were the same thing. They stopped being the same thing
when the runtime gained a Desk Page — and the field that would have taken it down is a
checkbox a Training Manager can untick, which was still labelled for the surface that no
longer renders. Every read endpoint answers a closed runtime with a *message* rather than
an exception, on purpose, so ticking it off would have shown all fifteen learners
"Training is not available yet" with nothing in the Error Log. One gate now:
`training_settings.runtime_ready()`.

`portal_enabled` keeps its real job — the customer-portal apparatus in
[`portal.py`](portal.py) — and is relabelled **Customer Portal Access** to say so. The
fieldname is unchanged: a Single stores one row per fieldname in `tabSingles`, so renaming
it is a data patch and not a JSON edit. `grant_portal_access` itself **refuses** as of
v1.429.2: it minted a login for a surface that no longer renders, and reinstating customer
training is a product decision — the apparatus under the refusal is intact for when it is
made.
