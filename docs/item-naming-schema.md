# ERPNext Item Naming Schema

> **This is a conversion, not the controlled document.**
> The document of record is `ERPNext_Item_Naming_Schema_SOP.docx` — **version 1.0,
> 19 August 2026**, Process Owner **Purchasing Agent**, Department Operations –
> Inventory Management, author Nikolas Bradshaw. This Markdown copy exists so that the
> rules can be cited from code and from the `ee-item-naming-validator` assistant skill,
> and so `inventory_enhancements/item_naming_rules.py` has something to be diffed
> against. If the DOCX moves to 1.1 and this file still says 1.0, that is the drift
> showing — which is the point of stamping it.
>
> Content below is the SOP's, restructured into Markdown tables and otherwise unaltered.
> Blockquotes marked **Verified 2026-08-19** are *not* part of the SOP: they record
> where a live check of ERPNext Production disagreed with it, and are the only editorial
> additions in this file.

## 1. Purpose

Establish one enforceable naming schema for records in the ERPNext Item doctype, so that
any employee can reliably find an item that already exists before creating a new one, and
so that items sort and group predictably in Material Requests, Purchase Orders, and stock
reports.

This document is grounded in a full audit of all 713 Item records in ERPNext Production,
taken 19 August 2026. Where the prior reference material — Inventory Item Naming
Convention Rev1, Item Description Naming Convention Notes, and the Naming Convention Sheet
— disagrees with what is actually in ERPNext, ERPNext is treated as the authority and the
conflict is recorded in Appendix C.

Measured baseline: 120 of 713 records (16.8%) satisfy the convention as previously
written. This document therefore does two jobs. It states the standard to follow going
forward (Section 5), and it records the gap that remains to be closed (Appendix B and
Appendix D).

## 2. Scope

Applies to every person who creates, renames, or searches for an Item record in ERPNext
Production: the Purchasing Agent, Technical Leads, Project Managers, group leads raising
Material Requests, and anyone entering items on behalf of a requestor under the Purchasing
Process Mapping SOP.

Fields covered:

- `item_code` — the Item Code, which is also the record's primary key
- `item_name` — the Item Name; this field carries the comma-delimited description schema
- `description` — the long description; optional supporting detail
- `item_group` — classification within the Item Group tree

Explicitly out of scope: Item Variants and Item Attributes (ERPNext currently holds zero
variant templates and zero variants), BOM naming, Supplier and Customer naming, and
pricing. The 135 QuickBooks-era records carrying a "(deleted)" suffix are out of scope for
day-to-day naming and appear only as a cleanup backlog in Appendix D.

### Which field carries the convention

Both prior reference documents are written around the description. In ERPNext as
configured, the comma-delimited schema lives in Item Name (`item_name`), not in the
Description field. Of the 578 active items, 230 have an empty description, 178 repeat the
Item Name verbatim, and 170 hold something different — in several cases a better-formed
string than the Item Name itself (Appendix C, C-8).

**Item Name is authoritative. Description is optional supporting detail.**

## 3. Prerequisites & Tools

- ERPNext Production access with the Item Manager or Purchase User role
- The approved category vocabulary in Appendix A of this document, which supersedes the
  Naming Convention Sheet spreadsheet
- The vendor's own part number, taken from the quote, invoice, or catalogue page, for any
  purchased item
- Stock Settings must remain set to **Item Naming By = Item Code**. Item Codes are typed by
  hand; ERPNext applies no naming series to the Item doctype, so nothing in this schema is
  enforced by the system. Compliance is procedural, which is why Phase 4 of Section 5 is
  not optional.
- Written approval from the Process Owner before any new CATEGORY is added to Appendix A

## 4. Visual Process Map

Follow this path every time an item is created. The search step at the top is the one most
often skipped, and it is the step that prevents the duplicate records listed in Appendix D.

## 5. Step-by-Step Procedure

### Phase 1: Search before you create

- **Step 1.1** *[Requestor]* Search the Item list by the vendor part number, exactly as
  printed on the quote or invoice. Most items in ERPNext are coded by vendor part number,
  so this finds the record if it exists.
- **Step 1.2** *[Requestor]* Search again by CATEGORY word alone — VALVE, ELBOW, PUMP. A
  record created before this SOP may be worded differently from what you expect, so a
  part-number search alone is not sufficient.
- **Step 1.3** *[Requestor]* If the item exists, use it. Do not create a second record.
  Five duplicate or near-duplicate pairs already exist in ERPNext and are listed in
  Appendix D.
