# The print design system

Every printed document this app composes shares one chrome, defined once, in
`erpnext_enhancements/print_style.py`. This page is the guide: what the chrome is, how a
format consumes it, and the three things about it that are not obvious.

Introduced in **v1.494.0** (the sales formats and the maintenance report), extended to every
other printed document in **v1.495.0**, alongside the matching email chrome
([email-design-system.md](email-design-system.md)). Both are the *Pillar Stripe* concept
Nik picked from the design canvas on 2026-09-21, built from the Sapphire Fountains design
system's own tokens.

---

## The look

Top to bottom, on white:

1. A **stripe** along the top edge in the pillar's left-to-right gradient — opening stop
   first, as the design system runs every pillar gradient — 12px tall.
2. The **wordmark** (the same 276×100 SVG the contracts inline) with our name, address and
   phone beside it. The address prefers the document's own company address and falls back
   to the constant in `enhancements_core/company_contact.py`; the phone *is* the constant.
3. An **eyebrow** naming the pillar and the document — `SERVICE · MAINTENANCE VISIT
   REPORT` — in the pillar's closing stop, over a **display-face title** in deep-sea-blue.
   A meta block at the right: number, date, status.
4. A **facts row** under a deep-sea-blue rule: who, where, when.
5. **Sections** with display-face headings in bahama-blue and **ruled tables**: a 2px rule
   in the pillar's opening stop under the header row, hairlines between rows, no zebra
   fills, numbers right-aligned.
6. A running line — `Sapphire Fountains, LLC · 85 W 300 S …` — over a 5px closing stripe.

Square structure, no shadows, no rounded corners, no tinted cards. Separation is a rule
or a change of weight, which is the design system's rule and also what survives a
grayscale office printer.

## Pillars

| Key | Name | Opening stop (stripe, table rule) | Closing stop (eyebrow, small text) |
|---|---|---|---|
| `service` | SERVICE | fresh-blue `#00a0df` | fresh-blue-deep `#005779` |
| `build` | BUILD | bahama-blue `#00609c` | navy-800 `#002136` |
| `design` | DESIGN | violet `#b14fc5` | violet-deep `#55265f` |
| `rent` | RENT | teal `#62cbc9` | teal-deep `#316564` |
| `None` | — | deep-sea-blue `#00263e` → navy-900 `#00111c` | bahama-blue `#00609c` |

The closing stop is the one that carries small text; every one clears AA on white and
`tests/test_print_style.py` measures it. The opening stop is a fill and a rule only —
fresh-blue reads 3.0:1 as text, teal 1.8:1.

`None` is the brand's own dark band, for a document that belongs to the company rather
than to one pillar. **Sales documents take it.** A quotation can be for any of the four
pillars and nothing on the document says which, so Quotation, Sales Order and Sales
Invoice carry the neutral stripe rather than a guess. Deriving the pillar from the linked
project's value stream is the obvious next step and deliberately not this one. The
Maintenance Visit Report is Service.

## How a format consumes it

There are two doors, one implementation.

**A Python-composed format** (`enhancements_core/setup_sales_print_formats.py`) calls the
functions at `after_migrate` and bakes the chrome into the stored HTML:

```python
from erpnext_enhancements import print_style as ps

html = ps.page_open(None)
html += ps.letterhead(None, "QUOTATION", "Quotation", meta_html, "company_address_display")
html += ps.facts_open() + ps.fact("PREPARED FOR", party_html, width="40%") + ps.facts_close()
html += '<table …><thead><tr><th style="' + ps.th() + '">Item</th> …'
html += ps.section_title("Terms &amp; conditions") + …
html += ps.page_close(None)
```

**A Jinja fixture format** (`Maintenance Record Print` in `fixtures/print_format.json`)
reaches the same functions at print time through the `ps_*` Jinja globals registered in
`hooks.py`:

```jinja
{{ ps_page_open("service") }}
{{ ps_letterhead("service", "MAINTENANCE VISIT REPORT", "Maintenance Visit Report", meta) }}
{{ ps_facts_open() }}{{ ps_fact("CUSTOMER", customer_name | e) }}{{ ps_facts_close() }}
{{ ps_section_title("Water Chemistry") }}
<th style="{{ ps_th("service") }}">Reading</th> … <td style="{{ ps_td(true) }}">{{ row.reading_value }}</td>
{{ ps_page_close("service") }}
```

The globals are registered individually and prefixed, for the same reason the `ee_*`
email globals are: `get_jinja_hooks` exports every function of a module-valued entry into
the namespace of every Print Format and web template on the site, and a bare
`letterhead` or `pillar` there is not a trade worth making.

