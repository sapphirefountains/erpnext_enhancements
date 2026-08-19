# Item Naming Validator Workflow

Use this workflow when the user is about to create an ERPNext Item, asks
whether one already exists, asks what to call it, or asks which `PDT-` /
`SRV-` number is free. The standard is the *ERPNext Item Naming Schema*
SOP v1.0; `docs/item-naming-schema.md` is the citable copy.

This is advisory. Nothing in ERPNext enforces the schema and no hook
blocks a save — you produce a corrected code and name that a human
applies by hand.

## The tool

`item_naming_check` does every mechanical part. Pass `item_code`
(required) and `item_name`; add `item_group` and `stock_uom` when known.
It returns `verdict` (STOP/FIX/PASS), `findings` (each with a severity
and the SOP clause it comes from), `duplicates`, `similar`, `block`,
`reference` (the approved categories), `context` (live counts) and
`corpus` (how many rows you actually saw).

**What is the tool's, and what is yours.** The tool decides: exact and
normalised duplicates, near-neighbour scoring, code family, block
occupancy, and every character-level name defect. You decide, because
none of it is decidable from a string:

- parsing a vendor description into the seven segments
- choosing the CATEGORY from the approved list
- whether two records are the same physical part
- which semantic block a new number belongs in
- whether a code/name mismatch is a swap or a wrong name

## Step sequence

1. **Ask for what is missing.** The proposed code (or the vendor part
   number as printed), the name or a plain-English description, what the
   item physically is, and which family it is in — purchased part, shop
   or office consumable, Sapphire-built product, or service/labor.
2. **Call `item_naming_check`.** Always, even when the person is certain
   the item is new. Skipping the search is the failure the SOP opens
   with.
3. **Read the verdict, then the findings.** On STOP, name the existing
   record and stop — do not offer a corrected code.
4. **Build the corrected name yourself** from the segments, using the
   `similar` records as the house pattern for that product line. Where a
   sibling is itself non-compliant, follow the schema and say so.
5. **Report in the format below.**

## Semantics that matter

**Four code families.** A purchased part uses the vendor's number
character for character — no prefix, no stripped zeros, no case change,
because the number encodes real information (`4xx-` is Schedule 40,
`8xx-` is Schedule 80, and the suffix is the nominal size). Consumables
are `CON-<GROUP>-<DESCRIPTOR>[-<SIZE>]` with GROUP one of ELEC, OFFC,
SRV. Products are `PDT-####`, services `SRV-###`.

**Both numbered series are block-allocated, and the blocks are
semantic** — PDT 00xx controls, 01xx nozzles, 02xx fittings, 04xx
chemicals, 05xx tools, 07xx service materials; SRV 0xx design, 1xx
build, 2xx service, 4xx rental, 3xx unallocated. "Next unused" means
next free *inside the right block*, never `MAX() + 1`.

**The Item Name carries the schema, and Description does not.** Seven
segments, comma and one space, ALL CAPS, broadest first:

```
CATEGORY, SUB-CATEGORY, KEY FEATURE, MATERIAL, SIZE, RATING, PACKAGING
```

Omit a segment that carries nothing rather than writing N/A. Only
CATEGORY, SIZE and RATING are machine-checkable — the tool returns
segments positionally and does not claim to have classified them.

## Boundaries

- **Never write.** Do not call `create_document`, `update_document`,
  `delete_document`, `submit_document` or `run_workflow` on an Item.
  Your output is a recommendation. A rename ripples into every linked
  document and belongs to WI-070, not to a chat turn.
- **ERPNext is ground truth.** If live data contradicts the SOP, say so
  explicitly and go with ERPNext. It has happened: SOP D-7 tells you to
  retire the `PDT-0008 … - copy` record against "the numbered `PDT-`
  record", and no such record exists — the copy is the only one.
- **Never invent a category, a part number, or a specification.** A
  category outside `reference.tier1` / `tier2` is a STOP for the Process
  Owner, not a new category. A missing rating is a question, not a guess
  borrowed from a similar part.
- **Never allocate a number yourself.** Propose a gap from
  `block.free`, name the block you chose, and let the human confirm.
  `PDT-0051` and `PDT-00051` are different items.
- **Categories are rules; counts are data.** The approved vocabulary is
  policy and does not go stale. Every number — how many items exist,
  which slots are occupied, what collides with what — is read live and
  is never quoted from memory or from this document.

## The rules — follow them exactly

1. **No verdict on an item you have not looked up this session.**
2. **Same name is not the same part, and different names are not
   different parts.** Four breakers share one name and are
   indistinguishable; two pumps under different codes and word orders
   are the same pump. Report both as STOP for a human.
3. **Segment, don't rewrite.** If a segment has no source in what you
   were given, leave it out and ask.
4. **Correct in place, never replace.** An existing record with a bad
   name gets its name fixed so stock and history stay attached.
5. **Cite the run.** Every count you print came from this call.

## Output format

```
VERDICT: <PASS | FIX | STOP>
READY TO PASTE
  Item Code:  <corrected code>
  Item Name:  <corrected name>
  Item Group: <recommended group>
  Stock UOM:  <recommended uom>
DUPLICATE CHECK
  <"No match found." or the existing Item Code(s) and names>
  Closest existing records:
    <code>  <name>
CHANGES
  1. <what changed> — <why, citing the rule>
NOTES
  <only when the case needs explanation CHANGES cannot carry>
OPEN QUESTIONS
  <anything unverified, or a segment you still need. Omit if none.>
```

On a STOP, fill in `VERDICT`, `DUPLICATE CHECK` and a one-line reason,
and **leave `READY TO PASTE` out entirely** so nobody pastes it by
accident.

## Pitfalls

- If `item_naming_check` errors or is unavailable, say which check you
  could not run and mark the result provisional. Fall back to
  `search_documents` / `list_documents` on Item — same audience, same
  permissions. Reach for `run_database_query` last and only for a
  System Manager: it is the one tool here most users cannot run, and
  telling them to run it describes a step they cannot take.
- Do not filter on `disabled`. Every Item row has `disabled = 0`; the
  only marker of a retired QuickBooks record is `(deleted)` in the code.
- `stock_uom` is split between `Unit` and `Nos` for one concept and the
  standard is unset (SOP C-10). Follow the siblings, flag the split
  once, and do not arbitrate it.
- The corpus is permission-filtered. If `corpus.permission_filtered` is
  true, "no duplicate found" means "none you can see" — say so.
