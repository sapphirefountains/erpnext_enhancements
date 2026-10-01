# Design Review

Concept reviews inside ERPNext: options drawn as real screens, ranked by the team, with notes
pinned to individual parts and decisions promoted into the Enhancement Request pipeline.
WI-079 slice 5; the decisions are in [ADR 0016 §2](../../decisions/adr/0016-every-source-files-an-enhancement-request.md).

It replaces the one-off claude.ai artifact ballots used for the Training, Desk, UI, Estimating
and Billing, and Email and Print reviews in September 2026. Those kept their votes and notes
outside ERPNext and needed proxy voting because the company has no Claude accounts. Here, everyone
who votes signs in to the Desk, and there is no proxy voting.

## How a review runs

1. **Import.** A System Manager uploads a bundle (below) from the Review Room or the Design Review
   form. The review starts in **Draft**.
2. **Set up.** On the Design Review form, add the participants (System Users only) and move the
   workflow to **Open** (`Open for Review`).
3. **Review.** Participants open `/desk/review-room`. Per track they rank every option; per screen
   they give a Yes, Maybe or No; on any part of any screen they write a note, optionally naming
   the Employee who raised it in the meeting.
4. **Decide.** The System Manager closes the review (`Close Review`), accepts or rejects notes,
   records Decisions (which option, what was decided, which accepted notes it carries) and moves
   the review to **Decided**.
5. **Promote.** A human System Manager promotes a Decision. That files an Enhancement Request with
   `source = Design Review`, already **Approved**, and queues its breakdown. The request's
   description and its Claude Code brief carry the notes by element code.

## Element codes

A part is addressed as `<option>-<screen>-E<nn>`, for example `L3-S04-E05`: option L3, screen
S04, part 5. Part numbers belong to the screen within its track, so `L1-S04-E05` and `L3-S04-E05`
are the same part ("Video block") drawn by two options, which is what makes notes comparable.

Numbers are **append-only** (`codes.check_append_only`). A revision may add parts; it may never
renumber or rename one, and an import that tries is refused whole, before anything is written.

## Security

Concept screens are model-written HTML rendered inside the Desk's origin, where a System Manager's
session lives. Two independent layers:

- **On import**, `sanitize.py` keeps only an allowlist of tags and attributes. It drops every
  script, style element, frame, form, event handler and comment, turns every `href` into `#`, keeps
  `src` only as a `data:image/` URI, and drops any style attribute that could fetch.
- **On render**, each screen is drawn in `<iframe sandbox="allow-same-origin">`, never with
  `allow-scripts`. That pair would let a frame lift its own sandbox. The frame carries a
  `Content-Security-Policy` of `default-src 'none'`.

The WI-079 spike tested this in Chrome with real mouse input. An *unsanitized* hostile screen ran
no script, and the policy blocked both of its outbound requests before they were sent. The parent
page still measured every part for its pins and received clicks inside the frame.

Access has three gates. First, the DocPerms grant read only to `Desk User` (v16's automatic role
for System Users) and `System Manager`, so Website Users and Guests never reach anything. Second,
`permissions.py` limits every other System User to reviews they participate in, for lists and for
single documents. Third, `Design Vote`, `Design Verdict` and `Design Note` grant no create or write
to any role; only `api/design_review.py` writes them, after checking participation and that the
review is Open. Every write is stamped with the session user.

## Files

| File | What it does |
|---|---|
| `service.py` | Every read and write the Review Room performs, with the checks: System User, participant, Open, moderator, human promoter. |
| `importer.py` | Validates, sanitizes and writes a bundle: review, options, screens, append-only parts, and imported artifact-era ballots (counted apart, never as a participant's own). |
| `sanitize.py` | The allowlist HTML and CSS sanitizer. Stdlib only. |
| `codes.py` | Element code format and the append-only rule. Stdlib only. |
| `tally.py` | Borda count (first of N scores N), ranking validation, verdict counts. Stdlib only. |
| `authority.py` | Service accounts (`triton@`, `mdm@`, `Administrator`, `Guest`) and the "human System Manager" test promotion and `EnhancementRequest.validate` both use. |
| `permissions.py` | `permission_query_conditions` and `has_permission` hooks, registered in `hooks.py`. v16 reads a `None` from a `has_permission` hook as a denial, so these return `True` when they have no objection. |
| `page/review_room/` | The Review Room Desk page, `/desk/review-room`. Every view is a route, so Back and Forward work. |
| `doctype/` | Design Review (+ Participant, Track), Option, Screen, Part, Note, Vote, Verdict, Decision. |
| `workspace/design_reviews/` | The Desk workspace: the Review Room, reviews, decisions. |

The lifecycle is the **Design Review Lifecycle** Workflow (`fixtures/workflow.json`) on the
review's own `status` field. Every transition is a System Manager's action, which is the case ADR
0016 adopts a Workflow for, unlike the Enhancement Request's machine states.

## The bundle format

One JSON document per review, `"format": "sapphire-design-review/1"`. The fields are documented
in `importer.py`'s module docstring. Briefly: `tracks` (each with stage `groups` and a `votable`
flag), `options`, `screens` (track, option, screen code, frame `desk`, `phone` or `board`, size,
HTML), `parts` (`{"learner:S04": [[1, "Top bar"], ...]}`), a `stylesheet`, the click-through
`flow` rules, and optional `ballots` from an artifact-era review.

`scripts/design_review/` holds the exporter that turns the claude.ai Concept Viewer's generator
into a bundle, and the browser test that drives the real Review Room against one.

## Tests

- `tests/test_design_review_sanitize.py`: the hostile-HTML fixture loses everything dangerous,
  and the concepts keep what they need.
- `tests/test_design_review_codes.py`: append-only codes, the tally, who may promote.
- `tests/test_design_review_access.py` (frappe stub, own CI step): the read and write gates,
  session-user stamping, and promotion through `file_request`.
- `tests/test_feedback_states.py`: the Enhancement Request insert rule.
- `tests/test_feedback_endpoint_surface.py` scans this module: nothing here creates a Task.