- **Step 1.4** *[Requestor]* If the item does not exist and you are not the Purchasing
  Agent, send the full specification to the Purchasing Agent, who creates the record. This
  matches the Purchasing Process Mapping SOP.

> **Verified 2026-08-19:** Step 1.3 says *five* pairs; Appendix D-3 lists *three*. The two
> are also different questions. Name collisions are mechanical and findable — under
> "uppercase, then strip every non-alphanumeric character" the live corpus has four
> colliding groups: the D-2 four-way, `100GM DC24V DMX512 3W` / `xyh100gm-3x1w`,
> `PDC80TIMDMX` / `PDC90TIM`, and the D-1 name collision. The D-3 VARIONAUT pair
> (`4010052576503` / `57650`) collides on **nothing**, because the same pump is described in
> two word orders. A duplicate count is a property of the normalisation rule, not of the
> data, and is meaningless quoted without it.

### Phase 2: Assign the Item Code

Four code families are in use. Choose the family first, then apply its rule.

| Family | Active records | Rule | Example |
|---|---|---|---|
| Vendor / manufacturer part number | 410 | Use the vendor's part number exactly as printed. Do not add a prefix, strip leading zeros, or change the case. | `806-020` |
| `CON-` consumable | 91 | `CON-<GROUP>-<DESCRIPTOR>[-<SIZE>]` — GROUP is ELEC, OFFC, or SRV. For shop and office stock with no useful vendor number. | `CON-ELEC-FUSE-2` |
| `PDT-` product | 38 | `PDT-####` — four digits, next unused number. Sapphire-built products and assemblies. | `PDT-0016` |
| `SRV-` service | 39 | `SRV-###` — three digits, next unused number. Labor, travel, fees, and rentals. | `SRV-202` |

- **Step 2.1** *[Purchasing Agent]* For a purchased part, copy the vendor part number
  character for character. Vendor numbers carry meaning that is lost if you edit them.

  *Worked example.* In the Spears/Lasco PVC range used throughout the system, `4xx-`
  denotes Schedule 40 and `8xx-` denotes Schedule 80, and the numeric suffix is the
  nominal size: `806-020` is a Schedule 80, 2 inch, 90° socket elbow. Across the 78 records
  in this family that state a schedule, 75 agree with their code prefix and 3 do not —
  those 3 are flagged in Appendix D.

- **Step 2.2** *[Purchasing Agent]* For a shop or office consumable with no useful vendor
  number, build a `CON-` code. Keep the descriptor short and put the size last.
- **Step 2.3** *[Purchasing Agent]* For a Sapphire-built product, take the next unused
  `PDT-` number. For a service, labor, travel, or fee line, take the next unused `SRV-`
  number.
- **Step 2.4** *[Purchasing Agent]* Never put a comma in an Item Code, and never leave a
  working suffix such as "- copy" on a saved record.

> **Verified 2026-08-19:** "next unused number" is not `MAX() + 1`. Both series are
> allocated in **semantic blocks of one hundred** — PDT runs 00xx control systems, 01xx
> nozzles, 02xx fittings, 04xx chemicals, 05xx tools, 07xx service materials; SRV runs 0xx
> design, 1xx build, 2xx service, 4xx rental, with 3xx unallocated. PDT holds 36 numeric
> codes spread across 0–701. Take the next free number **inside the right block**;
> `item_naming_check` reports occupancy and gaps and deliberately does not choose.
>
> Determining occupancy is also harder than it looks, and both obvious queries are wrong.
> `item_code REGEXP '^PDT-[0-9]{4}$'` reports **0008 as free** — the `$` rejects the trailing
> text on `PDT-0008 VFD BYPASS W/MOTOR PROTECTION, 5HP - copy`, which is the only record of
> that product. Unanchor it and `CAST(REGEXP_SUBSTR(item_code,'[0-9]+'))` swallows the
> five-digit family (`PDT-00000 (deleted)` … `PDT-00013`, `PDT-00040`, and the live
> `PDT-00051`), falsely occupying four-digit slots that are free. **`PDT-0051` and
> `PDT-00051` are different items.**

### Phase 3: Build the Item Name

The Item Name is a comma-delimited string, in ALL UPPERCASE, running from the broadest
category to the most specific detail:

```
CATEGORY, SUB-CATEGORY, KEY FEATURE, MATERIAL, SIZE, RATING, PACKAGING
```

Include a segment only if it carries information. Omit it entirely rather than writing N/A
or leaving an empty pair of commas. Segments always appear in the order below.

