# The email design system

Every email this app sends renders through one layout, defined once, in this repo.
This page is the guide and the inventory: what the pieces are, how to add an email,
and every email the app can send.

Introduced in **v1.331.0**.

---

## Why it exists, and what the old problem actually was

The app emailed from three surfaces that shared no chrome, no palette and no layout:
13 Jinja templates, ~26 inline `frappe.sendmail` call sites, and 21 `Notification`
records. Two things are worth recording because both were counter-intuitive.

**The narrow centred card was self-inflicted.** Frappe wraps every
`sendmail(message=...)` in `frappe/templates/emails/standard.html`, whose container is

```jinja
width="{% if header or with_container %} 600 {% else %} 100% {% endif %}"
```

Neither `Notification.send_an_email` nor any caller in this app passes `header` or
`with_container`, so the framework container is **already 100% wide, with no card, no
padding and no masthead**. The 640px box came from a `max-width` div hand-written into
the message body. Nothing upstream needed overriding — the fix was to stop re-imposing
a container, and `tests/test_email_design.py` now fails the build if one comes back.

**The good-looking emails were the unmanaged ones.** Of the 21 `Notification` records,
13 had been built in the Desk UI and carried 2–5.7k characters of hand-copied
letterhead each; the six that *were* in git were plain text whose `\n` line breaks
collapsed into a single run-on paragraph in every mail client. The prettier half lived
only in the site database. All 19 live rules are now fixtured, and each body is
300–800 characters of *content*.

---

## The pieces

```
erpnext_enhancements/email_style.py                    the Python API and the palette
erpnext_enhancements/templates/emails/_shell.html      the layout
erpnext_enhancements/templates/emails/_components.html the macros — the ONLY email markup
erpnext_enhancements/public/images/email/logo.png      the letterhead
erpnext_enhancements/tests/test_email_design.py        the guard
```

The markup exists **once**, in `_components.html`. `email_style.py` is a thin adapter
over those macros, reached through Jinja's own module API rather than reimplemented, so
there is no second copy of a `<td>` to drift. `email_style.py` is modelled on
`project_enhancements/contract_style.py` and diverges in one place: that module inlines
the logo as `<svg>` because its renderer is wkhtmltopdf, and **Gmail and Outlook strip
inline SVG**, so email uses a hosted `<img>`.

### Two rules that hold the whole thing together

1. **`_components.html` may not reference `frappe`.** Translated strings and absolute
   URLs arrive as arguments. This is what lets the guard suite render the macros under a
   vanilla `jinja2.Environment` with no frappe stub at all, and it is asserted.
2. **Frappe's Jinja env has no autoescape.** Every macro `|e`s its own *text* arguments,
   so a caller may hand it raw user input. **URLs are emitted unescaped on purpose** —
   entity-escaping a `get_url()` breaks the link — so `email_style.button()` runs them
   through `utils/url_safety.is_safe_url` first and degrades a rejected URL to plain
   text.

---

## Layout

Fluid `width:100%`, capped at `max-width:840px`. Full-bleed with comfortable gutters on
a phone; capped before body copy runs 1400px wide on a maximised desktop window. White
throughout — no tinted canvas, no border, no shadow, because a *card* is what made the
old emails read as a box.

Two mechanics, both verified against this site rather than assumed:

- **Outlook's Word engine ignores `max-width`**, so an MSO ghost table pins the measure
  for Outlook only. Both halves of the conditional comment survive Premailer's lxml
  round-trip.
- **The `<style>` block is a progressive enhancement and nothing may depend on it.**
  Every element already carries correct inline padding and font-size at 320px. It earns
  its place for a different reason: `.ee-md` descendant rules are the only way to reach
  the attribute-less tag soup `md_to_html()` produces for the morning briefing, and
  Premailer inlines them onto the generated elements (verified:
  `<h2 id="heading" style="color:#00263E; font-size:18px">`). The `@media` block only
  tightens padding and stacks the fact table.

  **The `.ee-md` rules reach the component macros too, and that cuts both ways.** They
  are descendant selectors on the body cell, so `.ee-md table{width:100%}` and
  `.ee-md td{border-bottom:…;padding:9px 10px}` land on *every* table and td in the
  body, not only the generated ones. A macro that omits `width` on a table, or
  `padding`/`border` on a td, therefore does not get the browser default — it gets the
  markdown body's. `button()` omitted all three and shipped a 772×60 coloured bar
  wrapped around a 212×41 link: 19% of what looked like the button was the button, and
  clicking the colour did nothing (v1.331.2). **Declare `width` on every macro table
  and `padding` *and* `border` on every macro td**; `tests/test_email_design.py` fails
  the build otherwise.

