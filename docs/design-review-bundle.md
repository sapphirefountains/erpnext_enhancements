# Design review bundles

A **bundle** is one JSON file that holds a set of UI/UX concepts for the team to review. It
contains the screens as HTML, the parts of each screen people can pin notes to, and the rules
that make the screens click through like a prototype. You import it at **`/review`** and
ERPNext turns it into a Design Review. People then rank the options, give each screen a Yes,
Maybe or No, and pin notes to element codes such as `L3-S04-E05`. A System Manager records
decisions and promotes them to Enhancement Requests, so they reach the ERPNext board already
approved.

Anything can write a bundle: a script, Claude, Triton, or a person. This page is the format.
The machine-readable shape is
[`design_review/bundle.schema.json`](../erpnext_enhancements/design_review/bundle.schema.json).
[`design_review/examples/minimal-bundle.json`](../erpnext_enhancements/design_review/examples/minimal-bundle.json)
is a complete, importable example with two options and two screens, and is the best place to
start. The importer, [`design_review/importer.py`](../erpnext_enhancements/design_review/importer.py),
is the authority: when this page and the importer disagree, the importer is right and this page
is the bug.

## Getting a bundle in

| Who | How |
| --- | --- |
| A System Manager | `/review` → **IMPORT A REVIEW**, or **IMPORT A REVISION** on a review. The file is checked first and you confirm what will happen. |
| An AI over MCP | `check_design_review_bundle` (read-only) and then `submit_design_review` (gated: a person with System Manager confirms the card in ERPNext). Pass the bundle inline as `bundle_json` (up to 3 MB) or name a private File as `file_name`. |
| Code | `erpnext_enhancements.design_review.importer.import_bundle(bundle, review=None)`. |

A new review starts in **Draft**. Before anyone can take part, add the participants on the
review's Desk form (**PARTICIPANTS** on its `/review` page), then set it **Open** at `/review`.
Only participants can rank, give verdicts and write notes. A System Manager who is not a
participant can moderate but cannot vote.

To import a **revision** of a review, import a newer bundle into that review. Everything
recorded on the review is kept: votes, verdicts, notes and decisions. Element codes are
append-only, so a revision may add parts but may never renumber or rename one. A revision
that tries to is refused before anything is written.

## The top level

```json
{
 "format": "sapphire-design-review/1",
 "title": "Training UX review",
 "description": "Five options for the Training Canvas and five for learners.",
 "source_url": "https://claude.ai/…",
 "kit": "sapphire-ux/1",
 "stylesheet": "",
 "tracks": [ … ],
 "options": [ … ],
 "screens": [ … ],
 "parts": { … },
 "flow": { … },
 "ballots": { … }
}
```

- **`format`** must be exactly `sapphire-design-review/1`.
- **`title`** is required.
- **`source_url`** is optional. An import that names no review updates the review with the
  same `source_url` if there is one, and creates a new review otherwise.
