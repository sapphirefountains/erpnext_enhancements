# Data normalization: phones, emails, websites, addresses and dates

What a contact value looks like when it is stored and when it is shown, the fields those rules
apply to, and the plan for applying them to production data. Form kit P2-A (2026-10-13); the
save hook and the Desk display are P2-B.

The rules live in one place each:

| Where | What |
|---|---|
| [`utils/normalize.py`](../erpnext_enhancements/utils/normalize.py) | The rules, server side. Pure, frappe-free (`print_style` imports it) |
| [`public/js/normalize/normalize.js`](../erpnext_enhancements/public/js/normalize/normalize.js) | The JavaScript twin: same API, same results. Reaches the Desk through `desk_display.js` |
| [`utils/normalize_hook.py`](../erpnext_enhancements/utils/normalize_hook.py) | P2-B: normalize on save, for every doctype in the registry |
| [`public/js/normalize/desk_display.js`](../erpnext_enhancements/public/js/normalize/desk_display.js) | P2-B: the Desk shows a stored phone the way people read it, and normalizes what is typed |
| [`utils/value_display.py`](../erpnext_enhancements/utils/value_display.py) | P2-B: `sf_phone`, the Jinja global for print, email and SMS |
| [`tests/fixtures/normalize_cases.json`](../erpnext_enhancements/tests/fixtures/normalize_cases.json) | The shared cases. `tests/test_normalize.py` runs every one through both and compares them field by field |
| [`utils/normalize_fields.py`](../erpnext_enhancements/utils/normalize_fields.py) | Which fields the rules may touch, and which they must not |
| *Data Normalization Preview* report | The dry run: what the cleanup would change, and what needs a person |
| [`utils/normalize_cleanup.py`](../erpnext_enhancements/utils/normalize_cleanup.py) | P2-C: the one-time cleanup of stored values, run by the patch `normalize_stored_values` |

## The one rule above the others

**A value that cannot be normalized is left exactly as typed and reported — never guessed and
never rejected.** Every function returns a `Result(value, changed, ok, reason)`. When `ok` is
false the value is the input untouched and `reason` says, in plain English, why a person has
to look ("no area code", "two numbers in one field"). Nothing here ever refuses a save.

## Phones

| Typed | Stored | Shown |
|---|---|---|
| `8015550100`, `801-555-0100`, `801.555.0100`, `(801) 555-0100`, `1-801-555-0100`, `+1 801 555 0100` | `+18015550100` | `(801) 555-0100` |
| `801-555-0100 x123` / `x 123` / `ext 123` / `ext. 123` / `extension 123` / `#123` / `,123` | `+18015550100,123` | `(801) 555-0100 x123` |
| `+44 20 7946 0958` | `+442079460958` | `+442079460958` |
| `65-688-000-88` | *unchanged* — not a North American number and no +country code | as typed |
| `555-0100` | *unchanged* — no area code | as typed |
| `1-800-FLOWERS` | *unchanged* — letters in the number | as typed |
| `801-555-0100 / 801-555-0101` | *unchanged* — two numbers in one field | as typed |
| `(101) 555-0100` | *unchanged* — area code cannot start with 0 or 1 | as typed |
| `855-444-9477` typed with U+2011 non-breaking hyphens, `801-555-0100` with en dashes or no-break spaces | `+18554449477` / `+18015550100` | `(855) 444-9477` |
| `801.566.7272,x5115`, `801-555-0100; ext 5` | `+18015667272,5115` / `+18015550100,5` | `(801) 566-7272 x5115` |
| `tel:+1(770)277-1120`, `TEL://801-555-0100` | `+17702771120` / `+18015550100` | `(770) 277-1120` |

**Stored in E.164, shown the familiar way.** E.164 is what Twilio and every dialer accept, it
compares exactly, and it is one string per number however it was typed.

**The extension goes after a comma, not an `x`.** A comma is the dial pause a phone understands,
and — the deciding reason — Frappe v16 validates a `Phone` field against
`([0-9\ \+\_\-\,\.\*\#\(\)]){1,20}$`. There is no letter in that class, so `x123` would make the
record unsaveable the day the field is typed `Phone`; and it stops at **20 characters**, so a
stored value that would exceed 20 (a long international number plus a six-digit extension) is
reported instead of stored.

**Conservative on purpose.** A wrong guess produces a number nobody can dial, which is worse
than an untidy one somebody can. So:

