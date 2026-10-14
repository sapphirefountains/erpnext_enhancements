# The app design system

The Sapphire Fountains Design System, translated for the Desk's own screens: the form kit,
the new Opportunity board, and anything else built after them. It is the third sibling of
[print-design-system.md](print-design-system.md) (paper) and
[email-design-system.md](email-design-system.md) (inboxes), and it shares their source of
truth: the colours are the design system's tokens, and the pillar records are
`print_style.PILLARS`.

Introduced in **v1.616.0** as form kit P1 (ERPNext TASK-2026-02742; P1.1 02744, P1.2 02745).
The design system itself is the claude.ai artifact *Sapphire Fountains* (Design System type,
`QSMwzGdAyPVaZ1i3BRe1XS`), built from the live website's CSS on 2026-09-19.

---

## What ships

One stylesheet, `erpnext_enhancements/public/css/sf_tokens.bundle.css`, loaded on every Desk
page through `app_include_css`. It holds `--sf-*` custom properties and the `@font-face` rules
for the brand's two faces (Big Noodle Titling, Lato), and **nothing else**. A face downloads
only when something renders in it. No selector in it reaches an element, so the Desk looks
exactly as it did until a screen reads a variable. `tests/test_app_design_tokens.py` fails
the build if a rule that styles something gets into the file.

A web page outside the Desk (a `www/` page) can load the same file with
`bundled_asset('sf_tokens.bundle.css')`, linked in its own template before its own stylesheet
(never through `web_include_css`, which would put it on every public page). The Review Room
(`/review`) and the itinerary (`/itinerary`) do (S3.5a, v1.659.0; `tests/test_www_app_tokens.py`).
A page with a theme toggle of its own writes `data-theme` on `<html>`, as the Review Room's THEME
button does; one without leaves it unset and follows the device.

The customer portal (`/pay`, `/pay-card`, `/rentals`, `/rent-a-fountain`) links the tokens the same
way, then `sf_portal.bundle.css`, and shares one nav bar, `templates/includes/sf_portal_nav.html`
(S6a, v1.674.0; `tests/test_customer_portal_polish.py`). It is the one place that pins
`data-theme="light"` (`templates/includes/sf_portal_light.html`), because the website around it has
no dark theme and Stripe's Payment Element draws dark labels in a frame no stylesheet reaches.

## Light and dark

The Desk writes the resolved theme to `<html data-theme="light|dark">`. Frappe v16's
`theme_switcher.js` turns *Automatic* into one of the two, so the tokens follow each
person's Desk setting. A page with no `data-theme` follows the device through a
`prefers-color-scheme` block, which the test holds identical to the Desk one.

Dark is the brand's **single-palette dark**, settled on 2026-09-19:

- the page is navy-900 and cards are navy-800;
- text is off-white, with sky-200 for anything secondary;
- **fields stay white**, so a form reads the same in both themes;
- pillar colours never change.

One departure from that note: dark-theme rules are **bahama-blue**, not deep-sea-blue,
because deep-sea-blue on a navy-800 card is 1.06:1 and a rule nobody can see.

Only the semantic tokens change with the theme. The palette, pillars, type, space and shape
are the brand and stay fixed (tested).

## Tokens

Read the semantic layer in screens. Read the palette only to draw a pillar or the band.

### Semantic

| Token | Light | Dark | Used for |
|---|---|---|---|
| `--sf-bg` | off-white | navy-900 | the page |
| `--sf-surface` | white | navy-800 | cards, panels, the step stage |
| `--sf-text` | ink-700 | off-white | body copy |
| `--sf-text-strong` | ink-900 | off-white | names, totals, values |
| `--sf-text-muted` | ink-500 | sky-200 | secondary lines, counts |
| `--sf-heading` | bahama-blue | off-white | section titles |
| `--sf-eyebrow` | bahama-blue | sky-200 | the small capitals above a title |
| `--sf-link` | bahama-blue | sky-200 | links (underline them; colour alone is not enough) |
| `--sf-rule` | border-100 | bahama-blue | hairlines that separate, never ones that carry meaning |
| `--sf-label` | ink-900 | off-white | a field's label |
| `--sf-hint` | ink-500 | sky-200 | the hint under a field (form kit P4.3) |
| `--sf-field-bg` / `--sf-field-text` | white / ink-900 | white / ink-900 | inputs |
| `--sf-field-border` | border-300 | border-300 | a field's edge |
| `--sf-focus` | bahama-blue | fresh-blue | focus rings |
| `--sf-primary` / `--sf-on-primary` | fresh-blue / navy-900 | same | the one primary button |
| `--sf-band` / `--sf-on-band` / `--sf-on-band-muted` | deep-sea-blue → navy-900 / off-white / sky-200 | same | the navy head band |
| `--sf-on-pillar` | off-white | same | a label on a pillar's closing stop |
| `--sf-danger` / `--sf-on-danger` | red / off-white | same | a status fill and its label |
| `--sf-warn` / `--sf-on-warn` | yellow / navy-900 | same | a status fill and its label |
| `--sf-good` / `--sf-on-good` | green / off-white | same | a status fill and its label |
| `--sf-chip-edge-<tone>` | the opening stop (design, build, service, rent, neutral); red (danger); navy-900 (warn); green (good) | sky-200 (build, neutral); off-white (danger); yellow (warn); the rest as light | a chip's 1px edge |

