# WI-078: Schedule of Values and AIA progress billing

**Phase:** 2   **Type:** APP_CODE   **Size:** L
**Blocked by:** WI-077 (cost code catalog) for the projection seam only — the manual path is independent
**Blocks:** nothing

## Why

Two of Sapphire's three money documents are still Excel, and one of them bills real money every month.
**DOC-00160 Schedule of Values** is the customer-facing sheet handed to a general contractor.
**DOC-0000 Pay Application Template** is AIA G702/G703 progress billing, in live use on Lehi Sanctuary
(PRJ-00219) since January 2025 and on Big D and Lagoon. A workbook that bills monthly against a signed
contract is the worst remaining place for arithmetic to live untested — and the current one hard-codes
six months, so a job running longer is handled by copying columns.

The Schedule of Values is also the document that makes WI-077's renumbering matter. A GC reads these
codes. Everything in WI-077 about MasterFormat 1995 numbers pointing at unrelated sections lands here,
on paper, in front of the customer.

## Native-first check

ERPNext has no Schedule of Values, no retainage ledger and no G702/G703. Native `Sales Invoice` with a
payment schedule covers none of the arithmetic: no scheduled value per line, no previous-applications
carry-forward, no stored materials, no dual retainage on work and materials, no continuation sheet.
Progress billing against a contract value is genuinely absent from the product. Retainage is the one
piece with a native foothold — a Company default overridden per contract and per application (D16).

## Independent of the estimate, deliberately

Track B is usable before Track A exists. An SOV can be authored by hand from day one, which is what
PRJ-00219 and the other 654 projects need now. Only the estimate-to-SOV projection seam depends on
WI-077. Build the manual path first and do not let the estimate block the billing.

## Scope

- `Project Schedule of Values` + line child table, manual authoring, contract value reconciliation.
- **Integer-cents engine.** Money that is summed across twelve monthly applications and then reconciled
  against a contract total cannot use floats. Shares `money()` with `quality/estimate_math.py` — one
  rounding implementation for the whole chain, ported verbatim from frappe's `_bankers_rounding`.
- `Pay Application`: previous applications, this period, stored materials, retainage on work and on
  materials separately, total earned less retainage, balance to finish.
- Time-phased change orders — a change order signed in month 4 changes the scheduled value from month 4,
  not retroactively, and the G703 must show the original and revised columns.
- G702, G703 and cover letter print formats. **Read `original_data`, never `data`**: in a report print
  format `data` is `get_data_for_print()`, the rows as currently sorted and inline-filtered on screen, so
  re-sorting reorders a legal billing document and a column filter silently drops lines from it.
- Retainage release, the monthly entry surface, and the Sales Invoice / QuickBooks seam.

## Acceptance criteria

- The Lehi Sanctuary workbook reproduces exactly — every month already billed, to the cent, including
  retainage. This is the acceptance test: if the numbers do not match a document a customer has already
  paid against, nothing else matters.
- A job longer than six months needs no template surgery.
- Sum of G703 line scheduled values equals the contract value, enforced, with the failure naming the lines.
- A pay application cannot be submitted when previous-application figures disagree with the prior
  submitted application.
- Print formats render. `crew_qualification_roster` produced nothing for 27 releases because a double
  brace in a header comment compiled as code — `frappe.template.compile` rewrites `{{` across the whole
  file before parsing, comments included. Render every print format through a real compile in CI.
- Retainage percentage resolves Company → contract → application, and clearing an override falls back
  rather than zeroing.

## Open, needs a human

1. **Retainage on stored materials** — same rate as work, or different? Common for them to differ.
2. **Does a GC ever require a specific G703 line ordering** that differs from cost-code order?
3. **Sales tax on a progress billing** — on the period amount or on the earned-to-date figure? Utah law
   branch, and WI-036's CPA matrix is the gate.
