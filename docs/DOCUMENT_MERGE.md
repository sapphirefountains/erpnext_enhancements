# Document Merge

Consolidate two duplicate documents of the **same doctype** into one. Every
reference to the duplicate is repointed at the record you keep, the kept record's
blank fields are filled in from the duplicate, and the duplicate is deleted.

Implemented in [`erpnext_enhancements/document_merge.py`](../erpnext_enhancements/document_merge.py)
with the desk UI in
[`public/js/merge_tool/merge_tool.js`](../erpnext_enhancements/public/js/merge_tool/merge_tool.js).

## Terminology

- **Survivor** — the document you keep. References end up pointing here.
- **Loser** — the duplicate that is absorbed and then **permanently deleted**.

## Enabling it

Off by default. Turn it on at **ERPNext Enhancements Settings → Document Merge →
Enable Document Merge Tool**. While off, the desk button is hidden and the server
endpoints refuse. Only **System Managers** see the button and can run a merge
(and they still need `write` on the survivor and `delete` on the loser).

## Using it

**Single document (form):** open the record you want to **keep** (the survivor),
click **Merge into…**, and pick the duplicate to absorb. A preview opens. Use
**Swap** if you opened the wrong one.

**Several at once (list view):** check 2+ rows, then **⋯ menu → Merge Selected…**,
choose which checked record is the survivor, and confirm. Each of the others is
merged into it in turn.

## What the preview shows

- **Fields** that differ (survivor's value is **kept**, the loser's is discarded)
  or that will be **backfilled** (survivor was blank, takes the loser's value).
- **Child rows** that will be appended from the loser (exact duplicates skipped).
- **References to be repointed**, with a total count.
- **Needs manual review** — places where the loser's *name* appears in free-text
  (comment/email bodies). These are **flagged, never rewritten** (see Limitations).

You must **type the loser's name** to confirm before the merge runs.

## What gets moved

- **Hard references:** standard Link fields, Dynamic Links, child-table links, and
  Single docs (the same discovery engine as "Unlink and Delete").
- **Soft references:** attachments/Files, Comments, ToDos/assignments,
  Communications/emails, Tags, Notification Logs, Versions, and similar framework
  tables keyed by `reference_doctype` / `reference_name`.

## Merge rules

- **Survivor wins; blanks are backfilled.** The survivor keeps every value it
  already has. Only its empty fields are filled from the loser — and a real `0`
  is treated as a value, never overwritten.
- **Child rows are appended** from the loser (rows identical to an existing
  survivor row are skipped).
- **The loser is deleted** once its references are clear.

## Safety

- Mandatory preview + typed confirmation.
- References on **submitted** documents (e.g. a posted invoice) are repointed
  low-level. The survivor and loser **themselves must not be submitted** — the
  tool refuses a submitted document on either side.
- Every merge is recorded in an append-only **Document Merge Log** (irreversible
  operation → durable audit trail).
- Merges touching more than **2,000 references** run as a background job and
  notify you on completion.

## Limitations

- **Free text is not rewritten.** If the loser's name was typed into a comment or
  email body, that text is flagged for manual review but left as-is (a blind
  find-and-replace would risk corrupting unrelated text).
- **Single doctypes** can't be merged (there is only ever one).
- The pre-existing, Project-specific "Merge Project" button is a separate, older
  tool and is unaffected by this feature.
