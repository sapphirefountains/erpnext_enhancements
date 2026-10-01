# Design Review

Concept reviews in ERPNext. Each option is drawn as real screens, the team ranks the options,
notes are pinned to individual parts, and decisions are promoted into the Enhancement Request
pipeline. WI-079 slice 5; the decisions are in
[ADR 0016 §2](../../decisions/adr/0016-every-source-files-an-enhancement-request.md).

It replaces the one-off claude.ai artifact ballots used for five reviews in September 2026:
Training, Desk, UI, Estimating and Billing, and Email and Print. Those ballots kept their votes
and notes outside ERPNext, and needed proxy voting because the company has no Claude accounts.
Here everyone who votes signs in, and there is no proxy voting.

**The Review Room is its own website app at `/review`**, outside the Desk, with the Concept
Viewer's layout: a screen rail, desktop and phone frames side by side, a notes panel, and the
MARKUP, LINKS and NEXT controls. ERPNext is the backend: sign-in, the participant list, votes,
verdicts, notes, decisions and promotion. v1.570.0 had built it as a Desk page whose content was
stored in Long Text fields. Frappe v16 sanitizes Long Text on save, so every screen lost its CSS
`gap` and most lost their SVG geometry, and the Desk chrome fought the layout. v1.571.0 moved the
room out of the Desk and the content into a File.

## How a review runs

1. **Import.** A System Manager imports a bundle at `/review` (**IMPORT A REVIEW**), or an AI
   submits one through `submit_design_review` and a person confirms the card. The review starts
   in **Draft**.