---

## Palette

Colour carries meaning; chrome is navy and blue. Contrast against white is computed by
the guard suite, not asserted in a comment.

| Token | Hex | Use | On white |
|---|---|---|---|
| `INK` | `#00263E` | Headings — the wordmark navy | 14.1:1 |
| `BODY_INK` | `#1f2d38` | Body copy | 12.5:1 |
| `MUTED` | `#5b6b78` | Secondary text, footer | 5.6:1 |
| `ACCENT` | `#0077a8` | Links, letterhead rule, primary CTA | 4.99:1 |
| `SUCCESS` | `#17753f` | Completed, submitted, paid | 5.83:1 |
| `WARNING` | `#8a5a00` | Overdue, out of range, needs attention | 5.94:1 |
| `DANGER` | `#a02a2a` | Failed, error | 6.51:1 |
| `RULE` | `#dbe4ea` | Borders — never carries text | — |
| `SURFACE` | `#f2f9fd` | Table tint — never carries text | — |

**Three colours are banned and the build enforces it.** `#00a0dd` is the brand blue and
may never be a text colour or a text-bearing background — 2.97:1, the same rule
`scripts/test_chat_source_rules.js` enforces for the chat CSS. `#1E9E5A` (the old CTA
green, 3.52:1 with white text) and `#00A1DE` (the old link blue, 2.90:1) both failed AA
and were carried by all 13 hand-written Notification bodies.

Colour never carries meaning alone: Gmail and Outlook.com force-invert in dark mode, so
every `callout` states its severity in words too. For the same reason the logo ships as
an **opaque** PNG — those clients invert page colours but never image pixels, so a
transparent navy wordmark would go navy-on-dark and vanish.

---

## Components

All in `_components.html`, all available as `email_style.<name>()` in Python and as
`ee_<name>()` in a Notification body.

| Macro | For |
|---|---|
| `h(text)` | A sub-heading inside the body |
| `p(text)` | A paragraph. Escapes its argument |
| `rich(html)` | A paragraph carrying inline `<b>`/links. **Does not escape** — you must |
| `note(text)` / `note_rich(html)` | Small print |
| `prose(text)` | Human sentences containing newlines. Body font, no box |
| `code(text)` | Machine output — a traceback, a transcript, a backup log |
| `kv(rows)` | Label/value facts. `[(label, value), ...]` |
| `table(headers, rows)` | A data table. A cell may be `(url, label)` to link |
| `kpis(cards)` | Headline numbers. `[{"label", "value", "tone"}, ...]` |
| `callout(text, tone)` | A boxed aside |
| `bullets(items)` / `links(items)` | Lists |
| `button(url, label, tone)` | The CTA |
| `button_fallback(url)` | "If the button does not work…" — use it on anything a customer must reach |
| `pill(label, tone)` | A short status word |

Tones: `primary`, `success`, `warning`, `danger`, `info`.

**`prose()` and `code()` are not interchangeable.** Four senders used to emit their whole
body as `<pre>`; two of them (offsite backup, call transcripts) really are machine
output, and two (status alerts, chat governance) are human sentences that merely contain
newlines. The old `status_alerts` markup said so itself with an inline
`font-family:inherit` override on its `<pre>`.

**Tables are four columns or fewer.** Five does not fit a phone even stacked. Three
digests were trimmed to fit when they moved onto the system; if you need more columns,
link to the report instead.

---

## Adding a new email

### A Jinja template

Body templates are **fragments**. They own content; the shell owns chrome. Keeping them
fragments is also what lets a sender hand the *unwrapped* body to `Notification Log` for
the desk bell panel while the wrapped one goes to the inbox.

