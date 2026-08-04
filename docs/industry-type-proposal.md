# Industry Type — proposed keep / merge / retire list

**This is a proposal. Nothing in it has been executed.** WP-5 asked for the list to
be produced, not applied — deletions are yours to approve.

Counts are live from production, 2026-08-04, across Customer, Opportunity and Lead.
89 Industry Type records exist; **47 are used, 42 have never been used on any
record**.

---

## 1. Retire — 42 values, zero usage anywhere

None of these appears on a single Customer, Opportunity or Lead. They are ERPNext's
stock defaults for a generic CRM, plus one obvious typo record.

Accounting · Aerospace · Airline · Biotechnology · Broadcasting · Brokerage ·
Chemical · Consumer Products · Cosmetics · Defense · Department Stores ·
Electronics · Energy · Entertainment · Executive Search · Grocery ·
Internet Publishing · Investment Banking · Legal · **mark** · Media ·
Motion Picture & Video · MSP (Management Service Provider) · Music ·
Newspaper Publishers · Online Auctions · Party Decorators · Pension Funds ·
Pharmaceuticals · Private Equity · Rental Agent · Retail & Wholesale ·
Securities & Commodity Exchanges · Soap & Detergent · Software · Sports ·
Technology · Telecommunications · Television · Transportation · Venture Capital ·
Water Chemicals

Two worth a second look before you agree:

- **`mark`** is not an industry. It is a stray record — almost certainly a
  half-typed entry saved by accident. Delete regardless of what you decide about
  the rest.
- **`Party Decorators`**, **`Rental Agent`** and **`Water Chemicals`** are unused
  but *plausible* for this business in a way that `Soap & Detergent` is not. If
  any of them describes a segment you intend to sell into, keep it — an unused
  value that matches your strategy is cheaper than re-creating it later.

Retiring 42 values shortens the picker from 89 to 47, which is the difference
between a list somebody scrolls and a list somebody scans. That is most of the
value of this exercise.

---

## 2. Merge — genuine duplicates already splitting your data

These are the ones that actively cost you reporting accuracy today.

| Merge | Into | Combined | Why |
|---|---|---|---|
| `Event Planner` (15) | **`Event Planning`** (18) | **33** | The same thing, spelled two ways. Today event planners are the 5th *and* 8th largest segments instead of being — by a distance — one of the largest. |
| `Artist/Sculpturer` (3) | **`Artist/Sculptor`** (new) | 3 | "Sculpturer" is not a word. Rename rather than merge. |
| `Fountain Construction` (2) | **`Pool Construction`** (15) | 17 | Judgement call — see below. |

**`Event Planner` → `Event Planning` is the one that matters.** At 33 combined it
becomes the third-largest segment after Architecture (57) and Tradeshow Booth
Builder (35). Any segment analysis run today understates it by nearly half. This
also interacts with the open scale-taxonomy decision: whatever you decide about
classifying planners by scale, it should be applied to *one* industry value, not
two.

**`Fountain Construction` (2) is a judgement call, not an obvious merge.** It may
be a genuinely distinct segment — firms that build fountains are competitors or
partners, not customers in the same sense a pool builder is. With only 2 records I
would **keep it separate** and revisit if it stays tiny.

---

## 3. Consider consolidating — overlapping, but defensible as-is

Not recommended for merging; flagged so the decision is deliberate rather than
accidental.

| Values | Counts | Note |
|---|---|---|
| `Event Venue` · `Event Production` · `Event Planning` | 24 · 17 · 33 | Three distinct roles in one industry. A venue books you, a production company subcontracts you, a planner refers you — different sales motions, so keep them apart. |
| `General Contractor` · `Home Builder` · `Developer` | 11 · 5 · 8 | Different points in the construction chain and different deal sizes. Keep. |
| `Home Owner` (5) | 5 | Overlaps with `customer_type = Residential` (185). The **customer type** is the right home for this, not industry. Retire once those 5 carry the right type. |

---

## 4. Keep as-is — the working taxonomy

The 47 in-use values minus the changes above. The head of the distribution is
healthy and clearly reflects the business:

| Industry | Customers |
|---|---|
| Architecture | 57 |
| Tradeshow Booth Builder | 35 |
| **Event Planning** (after merge) | **33** |
| Interior Design | 30 |
| Event Venue | 24 |
| Event Production | 17 |
| Pool Construction | 15 |
| Government/Military | 12 |
| General Contractor | 11 |
| Landscaper | 9 |

---

## Before executing any of this

**Deletion is two steps in this repo.** Removing a record from `fixtures/*.json`
only stops managing it — it does not delete it from the database. A one-shot patch
calling `frappe.delete_doc(...)` is also required. See
[`fixtures/README.md`](../erpnext_enhancements/fixtures/README.md).

**A merge is a rename, not a delete.** For `Event Planner` → `Event Planning`, use
`frappe.rename_doc("Industry Type", "Event Planner", "Event Planning", merge=True)`.
That repoints every referencing record. Deleting the record and hand-updating rows
would miss references on Opportunity and Lead, which also carry an `industry`
field.

**Order matters.** Merge first, then retire. Retiring a value that a merge was
about to fold in would strand its records with a blank industry, adding to the
1,292 that are already blank.

**Do it before turning on `require_industry_on_edit`.** Once industry is required
on every save, anybody editing an account whose industry you just deleted is
blocked on a field they cannot fix from the form.