Every text pairing above clears 4.5:1, and every boundary and focus ring 3:1, in both themes
(a chip's boundary is its fill or its edge, whichever meets the ground; see Chip, below). The
test measures them all.

### Palette

The design system's tokens by name (`--sf-deep-sea-blue`, `--sf-bahama-blue`,
`--sf-fresh-blue`, `--sf-fresh-blue-deep`, `--sf-violet`, `--sf-violet-deep`, `--sf-teal`,
`--sf-teal-deep`, `--sf-navy-900`, `--sf-navy-800`, `--sf-sky-200`, `--sf-off-white`,
`--sf-white`, `--sf-ink-900`, `--sf-ink-700`, `--sf-border-100`). The values equal
`print_style`'s constants (tested).

**Five values the design system doesn't have.** The test refuses any other hex, so a new one
needs its reason here and in the CSS:

| Token | Value | Why |
|---|---|---|
| `--sf-ink-500` | `#5c6066` | Muted text. The system's only greys are body inks (ink-700, ink-900). 6.3:1 on white, 6.0:1 on off-white. |
| `--sf-border-300` | `#80858b` | A field's edge. The system's border-100 is 1.4:1 on white, and its own FormField notes ask for 3:1; this is 3.7:1. |
| `--sf-red` | `#bd2e2b` | Status fill (failed, out of range, past due). From the 2016 guide's supportive set, as print uses it. |
| `--sf-yellow` | `#ffb819` | Status fill (attention, stale). Same source. 1.7:1 on white, so it is never text. |
| `--sf-green` | `#1e7e51` | Status fill for good (healthy, done, synced), read through `--sf-good` / `--sf-on-good`. Neither the system nor the 2016 guide has a green; this is the one the dashboard blocks already used, and it is clearly not teal (the Rent pillar). An off-white label on it is 4.76:1; the fill is 5.05:1 against a white card and 3.27:1 against a dark-theme navy-800 card. Added in sf_kit (TASK S2.2). |

## Pillars and value streams

| Pillar | Opening stop (flat colour, stripe) | Closing stop (chips, small text) | Gradient |
|---|---|---|---|
| Design | `--sf-pillar-design` violet | `--sf-pillar-design-deep` violet-deep | `--sf-gradient-design` |
| Build | `--sf-pillar-build` bahama-blue | `--sf-pillar-build-deep` navy-800 | `--sf-gradient-build` |
| Service | `--sf-pillar-service` fresh-blue | `--sf-pillar-service-deep` fresh-blue-deep | `--sf-gradient-service` |
| Rent / Events | `--sf-pillar-rent` teal | `--sf-pillar-rent-deep` teal-deep | `--sf-gradient-rent` |
| Neutral | `--sf-pillar-neutral` deep-sea-blue | `--sf-pillar-neutral-deep` bahama-blue | `--sf-gradient-neutral` |

The rules:

- **Gradients run left to right at 90deg**, opening stop first.
- **A flat pillar colour is the opening stop.** It is a fill or a rule, never text: fresh-blue
  is 3.0:1 on white and teal 1.8:1.
