# `process_documentation/` — Mermaid.js process charts

The business's process maps, as version-controlled documents.

| Path | Purpose |
|---|---|
| `doctype/process_document/` | A process map: `title` (unique, and the document name), `mermaid_code`, and a read-only `diagram` HTML field |
| `doctype/process_document_step/` | Steps within a process |
| `workspace/process_documentation/` | Desk workspace |

The controller has no custom logic — it was ported from a DB-only custom DocType. The diagram
is rendered client-side by `public/js/process_document.js`, wired through `doctype_js` in
`hooks.py`.

## The content lives in the repo, not the site

Chart content is defined in [`../setup/process_documents.py`](../setup/README.md) and upserted
on **every** `bench migrate`: missing documents are created, and a document whose
`mermaid_code` has drifted from the canonical text is **overwritten**. Same philosophy as
`fixtures/` — UI edits do not survive deploys.

Two boundaries: documents created on the site under titles *not* listed in the repo are left
alone, and nothing is ever deleted.

So to change a process chart, change `setup/process_documents.py`. Editing it in the desk is
a preview, not a change.

The one exception is `update_production_procurement_step`, a patch that rewrites a single
arrow in a live map — and it deliberately **logs and backs off if the document has drifted**
rather than overwriting a human's edit, because `seed_process_maps_finance_production` is
insert-only and its literal only reaches fresh sites.

## The mermaid contract

Seeded charts must start with `graph `, contain no raw `<`, and carry no pre-escaped
entities. `tests/test_process_documents.py` guards it:

```bash
python -m unittest erpnext_enhancements.tests.test_process_documents -v
```

That test existed but ran nowhere until WI-066 wired it into CI — a reminder to check that a
new suite is actually on a CI step (see the `run-tests` skill).
