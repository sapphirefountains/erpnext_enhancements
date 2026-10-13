# Data normalization: phones, emails, websites, addresses and dates

What a contact value looks like when it is stored and when it is shown, the fields those rules
apply to, and the plan for applying them to production data. Form kit P2-A (2026-10-13).

The rules live in one place each:

| Where | What |
|---|---|
| [`utils/normalize.py`](../erpnext_enhancements/utils/normalize.py) | The rules, server side. Pure, frappe-free (`print_style` imports it) |
| [`public/js/normalize/normalize.js`](../erpnext_enhancements/public/js/normalize/normalize.js) | The JavaScript twin: same API, same results. Not in a bundle yet |
| [`tests/fixtures/normalize_cases.json`](../erpnext_enhancements/tests/fixtures/normalize_cases.json) | The shared cases. `tests/test_normalize.py` runs every one through both and compares them field by field |
| [`utils/normalize_fields.py`](../erpnext_enhancements/utils/normalize_fields.py) | Which fields the rules may touch, and which they must not |
| *Data Normalization Preview* report | The dry run: what the cleanup would change, and what needs a person |

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

1. **P2-A (this change):** the rules, the twin, the registry, the report. Nothing on production
   changes.
2. **P2-B:** normalize on save (a `before_validate` hook over the registry) and show phones and
   dates the new way on the Desk, through the JavaScript twin.
3. **P2-C:** the one-time cleanup, run only after the product owner has read the preview and
   approved it, with the same rules and the same exclusions.
4. **Only after the cleanup** are fields re-typed `Phone`, `Email` or `URL`. Frappe v16 validates
   a typed Data field on **every save**, so typing a field before its data is clean freezes every
   record holding a bad value — edits to anything else on it fail. That is exactly how a website
   field once froze 408 records.