| # | Segment | Required | What goes here, with terms in use in ERPNext |
|---|---|---|---|
| 1 | CATEGORY | Always | The broadest noun, singular, taken from Appendix A: VALVE, ELBOW, PIPE, PUMP, TEE, BUSHING, COUPLING, FILTER, LIGHT, SENSOR |
| 2 | SUB-CATEGORY | Where one exists | The major variant within the category: BALL, SOLENOID, 90, 45, STRAIGHT, REDUCER, SUBMERSIBLE, SOLVENT |
| 3 | KEY FEATURE | Where it differentiates | Connection type, standard, or function: SOC, SOCXSOC, SPIGOTXSOC, SPIGOTXFPT, B/E, UTILITY, MOTORIZED, RGB-DMX |
| 4 | MATERIAL | Physical goods | PVC, SS304, 316 SS, CS, BRASS, EPDM, PLASTIC, FIBERGLASS. PVC appears 63 times and is the most common. |
| 5 | SIZE | Where the item has one | Nominal size with the inch mark, schedule appended: `2" SCH40`, `2-1/2"X2" SCH80`, `1" NPT`, `20X25X1` |
| 6 | RATING / ATTRIBUTE | Optional | Electrical rating, capacity, colour, or series: 24VAC, DC24V 18W, 5HP, 10300 GPH, GRAY, `1-5/8" SERIES` |
| 7 | PACKAGING | Optional | How the item is sold, where it matters: QUART, 6-PACK, BULK ROLL, 20FT. This is not a substitute for the `stock_uom` field. |

> **Verified 2026-08-19:** Only segments 1, 5 and 6 are machine-decidable — CATEGORY has a
> controlled vocabulary, SIZE and RATING have recognisable token shapes. SUB-CATEGORY, KEY
> FEATURE, MATERIAL and PACKAGING have neither, and nothing can tell that `BRASS` in
> position 4 is a MATERIAL while `BRASS` in position 3 is a KEY FEATURE. `item_naming_check`
> therefore returns segments **positionally** and says so; it does not claim to have
> classified them.

#### Formatting rules

Each rule below is written against a real record in ERPNext.

| Rule | Correct | Do not write |
|---|---|---|
| Separate segments with a comma and one space | `ELBOW, 90, SOC, PVC, 2" SCH80` | `Light, Submersible, RGB_DMX,SS, DC24V 3W` |
| Write the whole name in capitals | `PUMP, PENTAIR EQKT750, 7.5HP` | `Pump, Aquasurge 2000` |
| Keep the CATEGORY singular | `FUSE, 2 AMP` | `FUSES, 2 AMP` |
| Lead with the category, never with the sub-type | `FILTER, SAND, PENTAIR TR140C` | `SAND FILTER, PENTAIR TR140C-3` |
| Write sizes as digits with an inch mark | `COUPLING, REPAIR, PVC, 1", WHITE` | `COUPLING, REPAIR, PVC, 1 INCH, WHITE` |
| Put the size before its schedule, in one segment | `TEE, STRAIGHT, SOC, PVC, 2-1/2" SCH80` | `TEE, SOC, PVC, SCH80, 1"` |
| Never use a brand as the category | `SWITCH, DISCONNECT, NON-FUSED, ABB, 600V, 40A` | `ABB, N/F, SW, 600V, 40A, 6MM` |
| Write specifications as segments, not prose in brackets | `TERMINAL BLOCK, 2 POLE, ABB 1SNA115271R2200` | `Terminal block, 2 pole (ABB 1SNA115271R2200)` |
| Use GRAY, not GREY | `CEMENT, SOLVENT, 711, PVC, QUART, GRAY` | `CEMENT, SOLVENT, PVC, GREY, 1/2 PT` |
| Leave no trailing space and no trailing comma | `PIPE CLAMP, STRUT, SS304, 3", 1-5/8" SERIES` | `PLATE, WALL, COROSIVE RESISTANT, STAINLESS STEEL, ` |
| Make every name unique enough to tell records apart | `CIRCUIT BREAKER, SUPPLEMENTARY, GLADIATOR, 1 AMP` | `PROTE, SUPPLEMENTARY, MINIATURE, GLADIATOR` |

#### Worked examples from ERPNext