- **`kit`** names a house stylesheet. Every frame gets it before the bundle's own
  **`stylesheet`**. See [The kit](#the-kit).
- **`ballots`** is optional history, described [below](#ballots).

## Tracks, options and screens

A **track** is one thing being designed: "the Training Canvas" or "the learner app". People
rank the options *within* a track.

```json
{"id": "learner", "label": "Learner", "short": "Learn", "votable": true,
 "blurb": "What a crew member sees.",
 "groups": [{"name": "Arrive", "screens": ["S01", "S02"]}, {"name": "Learn", "screens": ["S03"]}]}
```

- `id` is lower case letters, digits and underscores, starting with a letter. It appears in
  URLs.
- `groups` are the stages of the journey, in order. The screen rail and the story order
  follow them. Screens in no group come last.
- Set `"votable": false` for a track nobody ranks, such as a shared entry screen or a role
  chooser.

An **option** is one design for a track:

```json
{"track": "learner", "code": "L3", "name": "Coach", "what": "One next step at a time.", "tradeoff": "Slower to browse."}
```

Option codes are capital letters and digits starting with a letter, such as `L3`. They must be
unique across the whole bundle, not just within a track, because a note's code begins with
one.

A **screen** is one drawing:

```json
{"track": "learner", "option": "L3", "screen": "S04", "name": "Lesson", "group": "Learn",
 "frame": "phone", "w": 390, "h": 844, "html": "<div class=\"ux-app\" …>…</div>"}
```

- Within a track, the same `screen` code means the same screen in every option. That is what
  lets **ALL** show every option's S04 side by side, and what makes a note on `L1-S04-E03`
  comparable with one on `L3-S04-E03`.
- `frame` is `phone`, `desk` or `board`. A screen with both a `desk` and a `phone` drawing
  shows them side by side, and **BOTH / DESKTOP / PHONE** switches between them.
- `w` and `h` are the drawing's size in CSS pixels. Size the screen's root element to them.
  Common sizes are 390×844 for a phone and 1280×800 for a desktop.

## The HTML

Each screen is a fragment of body markup, at most 400 KB. On import it passes through an
allowlist sanitizer, [`design_review/sanitize.py`](../erpnext_enhancements/design_review/sanitize.py).
It is then drawn in a sandboxed frame that runs no script and loads nothing from any host. Write
it as a static picture of the screen.

**Kept:** the usual layout, text, list, table and form tags (`div`, `span`, `p`, `a`, `button`,
`h1`–`h6`, `ul`/`ol`/`li`, `header`/`main`/`footer`/`nav`/`section`/`aside`/`article`,
`label`, `input`, `textarea`, `select`/`option`, `table` and its parts, `details`/`summary`,
`img`, `pre`/`code`, `blockquote`), and inline SVG (`svg`, `path`, `circle`, `rect`, `line`,
`polyline`, `polygon`, `ellipse`, `g`, `text`, `tspan`).

**Attributes kept:** `class`, `style`, `id`, `data-c`, `aria-label`, `aria-hidden`, `role`,
`title`, `alt`, the form attributes, and the SVG geometry and paint attributes.

**Removed:**

- `<script>`, `<style>`, `<iframe>`, `<form>`, `<video>`, `<canvas>` and their contents.
- Every `on…` handler, and every `data-*` attribute except `data-c`.
- Comments.
- A `style` attribute that mentions `url(`, `@import`, `expression(` or `javascript:`.
- An `img` `src` that is not a `data:image/png|jpeg|gif|webp;base64,` URI.

Every `href` becomes `#`, because clicks are decided by the [flow](#click-through) and never
by a link. The import report lists what the sanitizer removed, and **check** shows it to you
before you commit.

Put CSS in the bundle's `stylesheet` (the same rules apply to it) or in `style` attributes. A
`<style>` tag inside a screen is removed. Fonts: the frame can load
`/assets/erpnext_enhancements/fonts/big_noodle_titling.woff2` as `"Big Noodle Titling"` and
nothing else, so use it or a system font stack. Inputs are drawn but cannot be typed into.

### Parts and element codes

Mark every part someone might want to comment on with `data-c="Part name"`:

```html
<footer data-c="Action bar"><button class="ux-btn">START</button></footer>
```

Then number the parts in `parts`, keyed by `track:screen`:

```json
"parts": {"learner:S04": [[1, "Top bar"], [2, "Video"], [3, "Action bar"]]}
```

A part's element code is `<option>-<screen>-E<nn>`. For example, `L3-S04-E03` is the Action bar
on option L3's S04. The number belongs to the *track's* screen, not to one option, so the
same part has the same number in every option. **Numbers are permanent:** once imported, a
part keeps its number and name for ever. A revision can only add new numbers.

Under **MARKUP** (key `M`), every marked part is outlined and tagged with its number, and
clicking one opens its notes. A part that falls outside the drawn frame gets no tag.

## The kit

`"kit": "sapphire-ux/1"` gives every frame the house stylesheet,
[`design_review/kit/sapphire_ux_1.css`](../erpnext_enhancements/design_review/kit/sapphire_ux_1.css).
It follows the Sapphire Fountains design system: navy grounds, fresh blue fills and Big Noodle
Titling display type. With it, a generator can write screens using its classes and ship no CSS
at all. The kit is append-only: a class may gain rules but is never renamed or removed,
because imported reviews depend on it. A visual change that would alter old reviews becomes a
new kit, `sapphire-ux/2`.

Wrap each screen in `.ux-app` (it scopes the tokens and resets), sized to the frame. The main
classes are:

| Use | Classes |
| --- | --- |
| Ground | `ux-dark` (navy gradient), `ux-flat` (navy) |
| Type | `ux-display` with `ux-h1` / `ux-h2`, `ux-eyebrow`, `ux-strong`, `ux-muted`, `ux-small`, `ux-prose` |
| Buttons | `ux-btn` plus `ux-btn-quiet`, `ux-btn-outline`, `ux-btn-ghost`, `ux-btn-wide`, `ux-btn-lg`; `ux-iconbtn`; `is-disabled` |
| Bars | `ux-topbar` with `ux-title`, `ux-bottombar`, `ux-tabbar` with `ux-tab`, `ux-sidenav` with `ux-navitem` |
| Content | `ux-card`, `ux-row`, `ux-tiles` / `ux-tile`, `ux-divider`, `ux-kv`, `ux-callout`, `ux-hero` |
| Status | `ux-chip` plus `ux-chip-req`, `ux-chip-due`, `ux-chip-warn`, `ux-chip-ok`, `ux-chip-light`; `ux-status` and `ux-dot`; `ux-progress` with a `span`; `ux-meter`; `ux-badge` |
| Steps | `ux-stepper`, `ux-step`, `ux-stepnum`, `ux-timeline`, `ux-dots` |
| Form | `ux-field`, `ux-input`, `ux-check`, `ux-check-lg`, `ux-seg` |
| Scrolling | `ux-scroll`: the one element in a screen that scrolls inside the frame |
| Training editor | `ux-blocks`, `ux-blockwrap`, `ux-blocklabel`, `ux-blocktools`, `ux-addblock`, `ux-inspector`, `ux-outline*`, `ux-readiness*`, `ux-handle` |
| Training player | `ux-video`, `ux-play`, `ux-vlabel`, `ux-time`, `ux-chapter-row`, `ux-quizcard`, `ux-pin` |

Read the stylesheet itself for the exact rules. It is short.

## Click-through

With MARKUP off, the screens behave like a prototype: buttons, cards and links move to the next
screen. `flow` says where each click goes. Rules are resolved once per screen and written onto
the elements, so **LINKS** (key `L`) can outline every clickable spot before anyone clicks.

A **target** is one of:

| Target | Meaning |
| --- | --- |
| `S05` | That screen in this track, same option |
| `admin:S01` | That screen in another track, in the option at the same position (the third option of this track leads to the third option of that one) |
| `next` | `flow.next[track][screen]` if set, else the next screen in order |
| `up` | `flow.backto[track][screen]` if set, else browser Back |
| `end` | `flow.chain["track:screen"]` if set, else "the end of this option's story" |
| `+1`, `-1` | The next or previous screen in order |
| `done:<message>` | Show the message and stay. Use it for an action the prototype finishes, such as "Saved". |

Rules are checked in this order for every `a`, `button` and `[data-c]` element:

1. **`icon`**: `[[pattern, target, track?], …]` on an icon button's `aria-label`. An icon button
   is `.ux-iconbtn`, or `flow.dom.icon`.
2. **`aria`**: the same, on any element's `aria-label`.
3. **`disabled`**: `{track: {part: target}}` for an `.is-disabled` control inside that part.
4. **`text`**: `{track: [[pattern, target, [screens]?, [options]?], …]}` on the upper-cased text
   of a link or button. A step (`.ux-step`) is matched on its label.
5. **`nav`**: `{track: {part name: rule}}` on a `data-c` part. A rule is a target, an object
   `{"S01": target, "*": target}` keyed by screen, or `[[pattern on the part's text, target], …]`.

Patterns are regular expressions and must compile. An unmapped button inside a part that has a
target inherits it, so a bar's main button works without a rule of its own. Exclude elements from
inheritance with `flow.dom.noinherit`, and from everything with `flow.dom.inert`. List a bar's
part name in `flow.dom.bars` so that only its button is a door and its padding is not.

**`say`** adds one line of narration under the caption: `{"learner:S04": "…"}` on arriving at a
screen, or `{"learner:S03>learner:S04": "…"}` for one particular move. Each line is at most 300
characters. Use it to explain a jump that would otherwise surprise.

**NEXT** (key `N`) walks the story: `next`, then the order.

## Ballots

`ballots` imports rankings, verdicts and notes that were cast somewhere else, such as a claude.ai
ballot page, before the review was in ERPNext. They are stored apart from live ones, shown
apart, and never counted in the live tally. They need no ERPNext login, because the voter is
just a name.

```json
"ballots": {
 "source_url": "https://claude.ai/…",
 "votes": [{"voter": "Ana", "ranking": ["L3", "L1", "L2"]}],
 "verdicts": [{"voter": "Ana", "option": "L3", "screen": "S04", "verdict": "Yes"}],
 "notes": [{"code": "L3-S04-E02", "text": "Bigger play button.", "author": "Ana", "status": "Open"}]
}
```

`source_url` is required. Re-importing the same `source_url` replaces the earlier copy.

## Limits

| | |
| --- | --- |
| Bundle | 12 MB (3 MB inline through the AI tool) |
| Screens | 1,500 per bundle, 400 KB each |
| Frame size | 40 to 8,000 px each way |
| Narration | 300 characters per line |

## Writing one with an AI

Give the model this page and the example bundle, and ask for a bundle. Then:

1. Run `check_design_review_bundle` and fix every problem it reports.
2. Run `submit_design_review`. It returns a confirmation card, and nothing is imported until a
   person with System Manager confirms it in ERPNext.
3. When it has run, call `check_ai_pending_action` for the review's name and link.

A model that cannot pass megabytes in one call should write the file and have a person import
it at `/review`.

Good bundles share some habits. They give the same screen the same code and the same parts in
every option. They mark the parts people will argue about with `data-c`. They give every
primary button somewhere to go. And they say the trade-off of each option honestly, because the
team ranks against it.