- **A pillar chip** (the board's card band, a tag in a list) is filled with the **closing
  stop** under an off-white label, and edged with `--sf-chip-edge-<pillar>`: the opening stop
  in light, and sky-200 in dark for Build and Neutral, whose opening stops sink into the navy
  card. Every closing stop clears 6:1. The edge keeps a Build chip visible on a dark card (see
  Chip, below).
- **Never invent a fifth pillar colour or reassign one.** Design is violet.

**Value streams to pillars** live in `print_style.STREAM_PILLAR`, the one record of which
pillar a stream takes:

| Stream | Pillar |
|---|---|
| Design | design |
| Build | build |
| Products | build |
| Delivery | build |
| Service | service |
| Events | rent |

Products has always been drawn as Build in the project brief. Nik chose Build for Delivery on
2026-10-13. A stream mapped to `None` would take the neutral band. `project_brief.js` carries a
copy, and `tests/test_print_style.py` holds that copy to `STREAM_PILLAR`; so do the guided page's
`form_kit/summary.js` (`tests/test_guided_entry.py`) and the report face's `report_face/cells.js`
(`tests/test_report_face.py`). The Kanban's own
value-stream colours are retired by form kit P6: the new Opportunity board
(`crm_enhancements/page/opportunity_board`) draws each card's band from `form_kit/summary.js`'s copy, one segment
per stream in the closing stop, edged in `--sf-chip-edge-<pillar>` so Build and Neutral still show on a dark card.

## Type

- **`--sf-font-display`** is Big Noodle Titling, served from
  `public/fonts/big_noodle_titling.woff2`, the same file print inlines. It sets page and
  section titles **only**: never body copy, buttons or labels. Its leading is 1 (the face
  reads as a block), and it takes no letter-spacing. Sizes: `--sf-display-sm` 22px, `-md`
  28px, `-lg` 40px.
- **`--sf-font-sans`** is **Lato**, the brand's body face, vendored on 2026-10-13. The files
  are `public/fonts/lato_regular.woff2` (400) and `lato_bold.woff2` (700): Google Fonts' Lato
  v25 "latin" subset (U+0000–00FF and common punctuation), about 23 KB each, under the SIL
  Open Font License 1.1 (`public/fonts/LATO_OFL.txt`). A character outside the subset falls
  back to the next face in the stack, and italics are synthesized. Sizes: `--sf-text-xs`
  12px, `-sm` 13px, `-md` 15px, `-lg` 18px.
- **`--sf-text-input` is 16px and must stay at least 16px.** iOS zooms the page into any
  input set smaller (tested).
- **Capitals in buttons and eyebrows are typed into the copy, not applied with
  `text-transform`**, as on the website.

## Dates and times

Two styles, one for each place a date is read. Neither is picked per screen.

- **The Desk shows `10/14/2026` and a 24-hour `14:00`.** These are the site's System Settings
  (`date_format` `mm/dd/yyyy`, `time_format` `HH:mm`), set by
  `patches/set_desk_date_and_time_format` from the old `mm-dd-yyyy` / `HH:mm:ss`. Frappe v16 offers
  only `HH:mm:ss` and `HH:mm` for the Desk, so there is no 12-hour Desk time to choose. The patch
  moves a field **only while it still holds the old value** and writes the global default as well
  as the Single, because the Desk reads `tabDefaultValue`, not System Settings. Code that needs a
  date for a screen uses `frappe.datetime.str_to_user` or `formatdate` and so follows the site
  format; code that stores or compares a date keeps ISO `YYYY-MM-DD`. **A script report is the
  exception**: it shows dates through the report face (below), in the print style.
- **Print, email and SMS show `10/14/2026 2:00 PM`.** A date alone is `10/14/2026`; a time alone is
  `2:00 PM`. The formatting is `format_date`, `format_datetime` and `format_time` in
  `utils/normalize.py` (no frappe import), and a Print Format or a Notification body reaches them
  through three Jinja globals: `sf_date(value)`, `sf_datetime(value)` and `sf_time(value)`
  (`utils/date_display.py`). They are registered individually and prefixed, for the reason the
  `ee_*` and `ps_*` globals are. No existing format calls them yet; move one over when you next
  touch it.
- **A start/end pair that runs backwards warns and never blocks.** An end date before its start is
  usually a typo, and a hard stop would also stop someone halfway through an edit. The
  `validate` hook `utils/date_pairs.warn_out_of_order` shows one orange "Check the dates" pop-up
  naming both fields and their values in the print style, then lets the save go ahead. It speaks
  **once per person per record per problem** (`utils/popup_once.py`: autosaving pages re-validate
  constantly) and stays quiet in imports, installs, migrations, patches, background jobs and for
  guests. A sequence is compared in order over the values that are set, so a blank setup time
  does not hide a take-down before the event. Equal is in order.

The registry is `date_pairs.PAIRS`, and each doctype in it has the hook in `hooks.py`
(`tests/test_date_pairs.py` fails the build if one is missing, or names a field that does not
exist). Registered today:

| Doctype | Fields, in order |
|---|---|
| Opportunity, Project | Requested Start Date, Requested End Date |
| Opportunity, Project | Delivery, Setup, Event, Take Down (Date Time) |
| Lead | Rental Start Date, Rental End Date |
| Task | Start Datetime, End Datetime (the planner's; core validates `exp_*` and `act_*`) |
| Sapphire Maintenance Contract | Start Date, End Date |
| Training Batch | Start Date, End Date |
| Project Contract | Rental Start Date, Rental End Date; Mobilization, Substantial Completion, Final Completion |
| Managed Device | Purchase Date, Warranty Expiry |

**Register only a pair nothing validates.** ERPNext already refuses a Project's or Task's own
start/end dates, and these controllers throw on theirs: Rental Booking, Travel Trip, Time Off
Request, Work Restriction, Asset Booking, Asset Out of Service, Time Correction Request. Imported
or generated pairs, child-table rows and flights (departure and arrival are local to different
time zones) are left out for the reasons in the `date_pairs.py` docstring.

## The report face

Form kit P3.3 (v1.657.0, ERPNext TASK-2026-02756): one formatter that script reports call for
their **phone, party, pillar, money and date** columns, so the same piece of information reads the
same way in every report, and the way print already shows it (`print_style.ps_phone`, `ps_date`).

| Kind | A cell shows | From |
|---|---|---|
| `phone` | `(801) 555-0100 x12` from the stored `+18015550100,12`; a value the rules can't read, as stored | `phone_display` in the normalization twin |
| `party` | the record's human name, linked to the record (two column shapes, below) | `frappe.utils.get_form_link`; plain text when the person can't read the doctype, as Frappe's Link cells do |
| `pillar` | each value stream as a chip in its pillar's colors (`Build, Service` is two chips); a word that is not a stream ("Unallocated") stays plain text | `STREAM_PILLAR` (above) and the Chip (below) |
| `money` | the column's own currency formatting | Frappe's Currency formatter, with the column's `options` |
| `date` / `datetime` / `time` | `10/14/2026`, `10/14/2026 2:00 PM`, `2:00 PM`; a datetime in the person's time zone | `format_date` / `format_datetime` / `format_time` in the twin, after `frappe.datetime.convert_to_user_tz` for a datetime (as v16's own Datetime formatter does) |

**A column says what it is** with one key in the report's Python, `sf_face`. With no key, a
`Date`, `Datetime`, `Time`, `Currency` or `Phone` column takes its kind from its fieldtype, so a
report whose formatter calls the face gets its dates and money right with no marker at all.
`sf_face: "none"` leaves a column to Frappe, and so does an unknown kind (a typo must not blank a
column).

```python
{"label": _("Phone"), "fieldname": "phone", "fieldtype": "Data", "sf_face": "phone"},
{"label": _("Value Streams"), "fieldname": "value_streams", "fieldtype": "Data", "sf_face": "pillar"},
# A party, shape 1: the column is a Link (options = the doctype) or a Dynamic Link (options = the
# row field naming it) holding the DOCNAME; sf_label names the row field with the name. For a
# column that is the record itself (an account list's own Customer).
{"label": _("Account"), "fieldname": "name", "fieldtype": "Link", "options": "Customer",
 "sf_face": "party", "sf_label": "customer_name"},
# A party, shape 2: a Data column holding the NAME; sf_name names the row field with the docname,
# and sf_doctype (a doctype) or sf_doctype_field (a row field naming one) says what it is.
{"label": _("Customer / Party"), "fieldname": "party", "fieldtype": "Data",
 "sf_face": "party", "sf_name": "party_id", "sf_doctype_field": "party_type"},
```

**Prefer shape 2 for a party that belongs to the row** (an Opportunity's customer, an address's
party). A cell's value is what export writes (`build_xlsx_data` takes the raw value), what the
datatable sorts on and what its column search matches, so a Link cell exports and sorts by the
docname while showing the name. Shape 2 keeps all three on the name, shows the name unlinked when
there is no docname, and still shows it when the face is missing.

**The report's formatter is two lines**, plus anything of its own (a status color, a threshold
glow) wrapped around what the face returns:

```js
formatter(value, row, column, data, default_formatter) {
	const face = window.erpnext_enhancements?.report_face;
	return face?.format
		? face.format(value, row, column, data, default_formatter)
		: default_formatter(value, row, column, data);
},
```

For HTML a report builds itself, a dialog or a message, the same answers come one at a time:
`report_face.phone(v)`, `.date(v)`, `.datetime(v)`, `.time(v)`, `.money(v, currency)`,
`.party(doctype, name, label, { new_tab })`, `.pillar(streams)` and `.chip(label, tone)`. Every
one returns escaped HTML. The Python side of a report (its message, an HTML string built in
`execute()`) uses `utils/normalize` (`format_date`, `phone_display`) or the `print_style` helpers,
never `strftime`, `formatdate` or `frappe.format(x, {"fieldtype": "Date"})`.

**Two decisions in it.**

- **A report's date time is 12-hour.** The Desk's own fields show `14:00` because Frappe v16 has
  no 12-hour Desk format (Dates and times, above). A report is read like a printed page and is
  often printed, so it takes the print style, `2:00 PM`. A date alone is `10/14/2026` either way.
- **The chip is drawn with inline declarations**, not a class. A report page loads no sf_kit
  stylesheet, and loading the kit on every Desk page to style one pill would be the wrong trade.
  `report_face/cells.js` carries `.sf-chip`'s declarations as a table, which
  `tests/test_report_face.py` holds to `css/sf_kit.bundle.css` declaration for declaration; they
  read only `--sf-*` tokens, which `sf_tokens.bundle.css` puts on every Desk page, so the chip
  follows the light and dark themes like the kit's does. The markup carries the classes
  `sf-report-chip sf-report-chip--<tone>` for anything that wants to find it.

**Where it lives.** `public/js/report_face/cells.js` holds the rules: pure, no frappe and no DOM,
run under node by `tests/test_report_face.py` against the Python twin's own answers.
`report_face.js` is the Desk half, imported by `erpnext_enhancements.bundle.js` (a report's own
`.js` can import nothing, so the face has to be global), and it fails open: if the face throws,
the cell gets the report's default formatter and the console one warning.