2. **Set up.** Add the participants (System Users only) on the Design Review's Desk form
   (**PARTICIPANTS** on the review's page), then set the review **Open** at `/review`.
3. **Review.** Participants open `/review` (also listed in the Desk's Help menu). For each track
   they rank every option. For each screen they give a Yes, Maybe or No. On any part of any
   screen they can write a note, and can name the Employee who raised it in the meeting.
4. **Decide.** A System Manager sets the review **Closed**, accepts or rejects notes, and records
   Decisions: which option, what was decided, and which accepted notes it carries. Then they set
   the review **Decided**.
5. **Promote.** A human System Manager promotes a Decision. That files an Enhancement Request with
   `source = Design Review`, already **Approved**, and queues its breakdown. The request's
   description and its Claude Code brief carry the notes by element code.

Every view in the Review Room is a URL (`/review/<review>/<track>/<option>/<screen>`), so a link
can be shared, a refresh lands on the same screen, and Back and Forward step through the walk.

## The bundle format

One JSON document per review, `"format": "sapphire-design-review/1"`. It holds the tracks, the
options, the screens as HTML, the numbered parts, the click-through rules and, optionally,
artifact-era ballots. [`docs/design-review-bundle.md`](../../docs/design-review-bundle.md) is the
authoring guide. [`bundle.schema.json`](bundle.schema.json) is the machine-readable shape.
[`examples/minimal-bundle.json`](examples/minimal-bundle.json) is a complete small bundle and
the place to start.

A bundle may name a **kit** instead of shipping its CSS: `"kit": "sapphire-ux/1"` gives every
frame [`kit/sapphire_ux_1.css`](kit/sapphire_ux_1.css), the house classes the training concepts
were drawn with. Kits are append-only for the same reason codes are: imported reviews depend on
them.

## Element codes

A part is addressed as `<option>-<screen>-E<nn>`. For example, `L3-S04-E05` is option L3,
screen S04, part 5. Part numbers belong to the screen within its track, so `L1-S04-E05` and
`L3-S04-E05` are the same part ("Video block") drawn by two options. That is what makes notes
comparable.

Numbers are **append-only** (`codes.check_append_only`). A revision may add parts. It may never
renumber or rename one, and an import that tries is refused whole, before anything is written.

## Storage

A review's content is **one private JSON File** attached to the review (`content.py`), never
fields. A File is never passed through the save-time sanitizer, so the import's own sanitizer is
the only one. The File holds the bundle as validated and sanitized, without its ballots, and with
its parts merged across revisions. A new revision writes a new File and deletes the old one.
Reads are cached in redis by `content_hash`, which changes with every import, so a stale copy can
never be served. The content fields (`content_file`, `content_hash`, `revision`, ...) change only
by import; `service.validate_review` refuses any other edit.

## Security

Concept screens are model-written HTML shown to staff, on the same origin as their session. Two
independent layers protect it:

- **On import**, `sanitize.py` keeps only an allowlist of tags and attributes. It drops every
  script, style element, frame, form, event handler and comment. It turns every `href` into `#`,
  keeps `src` only as a `data:image/` URI, and drops any style that could fetch.
- **On render**, each screen is drawn in `<iframe sandbox="allow-same-origin">`, never with
  `allow-scripts`, because that pair would let a frame lift its own sandbox. The frame also
  carries a `Content-Security-Policy` of `default-src 'none'`. The page outside the frame builds
  every node with `textContent`; it never interpolates a note or a name into markup.

The WI-079 spike tested this in Chrome with real mouse input. An *unsanitized* hostile screen ran
no script, and the policy blocked both of its outbound requests before they were sent. The parent
page could still measure every part and receive clicks inside the frame.

Access is checked in `service.py`, on every call:

- Every Design doctype grants read to System Manager only, and create or write to nobody. So
  `/api/resource` is no way in, and `api/design_review.py` (all POST) is the only door.
- A reader is a System Manager or a participant. "Not found" and "not yours" give the same
  message, so a review's name cannot be probed.
- Votes, verdicts and notes need a participant, on an Open review, and are stamped with the
  session user.
- Moderating (status, note status, decisions, import) needs System Manager.
- Promotion and AI import need a *person* with System Manager. `authority.py` names the service
  accounts that hold the role without being anyone deciding: `triton@`, `mdm@`, `Administrator`
  and `Guest`.

`www/review.py` sends a Guest to the login page with the deep link kept, and refuses a Website
User.

## AI tools

`assistant_tools/check_design_review_bundle.py` is a read-only dry run.
`assistant_tools/submit_design_review.py` is a gated write (APP_MUTATING, Medium, prechecked). It
runs only from a confirmed card, as the person who confirmed it. Both take the bundle inline as
`bundle_json` (up to 3 MB) or as a private File named by `file_name`. The logic is in `ai_tools.py`.

## Files

| File | What it does |
|---|---|
| `service.py` | Every read and write the Review Room performs, with the checks: System User, participant, Open, moderator, human promoter. |
| `importer.py` | Validates, sanitizes and writes a bundle: the review, its content File, append-only parts, and imported artifact-era ballots, which are counted apart. Also `check_bundle`, the dry run. |
| `content.py` | Loads a review's content File (cached by hash), the kits, and lookups over the content. |
| `ai_tools.py` | What the two assistant tools do. |
| `sanitize.py` | The allowlist HTML and CSS sanitizer. Stdlib only. |
| `codes.py` | Element code format and the append-only rule. Stdlib only. |
| `tally.py` | Borda count (first of N scores N), ranking validation, verdict counts. Stdlib only. |
| `authority.py` | The service accounts, and the "human System Manager" test that promotion, AI import and `EnhancementRequest.validate` use. |
| `kit/` | House stylesheets a bundle can name. Append-only. |
| `bundle.schema.json`, `examples/` | The format, machine-readable, and a complete example. |
| `doctype/` | Design Review (+ Participant), Note, Vote, Verdict, Decision. |

The front end is `www/review.html` + `www/review.py` (the shell) and `public/js/design_review/`
with `public/css/design_review.bundle.css`:

| Module | What it does |
|---|---|
| `transport.js` | `fetch` to the endpoints (the `M` map), and the bundle upload. |
| `frames.js` | The sandboxed frames and the click-through rules. |
| `app.js` | The app: routes, the list, the overview (ranking, tally, decisions), the viewer, the notes panel. |
| `dom.js` | Element helpers. Text always goes in through `textContent`. |

`scripts/design_review/` holds the exporter that turns the claude.ai Concept Viewer's generator
into a bundle, and `qa/`, a headless-Chrome test that drives the real app at `/review` against a
mock server built from a bundle, with real mouse and keyboard input.

## Tests

- `tests/test_design_review_sanitize.py`: the hostile-HTML fixture loses everything dangerous,
  and the concepts keep what they need.
- `tests/test_design_review_codes.py`: append-only codes, the tally, who may promote.
- `tests/test_design_review_surface.py`: every endpoint is POST and dialled, the `/review` route,
  frames never given `allow-scripts`, the example, schema and importer agreeing, and no Long Text
  for screen markup.
- `tests/test_design_review_access.py` (frappe stub, own CI step): the read and write gates,
  session-user stamping, and promotion through `file_request`. It also imports real content
  through the importer into a File and checks that its SVG survives, that a revision may not
  rename a part, that the example imports, and that the AI tools refuse what they should.
- `tests/test_feedback_states.py`: the Enhancement Request insert rule.
- `tests/test_feedback_endpoint_surface.py` scans this module: nothing here creates a Task.