| Item Code | Item Name | Segments used |
|---|---|---|
| `806-020` | `ELBOW, 90, SOC, PVC, 2" SCH80` | 1 CATEGORY / 2 SUB / 3 KEY / 4 MATERIAL / 5 SIZE |
| `2622-010` | `VALVE, BALL, UTILITY, SOC, PVC, 1", EPDM` | 1 / 2 / 3 / 3 / 4 / 5 / 6 |
| `837-292` | `BUSHING, REDUCER, SPIGOTXSOC, PVC, 2-1/2"X2" SCH80` | 1 / 2 / 3 / 4 / 5 |
| `022035` | `PUMP, PENTAIR WHISPERFLO XF VS, 5HP` | 1 / 2 / 6 |
| `13974` | `CEMENT, SOLVENT, 711, PVC, QUART, GRAY` | 1 / 2 / 3 / 4 / 7 / 6 |
| `100GM DC24V DMX512 3W` | `LIGHT, SUBMERSIBLE, RGB-DMX, SS, DC24V 3W` | 1 / 2 / 3 / 4 / 6 |
| `2080-L20E-20QBB` | `CONTROLLER, ETHERNET, I/P, MICRO820, PLC` | 1 / 2 / 3 / 3 / 3 |

### Phase 4: Validate, classify, and save

- **Step 4.1** *[Purchasing Agent]* Confirm the CATEGORY is on the Appendix A list. If it is
  not, stop and request the addition from the Process Owner. Do not invent a category — 176
  undeclared category words are already in the system, which is the single largest cause of
  failed searches.
- **Step 4.2** *[Purchasing Agent]* Read the name back character by character: capitals
  throughout, a space after every comma, no double spaces, no trailing space, no trailing
  comma.
- **Step 4.3** *[Purchasing Agent]* Search the Item list one more time using the CATEGORY
  and SIZE you just typed, to confirm you have not created a near-duplicate of an existing
  record.
- **Step 4.4** *[Purchasing Agent]* Set the Item Group from the Item Group tree, and set
  `stock_uom`. Do not leave the item in All Item Groups — 122 active records sit there today
  and only one of them is compliant.
- **Step 4.5** *[Purchasing Agent]* Use the Description field only for detail that does not
  belong in the name: manufacturer notes, catalogue wording, or a link. Never let the
  Description contradict the Item Name.
- **Step 4.6** *[Purchasing Agent]* Save.

> **Verified 2026-08-19:** Steps 4.2 and 4.3 are the two the tooling can do for you, and
> both defeat the obvious SQL. `item_name <> TRIM(item_name)` returns **0** on a corpus with
> three trailing-whitespace offenders, because MariaDB's PAD SPACE collation ignores
> trailing spaces in comparison — use `BINARY` or compare lengths. `item_name LIKE '%,'`
> returns **0** against one real trailing comma, because that record has a trailing space
> after it — strip before testing. Any acceptance criterion written the naive way passes
> vacuously.

## 6. Troubleshooting & Exceptions

| If this happens... | Then do this... |
|---|---|
| The item has no category that fits anything in Appendix A | Name the item with the closest Tier 1 category and raise the gap with the Process Owner the same day. Do not invent a category and do not leave the item unnamed while you wait. |
| The vendor part number contains a comma or is longer than the Item Code field allows | Strip the comma only — keep every other character. Record the full original number in the Description field. |
| Two vendors sell the same physical part under different numbers | Create one record under the primary vendor's number. Record the alternate number in the Description. Do not create a second Item. |
| You find an existing item whose name breaks this SOP | Do not create a replacement. Correct the existing record's Item Name in place so history, stock, and transactions stay attached to it. |
| The item is a one-off, buy-once purchase for a single project | It still needs a compliant name. One-off items are the largest source of undeclared categories in the current data. |
| You are entering an item on behalf of someone who gave you a vague description | Go back for the missing segments. A record saved as PUMP with no size, material, or rating cannot be found again and will be re-created by the next person. |
| Two records look like they might be the same item | Do not merge or delete them yourself. Log them with the Process Owner, who confirms against stock and transaction history first. |
| A legacy QuickBooks record with a "(deleted)" suffix appears in a search | Do not transact against it and do not rename it. These 135 records are a migration artefact and are being retired as a batch — see Appendix D. |
| The Description field disagrees with the Item Name | The Item Name governs. Correct whichever of the two is wrong, then make them consistent. |
| You are unsure whether the category is singular or plural | Singular, always. FUSE, not FUSES. TERMINAL, not TERMINALS. BAG, not BAGS. |

## 7. Revision History

| Version | Date | Author | Description of Change |
|---|---|---|---|
| 1.0 | 08/19/2026 | Nikolas Bradshaw | Initial release. Reconciles Inventory Item Naming Convention Rev1, Item Description Naming Convention Notes, and the Naming Convention Sheet against a full audit of all 713 Item records in ERPNext Production. |