- With a `+`: `+1` must be followed by exactly ten digits; any other country code is kept as
  `+` and its 8–15 digits. A `(0)` trunk prefix (`+44 (0)20…`) is reported, not dropped.
- Without a `+`: **only North American shapes** are accepted — ten digits, `1` plus ten, or the
  3-3-4 grouping with parentheses, spaces, dots or dashes (the same shape `print_style` was
  checked against on every phone-shaped value on production, `(208)-283-2638`, `786-5361357`,
  `954. 579-9476` and `1800 407 6657` included). Production holds a Singapore contact stored
  `65-688-000-88`: ten digits, so a digit count alone would make it `(656) 880-0088`, somebody
  else's number. It is reported instead.
- The area code must start 2–9.
- **Three shapes the first production dry run flagged were rule gaps, not bad data** (P2-B,
  2026-10-13), and are read now: Unicode dashes (U+2010 to U+2015, U+2212) and Unicode spaces
  (no-break, narrow no-break, the fixed-width spaces) are read as `-` and a space *for parsing
  only* — a value that still cannot be normalized comes back with its Unicode characters, as
  typed; a `,` or `;` may come before an `x` / `ext` / `extension` marker; and a leading `tel:`
  (or `tel://`) is dropped, as `email()` drops `mailto:`. Everything else that run flagged —
  international numbers typed without a `+`, test numbers like `123-456-7890`, names and emails
  typed into phone fields, URLs in email fields, four-digit ZIPs — is genuine bad data and
  stays flagged.

`utils/phone.py` (`normalize_phone`) is a different tool — the exact-match key for party
matching — and keeps its behaviour. `tests/test_normalize.py` asserts it reads the raw value
and the stored E.164 value as the same ten digits for every North American case, so
normalizing a field never breaks a match. (Neither reads past an extension; that was already
true of `x123` and stays true of `,123`.) `ai_governance/call_routing_match.to_e164` is not a
delegate either: that file is copied byte-for-byte into Triton and must stay stdlib-only. The
same test asserts the two agree wherever `phone()` is sure.

Print reads the stored form too: `print_style.format_phone` uses `phone_display`, so
`+18015550100,123` prints `(801) 555-0100 x123`.

## Email

Trimmed, a leading `mailto:` dropped, lowercased: `Sales@SapphireFountains.COM` →
`sales@sapphirefountains.com`. Reported rather than guessed: two addresses in one field
(`a@b.com; c@d.com`), a display-name form (`Nik Bradshaw <nik@example.com>`), and anything that
is not `something@domain.tld`.

## Websites

`https://` is prefixed onto a bare domain and the scheme and host are lowercased — never the
path or query: `HTTPS://Example.COM/Path?Q=One` → `https://example.com/Path?Q=One`. A value
starting `/`, `#`, `mailto:` or `tel:` is left alone. The prefix rule is the one
`crm_enhancements/website_cleanup` has applied since v1.36.0 (`normalize.add_scheme`; the
healer delegates to it). The preview is stricter than the healer about what it calls clean: a
value with a space in it, an email address, or a "host" with no dot (`N/A`) is reported. The
healer still prefixes those on save, because its job is to keep a record saveable — 408
records were once frozen by a bare domain in a `URL` field.

## Addresses and names

`text`: trimmed, every run of whitespace (tabs, newlines, double spaces) collapsed to one
space, trailing commas dropped — `Salt Lake City, ` → `Salt Lake City`. Used for address lines,
city, county and Contact/Lead names.

`state` (US): a full name or an abbreviation becomes the two-letter USPS code — states, DC,
Puerto Rico and the territories, and the three military codes: `utah` → `UT`,
`Washington, D.C.` → `DC`. Anything else is reported. `zip` (US): `84010`, `84010-1234`, and
`840101234` → `84010-1234`. Four digits is reported ("a leading zero may have been lost"),
never padded. Both read the Address's own `country`; outside the US they apply `text` only.

## Dates and times (display only)

| Value | Shown |
|---|---|
| `2026-10-14` | `10/14/2026` |
| `2026-10-14 14:05:00` | `10/14/2026 2:05 PM` |
| `00:00:00` / `12:00:00` | `12:00 AM` / `12:00 PM` |
| a `timedelta` (a Frappe Time field), or its string `9:05:00` | `9:05 AM` |

