# Chart of Accounts — Target Design & Mapping Rationale (WI-004)

**Deliverables:** [`chart_of_accounts.csv`](chart_of_accounts.csv) (213 accounts — 46 groups, 167 leaves, in the native Chart of Accounts Importer format) and [`coa_mapping.csv`](coa_mapping.csv) (359 rows — one per production account: 264 MAP, 95 RETIRE).

**Consumed by:** WI-029 (CoA rebuild executes the CSV), WI-031 (modes of payment), WI-032 (opening TB), WI-036 (tax templates), WI-053 (historical P&L trend import). Nothing in this document executes anything — it is design + mapping data only.

**Status:** design for finance/CPA ratification on this PR. See [What finance/CPA must ratify](#what-financecpa-must-ratify-on-this-pr) below.

---

## 1. Why a clean rebuild

Production's 359-account chart is a raw QBO import: 51 `(deleted)`-suffixed dead accounts, 38 QBO automated-sales-tax jurisdiction/agency artifacts (`UT-Salt Lake-Salt Lake City`, `VA-Fredericksburg City`, `Bid d`…), `account_number` NULL on most rows, and QBO cash-basis artifacts (`Unapplied Cash Payment Income`) that are meaningless in accrual books. Production's ledger is effectively empty (4 GL Entries), which is the one moment a full re-import is possible. The June TEST pilot already proved the importer path with a 282-account chart; this design harmonizes with that pilot's conventions and fixes its leftovers (unnumbered strays, duplicate Retained Earnings/OBE, `Rent` naming).

Design ground rules (all resolved decisions binding):

- **OD-1 (single company):** one chart, but **no company identifier embedded in any account name** — no "SF"/"Sapphire", no bank-brand-specific names in the shipped CSV. If OD-1 is ever reopened, the identical CSV imports under a second Company without redesign (company-abbr suffixing is native).
- **OD-2 (Utah-law stream-differentiated tax):** per-jurisdiction UT sales-tax liability sub-accounts; Build stream treated as real-property improvement (contractor is the consumer of materials — see §6).
- **OD-3 (Rent → Events):** the stream is named **Events** everywhere — income, COGS, R&D, fixed assets. No "Rent" stream name survives.

## 2. Numbering scheme

| Range | Digits | Content |
|---|---|---|
| `1000–1910` | 4 | Assets. `11xx` current, `12xx` fixed, `13xx` intangible/other, `19xx` temporary |
| `2000–2230` | 4 | Liabilities. `21xx` current (payables `211x`, cards `212x`, sales tax `213x`, payroll `214x`, stock `215x`, deposits/deferred `216x`, other `217x`, LOC `218x`), `22xx` long-term |
| `3000–3400` | 4 | Equity |
| `4000–4260` | 4 | Income. `41xx` direct (stream leaves `4110–4150`), `42xx` other |
| `5110–5223` | 4 | System expense accounts (stock valuation `511x`, financial/system `52xx`) |
| `50000–55200` | 5 | Stream COGS: `50000` Design, `51000` Build, `52000` Service, `53000` Events, `54000` Products, `55000` shared direct |
| `60000–61900` | 5 | Operating (`60xxx`) and G&A (`61xxx`) expenses |

Conventions:

- **Groups end in `00`/`000`; leaves fill within their group's range.** Every account — group and leaf — has a non-NULL number (an acceptance criterion; prod's chart was "Standard with Numbers" in name only).
- **4-digit = balance sheet + ERPNext-system accounts; 5-digit = the business P&L** (QBO heritage). This is the TEST pilot's own convention (`4100 - Direct Income`, `50000 - Design COGS`, `5200 - Indirect Expenses`), kept deliberately.
- **Stream-COGS leaf suffixes are uniform across all streams** (TEST's convention, extended): `x100` Materials, `x200` Subcontract Labor, `x300` Freight & Delivery, `x400` Tools & Equipment, `x500` Direct Labor, `x600` Travel, `x700` Job Insurance.
- **Known cosmetic quirk:** ERPNext orders siblings lexicographically by number-string, so under `5000 - Expenses` the display order interleaves 4- and 5-digit children (`50000, 5110, 51000, 5200, 52000…`). The TEST pilot had the same behavior and the team accepted it; renumbering the four system accounts `5111/5112/5118/5119` was rejected because those numbers are already established on both sites.
- All raw account names are unique chart-wide (not just within a parent) — this keeps the importer's name-keyed validation and every downstream fuzzy lookup unambiguous.

## 3. Value-stream income and COGS layout

The business sells five ways; income and COGS mirror that 1:1 (prod's own account names prove the categories — `Design/Build/Service/Rent` income + per-stream COGS trees all exist today; Products was sold but tangled inside Build):

| Stream | Income | COGS group |
|---|---|---|
| Design | `4110` | `50000` |
| Build | `4120` | `51000` |
| Service | `4130` | `52000` |
| **Events** (OD-3, was "Rent") | `4140` | `53000` |
| Products (new split) | `4150` | `54000` |

Per-stream COGS leaves (7 per job stream, 4 for Products):

- **`x200 <Stream> Subcontract Labor`** — renamed from prod's "<Stream> Professional Services & Subcontractors". **77% of the 1,156 production suppliers are 'Staffing'** — subcontract cost is as large as materials, so it gets a first-class, consistently-numbered line in every stream. This is the single most important P&L line for job costing.
- `x100 Materials` — absorbs prod's per-stream "Inventory" accounts (periodic-inventory purchases expensed direct). When WI-060 turns on perpetual inventory, material flows shift to `1151 → 5111`; the `x100` accounts remain for non-stock job materials. **WI-060 requirement (do not lose this):** perpetual-inventory delivered-stock COGS posts to the *single* `5111` by default — WI-060 must set Item Group / Item default expense accounts back to the stream `x100` accounts (`50100/51100/52100/53100/54100`), or the team must explicitly accept dimension-based stream margin instead; otherwise per-stream materials gross margin (the headline win of this chart) silently degrades to one blended line.
- `x500 Direct Labor` — internal labor applied to jobs (Time Kiosk → Timesheet costing feeds this from WI-021 onward).
- `x300 Freight & Delivery`, `x400 Tools & Equipment`, `x600 Travel`, `x700 Job Insurance` — direct from prod's categories.
- Prod's per-fountain Events materials detail (`Materials Dancing Pond`, `Materials Art Glass Vase`, …) collapses into `53100 Events Materials`; per-unit profitability belongs to Item/Project reporting, not the GL.
- `55000 Shared Direct Costs`: `55100 Sales Commissions` (prod books commission as a direct cost; kept out of any single stream), `55200 Shop Supplies & Small Tools`.
- Products COGS carries only `54100/54200/54300/54500` (Materials, Subcontract Labor, Freight, Direct Labor) — no travel/insurance/tools history exists for catalog product sales; add leaves later if the business changes.
- **Historical-trend caveat (WI-053):** products *income* history moves to `4150`, but QBO never split product costs out of Build — all historical product COGS stays inside the Build `51xxx` mappings (`54xxx` has zero prod sources). In the WI-053 historical P&L trend, Products will show ~100% gross margin and Build will be correspondingly understated; historical Products margin is not reconstructible, and per-stream Products margin is meaningful only from 2027-01-01 forward.

Income extras: `4160 Billable Expense Income` (billable expenses + markup + billable mileage), `4180 Discounts & Allowances` (contra), `42xx` other income (interest, late fees, grants, tips, misc, surcharge — see §4).

Revenue **by segment (Commercial/Residential)** is deliberately *not* in the chart — that is the native Accounting Dimension per OD-4/WI-054.

## 4. Special-purpose accounts and their consumers

| Account | Type | Consumer |
|---|---|---|
| `1131 Stripe Clearing` | Bank | WI-005 (routing), WI-039 (Stripe go-live), WI-040 (payout JE: clearing → bank, fees → `5221`) |
| `1132 Undeposited Funds` | Bank | WI-031 modes of payment (checks/cash land here until deposit) |
| `5221 Merchant Fees` | Expense Account | WI-005/WI-040 (Stripe fee journaling); prod's `CC Processing fees` + `QuickBooks Payments Fees` map here |
| `1910 Temporary Opening` | **Temporary** | WI-032 — `opening_balances.py::_plug_line` requires exactly one Temporary-type account; **this is the only one in the chart** |
| `3300 Opening Balance Equity` | Equity | QBO OBE tie-out landing during WI-032/WI-035; must read zero after sign-off |
| `3400 Historical P&L Offset` | Equity | WI-053 monthly P&L trend import (offset side of the historical JEs) |
| `2141–2147` payroll liabilities + `60810–60870` payroll expenses | — | WI-047 payroll-return JE: gross wages `60810/60820/60830`, employer taxes `60840`, withholdings `2142/2143/2146`, employer accruals `2144/2145/2147`, net pay clears through `2141 Net Pay Clearing` |
| `1151 Inventory On Hand` (Stock), `2151 Stock Received But Not Billed`, `2152 Asset RBNB`, `2153 Service RBNB`, `5111 Cost of Goods Sold`, `5112/5118/5119` | stock types | WI-060 Phase-2 perpetual inventory — structure ships now, unused until then. `2153` also lets the importer auto-set `default_provisional_account` |
| `2131–2136` sales/use-tax sub-accounts | Tax | WI-036 tax templates; `2136` is the Build use-tax accrual credit side (see §6) |
| `1121 Operating Bank Account`, `1122 Savings Bank Account`, `2121/2122 Business Credit Card 1/2`, `2181/2182 Bank Line of Credit 1/2` | Bank / — | **Placeholders.** WI-042 renames/extends to finance's real bank list *before* WI-032 posts opening balances (each real bank account needs its own leaf to reconcile). Deliberately brandless per OD-1 discipline |
| `4260 Payment Surcharge Income` | Income Account | dormant until OD-7 revisited (WI-055); harmonizes with TEST's "Stripe Surcharge Income" without the brand name |
| `1230 Event Rental Assets` | Fixed Asset | the rental-fountain fleet (OD-3 naming) |
| `1280 CWIP`, `1290/1390 Accumulated Depreciation/Amortization`, `5203/5212/5218/5219/5220` | system types | ERPNext asset module, rounding, write-off, FX, disposal defaults |

Net-new leaves with no prod counterpart (everything else maps from production): `1131`, `1162 Prepaid Expenses`, `2136 Use Tax Payable` (§6), `2153`, `2162 Deferred Revenue`, `3400`, `4260`, `54100/54200/54300/54500` (Products COGS), `60850 Payroll Service Fees`. Each exists for a named consumer or an obvious accrual gap (prepaids/deferred revenue had no home in QBO); the CPA can strike any of them at review.

Booking guidance (for AP coding — two deliberately separate membership leaves): **`60460 Memberships & Networking`** = revenue-generating / lead-generation networking memberships (e.g. wedding-industry networks — prod's `Network Memberships - Wedding`); **`61060 Dues & Memberships`** = general professional dues and subscriptions (prod's `Membership Expense`). When in doubt: if the membership exists to win work, it is `60460`; otherwise `61060`.

## 5. Company default accounts (WI-029 sets these)

| Company field | Account |
|---|---|
| `default_receivable_account` | `1141 - Accounts Receivable` |
| `default_payable_account` | `2111 - Accounts Payable` |
| `default_bank_account` | `1121 - Operating Bank Account` (re-pointed to the real primary operating account after WI-042 renames it) |
| `default_cash_account` | `1111 - Petty Cash` |

The importer's own `set_default_accounts` will auto-pick `1141` (only Receivable leaf), `2111` (only Payable leaf) and `2153` (only Service-RBNB leaf, → `default_provisional_account`); bank/cash defaults are set explicitly in WI-029. Also recorded for WI-029: `write_off_account → 5218`, `exchange_gain_loss_account → 5219`, `disposal_account → 5220`, `depreciation_expense_account → 5203`, `accumulated_depreciation_account → 1290`, `round_off_account → 5212`, `stock_received_but_not_billed → 2151`, `stock_adjustment_account → 5119`, `default_inventory_account → 1151`, `default_expense_account → 5111`.

## 6. OD-2 tax structure (structure only — rates are the CPA's)

`2130 Sales Tax Payable` (Tax) carries six leaves:

- `2131 UT 4%`, `2132 UT 6%`, `2133 UT 6.25%` — the three rates live on both prod and TEST today (`ST 4%/6%/6.25%`).
- `2134 UT Other Jurisdictions` — bucket for (a) opening balances of QBO's agency-payable accounts (Utah State Tax Commission Payable etc.) and (b) any additional locality rate the CPA matrix adds before WI-036 finalizes templates. If the matrix lands more distinct live rates, add `2137+` leaves in WI-029 — the group structure is the contract, not the leaf count.
- `2135 Out of State` — out-of-state exposure bucket (also absorbs the Virginia agency remnant, presumed lapsed).
- `2136 Use Tax Payable` — the credit side of **self-assessed use tax** (chiefly Build materials bought untaxed — see the Build bullet below). Kept strictly separate from the collected-sales-tax leaves `2131–2135` because Utah TC-62 reports sales tax and use tax on *separate lines*; conflating them would force a manual split at every filing.

**Open structural question the CPA matrix must resolve before WI-036 locks templates — per-rate vs per-jurisdiction leaves.** OD-2/WI-004 say "per-jurisdiction" sub-accounts, but `2131–2133` are per-*rate* (inherited from prod's `ST 4%/6%/6.25%`). Utah is single-agency (USTC administers the locals), yet TC-62M schedules report by **location code**, not rate: if two jurisdictions ever share a combined rate, one rate bucket cannot be decomposed into filing lines from the GL alone — which undercuts the "GL as filing schedule" premise (WI-038). Rate-named accounts also go stale: Utah locality rates change quarterly, and when a locality re-rates the account name lies or forces a rename. Resolution paths (pick one, in writing, with the matrix): **(a)** the CPA confirms each `213x` rate bucket corresponds 1:1 to exactly one filing jurisdiction/location code for this business, and that mapping is recorded here; or **(b)** the leaves are renamed per jurisdiction (e.g. `Sales Tax Payable - Davis County`) in WI-029 and the rate lives only on the WI-036 tax templates. Either way the matrix must record where TC-62M location detail comes from: the GL directly (option b) or the Sales Register report grouped by tax template (option a). The matrix must also explain what prod's `ST 4%` actually is — no current UT combined sales-tax rate is 4%; `2131` is carried from prod without explanation and may be a stale or special-case rate.

Stream treatment encoded by this structure (per the OD-2 "follow Utah law" resolution — **the CPA's written matrix confirms every rate and taxability call before go-live; that written confirmation is the WI-036 sign-off gate**):

- **Build = real-property improvement:** the contractor is the *consumer* of materials — pays sales/use tax on purchases, does not charge customers sales tax on the improvement. Two purchase cases: (1) the vendor charged UT sales tax → the tax is simply part of purchase cost (or expensed to `60920`), **no accrual entry**; (2) materials bought untaxed — common with out-of-state fountain-equipment/component vendors — the contractor **self-assesses use tax: debit `60920 Sales & Use Tax Expense` / credit `2136 Use Tax Payable`**, and `2136` is drawn down when the TC-62 remittance is paid. Prod's `Sales Tax Expense - Utah` maps to `60920` — the debit-side pattern already exists in the books; `2136` gives the accrual a home so it never lands in the collected-tax buckets.
- **Service / Events / Products:** taxable sales; tax collected at the delivery-point jurisdiction rate posts to the matching `213x` leaf via WI-036 Sales Taxes and Charges Templates + Tax Rules.
- **Design:** professional services, presumptively not taxable stand-alone — explicitly on the CPA-confirmation list (design bundled into a Build contract follows the Build treatment).
- The ~36 QBO locality/agency artifacts (`UT-Salt Lake-Salt Lake City`, `Orem`, `Big D`, `Tax Exempt Payable`, …) are RETIREd: under ERPNext the *templates* carry jurisdiction detail and the GL carries the liability by rate bucket, so the GL itself becomes the filing schedule (the WI-038 premise).

## 7. Harmonization vs the TEST pilot chart (282 accounts)

Kept from TEST verbatim: `4100 Direct Income` group; stream-COGS group numbers `50000/51000/52000/53000`; COGS leaf suffix convention `x100–x600`; system stock accounts `5110/5111/5112/5118/5119`; `5200 Indirect Expenses` as home of Merchant Fees; operating-expense skeleton `60000/60100/60200 (60210/60215/60216/60220/60230/60240)/60300/60400 (60430/60440)/60500-range/60700/60800/60900`; G&A `61000/61050/61060/61100/61200/61300/61400 (61420–61470 insurance leaves, same numbers)/61500/61600/61700`.

Deliberate divergences (this design owns coherence):

| TEST | This design | Why |
|---|---|---|
| `53000 Rent COGS`, `43000 Rent Income`, `60360 Rent R&D` | Events everywhere (`53000/4140/60340`) | OD-3 |
| 5-digit income (`40000 Design Income` … `43000`) | 4-digit `4110–4150` under `4100` | WI-004 mandates 4xxx income; TEST's own `4100 Direct Income` sat empty beside the 5-digit strays — duplication removed |
| duplicate `30100 Retained Earnings` / `31000 OBE` beside `3400/3300` | single `3200` / `3300` | pilot artifact |
| `54000 Sales Commission` | `54000 = Products COGS`; commissions → `55100` | Products is the 5th stream and deserves the next stream slot |
| `x700 Inventory` + `x800 Insurance` COGS leaves | Inventory folded into `x100 Materials`; Insurance takes `x700` | per-stream "Inventory" was a QBO periodic-inventory artifact; WI-060 replaces the mechanism |
| `53101/53102 Materials Fountain Pillar/Jumping Laminar` | collapsed into `53100` | per-unit detail → Item/Project reporting |
| real bank/card names (`13000 US Bank Checking`…) | brandless placeholders (`1121/1122/2121/2122/2181/2182`) | OD-1 reusability; WI-042 supplies the real list |
| `60500/60600` software leaves + stray `64410` | `60500 Software & Technology` group (`60510/60520/60530`) | consolidation |
| payroll leaves `60820–60860` (mixed semantics) | `60810–60870` clean sequence + `60850 Payroll Service Fees` | WI-047 needs unambiguous landing spots |
| `63200 Public Relations`, `60475`, `65430/65440`, `70000/71000/72xxx/80000/81000/82000` strays | renumbered into their parents' ranges | stray QBO numbers broke the group ranges |
| `ST 4%/6%/6.25%` unnumbered under `2300 Duties and Taxes` | `2131–2133` under `2130 Sales Tax Payable` | OD-2 structure, numbered |

## 8. Importer-format notes (why this CSV passes preview)

Derived from the v16 `chart_of_accounts_importer.py` source, not from documentation:

- **Exactly 8 columns** on every row (`validate_columns` hard-fails otherwise): `Account Name, Parent Account, Account Number, Parent Account Number, Is Group, Account Type, Root Type, Account Currency`.
- **Root rows** (the five `1000/2000/3000/4000/5000` groups) leave Parent Account *and* Parent Account Number empty — the importer substitutes self-reference; all five root types must be present (`validate_missing_roots`).
- **Every non-root row fills both parent columns**, matching the parent row's Account Name/Number exactly — `build_forest` resolves parents by the composed `"number - name"` string.
- Account Currency is left blank (company default USD applies).
- Exactly one `Temporary`-type account (`1910`), per the WI-004 acceptance criterion and `opening_balances.py`.
- All account types used are values already present on the live v16 sites (Bank, Cash, Stock, Receivable, Payable, Fixed Asset, Accumulated Depreciation, Capital Work in Progress, Temporary, Tax, Stock/Asset/Service Received But Not Billed, Cost of Goods Sold, Depreciation, Round Off, Stock Adjustment, Expenses Included In (Asset) Valuation, Equity, Income Account, Expense Account).
- A generator+validator script reproduces the importer's `validate_columns`/`validate_accounts`/`build_forest` logic (column count, root-type set, parent resolution, cycle detection, composed-name uniqueness) and passes; the acceptance criterion remains an actual preview run on the TEST site (WI-004 AC #1).

## 9. Mapping conventions (`coa_mapping.csv`)

- **359 rows, one per prod `tabAccount.name` verbatim** (including the ` - SF` suffix). 264 MAP / 95 RETIRE.
- **MAP** = activity/balances/references re-point to the named target (WI-029 re-points QuickBooks Sync Mappings, Company defaults, Stripe settings off this file; WI-053 uses it for the historical P&L; WI-032 uses it for opening-balance landing).
- **RETIRE** = no successor. Five retirement classes, each with a reason note: `(deleted)`-suffixed QBO corpses (51), QBO automated-sales-tax jurisdiction/agency artifacts (38), QBO cash-basis artifacts (`Unapplied Cash Payment Income`, `Unapplied Cash Bill Payment Expense`), QBO catch-alls that must not survive (`Uncategorized Asset`), and unused ERPNext-standard accounts with no US meaning (`TDS Payable`, `Tax Assets`, `Investments`). **A RETIREd account must carry a zero balance at cutover; WI-032's tie-out enforces this** — any RETIREd row that turns out to hold a balance gets re-pointed at that moment with CPA sign-off.
- Group rows map to the corresponding new group (structural — groups never hold postings) or RETIRE.
- Misclassifications fixed in flight (all flagged for CPA confirmation in the note column): `America First Auto Loan` (QBO bank-type *asset*) → `2210` liability; `F-150 Harris Loan` (QBO *equity*) → `2220 Notes Payable - Related Parties`.
- Many-to-one mappings are intentional: seven QBO bank accounts land on the two placeholders pending WI-042; five per-fountain material accounts land on `53100`; the ERPNext-default indirect expenses (`5204–5217`) land on their operating-tree equivalents.

## 10. What finance/CPA must ratify on this PR

1. **Stream COGS shape** — the 7-leaf template per stream (esp. Subcontract Labor as its own line) and the 4-leaf Products trim.
2. **Products as a fifth stream** — `4150/54000` split out of Build (`Build Products` + `Sales of Product Income` map there).
3. **Tax structure** (§6) — rate buckets `2131–2135`, Build use-tax debit to `60920`, and the retirement of all QBO locality/agency accounts; **written rate/taxability matrix to follow before WI-036 go-live**.
4. **Reclassifications** — `America First Auto Loan` asset→liability, `F-150 Harris Loan` equity→liability, `Dividends Paid`/`Capital Stock` → member draws/contributions (LLC treatment).
5. **Retirement list** — every RETIRE row's note, esp. the presumption that VA registration is lapsed and that `Ask My Accountant`/`Uncategorized *` balances get recoded at review.
6. **Net-new accounts** (§4 list) — strike any unwanted (e.g., `1162 Prepaid Expenses`, `2162 Deferred Revenue`).
7. **Placeholder banking** — confirm WI-042 will deliver the real bank/card/LOC list before opening balances post.
8. **Statutory rollups** — group subtotals (`2140` payroll, `2130` sales tax, `60800` payroll expense) match how the CPA wants the trial balance to read for returns.
9. **Use-tax accrual** (§6) — `2136 Use Tax Payable` as the credit side of Build self-assessed use tax (debit `60920` / credit `2136` when materials are bought untaxed; nothing when the vendor charged the tax). Confirm accrual timing (per purchase vs period-end) and that TC-62 use-tax lines will be filed off `2136` alone.
10. **Per-rate vs per-jurisdiction sales-tax leaves** (§6) — resolve the open question in writing with the matrix: either (a) confirm each `213x` rate bucket maps 1:1 to exactly one TC-62M location code for this business, or (b) rename the leaves per jurisdiction at WI-029 and carry rates only on WI-036 templates. Include the answer to what prod's `ST 4%` is.
11. **Meals & entertainment deductibility scheme** — `60420 Promotional Entertainment` (0% deductible post-TCJA), `60425 Promotional & Business Meals` (50%), `61860 Travel Meals` (50%), `61850 Overhead Travel & Lodging` (100% — lodging/transport only, kept pure). Confirm the split matches how the return is prepared; prod's `Meals- Promotional` → `60425`, `Travel-Related Meals` → `61860`.
12. **Current LLC member count** — prod shows at least two historical members (`Member Contributions - JDH Business Investments`, `Member Contributions - Oak Ventures LC (deleted)`); the new chart has single `3110/3120` leaves with member identity in JE remarks. If the LLC is currently **multi-member**, per-member tax-basis capital tracking for K-1s (required since 2020) needs per-member sub-leaves (`3111/3112` contributions, `3121/3122` draws) added at WI-029 — numbering already leaves room, and member names do not violate the OD-1 company-agnostic rule (members are not the company). If single-member (Oak Ventures exited), the single leaves stand.
13. **Vendor rebates/credits as income** — `Amazon Credit` maps to `4250 Other Miscellaneous Income`; booking rebates as other income grosses up both income and expense, where standard treatment is contra-expense (credit the original purchase category, e.g. via `61900`). Explicit question for the CPA: income or contra-expense? Small dollars; re-point at review if contra is preferred.

Reviewer notes — accepted as-is, no action needed (recorded so nobody re-litigates them):

- `60210 Building Rent & Lease` and `60240 Storage Rent` contain the token "Rent" but are **occupancy expenses** (rent the company *pays*, leaves under `60200 Physical Facilities`, Expense root) — not the OD-3 value stream, which is named **Events** everywhere it appears (`4140`, `53000–53700`, `60340`). Rename to "Building Lease & Occupancy" / "Storage Fees" only if the team wants zero "Rent" tokens chart-wide.
- `1230 Event Rental Assets` contains "Rental", which describes the fleet's *function* (units rented out) under the OD-3-compliant "Event" prefix — it is not a stream name.
