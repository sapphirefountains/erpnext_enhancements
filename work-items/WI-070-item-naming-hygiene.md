# WI-070: Item naming hygiene — 75 live names to normalise and a 135-row `(deleted)` shadow master to retire
**Phase:** 1   **Type:** DATA   **Size:** M
**Blocked by:** WI-028 (bucket A only), WI-050   **Blocks:** nothing

## Why
The *ERPNext Item Naming Schema* SOP v1.0 (19 Aug 2026) set the standard and measured the gap:
120 of 713 records satisfied it. The standard is now enforceable in the sense that matters —
`item_naming_check` and the `ee-item-naming-validator` skill stop *new* records drifting
(v1.335.0, advisory) — but nothing has been done to the records already there.

Two populations, and conflating them is what makes the headline numbers misleading. **135 rows
are a QuickBooks shadow master**: every one carries a `(deleted)` suffix, and every one has
`item_name` identical to `item_code`, so they contribute nothing but noise to every search
(prod_items, verified 2026-08-19). **581 rows are the live catalogue**, and their defects are
far smaller than the raw counts suggest — 75 non-uppercase names, not 210, because 135 of the
210 are the shadow master. Retiring the shadow master is therefore the single cheapest action
available and must come first.

Naming is also not merely untidy in places, it is wrong: `CON-OFFC-INK-HP-952-COLOR` (an HP 952
ink cartridge) is named `WATER LEVEL SENSOR, REED SWITCH, M10 THREAD`, and four breakers share
one name with no amperage in it, so they cannot be told apart (SOP D-1, D-2).

## Native-first check
No ERPNext feature validates Item Name shape; `Item Naming By` in Stock Settings governs code
generation, not descriptions. Remediation is entirely native — Data Import or
`frappe.db.set_value`, plus `frappe.delete_doc` for the shadow master, all of which WI-025,
WI-026 and WI-028 already use. Detection is native too: a Query Report over `tabItem` would
serve the desk as well as anything custom. The one piece of custom code involved,
`inventory_enhancements/item_naming_rules.py`, already exists and was built for the *assistant*
channel, which a Query Report cannot serve; this item reuses it rather than reimplementing its
rules in SQL. **Verdict:** native for both the fix and desk-side detection; DATA only, no new
code.

## Preconditions
- **WI-028 executed** — required for bucket A only, and the reason is in the data. Every
  reference to a `(deleted)` code sits on a **draft**: 218 Sales Invoice lines (31 distinct
  codes), 74 Quotation lines (24), 15 Purchase Order lines (13), 7 Purchase Invoice lines (6).
  Zero submitted documents, zero Stock Ledger Entries, zero Material Requests (prod_items,
  verified 2026-08-19). WI-028 bulk-deletes the draft Sales Invoice and Quotation mirror, so
  running bucket A first means carefully preserving 45 codes of which 32 stop being referenced
  days later. Buckets B–F are not blocked and can start immediately.
- **WI-050 hazard verification** — the wildcard `'*'` `after_save` Triton sync hook (WI-025
  hazard H1) fires one queued POST per ORM save. A 75-row normalisation pass is 75 of them.
- Verified Frappe Cloud backup, and a pre-run CSV export of `(name, item_code, item_name,
  item_group, disabled)` for every row this item touches. The export is the rollback.
- **A vendor-catalogue source for the four GMCB breakers.** This is fact-finding against a
  supplier datasheet, not a business decision — note the OD-2/OD-3 adjacency and do **not**
  open an OD for it.
- The normalisation function agreed in writing before any collision count is quoted. This item
  uses `item_naming_rules.NORMALISATION` — *uppercase, then remove every character that is not
  A-Z or 0-9* — and every collision figure below is relative to it.

## Scope
Ordered so the cheapest wins land first.

**A. Retire the `(deleted)` shadow master (135 rows).** After WI-028: delete the rows that are
then fully unreferenced; set `disabled = 1` and leave in place any that a surviving draft PO or
Purchase Invoice still points at. Do **not** rename them individually (SOP D-8). This bucket
alone removes 135 of the 190 `item_code = item_name` rows and 135 of the 210 non-uppercase
names.

**B. The two live placeholders.** `PDT-00XX` and `PDT-00051` — both have `item_name` identical
to `item_code`, neither carries a `(deleted)` suffix, so neither is caught by bucket A. Confirm
unreferenced, then delete. **`PDT-00051` is not `PDT-0051`**; the latter is a real level sensor
and the two differ by one digit. **Not in this bucket:** `PDT-0008 VFD BYPASS W/MOTOR
PROTECTION, 5HP - copy`. SOP D-7 says to retire it against "the numbered `PDT-` record" and
there is no such record — it is the only copy of that product. Rename it in place to `PDT-0008`
with a compliant name, and never reissue 0008.

**C. Mechanical normalisation, live rows only.** Uppercase (75); leading/trailing whitespace
(3); trailing comma (1); double space (1); comma with no following space (1); GREY → GRAY (7);
the word INCH → the inch mark (9); COROSIVE → CORROSIVE (1, `B01M3Y86MY`); SS304S → SS304 (1,
`P1126 SS`). No judgement required for any of these, which is what makes them first.

**D. Name collisions (4 groups under the stated normalisation).** The `GMCB-1B-1` / `-6` / `-10`
/ `GMCB-1C-10` four-way is blocked on the vendor catalogue — rename each with its amperage and
curve per SOP D-2. `100GM DC24V DMX512 3W` / `xyh100gm-3x1w` and `PDC80TIMDMX` / `PDC90TIM` are
suspected same-part pairs (SOP D-3): confirm against transaction history, then retire the
redundant record. The fourth group is the D-1 mismatch and is fixed by bucket E.