## Appendix A — Approved Category Vocabulary

This appendix replaces the Naming Convention Sheet spreadsheet as the controlled list. It
is built from the 72 categories on that sheet, reconciled against the words actually used
in ERPNext, with the sheet's structural errors corrected (Appendix C, items C-4 to C-7 and
C-9).

The abbreviation column from the original sheet is retired. Not one of its abbreviations —
CB, XFMR, TB, SWGR, FSTNR, GSKT, CTRAY — appears as a leading word on any active record.
Every category in use is spelled out in full, and that is now the rule.

### Tier 1 — Approved and in active use

Use these freely. The count is the number of active records currently led by that word.

**From the Naming Convention Sheet, confirmed in use:**
PUMP (17), ELBOW (15), PIPE (15), TEE (11), VALVE (10), PIPE CLAMP (7), BUSHING (7),
COUPLING (5), FILTER (4), SENSOR (3), LIGHT (3), ADAPTER (3), GLOVE (2), UNION (2),
RELAY (2), SCREW (2), PAD (2), TERMINAL BLOCK (2), FLANGE (2), CEMENT (2), CONNECTOR (1),
CONTACTOR (1), PEDESTAL (1), STONE (1), TRANSFORMER (1), MOTOR (1), TRAY (1),
STRUT CHANNEL (1), GASKET (1), SPACER (1), CONTROLLER (1), TILE (1), GRATING (1)

**Promoted from ERPNext usage (2 or more records):**
SLEEVE (5), BLOCK (4), COVER (4), PLATE (4), WATERPROOFING (4), RAM BIT (4), CLEANER (3),
FERRULE (3), STRAINER (3), TAPE (3), ANEMOMETER (2), CABLE (2), CARTRIDGE (2),
ENCLOSURE (2), FITTING (2), HOSE (2), LINK SEAL (2), NIPPLE (2), POWER SUPPLY (2),
RECEPTACLE (2), WATERSTOP FITTING (2), BATTERY (5), BAG (5), PAPER (2), PEN (2),
MARKER (2), MOUSE (3), FILAMENT (3), SCOURING PAD (2)

The promoted column matters for a structural reason: the original sheet is entirely
engineering-focused and contains no categories at all for office and janitorial
consumables. That is why the Office group scores 1 compliant record out of 61. Those
categories are now declared.

### Tier 2 — Approved but not yet used

These 39 categories are carried forward from the sheet and remain valid. They have no
records yet. Use them when the need arises rather than inventing a synonym.

BOLT, CABLE / WIRE, CABLE TRAY / SUPPORT, CAP, CAPACITOR, CAPACITOR BANK, CHEM FEEDER,
CIRCUIT BREAKER, CONDUIT / RACEWAY, DIFFUSER, DIODE, DRAIN, DRIVE / VFD, FASTENER,
FUSE / FUSEHOLDER, GLAND / FITTING, HANGER, HEATER, HMI, HOSE / TUBE, IC / CHIP,
INDICATOR / LAMP, MANIFOLD, METER, NOZZLE, NUT, PLC, PLUG, RESISTOR, SEAL, SKIMMER,
STRUT BOLT, STRUT COVER, STRUT FITTING, STRUT NUT, STRUT WASHER, SWITCHGEAR, TRANSISTOR,
WET NICHE

> **Verified 2026-08-19:** `item_naming_rules.APPROVED_CATEGORIES` reads the eight slashed
> Tier 2 entries as **alternatives** — `FUSE / FUSEHOLDER` admits both `FUSE` and
> `FUSEHOLDER` — because an Item Name cannot lead with the literal string `FUSE /
> FUSEHOLDER` and stay comma-delimited, and because Tier 3 below prescribes `FUSES → FUSE`,
> which would otherwise be rejected as an unapproved category. That reading is an
> interpretation of this appendix, not a quotation of it.

### Tier 3 — Non-conforming words found in ERPNext, with their replacement

These leading words are in the data today and must not be used again. The table gives the
correct form. 176 undeclared leading words exist in total; the ones below are those
appearing on two or more records, plus the outright errors.

