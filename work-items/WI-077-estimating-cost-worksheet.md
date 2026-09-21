# WI-077: Estimating — cost code catalog and the Cost Worksheet

**Phase:** 2   **Type:** APP_CODE   **Size:** L
**Blocked by:** the cost-code renumbering below returning from PM review   **Blocks:** WI-078

## Why

Sapphire prices every job on **DOC-0041 Sapphire Cost Worksheet**, an Excel workbook, and nothing in
ERPNext models it. `Project.estimated_costing` is zero on all 655 projects, so WI-058's
percentage-of-budget PO escalation has no denominator, and WI-075 sub-phase M shipped the budget
structure (`Project Budget Category`, `Project Budget Line`, `Budget Reallocation`) holding zero rows.
This item is the numbers-producer that structure was built for.

The workbook also disagrees with itself. Three tabs carry three different cost-code lists — 50, 51 and 50
codes — with twelve codes used to price jobs that the catalog does not list, four numbers carrying two
meanings and one carrying three. That reconciliation is done and approved (68 active + 5 retired). What
is **not** done is the finding below, which supersedes it.

## The catalog is MasterFormat 1995 wearing a 2004 format

Nik ruled on 2026-09-21 that where Sapphire's usage conflicts with the industry standard, **the standard
wins**. Verifying the catalog against CSI/CSC *MasterFormat Numbers and Titles* (2011, 2016 and 2020
editions, read as PDFs and text-extracted; eight independent adversarial passes on the fountain block,
none of which broke a number) established that the catalog is not slightly drifted. It is the **retired
MasterFormat 1995 16-division list**, mechanically reformatted by splitting each 5-digit number after two
digits and left-zero-padding the rest.

The transform is exact and provable. `05500 Metal Fabrications` became `05 0500`; `09900 Paints and
Coatings` became `09 0900`; `16300 Transmission and Distribution` became `26 0300`. Nine of the twelve
Division 02–09 titles are verbatim 1995 broadscope titles. The result reads as a current six-digit
MasterFormat number and points at an unrelated section.

**Of 71 codes checked: 8 are already correct, 24 are real sections meaning something else, 32 are not
sections at all, and 7 describe resources the standard deliberately has no home for.**

The dangerous class is the middle one — these do not error, they silently misinform a general contractor
reading a Schedule of Values or a G702/G703. Examples: `22 3100` (Sapphire: Pumps) is **Domestic Water
Softeners**; `22 5100` (Nozzles) is **Swimming Pool Plumbing Systems**; `26 0800` (Sound and Video) is
**Commissioning of Electrical Systems**; `00 5000` (Fee) is **Contracting Forms and Supplements**; and
`02 0300` (Earthwork) is **Conservation Treatment for Existing Period Conditions** — earthwork moved to
Division 31 when MasterFormat went from 16 to 50 divisions in 2004.

Two Division 02/09 collisions are **newer than the codes**: `02 03 00` and `09 03 00` are absent from
MasterFormat 2011 and present in 2016. Those numbers were merely meaningless when Sapphire adopted them
and have since become live collisions. That is an argument for renumbering on a schedule rather than
treating today's state as stable.

## Two dimensions, not one

Roughly ten codes are **resources, not work results** — project engineer, superintendent, project
executive, direct labor, travel, software expense, fee. MasterFormat classifies work results and has no
home for these by design; no future edition will add one. Their present shape is the hazard: `00 2200` is
indistinguishable from a real section reference, so it will be read as one the moment it reaches a GC.

- **MasterFormat field** — real published section numbers only, validated against a section list on save.
- **Internal resource/cost-type dimension** — visibly *not* MasterFormat-shaped (`LAB-SUPT`, `OH-TRAVEL`,
  `FEE`), so nobody can mistake one for a spec reference.

Division 00 should be vacated entirely for cost coding: every Division 00 number names a procurement
document, and none of it is ever a cost line.

## Numbering format

CSI defines five levels. A six-digit section (`22 52 13`) is **Level 3**, not Level 4 — an off-by-one that
must not propagate, because CSI's publication restriction is worded in terms of Levels 4 and 5. The
decimal tier (`22 52 13.13`) is CSI's **Level 4** and CSI publishes it itself (1,750 such numbers in 2011,
2,417 in 2016 — the tier is actively filling). True Level 5 (`22 52 13.00.F1`) is alphanumeric and
**internal use only**: CSI forbids it on documents that leave the office, which includes an SOV and a
G702/G703.

Extend the cost-code format to `NN NNNN.NN`. This is not an invention — it is CSI's own display option 2
(`11 2233.44`) quoted verbatim in the Applications Guide. Size the field for Level-5 headroom (20 chars)
with a regex validator.

