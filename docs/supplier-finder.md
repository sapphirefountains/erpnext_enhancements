# Find a Supplier (Supplier & Item Search)

Shipped in v1.722.0. Tracked as ERPNext TASK-2026-02911 (PRJ-00580, under *Purchase Order Usability*).

## The problem

Nik, 2026-10-22:

> "Supplier Groups also should be easier to find. … say I know we need a pump, but I don't remember all our pump
> suppliers. So I could search in the Supplier DocType for Supplier Group - "Pumps", but what if we have two pump
> categories and one pump supplier I'm looking for is in a different supplier group. This tool should make it easy to
> be general or specific with item and supplier searching. So if I need a particular pump model, I can even query that
> and the filtering goes even further to show which Pumps Suppliers sell that pump or have historically."

Production on the same day (read-only queries):

| What | Measured |
|---|---|
| Supplier Groups | 174, all flat under the root; 11 empty |
| Suppliers | 1,193; **629 have no group** |
| Groups a pump supplier might be in | at least five (Pumps; Fountain Supplies - Pumps, Filters; Motors; Fountain Components; Pool Supplies) |
| Suppliers we have bought pumps from that are in a group with "pump" in its name | 1 of 7 |
| Duplicate supplier records (same company entered twice, by normalised name) | about 50 clusters |
| Suppliers mapped to QuickBooks vendors | 918 (so merging records is risky) |
| `Supplier.custom_additional_supplier_groups` (multi-group) in use | 1 supplier |
| Submitted PO lines | 592 since 2025-10-01; 57 suppliers; 466 supplier–item pairs; **377 at $0** |
| Purchase Receipt lines | 380 (94 with a rate) |
| Supplier Quotation lines | 51 |
| Item Supplier rows | 74 (34 with a part number) |
| Buying Item Prices with a supplier | 1 of 381 |
| Items | 1,169; 750 in "Products" or the root group; Brand and Manufacturer empty |
| Pre-ERPNext purchases | QuickBooks Bills (1,353) and Purchases (9,831) became Journal Entries with no line text. The line descriptions survive only in `QuickBooks Raw Payload` (2.6 GB, no index on entity type). |

The conclusion is that a Supplier Group cannot say who sells a pump. Purchase evidence can, and the item name is the
best text there is to search. POL-0602 names lead with the category, as in "PUMP, PENTAIR …".

## Nik's decisions (2026-10-22)

1. **Where it lives:** a Desk page, *Find a Supplier* (`/desk/supplier-finder`). There is a **Find suppliers**
   button on the Item and Purchase Order forms that opens the page on one Item. There is also an MCP assistant tool,
   `supplier_search`, so Triton and Claude can answer "who sells X". It is for Nik, purchasing and PMs.
2. **How much AI:** *query expansion only*. When the "Let AI broaden the search" box is ticked, Gemini rewrites a
   fuzzy search into search phrases and Supplier Group names. The model sees **only what was typed and the list of
   group names**. It never sees a supplier, an item or a price. Ranking is deterministic. If the AI is not set up, is
   rate-limited, times out or answers something unusable, the keyword results come back with a one-line note.
3. **QuickBooks history:** *later, as phase 2*. Phase 2 is a derived index of the QBO bill and purchase line
   descriptions, built by a background job. That is a production write and is shown to Nik before it runs.
4. **Supplier Group and duplicate-supplier cleanup:** *proposal only*. The search already folds duplicate supplier
   records into one row as aliases. A cleanup sheet goes to Nik, and nothing is written until he signs off.

## How a search works

`api/supplier_search.py` does the reads and the one optional model call. Everything that decides an answer is in
the pure `inventory_enhancements/supplier_search_rules.py`, and `tests/test_supplier_search_rules.py` runs it on
every push.

1. **Terms** (`query_terms`):
   - Filler words are dropped ("who sells a").
   - Plurals are folded (PUMPS → PUMP).
   - Sizes and units become one term: "1-1/2" → `112`, "2 hp" → `2HP`, as a squashed item name spells them.