Twelve-hour clock, no leading zero on the hour, uppercase AM/PM, no seconds. The JavaScript
twin parses date strings by hand: `new Date("2026-10-14")` is UTC midnight and shows the 13th
anywhere west of Greenwich, and the server's strings are site-local.

## The registry

`utils/normalize_fields.py` lists every field the rules may touch, by kind, from production
DocField and Custom Field metadata (2026-10-13): phones and emails on Address, Contact (and its
Contact Phone / Contact Email rows), Customer, Supplier, Lead, Opportunity, Project, Employee,
Company, Warehouse and this app's own doctypes; websites on the ten doctypes that carry one;
Address lines, city, county, state and ZIP; Contact and Lead names.

It also lists what is **deliberately left alone**, each with its reason: the contact snapshots
ERPNext copies onto transactions (Sales Invoice, Quotation, Payment Entry and fourteen more —
they follow their source Contact); mail headers (`Issue.raised_by`); the derived matching key
`Fountain Move Request.phone_normalized`; `phone_ext` fields; fields named like contact values
that are not (`Triton Settings.email_model_id`); another app's doctype; every URL field that is
not a website; payment-gateway payloads; and a field fetched from the User record.

Two rules every consumer follows:

- **A record with `docstatus` 1 or 2 is never touched.** It records what was true when it was
  submitted.
- **The bulk cleanup never writes a Single.** Writing one field of a never-saved Single blanks
  its other defaults (CLAUDE.md). Settings pages are reported; a person saves them in the form.

`tests/test_normalize_fields.py` checks every `custom_` field is in the fixtures, every field on
one of this app's doctypes is in its JSON, and nothing is both registered and excluded.

## The dry-run report and the plan

**Data Normalization Preview** (Enhancements Core, System Manager) runs every registered value
through the rules and writes nothing. Each row is *Will change*, *Can't normalize — needs a
person* or *Already clean*; submitted records are counted at the top, not listed; child rows
link to their parent document.

1. **P2-A:** the rules, the twin, the registry, the report. Nothing on production changes.
2. **P2-B:** normalize on save and show phones the new way on the Desk — below. From here on every
   new or edited value is stored normalized; an existing value changes only when its record is
   saved.
3. **P2-C:** the one-time cleanup, run only after the product owner has read the preview and
   approved it, with the same rules and the same exclusions — below. Approved 2026-10-13
   (TASK-2026-02751), no exceptions.
4. **Only after the cleanup** are fields re-typed `Phone`, `Email` or `URL`. Frappe v16 validates
   a typed Data field on **every save**, so typing a field before its data is clean freezes every
   record holding a bad value — edits to anything else on it fail. That is exactly how a website
   field once froze 408 records. The cleanup does not make every field clean: the 84 values the
   rules cannot read stay as typed. So typing is a separate change, decided field by field from
   the report after the cleanup has run, and a field still holding a *Can't normalize* value is
   not typed until a person has fixed it.

## The cleanup (P2-C)

`utils/normalize_cleanup.run`, run once on deploy by the patch `normalize_stored_values`. **It
writes exactly the report's *Will change* rows** — its plan *is* the report's `collect()`, asked
for write targets — so what was approved is what runs.

| It leaves alone | Why |
|---|---|
| every *Can't normalize* value | The rules cannot read it; it stays exactly as typed for a person |
| a submitted or cancelled record, and every Single | The report's own rules: history is not rewritten, and writing one field of a never-saved Single blanks the rest |
| a value whose normalized form would be blank | A blank in a mandatory field (an address line) makes the record unsaveable. None existed on prod on 2026-10-13 |
| a value edited between the read and the write | Re-read and compared exactly, in Python: SQL equality would ignore a trailing space (PAD SPACE). The edit wins, and the save hook normalized it |

It writes with `frappe.db.set_value(..., update_modified=False)`: no controller and no
validation, so no record can fail half-way; `modified` is untouched, so no open form reports a
conflict. The parent's cached document is cleared for a child row. One commit per table.

**Before the first write** it saves a private undo file, `data-normalization-undo-<stamp>.json`,
attached to nothing, so only Administrator and server code can read it. It lists every planned
change's before and after; if it cannot be saved, nothing is written. To put the old values back
(only where a value still holds what the cleanup wrote):
`bench --site <site> execute erpnext_enhancements.utils.normalize_cleanup.undo --args "['<file name>']"`.