**Never invent six-digit siblings** (`22 5214`, `22 5226`). CSI allocates in groups of three — 13, 16, 19,
then 23, 26, 29 — specifically to leave room for future assignments, so inventing there is the exact
`22 3100` failure mode in CSI's live growth space. Extending downward is strictly safer than sideways.

For the six fountain-piping lines that all collapse into `22 52 13`, use Level 4 on CSI's own spacing
(`.13 .16 .19 .23 .26 .29`). For service work, CSI publishes a recommended O&M scheme that `22 01 50` has
no children for yet: `.51` cleaning, `.61` repairs, `.81` replacement.

## Native-first check

`Project Budget Category` (WI-075 sub-phase M) already holds the seven budget categories the catalog rolls
into, so `budget_category` is a Link to an existing master, not a new Select. ERPNext's native `Budget`
doctype was rejected twice for per-project budgeting and is not revisited here. Nothing native models a
cost code, a rate card or an estimate; this is greenfield.

## Scope

- `Cost Division`, `Cost Code`, `Estimate Rate Card`, `Labor Class` in Project Enhancements.
- `quality/estimate_math.py` — the pure engine, importing no frappe so the bench-free CI tier can run it.
- Insert-only seed patches. Finance owns edits in the Desk (Nik, 2026-09-21), so never fixtures, and
  never a Desk Data Import — an import sets `frappe.flags.in_import`, which skips Select validation and
  can plant a value that makes the row unsaveable forever.
- Permissions mirror `Project Budget Category` exactly: System Manager full; Finance Team create/read/write
  without delete; read and report for Project Manager, Executive Team and Production Team.

## Acceptance criteria

- Every seeded `Cost Code` validates against a published MasterFormat section list, not a regex shape.
  `01 5433` is the proof case: it has a legal Level-4 shape inside a real family whose children stop at
  `01 54 26`, so it passes any pattern check and fails only a list check.
- No code in the catalog is a real MasterFormat section used with a title other than its assigned one.
- Resources carry non-MasterFormat-shaped codes and cannot be entered in the MasterFormat field.
- Cost-code normalization collapses whitespace runs and converts U+00A0 **in Python**. Under MariaDB's PAD
  SPACE collation `WHERE cost_code <> TRIM(cost_code)` is always false and reports clean on broken data.
- A retired code is disabled with `superseded_by` set, never deleted — `frappe.delete_doc` refuses any doc
  another doctype Links to and `force=1` does not bypass that check.
- 2.10 × 2.45 = 5.145 rounds to **5.14** (Banker's rounding, ported verbatim from frappe's
  `_bankers_rounding`), pinned as a test case.
- `cost_of_work` ≡ `total_cost + markup_total`, exactly, over generated awkward quantities.

## Decisions taken 2026-09-21

Nik ruled: **where Sapphire's usage conflicts with the industry standard, the standard wins.** Four
questions that previously needed a human are resolved by that rule, and the remaining four were
answered directly.

| Question | Answer |
|---|---|
| PM sign-off on the renumbering | **Approved** |
| `04 0600` Grouts | **03 60 00 Grouting** — 1995 provenance is `03600 Grouts`; flagged for recheck |
| `26 1000` Panel | **26 27 16 Electrical Cabinets and Enclosures** — matches the "enclosure itself" gloss |
| `00 1100` Prototyping | **both** — `01 4339 Mockups` when it is a billable physical mockup, `OH-PROTO` when it is internal R&D. The estimator picks per job. |
| `13 0700` Splash Pad Toys | **13 12 13 Exterior Fountains** (as `13 1213.13`) |
| `26 0425` Solenoid Valves | **22 52 23 Fountain Equipment Controls** — a controlled valve in the water circuit, not electrical distribution |
| Sub-lines under a section | cost type stops consuming numbers; genuine distinct work results get a Level-4 decimal on CSI's own spacing |
| "Relocate existing" | CSI's published O&M scheme (`.51` cleaning, `.61` repairs, `.81` replacement) |

`22 3300` Water Heater remains unresolved **by the standard, not by us**: MasterFormat has no
fountain-heating section anywhere, so it rolls up to `22 52 00 Fountain Plumbing Systems`.

## The corrected catalog

The catalog now lives in [`erpnext_enhancements/quality/cost_codes.py`](../erpnext_enhancements/quality/cost_codes.py)
— bench-free, importing no frappe, and the single authority the seed patch reads. It carries
`MASTERFORMAT_SECTIONS` (every section used, with its verbatim CSI title),
`COST_CODES`, `RESOURCE_CODES`, `RETIRED_CODES`, `REUSED_CODES`, `MERGED_OLD_CODES` and `OLD_TO_NEW`.