**E. Code/name mismatches.** `CON-OFFC-INK-HP-952-COLOR` → `INK CARTRIDGE, HP 952, COLOR` (SOP
D-1). `Tubing, 1-1/2", 316 SS, 3'` has the vendor number `89995K648` as its name — code and name
are swapped. Also the third suspected D-3 pair, `4010052576503` / `57650`: the same VARIONAUT
pump under two codes, two word orders and two Item Groups. It collides on **nothing** under any
normalisation, which is why it needs a human and not a query.

**F. Schedule conflicts (SOP D-4).** `406-040`, `417-040`, `417-080` state SCH80 while their
`4xx-` prefix is the Schedule 40 series. Verify against the vendor catalogue and correct
whichever of the two is wrong; 75 of the 78 records in this family agree with their prefix.

**G. Appendix A gaps for the Process Owner.** Eight unapproved leading words are live on two or
more records and are missing from SOP Tier 3: `BATTERIES` (5), `FERRULES` (3), `PENS` (2),
`SCOURING PADS` (2) — plurals of approved categories; `PRINTER FILAMENT` (3) — sub-type led,
should be `FILAMENT, PRINTER`; `SHARPIES` (2) — a brand and a plural; `UNI-INSERT` (2) and
`UNI-SHIM` (2) — vendor product-line names used as categories. Separately, Tier 3's
`SUBPANELT → PANEL, SUB` names `PANEL`, which is on neither Tier 1 nor Tier 2, so the SOP's own
replacement fails its own Step 4.1. Route all of it to the Process Owner; do not invent
categories to close it.

Execution: batched `frappe.db.set_value` / `frappe.delete_doc`, commit every 100, Triton target
paused — the WI-025 / WI-026 / WI-028 house pattern.

## Acceptance criteria
Every predicate below is written in the `BINARY` / `TRIM` form deliberately. The naive forms
return 0 today against records that are genuinely defective, so a criterion written the obvious
way passes vacuously — which is the trap this item exists to close.

- ``SELECT COUNT(*) FROM `tabItem` WHERE item_code LIKE '%(deleted)%' AND disabled = 0`` = 0
- ``SELECT COUNT(*) FROM `tabItem` WHERE item_code NOT LIKE '%(deleted)%' AND BINARY item_name <> BINARY UPPER(item_name)`` = 0
  — `BINARY` is load-bearing; without it MariaDB's default collation makes the comparison always false.
- ``SELECT COUNT(*) FROM `tabItem` WHERE item_code NOT LIKE '%(deleted)%' AND BINARY item_name <> BINARY TRIM(item_name)`` = 0
  — likewise: `item_name <> TRIM(item_name)` returns 0 today against three real offenders, because PAD SPACE collation ignores trailing spaces in comparison.
- ``SELECT COUNT(*) FROM `tabItem` WHERE item_code NOT LIKE '%(deleted)%' AND (TRIM(item_name) LIKE '%,' OR item_name LIKE '%  %' OR item_name REGEXP ',[^ ]')`` = 0
  — the `TRIM` is load-bearing: the one live trailing comma is followed by a space, so `LIKE '%,'` alone returns 0.
- ``SELECT COUNT(*) FROM `tabItem` WHERE item_code NOT LIKE '%(deleted)%' AND (UPPER(item_name) LIKE '%GREY%' OR UPPER(item_name) REGEXP '[[:<:]]INCHE?S?[[:>:]]' OR UPPER(item_name) LIKE '%COROSIVE%' OR UPPER(item_name) LIKE '%SS304S%')`` = 0
- ``SELECT COUNT(*) FROM `tabItem` WHERE item_code IN ('PDT-00XX','PDT-00051')`` = 0 **and** ``SELECT COUNT(*) FROM `tabItem` WHERE item_code = 'PDT-0051'`` = 1 — proves the four-digit/five-digit distinction was honoured rather than eyeballed.
- ``SELECT COUNT(*) FROM `tabItem` WHERE item_code LIKE 'PDT-0008%'`` = 1, and that row's `item_name` contains no comma-plus-`copy`. The product survives; the working suffix does not.
- The collision query returns 0 rows:
  ``SELECT UPPER(REGEXP_REPLACE(item_name,'[^A-Za-z0-9]','')) AS n, COUNT(*) c FROM `tabItem` WHERE item_code NOT LIKE '%(deleted)%' GROUP BY n HAVING c > 1``
- Human action: open `CON-OFFC-INK-HP-952-COLOR` in the desk and confirm the name describes an ink cartridge.
- Human action: the Process Owner has ruled on every word in bucket G, and Appendix A of `docs/item-naming-schema.md` plus `item_naming_rules.APPROVED_CATEGORIES` / `TIER3_REPLACEMENTS` agree with that ruling.

## Rollback
Keyed restore from the pre-run CSV export of `(name, item_code, item_name, item_group,
disabled)`; deleted rows re-created from the same export. Note that `public/js/item.js` rewrites
`custom_item_identifier` from `doc.name` on every form refresh, so a desk-side restore touches
that field too and a diff taken afterwards will look noisier than the change was.

## Explicitly NOT in this work item
Renaming `item_code` to the SKU scheme or any other scheme (renames ripple through every linked
document — WI-025 fenced this off and it stays fenced); populating `custom_sku` (14 ad-hoc
values, not authoritative); `item_group` assignment for the 122 rows on the root, which is
WI-025; merging the `CON-ELEC-BREAK-*` and `GMCB-*` parallel families or resolving their
`is_stock_item` disagreement (a product decision for the inventory workstream); standardising
the `Unit` / `Nos` stock-UOM split (SOP C-10 — a governance decision, and `item_naming_check`
reports the distribution rather than arbitrating it); adding any `Item` doc_event or otherwise
making the schema block a save; and any change on test.