| Found in ERPNext | Records | Use instead |
|---|---|---|
| PROTE | 4 | CIRCUIT BREAKER, SUPPLEMENTARY, … — apparently a truncation of PROTECTOR |
| BREAKER | 5 | CIRCUIT BREAKER |
| FUSES | 4 | FUSE — singular |
| TERMINALS | 3 | TERMINAL BLOCK |
| CHECK VALVE | 4 | VALVE, CHECK |
| BACKWASH VALVE | 2 | VALVE, BACKWASH |
| SAND FILTER | 2 | FILTER, SAND |
| WATER LEVEL SENSOR | 2 | SENSOR, LEVEL |
| OUTLET COVER | 3 | COVER, OUTLET |
| MINI RELAY | 2 | RELAY, MINI |
| WIRE TRAY | 2 | CABLE TRAY / SUPPORT |
| RED BUSH | 1 | BUSHING, REDUCER |
| BRUSHING | 1 | BUSHING — spelling |
| CATRIDGE | 1 | CARTRIDGE — spelling |
| EDISION FUSE | 1 | FUSE, EDISON — spelling |
| WIRE CONNECCTORS | 1 | CONNECTOR, WIRE — spelling |
| SUBPANELT | 1 | PANEL, SUB — spelling |
| ABB | 3 | The actual category. A brand is never a category. |
| 2-1/2 PVC, 3 PVC, 1 PVC, 2X3/4 | 6 | The actual category. A size is never a category. |
| HOSE used on a tee or bushing | 2 | TEE or BUSHING — inherited from the sheet's PIPE→HOSE error, C-5 |

> **Verified 2026-08-19, two defects in this table.**
>
> **`SUBPANELT → PANEL, SUB` points at a category that does not exist.** `PANEL` is on
> neither Tier 1 nor Tier 2, so the SOP's own prescribed replacement fails its own Step 4.1.
> `item_naming_rules.TIER3_REPLACEMENT_UNAPPROVED` computes this set rather than listing it,
> so a validator never hands somebody a correction that would itself be rejected. Needs the
> Process Owner: either declare `PANEL`, or choose a different replacement.
>
> **Five more unapproved leading words are live on two or more records and are missing from
> this table:** `BATTERIES` (5) and `FERRULES` (3), `PENS` (2), `SCOURING PADS` (2) — all
> plurals of approved Tier 1 categories; `PRINTER FILAMENT` (3) — sub-type led, should be
> `FILAMENT, PRINTER`; `SHARPIES` (2) — a brand *and* a plural, should be `MARKER, …`; and
> `UNI-INSERT` (2) / `UNI-SHIM` (2) — vendor product-line names used as categories. Tracked
> in WI-070.

## Appendix B — Current-State Measurement

All figures taken from ERPNext Production on 19 August 2026 across all 713 Item records.
"Active" excludes the 135 QuickBooks-era records carrying a "(deleted)" suffix in the Item
Code.

> **Verified 2026-08-19 — this appendix drifted twice on the day it was written.** The audit
> counted 713. A re-check the same morning returned **715**; a second re-check a few hours
> later returned **716** (581 live + 135 tombstones), the new row being `10591716`, created
> 2026-08-19 10:51. Nothing was wrong with the audit — the catalogue is simply a live table
> that people add to during the working day. That is the whole argument for
> `item_naming_check` and the `ee-item-naming-validator` skill quoting **nothing** from this
> appendix and reading every number at call time. Treat every figure below as dated rather
> than current.
>
> Note also that "active" here does **not** mean `disabled = 0`: every row carries
> `disabled = 0`, and the `(deleted)` suffix is the only marker there is.

### B.1 Compliance with the convention as previously written

A record passes only if the Item Name contains at least one comma, its first segment
exactly matches a category from the Naming Convention Sheet, and the whole name is
uppercase.

| Test | All 713 records | 578 active records |
|---|---|---|
| Contains at least one comma | 388 (54%) | 388 (67%) |
| First segment is an approved CATEGORY | 129 (18%) | 129 (22%) |
| Entirely uppercase | 503 (71%) | 503 (87%) |
| **All three — fully compliant** | **120 (16.8%)** | **120 (20.8%)** |

### B.2 Compliance by Item Group, active records

| Item Group | Active records | Fully compliant | Rate |
|---|---|---|---|
| Products | 264 | 107 | 41% |
| All Item Groups | 122 | 1 | 1% |
| Electrical | 68 | 8 | 12% |
| Office | 61 | 1 | 2% |
| Service | 35 | 2 | 6% |
| E-Stop Components | 18 | 0 | 0% |
| Pumps | 6 | 0 | 0% |
| Configured Products | 3 | 1 | 33% |
| Service Fountains | 1 | 0 | 0% |