**Afterwards** the counts per field — never a value — are left as a comment on the *Data
Normalization Preview* report, and printed in the migrate log. It never raises, a second run plans
nothing, and the kill switch stops it.

## Normalize on save (P2-B)

`utils/normalize_hook.normalize_doc` is `doc_events["*"]["before_validate"]`. Frappe v16's
`Document.hook` composes `doc_events[doctype]` and then `doc_events["*"]` for the same event, so it
runs on every save on the site — and returns on one set lookup unless the doctype is in the
registry or is the parent of a registered child table (`CHILD_TABLE_PARENTS`: Contact, Prospect,
Call Routing Rule, ERPNext Enhancements Settings).

| It writes when | Why |
|---|---|
| `docstatus` is 0 and `_action` is `save` (or unset) | v16 `run_before_save_methods` runs `before_validate` for `save` and `submit` only; on a submit the incoming `docstatus` is already 1. Update-after-submit and cancel never reach it — and a write there would raise "Not allowed to change … after submission". The save that creates a record is a draft save. |
| `result.ok and result.changed` | A value the rules cannot normalize is left exactly as typed. The save is never refused. |
| not `frappe.flags.in_install` | Nothing an install saves was typed by a person, and `doc_events` fire during ERPNext's test bootstrap before this app's custom fields exist. A migrate, a patch or a Data Import *is* normalized. |
| the kill switch is off | See below. |

It never raises: each field and the document are guarded, and an unexpected error is logged at
most once per request, naming the doctype, field and exception type — never the value.

Registered child rows are normalized with their parent (a child row has no `before_validate` of
its own). Address `state` and `pincode` read the Address's own `country`.

**Kill switch, live, no deploy:** `bench --site <site> set-config disable_value_normalization 1`
(`... disable_value_normalization 0` turns it back on). Frappe re-reads `site_config.json` per
request, so the save hook stops at once; the Desk display stops on each person's next page load,
because boot then ships no `frappe.boot.sf_normalize`.

## On the Desk (P2-B)

`public/js/normalize/desk_display.js`, in `erpnext_enhancements.bundle.js`, imports the twin and
wraps exactly three things, each failing open to Frappe's own behaviour:

| Surface | How it is reached |
|---|---|
| Form, read-only field | `ControlInput.set_disp_area` → `frappe.format` → the Data formatter |
| Grid, static cell | `GridRow._format_static_value` → `frappe.format` |
| List view column | `ListView` → `frappe.format` |
| Report view cell | `ReportView` datatable `format` → `frappe.format(value, column.docfield)` |
| Script report cell | `query_report`'s `format_cell` → `frappe.format(value, column)` |
| Form and grid-row input | `ControlData.format_for_input` shows `(801) 555-0100`; `ControlData.parse` hands the model `+18015550100` |

The formatter matches a registered phone field on the docfield's `parent` + `fieldname`
(`frappe.boot.sf_normalize`, phones only, from `normalize_hook.desk_registry`). A Data value in
exactly the stored North American shape, `^\+1[2-9][0-9]{9}(,[0-9]{1,6})?$`, is shown the same way
with no match, because script-report columns and hand-built dfs carry no `parent`. Phone,
Read Only and Select have no formatter of their own in v16 and reach the same wrapper; a Link or
Select value is never reformatted by the fallback. In an input, what is being typed stays on
screen while it means the stored value, so the caret never jumps; a value that cannot be
normalized is kept as typed; clearing the field clears it. List filters (`df.is_filter`) are left
alone, so a search stays what was typed.

Hand-built HTML on the Desk reads `erpnext_enhancements.normalize.phone_display` (the contacts
directory in `unified_tab_controller.js` does).

## Print, email and SMS (P2-B)

`sf_phone(value)` is the Jinja global (`utils/value_display.py`, registered individually like
`sf_date`). `print_style.format_phone` already shows the stored form (P2-A). Switched in P2-B:

| Where | Now |
|---|---|
| Notification *New Lead Created* | `sf_phone(doc.get("mobile_no") or doc.get("phone"))` |
| Package Dispatch print format (`package_dispatch/setup_print_formats.py`) | `sf_phone(doc.recipient_phone)` |
| Fountain move duplicate / failed emails | `sf_phone(req.phone)` |
| The company phone on customer pages and emails (`fountain_move.get_contact_phone`: contract signing, visit sign-off, fountain move, the invite email) | `phone_display(Company.phone_no)` |
| Customer confirmation email (`customer_confirmations._phone`) | `phone_display(...)` |
| Water Engineering submittal packet (engineer and contractor phone) | `phone_display(...)` |
| Rental crew task body and rental portal (`site_contact_phone`) | `phone_display(...)` |
| Contract templates (`project_contract._fill`) | `shown_if_stored_phone`: only a value in the exact stored shape is reformatted, so a contract already out for signature does not drift |
| Planner person card (`api/planner_views._contact`) | `phone_display(...)`; `tel_number` leaves an extension off |

## Who compares a phone or an email, and does storing the one form break it? (P2-B audit)

Storing `+18015550100` where `801-555-0100` used to be breaks any lookup that compares the stored
string exactly. Every consumer in this app, 2026-10-13:

| Consumer | How it compares | Verdict |
|---|---|---|
| `api/telephony._get_caller_info` (inbound caller ID, SMS and missed-call linking, `send_sms`) | REGEXP over the last ten digits, `.*`-joined | OK — matches every format |
| `telephony.send_sms` 24-hour signature check | `Communication.phone_no LIKE %<10 digits>` | OK |
| `telephony.process_unified_sms` assignment | `Communication.phone_no` = Twilio's E.164 `From` | OK — Communication is not registered; a Desk-sent SMS now logs E.164, so this matches *more* |
| `fountain_move.matching._phone_query` | separators and `+` stripped in SQL, `LIKE %<10 digits>` | OK |
| `Fountain Move Request.phone_normalized`, invites | `utils/phone.normalize_phone` digits | OK — computed in `validate`, after the hook |
| Call routing: Employee `cell_number`, Call Routing Target `phone_number`, `default_forward_number` (`call_routing_match.to_e164`) | trusts a `+` and keeps every digit | **BREAKS with an extension**: `+18015550100,12` became `+1801555010012`, a stranger's number. Fixed: `ai_governance/call_routing_dial.to_dialable` refuses a value with a `,` or `;`, as `to_e164` refused `801-555-0100 x12` before |
| `api/planner_views.tel_number` | digits | **BREAKS with an extension** (dials 14 digits; it already did for `x12`). Fixed: the extension is left off |
| Emails: `portal_login._link_contact`, `stripe_payments.get_portal_customers`, `handoff` Contact lookup, `fountain_move` Contact and Lead email, QuickBooks Customer/Supplier email, ERPNext `Lead.check_email_id_is_unique` | SQL `=` | OK — v16 creates every table `utf8mb4_unicode_ci` (`frappe/database/mariadb/database.py`), case-insensitive and PAD SPACE, and the stored value is now lowercased and trimmed: it matches more, never fewer |
| e-sign `_email_matches`, visit sign-off `clean_email` | casefold / lowercase both sides | OK |
| `sync_contact` (`sync_from_contact`, `sync_from_main_doc`) | `!=` between copies | OK — both sides normalized on save; a legacy copy causes one re-save of the party, which normalizes it too |
| `sync_employee_phone_to_user` | copies `cell_number` to `User.phone` | OK — v16's Phone check allows `+` and `,` |
| `dev_checks._cleanup` | `custom_phone_number = "+18015550999"` | OK — already E.164 |
| Kiosk / PIN, web lead ingress, Google sync | no phone or email lookup | — |

**Triton** (read only; to be changed there): its caller-ID lookups compare digits or E.164 on both
sides and are fine for plain numbers. With an extension: `core/call_routing.to_e164` (its copy of
the shared contract) dials `+18015550100,123` as `+18015550100123` — it needs the same guard as
`call_routing_dial`, with a shared test vector; `voice.py` `_lookup_caller_name` compares digits
and so misses a number stored with an extension; outbound click-to-call, SMS send and the gateway
endpoints hand an ERPNext number with `,123` straight to Twilio. Its Voice Monitor, Voice Archive
and chat call card show ERPNext phones raw and will now show `+18015550100`.

**Not covered yet:** the Plan a Trip and itinerary contact cards (`api/travel.py` returns phones
as stored and the pages draw them as text; their `tel:` links already drop an extension), and any
other custom page that draws a stored phone as text rather than through `frappe.format`.
