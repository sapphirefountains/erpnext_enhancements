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
`bundled_asset('sf_tokens.bundle.css')`.

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

Every text pairing above clears 4.5:1, and every boundary and focus ring 3:1, in both themes.
The test measures them all.

### Palette

The design system's tokens by name (`--sf-deep-sea-blue`, `--sf-bahama-blue`,
`--sf-fresh-blue`, `--sf-fresh-blue-deep`, `--sf-violet`, `--sf-violet-deep`, `--sf-teal`,
`--sf-teal-deep`, `--sf-navy-900`, `--sf-navy-800`, `--sf-sky-200`, `--sf-off-white`,
`--sf-white`, `--sf-ink-900`, `--sf-ink-700`, `--sf-border-100`). The values equal
`print_style`'s constants (tested).

**Four values the design system doesn't have.** The test refuses any other hex, so a new one
needs its reason here and in the CSS:

| Token | Value | Why |
|---|---|---|
| `--sf-ink-500` | `#5c6066` | Muted text. The system's only greys are body inks (ink-700, ink-900). 6.3:1 on white, 6.0:1 on off-white. |
| `--sf-border-300` | `#80858b` | A field's edge. The system's border-100 is 1.4:1 on white, and its own FormField notes ask for 3:1; this is 3.7:1. |
| `--sf-red` | `#bd2e2b` | Status fill (failed, out of range, past due). From the 2016 guide's supportive set, as print uses it. |
| `--sf-yellow` | `#ffb819` | Status fill (attention, stale). Same source. 1.7:1 on white, so it is never text. |

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
  stop** under an off-white label, and edged with the opening stop. Every closing stop clears
  6:1. The edge keeps a Build chip visible on a dark card (see Chip, below).
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
copy, and `tests/test_print_style.py` holds that copy to `STREAM_PILLAR`. The Kanban's own
value-stream colours are retired by form kit P6.

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
| **Chip** | A pill in a closing stop with an `--sf-on-pillar` label and a 1px border in the pillar's **opening** stop, or `--sf-danger` / `--sf-warn` with its own label. The border is not decoration: Build's closing stop is navy-800, the same colour as a dark-theme card, so without it a Build chip is a floating word (seen in the 2026-10-13 render). A status always carries its word, not just its colour. |
| **Button** | `--sf-radius-control`. Primary: `--sf-primary` fill, `--sf-on-primary` label, at most one per panel. Secondary: transparent with a 2px `--sf-text` stroke. Hover changes colour only. |
| **Toast** | Inverted: a `--sf-text-strong` ground with `--sf-surface` text, so it stands out in both themes (a navy toast would vanish against the dark page). One action. |

## Before you push

```bash
python -m unittest erpnext_enhancements.tests.test_app_design_tokens -v
python -m unittest erpnext_enhancements.tests.test_print_style -v
```

Both are bench-free, and CI runs each in its own step.