Products carries the convention; everything else has largely not adopted it. The Pumps
group reads 0% for one reason only — all six records are in mixed case ("Pump, Aquasurge
2000"). They are otherwise well formed and are the cheapest records in the system to fix.

### B.3 Vocabulary

| Measure | Count |
|---|---|
| Categories on the Naming Convention Sheet (74 rows, 2 duplicated) | 72 |
| Sheet categories used on at least one active record | 33 |
| Sheet categories never used | 39 |
| Distinct leading words across active comma-formatted names | 209 |
| Leading words not declared anywhere (44 used twice or more, 132 used once) | 176 |

### B.4 Item Code families, active records

| Family | Records | Share |
|---|---|---|
| Vendor / manufacturer part number | 410 | 71% |
| `CON-` consumable | 91 | 16% |
| `SRV-` service | 39 | 7% |
| `PDT-` product | 38 | 7% |

### B.5 Character-level defects

| Defect | Records |
|---|---|
| Size written as the word INCH rather than the inch mark | 9 |
| Colour spelled GREY rather than GRAY (6 records use GRAY) | 7 |
| Leading or trailing whitespace in the Item Name | 3 |
| Comma not followed by a space | 1 |
| Double space inside the name | 1 |
| Trailing comma | 1 |

## Appendix C — Discrepancy Register

Every point at which the prior reference material disagrees with ERPNext, or with itself.
In each case ERPNext governs and the resolution below is what this SOP adopts.

| Ref | The reference material says | ERPNext shows / Resolution |
|---|---|---|
| C-1 | **Separator.** Item Description Naming Convention Notes states "The pipe \| is often preferred." Rev1 and the Sheet specify a comma. | 388 Item Names use commas. None use pipes. The comma is the standard. |
| C-2 | **Which field.** Both reference documents are written about the item description. | The schema lives in `item_name`. Description is empty on 230 of 578 active records and duplicates the name on 178. Item Name is authoritative. |
| C-3 | **Item Code.** Neither reference document mentions the Item Code at all. | Four code families are in production use across all 578 active records. Documented for the first time in Section 5, Phase 2. |
| C-4 | **Sheet column headers.** Columns are headed Category, Sub-Category, Key Feature. | The "Sub-Category" column actually holds abbreviations of the category (CIRCUIT BREAKER→CB), and "Key Feature" holds the real sub-categories (MCCB, ACB, VCB). Appendix A relabels them. |
| C-5 | **Sheet, PIPE row.** PIPE is assigned the abbreviation HOSE with key features identical to the HOSE/TUBE row. | A copy-and-paste error, and it has propagated: `802-247S` (a tee) and `837-249S` (a bushing) are both named with a leading HOSE. PIPE is its own category; the HOSE mapping is removed. |
| C-6 | **Sheet, duplicate rows.** CAPACITOR appears twice and TRANSFORMER appears twice, across 74 rows for 72 categories. | The abbreviation CAP is also assigned to two different categories — CAP the fitting and CAPACITOR. One row per category; colliding abbreviations dropped. |
| C-7 | **Sheet, STONE row.** STONE is assigned the abbreviation PEBBLE. | PEBBLE is a different word, not a shortening. The category is STONE. |
| C-8 | **Field agreement.** Neither document says which field wins when they disagree. | On several records the description is the better-formed string: `140342` is named `SAND FILTER, PENTAIR TR140C-3` but described `FILTER, SAND, PENTAIR TR140C`. The description is correct in these cases; the Item Name must be corrected to match. |
| C-9 | **Abbreviations.** Rev1 permits "a four-to-six-character abbreviation or the full common name." | Not one sheet abbreviation appears as a leading word on any active record. All 33 categories in use are spelled out. Full words only; the abbreviation column is retired. |
| C-10 | **UOM.** Both documents make packaging and unit of measure segment 7 of the name. | ERPNext has a dedicated `stock_uom` field, currently split between "Unit" (431) and "Nos" (281) — two labels for one concept. Set `stock_uom` on the record; use segment 7 only for pack configuration. Standardise `stock_uom` on a single value. |
| C-11 | **Sheet, Material and Size columns.** Columns D to G are populated on only 3 or 4 of the 74 rows. | They are global examples, not per-category values, but the grid layout implies otherwise. Dropped from Appendix A; segment vocabularies live in Section 5, Phase 3. |

> **Verified 2026-08-19:** C-10's split is **433 `Unit` / 281 `Nos` / 1 other**, and it is
> still unstandardised. Until someone sets the standard, `item_naming_check` reports the
> distribution and does not arbitrate — a check that fired on ~40% of correct records is a
> check nobody reads.

## Appendix D — Records Requiring Correction

Specific records where the current data is wrong, not merely unformatted. These are the
cleanup backlog. Item Codes are quoted exactly as they appear in ERPNext.

| Ref | Record(s) | What is wrong | Recommended action |
|---|---|---|---|
| D-1 | `CON-OFFC-INK-HP-952-COLOR` | The Item Name reads `WATER LEVEL SENSOR, REED SWITCH, M10 THREAD`. The Item Code and the Description both say HP 952 colour ink cartridge. | Correct the Item Name to `INK CARTRIDGE, HP 952, COLOR`. Check any transactions posted against this record. |
| D-2 | `GMCB-1B-1`, `GMCB-1B-6`, `GMCB-1B-10`, `GMCB-1C-10` | Four different breakers share one identical name, `PROTE, SUPPLEMENTARY, MINIATURE, GLADIATOR`. Amperage and curve are missing, so the four cannot be told apart. | Rename each with its rating, e.g. `CIRCUIT BREAKER, SUPPLEMENTARY, GLADIATOR, B CURVE, 1 AMP`. |
| D-3 | `4010052576503` / `57650`; `100GM DC24V DMX512 3W` / `xyh100gm-3x1w`; `PDC80TIMDMX` / `PDC90TIM` | Three pairs of records that appear to describe the same physical part under two Item Codes. | Confirm against stock and transaction history, then retire the redundant record. Do not merge without checking first. |
| D-4 | `406-040`, `417-040`, `417-080` | The Item Name states SCH80 but the code prefix is the Schedule 40 series. 75 of the 78 records in this family agree with their prefix; these three do not. | Verify against the vendor catalogue and correct whichever of the two is wrong. |
| D-5 | `802-247S`, `837-249S`, `837-249`, `140342`, `261050` | Category errors: a tee and a bushing named with a leading HOSE, a bushing named RED BUSH, and two records led by the sub-type rather than the category. | Rename per Appendix A, Tier 3. For `140342` and `261050` the existing Description already holds the correct form. |
| D-6 | `B01M3Y86MY`, `P1119 SS`, `WS2R`, `xyh100gm-3x1w`, `P1126 SS` | Character-level defects: trailing comma and space, trailing spaces, a comma with no space after it, and SS304S where SS304 is meant. `B01M3Y86MY` also misspells CORROSIVE. | Trim and correct in place. Low effort, and they defeat exact-match searching. |
| D-7 | `PDT-0008 VFD BYPASS W/MOTOR PROTECTION, 5HP - copy` | A working copy left in production. The Item Code contains both a comma and a "- copy" suffix. | Confirm it is redundant against the numbered `PDT-` record, then retire it. |
| D-8 | 135 records carrying a "(deleted)" suffix | QuickBooks migration artefacts. They inflate every search result and account for the bulk of the 122 records sitting in All Item Groups. | Retire as a batch once confirmed to carry no open transactions. Renaming them individually is not worth the effort. |

> **Verified 2026-08-19 — D-7 is not executable as written.** There is no numbered
> `PDT-0008` record in `tabItem` (`SELECT COUNT(*) … WHERE item_code LIKE 'PDT-0008%' AND
> item_code NOT LIKE '%copy%'` returns 0). The "- copy" record is the **only** record of the
> 5HP VFD bypass, so there is nothing to confirm it redundant against and retiring it would
> destroy the product. Rename it in place to `PDT-0008` with a compliant Item Name, and do
> not reissue 0008 to anything else. Needs the Process Owner.
>
> **Verified 2026-08-19 — two records missing from D-7's class.** `PDT-00XX` and `PDT-00051`
> are both live, both have `item_name` identical to `item_code`, and neither carries a
> "(deleted)" suffix, so neither is caught by D-8 either. `PDT-00051` is especially easy to
> destroy by eyeball: `PDT-0051` is a real level sensor.
>
> **Verified 2026-08-19 — D-8's records are still referenced.** 218 Sales Invoice lines, 74
> Quotation lines, 15 Purchase Order lines and 7 Purchase Invoice lines point at
> "(deleted)"-suffixed codes. Every one of those parent documents is a **draft** — zero
> submitted, and zero Stock Ledger Entries — but "carry no open transactions" is not true
> today, and the batch retirement is sequenced behind WI-028 in WI-070 for that reason.

**Priority.** D-1 and D-2 are correctness problems and should be fixed first. D-3 through
D-6 are small in number and cheap to close. The largest single gain in measured compliance
is not on this list at all — it is converting the 210 mixed-case Item Names to uppercase,
which alone moves the fully-compliant figure substantially and requires no judgement about
what any item is.

---

*Confidential — Sapphire Fountains Internal Use Only*