`erpnext_enhancements/tests/test_cost_codes.py` guards it bench-free, on its own CI step. The gate is
the **published-section list, not the regex** — `01 5433` has a legal Level-4 shape inside a real
family whose children stop at `01 54 26`, so a pattern check passes it and only a list check fails it.

### Two hazards the tests surfaced

**Reused strings.** A code string that exists in both catalogs with different meanings. Un-migrated
data holding one of these silently changes meaning rather than failing to resolve, so the migration
must key on `old_code` and never match the string.

| Code | Meant | Now means | Old meaning moved to |
|---|---|---|---|
| `22 1119` | Water Hammer Arrestors | Domestic Water Piping Specialties | `22 5213.19` |
| `26 0500` | General Lighting | Common Work Results for Electrical | `26 5000` |

**Deliberate merges.** Several old codes collapse into one:

| New | Absorbs |
|---|---|
| `04 4000` | `04 0400`, `04 0450` |
| `22 5216` | `22 1123`, `22 3100` |
| `26 0500` | `26 0400`, `26 0410` |
| `31 2300` | `02 0300`, `31 0100` |
| `FEE` | `00 5000`, `01 1000` |

### Retired

| Retired | Superseded by | Why |
|---|---|---|
| `00 5000` | `FEE` | Division 00 vacated: 00 50 00 is Contracting Forms and Supplements. |
| `01 1000` | `FEE` | 01 10 00 is Summary. |
| `04 0450` | `04 4000` | Never a real section; duplicate of 04 0400. |
| `22 1123` | `22 5216` | OLD list's Domestic water pumps. 22 11 23 IS that section - but these are fountain pumps. |
| `26 0400` | `26 0500` | Not a real section. |
| `31 0100` | `31 2300` | Not a real section; 31 10 00 is Site Clearing. |

### Full delta — 57 codes, 12 resources