**Moving the reports.** The CRM and sales reports moved first (all seven in CRM Enhancements,
plus Value Stream Performance). `tests/test_report_hand_formatting.py` reads every Script
Report's `.js`, `.py` and `.html`, comments stripped, for a phone or a date formatted by hand:
`frappe.datetime.str_to_user`, `moment(...).format`, `toLocaleDateString`, `strftime` with a day
in its format (a `%Y-%m` or `%b %Y` key is not a shown date), `formatdate`,
`frappe.format(x, {fieldtype: "Date"})`, a bare `*_date` passed into `.format(...)` (read with
`ast`, so a keyword's name is not one), and a phone rebuilt with a regex or `[3:6]` slices of a
phone-ish name. The CRM and sales reports fail the build on a finding, on not calling the face,
or on a phone, party or value-stream column left unmarked. Every other report is listed in the
CI log and on `KNOWN_REMAINING`, which pins a count per report and rule and **takes only
removals**: a new report, a new rule or a higher count fails, and so does a count the report no
longer reaches, until it is lowered. Move a report when you next touch it, and take it off the
list. `ALLOWED` takes a report that legitimately differs, with its reason; it is empty today.

## Space and shape

- **Space** follows the design system's steps, named by pixel value: `--sf-space-5`, `-10`,
  `-15`, `-20`, `-30`, `-40`, `-60`.
- **Structure is square** (`--sf-radius-none`): cards, fields, bands and chips' containers.
- **Controls round at `--sf-radius-control` (10px)**: buttons and segmented toggles.
  **Chips and avatars are pills** (`--sf-radius-pill`). Keep the three groups apart: a
  rounded card reads as a different brand.
- **No shadows.** Separation comes from a change of ground, the band, or a hairline. The test
  refuses the word.
- **Motion** is `--sf-duration` (200ms) on colour and transform. Nothing animates on load,
  and `prefers-reduced-motion` turns movement off.

## Phones

- **Touch targets are at least `--sf-touch-min` (44px).**
- **Below 768px** (`--sf-phone-max` is 767px; CSS can't read a variable in a media query, so
  write the number), use one column, a sticky footer at the bottom of the viewport, and
  safe-area insets (`env(safe-area-inset-bottom)`).
- **Every screen and every open panel gets a history entry**, so the phone's Back closes a
  panel before it leaves the page (Nik's rule, 2026-09-24).

## Components the form kit builds on these

Built in form kit P4 and P6, specified here so they come out the same everywhere:

| Component | Shape |
|---|---|
| **Head band** | `--sf-band` with `--sf-on-band` title in the display face, an `--sf-on-band-muted` eyebrow, and a 6px pillar stripe (`--sf-gradient-<pillar>`) under it. |
| **Step rail** | A list on `--sf-surface`. The current step is marked by a 3px `--sf-focus` bar **and** its weight, never by colour alone. Done steps carry a tick; "N to fill" is `--sf-text-muted`. |
| **Field** | `--sf-label` above (never a placeholder for a label), a square `--sf-field-bg` input with a 1px `--sf-field-border` and 16px text, then the hint in `--sf-hint` under it. Focus is a 2px `--sf-focus` ring. |
| **Help bubble** | An (i) button, 44px hit area, beside the label. It opens the field's longer explanation in a square panel on `--sf-surface` with a `--sf-rule` edge, and Back or Esc closes it. |
| **Card** | Square `--sf-surface` on `--sf-bg`, a `--sf-rule` hairline, no shadow. A pillar card leads with a band in the pillar's closing stop. |
| **Chip** | A pill in a closing stop with an `--sf-on-pillar` label, or `--sf-danger` / `--sf-warn` / `--sf-good` with its own label, and a 1px border in `--sf-chip-edge-<tone>`. The border is not decoration: Build's closing stop is navy-800, the same colour as a dark-theme card, so without it a Build chip is a floating word (seen in the 2026-10-13 render). The first edge, the opening stop, still failed there: bahama-blue is 2.48:1 on navy-800 and 2.88:1 on navy-900, Neutral's deep-sea-blue 1.06:1, and danger's red fill 2.82:1. So the edge is a themed token (v1.632.0): in dark, Build and Neutral are edged sky-200 (8.49:1 on navy-800, 9.83:1 on navy-900) and danger off-white (15.56:1, 18.03:1); in light, warn is edged in its navy label colour, because its yellow fill is 1.7:1 on white. The rule the test holds, for every tone in both themes: whichever of fill or edge meets the ground clears 3:1 against `--sf-surface` and `--sf-bg`. (Service's and Rent's light edges, fresh-blue and teal at 2.96:1 and 1.93:1 on white, are decoration: their closing-stop fills, 7.96:1 and 6.61:1, are the boundary.) A status always carries its word, not just its colour. |
| **Button** | `--sf-radius-control`. Primary: `--sf-primary` fill, `--sf-on-primary` label, at most one per panel. Secondary: transparent with a 2px `--sf-text` stroke. Hover changes colour only. |
| **Toast** | Inverted: a `--sf-text-strong` ground with `--sf-surface` text, so it stands out in both themes (a navy toast would vanish against the dark page). One action. |

## sf_kit: the shared page pieces

The small parts every custom page draws, built once on these tokens: the state panel, chip,
card, buttons, toast, a status-tile grid, one HTML escape and one `frappe.call` wrapper. The
modules are `erpnext_enhancements/public/js/sf_kit/` (pure ES modules: no DOM or frappe at
import time, so `scripts/test_sf_kit.mjs` runs every one under node), the entry point is
`public/js/sf_kit.bundle.js`, and the styles are `public/css/sf_kit.bundle.css`.

**The rule: a page never writes its own loading, empty or error panel, chip or toast again.**
It draws them from the kit, so a fix or a colour change reaches every screen at once. Moving the
existing pages and blocks over is a separate step; new code starts on the kit.

### On a Desk page

Neither file is a global include (nothing in `hooks.py` `app_include_*`; tested): only a page
that uses the kit loads it, both files together.

```js
frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], () => {
	const sf = window.sf_kit;
	sf.state.render(this.body, "loading");
	...
});
```

v16's `frappe.require` passes every name through `frappe.assets.bundled_asset`, which looks it up
in `frappe.boot.assets_json` (`path = frappe.boot.assets_json[path] || path`, `assets.js` on
`version-16`), so both names resolve to their content-hashed files; a `.css` name becomes a
`<link rel="stylesheet">`, and the callback runs once both have loaded. Never write the raw
`/assets/...` path (the one-year immutable cache in CLAUDE.md). The bundle does not inject CSS
from JS; the `.css` bundle is the stylesheet. The tokens themselves are already on every page.

### In a Custom HTML Block

A block renders inside a shadow root (`root_element`). The `--sf-*` tokens are custom
properties, which inherit across the shadow boundary, but a page's stylesheet does not reach
inside it. So a block loads the script and adopts the kit's CSS text, which `sf_kit.CSS`
exports (the same text as the `.css` file; tested character for character):

```js
frappe.require(["sf_kit.bundle.js"], () => {
	const sheet = new CSSStyleSheet();
	sheet.replaceSync(sf_kit.CSS);
	root_element.adoptedStyleSheets = [...root_element.adoptedStyleSheets, sheet];
	const toast = sf_kit.create_toast({ container: root_element });
	...
});
```

A toast for a block goes into its own root (`container`), so it sits inside the adopted sheet.

A block on a workspace does not write those lines itself: `blocks.kit(root)` on
`erpnext_enhancements.workspace_blocks` (`public/js/global_enhancements/workspace_block_return.js`,
global) does the require once per page load and the adoption once per root, and resolves the
kit, or a plain-text stand-in when the bundle could not load. `blocks.ticket(root)` is the
"only the newest answer draws" counter that goes with it. How a block uses both, and what it
does on a bundle from before them, is in `custom_html_blocks/README.md` (v1.649.0).

### The pieces

**`sf_kit.esc(value)`**: the one HTML escape (`& < > " ' \``); `null` and `undefined` become
`""`. Everything a person typed or the server sent goes through it before it becomes markup.
`planner_kit`'s `escape` is this same function.

```js
row.innerHTML = `<b>${sf_kit.esc(doc.customer_name)}</b>`;
```

**`sf_kit.call(method, args, { quiet, method, with_messages, silent, freeze })`**: a server call
as a promise that always settles, exactly once. It resolves `r.message` (or, with
`with_messages: true` on the quiet path, `{ message, server_messages }`), and rejects
`{ status, message, exc_type, session_expired }`:

- `status`: the HTTP status, or **0** when the server was never reached (offline, a network
  error) or what answered was not the server (a 200 whose body is not JSON: a proxy or login page);
- `message`: the server's own words for a `frappe.throw` (from `_server_messages`, HTML taken
  out and entities decoded), or a plain translated sentence ("Could not reach the server.", a
  generic one for a 5xx, never the exception text or a traceback);
- `exc_type`: the server's exception class (`ValidationError`, `PermissionError` ...), or `""`;
- `session_expired`: `true` when the reply means the person is signed out, and `exc_type` is
  then `"SessionExpired"` too. **Test `err.session_expired`**, not the status: v16 answers the
  first call after a session lapses with **401** `{ exc_type: "SessionExpired", session_expired:
  1 }` (`sessions.py` sets the flag, `is_whitelisted` raises `SessionExpired`), but that reply
  deletes the `sid` and `user_id` cookies, so every call after it arrives as a plain Guest and
  is refused **403** `PermissionError`. The kit therefore also counts a 401/403 as expiry when
  the page was signed in (`frappe.session`) and the `user_id` cookie is now gone or `Guest`, the
  same test v16's own `frappe.request.is_session_expired` makes. On a signed-in page a **2xx**
  whose body carries `session_expired: 1` (an `allow_guest` method that ran as Guest because the
  session had lapsed) rejects as expired too, with its 2xx status. A **417** is expiry only when
  its own body says so: the cookies never replace a `frappe.throw`'s words from a method that ran
  signed in. A retry queue stops on `session_expired` and asks the person to sign in.

There are two paths, and they differ in which dialogs frappe shows:

- **`quiet: true` (use it for anything that reports errors in its own panel).** No `frappe.call`:
  a `fetch` to `/api/method/<method>`, so frappe shows **nothing**: no msgprint, no "Server
  Error" dialog, no session-expired dialog. **It sends exactly what `frappe.call` sends**: POST
  by default, form-encoded (`application/x-www-form-urlencoded; charset=UTF-8`), or `method:
  "GET"` with the same pairs as a query string, for reads. One encoder for both, and it is
  frappe.call's: `frappe.request.prepare` JSON-stringifies every truthy plain object or array,
  then jQuery 3.7.1's `$.param` writes `3` as `"3"`, `true` as `"true"`, and `null` and
  `undefined` as `""` (the key is still sent). A JSON body would not be the same request: the
  server would see `3`, `False` and `None`, v16's argument type checks reject an int or `None`
  for a `str` parameter with a 417, and an untyped `== "true"` changes its answer.
  - Headers: `X-Frappe-CSRF-Token` from `frappe.csrf_token` on a POST only, `Accept:
    application/json`, `X-Requested-With`; same-origin credentials, `cache: "no-store"`, and
    `redirect: "error"`: a 3xx is never followed (it would carry the token wherever it points)
    and rejects as status 0.
  - **A GET to a method whitelisted `methods=["POST"]` is refused 403 `PermissionError` "Not
    permitted"** (`handler.py` `is_valid_http_method` calls `frappe.throw_permission_error`). It
    reads like a permission problem and is a wiring one, so a quiet GET refused that way while
    signed in writes one `console.warn` naming the method.
  - On a 2xx it resolves `r.message` and drops the rest of the reply: no doc sync into `locals`
    (`docs`), no translation sync (`__messages`), and no msgprint of the reply's
    `_server_messages`, such as a warning from a method that succeeded. Pass `with_messages:
    true` to resolve `{ message, server_messages }`, the messages as plain text (HTML out) in the
    server's order. It does not run `freeze` either.
  - It is not `frappe.call`'s own `quiet` option, which only hides the spinner.
- **The default, `frappe.call`.** `silent: true` (passed through, with `freeze` /
  `freeze_message`) stops exactly **one** dialog: the `_server_messages` msgprint in
  `frappe.request.cleanup` (`request.js` on `version-16`), i.e. a `frappe.throw` or msgprint
  carried by a 417 or a 200. It does **not** stop the per-status handlers: 403 "Not permitted",
  404 "Not found", 413 "File too big", 502 "Internal Server Error", 504 "Request Timed Out", 508
  "Please try again", the 500 "Server Error" dialog from `report_error` (with the traceback, for
  a system user, when tracebacks are allowed), the QueryTimeout / QueryDeadlock dialogs, the
  session-expired dialog, or the offline "Connection Lost" toast. Use it only where those are
  acceptable.

```js
sf_kit.call("erpnext_enhancements.api.x.get_rows", { day }, { quiet: true, method: "GET" })
	.then((rows) => draw(rows))
	.catch((err) => {
		if (err.session_expired) return ask_to_sign_in();
		sf_kit.state.render(el, "error", { body: err.message, on_action: load });
	});
```

**`sf_kit.state.render(el, kind, opts)`** / **`sf_kit.state.html(kind, opts)`**: the panel a page
shows in place of its content. `kind` is `loading`, `empty`, `error` or `no-access`; `opts` are
`title`, `body`, `action_label` and `on_action`. It is page content, never an overlay, and it
pushes no history entry. The one button ("Try again" by default) is drawn when there is an
`action_label` or an `on_action`: `render()` wires `on_action`, and markup from `html()` with
only a label is for a caller that delegates clicks on `.sf-state-action`. Loading is
`role="status"`, error `role="alert"`; the spinner stops under `prefers-reduced-motion`.

```js
sf_kit.state.render(el, "loading");
sf_kit.state.render(el, "empty", { title: __("No visits this week.") });
```

**`sf_kit.chip(label, tone)`**: the Chip above, as HTML. A pillar tone (`design`, `build`,
`service`, `rent`, `neutral`) or a status (`danger`, `warn`, `good`); an unknown tone is
neutral, and a chip with no word is not drawn, because the word is the meaning.

```js
`${sf_kit.chip(project.stream, "build")} ${sf_kit.chip(__("Overdue"), "danger")}`
```

**Card**: the class `sf-card` (square, `--sf-surface`, a `--sf-rule` hairline, no shadow), plus
`sf-card--pillar-<pillar>` for the leading band in the closing stop. `sf_kit.card.html({ title,
body_html, pillar })` covers the simple case; the title is escaped and `body_html` is markup the
caller built (and escaped).

**Buttons**: the classes `sf-btn sf-btn--primary` (at most one per panel), `sf-btn
sf-btn--secondary` and `sf-btn sf-btn--quiet` (a text button). 44px tall, `--sf-radius-control`,
a 2px `--sf-focus` ring, and hover changes colour only. Written as an `<a>`, a button keeps its
colours on hover and focus: the Desk's `a:hover, a:focus` (specificity 0,1,1, `color:
var(--text-color)`) outranks one class, so the kit states `.sf-btn--primary:hover` and the rest
at 0,2,0 (in a block's shadow root too, where the Desk links its stylesheet as well). Tile links
get the same, and a `:focus-visible` ring the Desk's `a:focus { outline: 0 }` would remove.

```html
<button type="button" class="sf-btn sf-btn--primary">Save visit</button>
```

**`sf_kit.toast(message, { action_label, on_action, timeout, tone })`**: one at a time, 8 seconds
by default, held while the pointer is over it **or focus is inside it** (focusin pauses the timer,
focusout to outside re-arms it), announced politely, one action and a Dismiss. When a toast that
holds focus closes, focus goes back to the element that had it before (the `relatedTarget` of the
focusin that brought it in), if that element is still in the page and rendered and the `focus()`
takes; otherwise focus is left where the browser put it. When focus came in from nowhere (the
person clicked the page background first), nothing gets it back: jumping to an input they left a
while ago would be worse. Inverted, so it shows on the dark page too. `tone` is `success`, `warning` or `info`.
`toast.close()` and `toast.current()` act on that instance's own toast.

"One at a time" is per **document**, across every instance: the page's `sf_kit.toast`, any
`create_toast()`, a block's toast in its shadow root, and the planners' pk toast. The toast is
fixed to the bottom of the viewport wherever it is appended, so two would overlap; the current
one is kept on the document under `Symbol.for("sf_kit.toast.current")`, which the separate
planner_kit and sf_kit bundles share, and a new toast from any of them replaces it.

```js
sf_kit.toast(__("Visit saved"), { action_label: __("Undo"), on_action: undo });
```

**`sf_kit.tiles.html(items)`**: the grid of a health dashboard (Integrations Health, the device
fleet). Each item is `{ key, title, tone | status, status_label, value, detail, metrics: [{ label,
value }], links: [{ label, href }] }`; `green` / `amber` / `red` are read as `good` / `warn` /
`danger`. Every text is escaped, and a link becomes a link only for a `/desk/` route or an http(s)
URL; anything else is shown as plain text. A blank `href` (`null`, `""`) falls back to `route`.
Malformed data costs only its own part: `metrics` or `links` that are not a list are skipped, so
one bad tile never blanks the grid, and a tile with no title draws no empty heading. A metric may
carry a `tone` too (v1.651.0): a status tone (or `green` / `amber` / `red`) draws its value as a
chip of that tone, so a metric is never marked by colour alone; any other tone leaves the value as
text.

```js
el.innerHTML = sf_kit.tiles.html([{ title: "Google Drive", tone: "good", detail: "Synced 4 min ago",
	metrics: [{ label: __("Failed (24h)"), value: 0, tone: "good" }],
	links: [{ label: __("Settings"), href: "/desk/google-drive-settings" }] }]);
```

Every health dashboard draws its tiles this way (S3.2, v1.651.0): Integrations Health, the Device
Fleet Dashboard, the QuickBooks Online dashboard and the Stripe dashboard. A page may decide what a
status *means* (a `{ Paid: "good", Failed: "danger" }` map onto the kit's tones), never what it
looks like: no page keeps a colour map, a literal colour or hand-built tile markup
(`tests/test_s3_health_tiles.py`). The two pages that show server health checks are thin callers of
`public/js/health_board.bundle.js` (`window.sf_health_board.mount(page, opts)`), which draws them
with the S3.1 states.

**`sf_kit.t(text, args)`**: `__()` when the Desk is there, `{0}` substitution otherwise. It is
for a caller's own dynamic text. The kit's own words are written as literal `__("...")` calls,
with a module-level `__` imported from `sf_kit/t.js` that hands to the Desk's: frappe's JS
extractor (`frappe/gettext/extractors/javascript.py`) only finds a call whose name is `__` and
whose first argument is a string literal, so a word said through `t("...")` is never translated.
A module built on the kit (form kit P4) does the same: `import { __ } from "../sf_kit/t.js"`.

As ES modules, the same functions are exported by `public/js/sf_kit/index.js` (`call` and
`create_toast` take an `env` there: `{ frappe, fetch, cookie }` and `{ doc, set_timeout,
clear_timeout }`; the bundle builds both from the page).

### What stays with form kit P4

The head band, step rail, field, help bubble, the guided page and its side panel, and offline
saving are form kit P4's, specified in the Components table above and built on top of sf_kit
(they import its modules). sf_kit stays the small shared base: a piece goes in it only when more
than one kind of page needs it.

### Before you push

```bash
node scripts/test_sf_kit.mjs
python -m unittest erpnext_enhancements.tests.test_sf_kit -v
```

## The guided-flow engine

The components above are drawn by form kit P4's page; what the page *shows* comes from the
server engine in [`erpnext_enhancements/form_kit/`](../erpnext_enhancements/form_kit/README.md)
(P4-A). It walks a DocType's own form into steps (one per section, merged, ordered and
introduced by a per-DocType config kept in code), decides per field and per user what is
editable, read-only or not sent at all, saves one step at a time onto the real record with an
optimistic lock, and measures how complete the record is for its stage. The page renders
what it is given: a field's **hint** is its docfield description, and **help** is the hook for
the (i) bubble's longer text (P4.3). Read that README before building a screen on it.

## Before you push

```bash
python -m unittest erpnext_enhancements.tests.test_app_design_tokens -v
python -m unittest erpnext_enhancements.tests.test_print_style -v
python -m unittest erpnext_enhancements.tests.test_desk_date_format_patch -v
python -m unittest erpnext_enhancements.tests.test_date_pairs -v
python -m unittest erpnext_enhancements.tests.test_report_face erpnext_enhancements.tests.test_report_hand_formatting -v
```

All are bench-free, and CI runs each in its own step.
