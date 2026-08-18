# Email templates

Every email this app sends renders through the layout in this directory. The full
guide, the palette, and an inventory of every email the app can send are in
[`docs/email-design-system.md`](../../../docs/email-design-system.md).

| File | What it is |
|---|---|
| `_shell.html` | The one layout — letterhead, heading, body slot, footer. Fluid, capped at 840px. |
| `_components.html` | The macros. **The only email markup in the app.** |
| `crm_enhancements/` | Fountain Move request + intake invite bodies |
| `project_enhancements/` | Contract e-sign bodies |
| `travel/` | Travel Trip bodies |

## The shape of a body template

Body templates are **fragments**. They own content; `_shell.html` owns chrome. That
split is not cosmetic — several senders hand the *unwrapped* fragment to
`Notification Log` for the desk bell panel while the wrapped one goes to the inbox, and
a template that wrapped itself would put a letterhead inside a dropdown.

```jinja
{# Context: doc, doc_url. NOTE: frappe's Jinja env has NO autoescape. #}
{% import "erpnext_enhancements/templates/emails/_components.html" as ee %}

{{ ee.p("The thing happened.") }}
{{ ee.kv([("Reference", doc.name), ("When", doc.creation)]) }}
{{ ee.button(doc_url, "Open it") }}
```

The Python side wraps it:

```python
from erpnext_enhancements import email_style

message = email_style.render(TEMPLATE, context, title=subject, eyebrow="Projects")
```

## Rules

- **Escaping.** There is no autoescape. The macros `|e` their own *text* arguments, so
  values passed into `ee.kv()`, `ee.p()`, `ee.table()` need no second `|e` — and adding
  one gives you `Smith &amp;amp; Sons` in the reader's inbox. Only `ee.rich()` and
  `ee.note_rich()` trust their input, and there you must escape every interpolated value
  yourself.
- **URLs are never escaped.** Entity-escaping a `get_url()` breaks the link.
- **No containers.** Never add `max-width` or `margin:0 auto`. The shell owns the
  measure, and a body-level container is exactly what made these emails render as a
  narrow centred box before v1.331.0.
- **No new colours.** Everything comes from `email_style.py`. Three hexes are banned
  outright for contrast; the guard suite lists them.
- **No logo `<img>`.** The shell owns the letterhead.
- **Four columns maximum** in `ee.table()`. Five does not fit a phone even stacked.
- **`_components.html` may not reference `frappe`.** URLs and translated strings arrive
  as arguments. This is what lets the guard suite render the macros with no frappe stub.

## Before you push

```bash
python -m pytest erpnext_enhancements/tests/test_email_design.py -q
```