| Old | New | Title | Section | Change |
|---|---|---|---|---|
| `00 1000` | `LAB-DESIGN` | Design | — resource — | **left MasterFormat** |
| `00 1100` | `01 4339` | Mockups | 01 43 39 | **renumbered** |
| `00 1100` | `OH-PROTO` | Prototyping (internal) | — resource — | **left MasterFormat** |
| `00 1200` | `LAB-DESIGN-ASSIST` | Design Labor and Assistance | — resource — | **left MasterFormat** |
| `00 2000` | `OH-EXPENSE` | Expenses (software, hardware) | — resource — | **left MasterFormat** |
| `00 2200` | `OH-TRAVEL` | Travel | — resource — | **left MasterFormat** |
| `00 5000` | `FEE` | Fee (based on the Cost of Work) | — resource — | **left MasterFormat** |
| `01 0100` | `LAB-PM` | Project Management | — resource — | **left MasterFormat** |
| `01 0110` | `LAB-PE` | Project Engineer | — resource — | **left MasterFormat** |
| `01 0120` | `LAB-SUPT` | Superintendent | — resource — | **left MasterFormat** |
| `01 0130` | `LAB-COORD` | Project Coordinator | — resource — | **left MasterFormat** |
| `01 0140` | `LAB-EXEC` | Project Executive | — resource — | **left MasterFormat** |
| `01 0150` | `LAB-DIRECT` | Direct Labor | — resource — | **left MasterFormat** |
| `01 0200` | `01 5300` | Temporary Construction | 01 53 00 | **renumbered** |
| `01 0210` | `01 5423` | Temporary Scaffolding and Platforms | 01 54 23 | **renumbered** |
| `01 0300` | `01 5800` | Project Identification | 01 58 00 | **renumbered** |
| `01 0400` | `01 7400` | Cleaning and Waste Management | 01 74 00 | **renumbered** |
| `01 0410` | `01 7600` | Protecting Installed Construction | 01 76 00 | **renumbered** |
| `01 0500` | `01 7133` | Protection of Adjacent Construction | 01 71 33 | **renumbered** |
| `01 1100` | `01 2116` | Contingency Allowances | 01 21 16 | **renumbered** |
| `01 5433` | `01 5416` | Temporary Hoists | 01 54 16 | **renumbered** |
| `01 7900` | `01 7900` | Demonstration and Training | 01 79 00 | unchanged |
| `02 0300` | `31 2300` | Excavation and Fill | 31 23 00 | **renumbered** |
| `02 1200` | `02 4113` | Selective Site Demolition | 02 41 13 | **renumbered** |
| `03 0210` | `03 3000` | Cast-in-Place Concrete | 03 30 00 | **renumbered** |
| `03 0400` | `03 4000` | Precast Concrete | 03 40 00 | **renumbered** |
| `04 0400` | `04 4000` | Stone Assemblies | 04 40 00 | **renumbered** |
| `04 0600` | `03 6000` | Grouting | 03 60 00 | **renumbered** |
| `05 0500` | `05 5000` | Metal Fabrications | 05 50 00 | **renumbered** |
| `06 0100` | `06 1000` | Rough Carpentry | 06 10 00 | **renumbered** |
| `07 0100` | `07 1000` | Dampproofing and Waterproofing | 07 10 00 | **renumbered** |
| `09 0300` | `09 3000` | Tiling | 09 30 00 | **renumbered** |
| `09 0700` | `09 7000` | Wall Finishes | 09 70 00 | **renumbered** |
| `09 0900` | `09 9000` | Painting and Coating | 09 90 00 | **renumbered** |
| `13 0600` | `13 1200` | Fountains | 13 12 00 | **renumbered** |
| `13 0700` | `13 1213.13` | Splash Pad Play Features | 13 12 13 | **renumbered** |
| `13 0812` | `13 0812` | Commissioning of Fountains | 13 08 12 | unchanged |
| `13 1213` | `13 1213` | Exterior Fountains | 13 12 13 | unchanged |
| `13 1223` | `13 1223` | Interior Fountains | 13 12 23 | unchanged |
| `13 1250` | `13 0112.16` | Seasonal Shut-down | 13 01 12 | **renumbered** |
| `13 1260` | `13 0112.13` | Seasonal Start-up | 13 01 12 | **renumbered** |
| `22 0150` | `22 0150` | Operation and Maintenance of Pool and Fountain Plumb | 22 01 50 | unchanged |
| `22 1100` | `22 5213.13` | Rough Fountain Piping | 22 52 13 | **renumbered** |
| `22 1110` | `22 5213.16` | Equipment Room Piping | 22 52 13 | **renumbered** |
| `22 1119` | `22 5213.19` | Water Hammer Arrestors | 22 52 13 | **renumbered** |
| `22 1120` | `22 5213.23` | Check Valves | 22 52 13 | **renumbered** |
| `22 1150` | `22 5213.26` | Manifold Materials | 22 52 13 | **renumbered** |
| `22 1160` | `22 1119` | Domestic Water Piping Specialties | 22 11 19 | **renumbered** |
| `22 3100` | `22 5216` | Fountain Pumps | 22 52 16 | **renumbered** |
| `22 3200` | `22 5219` | Fountain Water Treatment Equipment | 22 52 19 | **renumbered** |
| `22 3250` | `22 5223.13` | Chemical Controller | 22 52 23 | **renumbered** |
| `22 3300` | `22 5200` | Fountain Plumbing Systems | 22 52 00 | **renumbered** |
| `22 3400` | `23 6400` | Packaged Water Chillers | 23 64 00 | **renumbered** |
| `22 4516` | `22 4516` | Eyewash Equipment | 22 45 16 | unchanged |
| `22 5100` | `22 5213.29` | Fountain Nozzles | 22 52 13 | **renumbered** |
| `26 0300` | `26 2000` | Low-Voltage Electrical Distribution | 26 20 00 | **renumbered** |
| `26 0350` | `26 0583` | Wiring Connections | 26 05 83 | **renumbered** |
| `26 0410` | `26 0500` | Common Work Results for Electrical | 26 05 00 | **renumbered** |
| `26 0420` | `26 0519` | Low-Voltage Electrical Power Conductors and Cables | 26 05 19 | **renumbered** |
| `26 0425` | `22 5223.16` | Solenoid Valves | 22 52 23 | **renumbered** |
| `26 0500` | `26 5000` | Lighting | 26 50 00 | **renumbered** |
| `26 0700` | `27 0000` | Communications | 27 00 00 | **renumbered** |
| `26 0750` | `26 0900` | Instrumentation and Control for Electrical Systems | 26 09 00 | **renumbered** |
| `26 0760` | `26 0900.16` | Control Panel Build and Programming | 26 09 00 | **renumbered** |
| `26 0800` | `27 4000` | Audio-Video Communications | 27 40 00 | **renumbered** |
| `26 1000` | `26 2716` | Electrical Cabinets and Enclosures | 26 27 16 | **renumbered** |
| `26 5500` | `26 5500` | Special Purpose Lighting | 26 55 00 | unchanged |
| `26 5529` | `26 5529` | Underwater Lighting | 26 55 29 | unchanged |
| `26 5550` | `26 0150.81` | Luminaire Removal and Replacement | 26 01 50 | **renumbered** |