```jinja
{# Context: doc, doc_url. NOTE: no autoescape — the macros escape their own args. #}
{% import "erpnext_enhancements/templates/emails/_components.html" as ee %}

{{ ee.p("The thing happened.") }}
{{ ee.kv([("Reference", doc.name), ("When", doc.creation)]) }}
{{ ee.button(doc_url, "Open it") }}
```

```python
from erpnext_enhancements import email_style

frappe.sendmail(
    recipients=[address],
    subject=subject,
    message=email_style.render(TEMPLATE, context, title=subject, eyebrow="Projects"),
)
```

### Python that already holds an HTML string

```python
frappe.sendmail(
    ...,
    message=email_style.wrap(body_html, title=subject, eyebrow="Operations"),
)
```

`wrap()` never raises — it returns the unwrapped body rather than taking a deliverable
email down with it. Pass `tagline=True` for customer-facing mail; it reads oddly under
an Error Log alert, so it is off by default.

### A `Notification` record

Bodies use the `ee_*` Jinja globals, **not** `{% extends %}`. `{% extends %}` does work
from a DB-stored string, but in a child template anything outside a `{% block %}` is
silently discarded, and a mistyped extends path raises inside `Notification.send()`'s
own `except`, which logs an Error Log and drops the email. Neither is survivable in a
field people edit through the Desk.

```jinja
{% set body %}
{{ ee_kv([("Task ID", doc.name), ("Project", doc.get("project"))]) }}
{{ ee_doc_button(doc, "View the task") }}
{% endset %}
{{ ee_email(body, title=doc.subject, eyebrow="Projects · Task completed") }}
```

Use **`doc.get("field")`, never `doc.field`**. A Document raises `AttributeError` on a
missing field, and that becomes a silently dropped email. `ee_kv` renders `None` as an
em dash.

Then add the record to `fixtures/notification.json` **and** to the name filter in
`hooks.py`, with an explicit `"enabled": 1` — without it the fixture imports disabled and
is re-disabled on every migrate.

### Before you push

```bash
python -m pytest erpnext_enhancements/tests/test_email_design.py -q
```

### Do not

- Add a `max-width` or `margin:0 auto` container to a body template.
- Use a colour that is not in `email_style.py`.
- Use `email_style.raw()` without a comment saying why. It exists for HTML this app did
  not generate — `Auto Email Report.get_report_content()` and pre-rendered feedback
  bodies — and the guard suite watches it.
- Put a logo `<img>` in a body. The shell owns the letterhead.

---

## Inventory

Every email the app can send. **Audience**: `customer` = leaves the company.

### Template-backed (`templates/emails/`)

| Email | Template | Trigger | Recipients | Audience |
|---|---|---|---|---|
| Fountain move — converted | `crm_enhancements/fountain_move_new.html` | Request converted | Opportunity owner + configured extras | staff |
| Fountain move — failed | `crm_enhancements/fountain_move_failed.html` | Conversion errored | as above | staff |
| Fountain move — duplicate | `crm_enhancements/fountain_move_duplicate.html` | Matched >1 party | as above | staff |
| Fountain move — daily digest | `crm_enhancements/fountain_move_digest.html` | `daily` scheduler | configured extras | staff |
| Fountain intake invite | `crm_enhancements/fountain_intake_invite.html` | Invite sent | the customer | **customer** |
| Contract signing invite | `project_enhancements/contract_signature_invite.html` | Send/resend for signature; reminders | the signer | **customer** |
| Contract signed copy | `project_enhancements/contract_signed_customer.html` | Signature completed | the signer | **customer** |
| Trip booked | `travel/trip_booked.html` | `Travel Trip` → Booked | travelers + owner | staff |
| Traveler added | `travel/traveler_added.html` | Traveler added mid-trip | the traveler | staff |
| Trip closed unsettled | `travel/trip_closed.html` | Trip closed, claim not settled | the traveler | staff |
| Expense claim drafted | `travel/expense_claim_generated.html` | Draft claim created | the traveler | staff |
| Pre-travel itinerary | `travel/pre_travel_reminder.html` | Daily reminder; "Send Itinerary" button | the traveler | staff |
| Unclaimed expenses nudge | `travel/expense_nudge.html` | Daily post-trip sweep | the traveler | staff |