`print_style` imports no frappe. The four format modules compose at import time and the
bench-free suites `exec` them under a stub `frappe`; a top-level `import frappe` in this
module would break both. The two files it needs — the display font and the wordmark —
are read relative to `__file__`.

## Three things that are not obvious

**The font is a data URI, not a URL.** Print formats render server-side in Chromium
(`enhancements_core/setup_print_formats.ensure_chrome_pdf_generator` pins every format to
it). A `url(/assets/…)` would make every PDF depend on the worker reaching the site's own
public origin — one more thing to go wrong on a host whose PDF history is
[pdf-generation.md](pdf-generation.md). Base64 of the 13 KB woff2 in
`public/fonts/big_noodle_titling.woff2` is ~18 KB per document and depends on nothing;
wkhtmltopdf, still the fallback for report exports, takes a data-URI `@font-face` too.
The fallback stack is Arial Narrow, a condensed bold that reads as the same idea.

**The site's Letter Head is no longer rendered by any Python-composed format.**
`print_style.letterhead()` inlines the wordmark itself, so `{{ letter_head }}` — the site's
bare right-aligned logo — would put a second logo on the page. The sales, Purchase Order and
certificate suites now assert it is *absent*, having asserted its presence since the month
the Purchase Order format went out unbranded. The report sheets are the exception: the
report wrapper prints the Letter Head above them, so they draw no wordmark at all.

**Print-safe CSS only.** The letterhead, facts row and signature lines are
`display:table`; nothing is flex or grid, because the PDF backends on this host do not
lay those out and the format suites forbid them. The stripe is a `background-color`
*before* a `background-image: linear-gradient(...)`, so a renderer with no gradient
support still paints the pillar's flat colour.

## What is on it, and what is not

| Document | Module | Pillar |
|---|---|---|
| Quotation, Sales Order, Sales Invoice | `enhancements_core/setup_sales_print_formats.py` | neutral |
| Purchase Order | `enhancements_core/setup_print_formats.py` | neutral |
| Maintenance Record Print | `fixtures/print_format.json` via `ps_*` | Service |
| Contracts (all eight templates) | `project_enhancements/contract_style.py` + the `Project Contract Print` fixture CSS | by template: owner / architect / SOW / MSA → Build, maintenance → Service, rental → Rent, NDA and employee → neutral |
| Project Brief | `public/js/project_enhancements/project_brief.js` | the job's leading stream: Design, Build (and Products), Service, Events → Rent |
| Training Certificate | `training/setup_print_formats.py` | neutral |
| Crew Qualification Roster, Supplier Pickup List (report sheets) | the report's `.html` beside it | neutral, **without the wordmark** |

**Contracts are a third door.** The chrome cannot go into the agreement body — a signed
contract prints its frozen `agreement_html` snapshot, and chrome inside it would never
reach one signed before it existed — so `contract_style.letterhead_html()` emits the
stripe, the wordmark, the pillar eyebrow *and* the `@font-face` inside the one
`.ct-letterhead` element the stylesheet already anchors on, and `wrap()` picks the
pillar from the document's `template_key`. The `Project Contract Print` fixture CSS
carries the rest (sans body, display-face titles and section heads, ruled tables, black
signature ink) and is published to all four surfaces by `_contract_css()`.

**The Project Brief is rendered in the browser**, so it carries a *copy* of the pillar
records and loads the wordmark and the font by raw `/assets` path — both files are
immutable by content, so the one-year cache on raw paths cannot serve a stale one.
`tests/test_print_style.py` holds the copy equal to `print_style.PILLARS`; the print window
waits for `document.fonts.ready` before it prints and forces `print-color-adjust: exact`
so the stripe survives the browser's print dialog.

**The report sheets are a fourth door, and they leave the wordmark out.** A report's
`.html` print sheet is compiled in the browser by frappe's microtemplate and framed by
`print_template.html`, which prints the chosen Letter Head *above* the sheet — so a
wordmark in the sheet would be a second logo. The sheets carry the stripe, the eyebrow, the
display-face title, ruled tables and the closing stripe, written as static CSS in the
tokens (a test holds every hex to the palette), with the font by relative `/assets` path:
the desk print window resolves it against the site and the PDF route makes it absolute in
`scrub_urls`. Microtemplate's two traps apply to the chrome too: no double brace anywhere
in the file, comments included, and no apostrophe in the markup, which is why the CSS
quotes its font names with double quotes.

Everything the app prints is now on the chrome.

## Before you push

```bash
python -m unittest erpnext_enhancements.tests.test_print_style erpnext_enhancements.tests.test_sales_print_formats -v
```

`test_print_style` renders the Maintenance Record Print fixture against sample records —
per-site and single-feature, signed and unsigned, with an out-of-range reading, with
every table empty — because nothing else compiles a fixture's html before a customer is
holding the PDF.