2. **Items** (`match_items`). The whole catalog is read once (about 1,200 rows), together with the Item Supplier part
   numbers.
   - A term matches a word it starts (PENT → PENTAIR).
   - A term with a digit, or a word of six letters or more, also matches inside the squashed text ("SQ 3-65" finds
     SQ365). Shorter words do not, or LAMP would find CLAMP.
   - A short synonym table covers the old vocabulary, for example LIGHT/LAMP, WIRE/CABLE and NOZZLE/JET.
   - The whole search squashed and found as an item code or supplier part number (or inside one) is a **model match**
     and ranks first.
   - An item whose name *starts* with the term ("PUMP, …") outranks one that only mentions it ("COVER, PUMP, …").
   - When an item matches every term, only those items are shown. When none does, items matching at least half the
     terms are shown with a "partial" note.
3. **Groups** (`match_groups`). Group names are matched by their words, ignoring SUPPLY/SUPPLIES/SUPPLIER/AND.
   - "Pumps" ranks before "Fountain Supplies - Pumps, Filters" because more of its own words matched.
   - A model search names no group. It uses the matched items' category word instead: EQKT750 is a PUMP, so the pump
     groups apply.
4. **Evidence** for the matched items, per supplier–item pair:
   - submitted PO lines and Purchase Receipt lines (count, last date, last nonzero rate);
   - Supplier Quotation lines;
   - Item Supplier rows (and part numbers);
   - Item Default `default_supplier`;
   - buying Item Prices with a supplier.
5. **Ranking** (`rank_suppliers`):
   - Duplicate records fold into one row (`supplier_key` drops INC/LLC/CO/CORP/COMPANY/LTD/THE/.COM and
     punctuation). The record with the most evidence leads and the rest are listed as aliases. **Nothing is merged.**
   - Tiers come first: **history** (bought or received), then **carries** (quoted, listed, default or priced), then
     **group** (only a matching group or name). Disabled suppliers sink to the bottom.
   - Within a tier, the order is by points: a PO is 3 (plus 1 for each further line, up to 4), a receipt is 2 (plus
     0.5 each), a quote 1.5, a listing 2 (plus 0.5 for a part number), a default 1.5 and a price 1.
   - Bought and received points are scaled by recency: 1.0 within a year, 0.8 within three years, 0.6 older.
   - Each pair is weighted by how well its item matched: 1.0 for a model match, 0.9 for strict, 0.5 for partial or
     AI-only.
   - A group or name match adds at most 0.5 each.
6. Each supplier gets a plain **reason**, for example "Bought 4 matching items from them, last on 2026-07-21".

Prices (`last_rate`) are returned only to someone who may read Purchase Order. Everyone else gets counts and dates.

## The AI step

- **Prompt:** the search (tags stripped, fenced) and the Supplier Group names (`build_expansion_prompt`). Its
  feature on AI Model Usage is `supplier_search_expand`.
- **Call:** `api/gemini.py` with JSON output, LOW thinking and a 20-second timeout.
- **Reply handling** (`parse_expansion`): the reply is untrusted.
  - At most 6 phrases of up to 3 words each.
  - Groups are kept only when the site has a group of exactly that name, ignoring case.
  - The note is plain text of at most 200 characters.
- **How the result is used:**
  - Each phrase is searched strictly. New items are added marked "AI" at half weight.
  - Each group adds its suppliers in the *group* tier.
- **Limits:**
  - Skipped when the search is a model or part number match.
  - Cached for a day per search.
  - 60 calls per user per hour.
- **Logging:** one Error Log row on failure, with the exception's class only and never the query.
- **Assistant tool:** `expand` is off by default, because the calling model can rephrase the search itself.

## Screens and history

Every screen is its own history entry, so Back and Forward work. The page reads `frappe.route_options` on each
show, which the router fills from the address bar on Back as well.

- `/desk/supplier-finder`: the empty search, with example chips.
- `?q=pump&ai=1`: a search.
- `?item=<code>`: every supplier of one Item (the form buttons).
- `?supplier=<name>`: what we have bought from a supplier and its aliases.

A group chip opens the Supplier list filtered by that group. The list's existing filter rewrite
(`supplier_list.js`) includes suppliers whose *additional* groups match.

## Not in this version

- Phase 2: an index of the QuickBooks line descriptions (prod write; needs Nik's sign-off).
- The Supplier Group and duplicate-supplier cleanup itself, which needs Nik's sign-off on the proposal sheet.
- Lead times. Nothing records them yet: PO `schedule_date` is a requested date, not a promised one.