All travel emails are gated by **Travel Settings → Send Travel Notifications**.

### Code-built (`frappe.sendmail`)

| Email | Source | Trigger | Audience |
|---|---|---|---|
| Morning briefing | `api/briefing.py` | cron `30 6 * * 1-5` | staff |
| Maintenance route digest | `api/maintenance_dispatch.py` | cron `0 6 * * *` | staff |
| Customer service report | `api/maintenance_workflow.py` | Record finalized (settings-gated) | **customer** |
| Call transcript / voicemail | `api/telephony.py` | Call analysed; voicemail left | staff |
| New project created | `crm_enhancements/api.py` | Closed-Won project creation | staff |
| Hand-off meeting invite | `crm_enhancements/handoff.py` | Meeting scheduled | staff |
| Commission report | `crm_enhancements/pay_period_reports.py` | Pay-period boundary | staff |
| Administrator login alert | `security_alerts.py` | `Activity Log` after_insert | staff |
| Offsite backup result | `offsite_backup/backup.py` | cron; watchdog | staff |
| Project start reminder | `project_enhancements/__init__.py` | `daily` | staff |
| Awaiting-signature digest | `project_enhancements/esign/tasks.py` | weekly | staff |
| Contract signed — staff | `project_enhancements/esign/lifecycle.py` | Signature completed | staff |
| Chat governance alert | `chat/governance/alerts.py` | Governance events | staff |
| Hand-off SLA digest | `process_steps.py` | cron `30 7 * * 5` | staff |
| Hand-off escalation | `status_alerts.py` | Overdue step / meeting | staff |
| Enhancement request update | `product_feedback/notify.py` | Status/decision change | staff |
| Training assigned / due / escalation / pass | `training/notifications.py` | Assignment; cron `15 7 * * *` | staff |
| Payment link | `stripe_payments/core/api.py` | Desk action | **customer** |
| Card declined / autopay paused | `stripe_payments/core/dunning.py` | `daily` dunning cycle | **customer** |

### `Notification` records (`fixtures/notification.json`, 19)

All `channel: Email`, all `enabled: 1`, all fixtured as of v1.331.0.

| Notification | DocType | Event | Recipients |
|---|---|---|---|
| Task Completed | Task | Value Change `status` | `operations@` |
| New Lead Created | Lead | New | `sales@` |
| New Opportunity | Opportunity | New | `sales@` |
| Email Team on Opportunity Won | Opportunity | Save (→ Closed Won) | `operations@`, `billing@`, `production@`, `sales@` |
| New Project Created | Project | New | `billing@` |
| Project Type Change Alert | Project | Value Change `project_type` | `billing@`, `operations@` |
| New ToDo Created | ToDo | New | by document field ×4 |
| Remind Me Email | Reminder | Value Change `notified` | by document field |
| New Fiscal Year Created | Fiscal Year | New (auto_created) | `billing@` |
| Material Request Received | Material Request | Value Change `status` | `operations@`, `billing@`, `production@` |
| Material Request Submission | Material Request | Submit | `operations@`, `billing@` |
| Error Log | Error Log | New | role: System Manager |
| Integration Request | Integration Request | Save (Failed) | role: System Manager |
| Maintenance Review Needed | Sapphire Maintenance Record | Value Change | `operations@` |
| Maintenance Finalized | Sapphire Maintenance Record | Value Change | `billing@` (+ PDF) |
| Maintenance Reading Out of Range | Sapphire Maintenance Record | Submit | `service_repair@` |
| Maintenance Contract Renewal Due | Sapphire Maintenance Contract | Days Before ×30 | `operations@` |
| High Escalation Risk Call | Call Log | Value Change | `service_repair@` |
| Compliance Flag on Call | Call Log | Value Change | `service_repair@` |

**The fixture now sets these recipients on every migrate.** A hand edit in the Desk will
be reverted by the next migrate rather than persisting — see the note in
`patches/repoint_notifications_to_group_emails.py`.

### Deliberately outside the system

- **Chat message/mention notifications** are never emailed at all
  (`notification_skip_email_types`, ADR 0009 Decision #3).
- **Stock ERPNext `Email Template` records** (7, e.g. Request For Quote, Leave Approval)
  are untouched framework defaults.
