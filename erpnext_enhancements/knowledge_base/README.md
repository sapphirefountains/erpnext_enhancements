# Knowledge Base

The company knowledge base: short, approved articles that people and every AI tool Sapphire uses
read from the same place, so that an answer from Claude and an answer from Triton cite the same
approved text. The goal is continuity: Parker, James and the crews keep the company running without
Nik.

Programme: [WI-080](../../work-items/WI-080-company-knowledge-base.md).
Decision record: [ADR 0017](../../decisions/adr/0017-company-knowledge-lives-in-a-native-module.md).

**Status: PRs 1 and 2 of the v1 build (v1.538.0, v1.539.0).** PR 1: the module, the two doctypes, the
two roles, the locked permissions and the AI-gate denylist. PR 2: the approval rules and the content
rules, applied by the Version controller, and private Files. There is **no workspace, no action
button and no AI tool yet**, and **nothing can publish**: a version is submitted only when the
publish action sets `flags.kb_publish`, and that action arrives in PR 3. Until then the Article
table stays empty on purpose; a KB Author can already save drafts in the Desk, and PR 2's content
rules apply to them.

## The shape of it

**Published text and drafts live in two different doctypes.** That split is the whole design.

- **Knowledge Article** is the approved, published text of one KB number, and nothing else. It is
  named by its number (`KB-0612`: department block `06`, article `12`). Every staff user reads it,
  and every field is read-only.
- **Knowledge Article Version** is submittable and holds the drafts and the permanent history.
  Publishing *is* submitting: a submitted version is the record of what a second person approved,
  and the Article is a copy of it. Only KB Authors and KB Approvers can open this doctype.

**A draft cannot leak through a reader path because it is not in the reader's doctype.** Hiding
draft fields at a higher permlevel was rejected: FAC's `get_document` checks doctype permission, not
permlevel. (The Version does use permlevel 1, for a different job: see "Server-set fields" below.)

**Nothing is ever deleted, canceled, amended or renamed.** An article is retired, never deleted;
an abandoned draft is Discarded, not deleted.

## Who can see what

| | Knowledge Article | Knowledge Article Version |
|---|---|---|
| Every staff user (`Desk User`) | read, report, print | nothing |
| System Manager | read | **nothing** |
| KB Author | read (as a staff user) | read, create, write, print, report; **read only** at permlevel 1 |
| KB Approver | read (as a staff user) | read, create, write, print, report; **read only** at permlevel 1 |
| Guest, `All` (portal users) | nothing | nothing |

No row anywhere carries share, submit, cancel, amend, delete, export, import or email.
`Desk User` is v16's automatic role for System Users (frappe `origin/version-16` `permissions.py:35`,
`:559-562`), so customer contacts never hold it. `tests/test_knowledge_base_schema.py` compares this
whole matrix, so an added row fails the build as surely as a changed one.

KB Author and KB Approver hold the same DocPerm. What makes an approver different is enforced in
code (`workflow.approval_problems`, applied by the Version controller since PR 2; the button that
asks arrives in PR 3): the approver holds KB Approver, is not the owner, the submitter, a
contributor or the person who asked an AI to draft it, approves from a signed-in browser and not
through an AI gate card, and approves the version exactly as they opened it. See "The rules" below.

### Server-set fields

Those rules read stored fields (`contributors`, `ai_requested_by`, `submitted_by`, `review_state`
and the rest), so the people they constrain must not be able to write them. `read_only` does not do
that: it is a Desk hint, and v16 never checks it on the server. A KB Approver holding write at level
0 could clear `contributors` on a draft with `frappe.client.set_value` and then approve their own
edit.

So **every field on the Version except the eight content fields and `amended_from` is at permlevel
1**, and both KB roles hold **read only** there. On every save and insert, v16's
`validate_higher_perm_levels` (frappe `origin/version-16` `model/document.py:1021-1044`) puts back
the stored value, or the default on a new document, for any level the user cannot write. The schema
test pins both halves: the field levels, and that no row has write above level 0.

**The rule this puts on later PRs:** code that writes a server-set field must run with
`ignore_permissions` (or list the field in `flags.ignore_permlevel_for_fields`). Otherwise the value
is silently put back, and a supersede or a submit-for-review looks like it worked. A value computed
in `validate` or `before_save` survives a user's own save, because the reset runs before those hooks
(`:592`, `:594`); one set in `before_insert` does not (`:480`, then the reset at `:483`).

## Every leak path, and what closes it

| Path | Closed by |
|---|---|
| The web, and guests | `has_web_view 0`, `allow_guest_to_view 0`, no Guest or `All` row. `GET /api/resource/Knowledge Article` with no cookie is a 403 |
| Global search and the AwesomeBar | `show_in_global_search 0`, and no field has `in_global_search`. Search arrives in PR 5, as the caller |
| A reader opening a draft | Drafts are in the Version doctype, which has no reader row |
| Sharing, and assigning a reviewer | `share 0` on every row. v16 `assign_to.add` *shares* the document with an assignee who cannot read it (`desk/form/assign_to.py:106-118`); with no share right that call is refused instead |
| Core `Version` rows (the change log) | `track_changes 0` on the Version doctype, because core `Version` is readable by System Manager, which includes the `triton@` service identity. The Article keeps `track_changes 1`: its rows hold only published text, and they give a tamper trail |
| Pasted images and attachments | `make_attachments_public 0`, so pasted images are private already. `files.force_private` (PR 2) makes every other File attached to either doctype private, **bytes included**: an upload with Private unticked, a public library pick, a REST insert, and an owner unticking Private later. See "Files" below |
| Hidden text in the body | `content.strip_presentation` (PR 2) removes colour, background, size and font on every content save, so nothing a reviewer cannot see stays in the HTML a model reads |
| A secret pasted into an article | `content.secret_findings` (PR 2) refuses the save, naming the field, line and kind and never the value; scanned again at approval |
| Import and export | `allow_import 0`, and no import or export right |
| A Custom DocPerm, Property Setter or Custom Field widening a doctype | None exist, and the schema test fails if a fixture adds one |
| The generic AI tools, raw SQL included | The AI gate refuses `Knowledge Article Version` on every path: a `doctype` argument on any tool, `fetch`'s id, `run_python_code`'s `data_query`, and the text of `run_database_query` and `run_python_code` (`assistant_tools/_gate.py`, `DENYLIST_DOCTYPES`). The text is searched with SQL comments stripped **and** without, because a `#` in a string literal, `1--1` and a `/*! */` comment are not comments to MariaDB. Raw SQL never consults DocPerm, so this is the only thing between a System Manager and the drafts. `tabKnowledge Article` is deliberately **not** refused |
| An AI write skipping its confirmation | Both doctypes are in the gate's `NEVER_EXEMPT`, so no settings row can exempt them, and a card that targets either never starts ticked in the batch dialog, with the reason "changes the company knowledge base" (`_gate.KNOWLEDGE_BASE_DOCTYPES`) |

The one thing none of this stops is a System Manager writing past the ORM with `frappe.db.set_value`,
raw SQL, or a batch-approved `run_python_code` card. The Knowledge Base Integrity report (PR 4)
detects it, and excluding `run_python_code` cards from batch approval is the real control, a
follow-up to ADR 0014.

## The flags the code sets

The controllers refuse every write the Knowledge Base's own code has not announced. Nobody holds
write on the Article or submit on the Version, so these refusals are reached only by server code
running with `ignore_permissions`; the point is that such code has to opt in by name.

| Flag | Set by | Lets through |
|---|---|---|
| `doc.flags.kb_action` | The publish, retire and still-accurate actions (PR 3) | An insert or save of a Knowledge Article; `review_state` changing on a submitted version (Superseded) |
| `doc.flags.kb_publish` | `approve_and_publish` only (PR 3) | Submitting a Knowledge Article Version, **as far as the approval rules**, which then run in the same two hooks |
| `doc.flags.kb_opened_modified` | `approve_and_publish` only (PR 3), from the page | Nothing by itself: it is the `modified` value of the copy the approver had open, and the approval rules refuse unless it equals the stored one. Missing means refused |
| `file.flags.kb_action` | KB code deleting a File, via `frappe.delete_doc("File", name, flags={"kb_action": True})` | Deleting a File attached to a Knowledge Article. **Nothing in v1 sets it**: publishing moves Files and retiring keeps them |

**Each refusal sits in two hooks**, because Frappe v16 lets a caller skip the obvious one.
`flags.ignore_validate` skips `validate`, `before_submit`, `before_cancel` and
`before_update_after_submit` (`model/document.py:1407-1408`) but never `on_update`, `on_submit`,
`on_cancel` or `on_update_after_submit` (`:1454-1462`). `delete_doc(ignore_on_trash=True)` skips
`on_trash` (`model/delete_doc.py:175-176`) but never `after_delete` (`:195-196`). The second hook
runs inside the same transaction, so raising there rolls the write back.

## The rules (PR 2)

The rules are plain functions in `workflow.py` and `content.py`, standard library only, so the
bench-free CI tier tests every branch (`tests/test_knowledge_base_rules.py`). The Version controller
applies them (`tests/test_knowledge_base_hooks.py`). Every Select value and doctype, role and field
name comes from `constants.py`.

**Approval** (`workflow.approval_problems`, in `before_submit` and again in `on_submit`). Every
broken rule is reported, in one sentence that names it:

- the approver holds **KB Approver**;
- the request comes from **a person signed in in a browser**: `signed_in_browser`, imported from
  `marketing/publish/workflow.py` so both modules share one definition. A token, an API key, a
  background job and the console are refused;
- it is **not an AI gate action** (`frappe.flags.ai_gate_pending` / `ai_gate_bypass`). Nik confirming
  a card runs the tool in his own browser session, so the browser test alone would pass;
- the version is **In Review**;
- the approver is **not the owner, the submitter, a contributor or the AI requester**;
- the stored `modified` equals `flags.kb_opened_modified`, the copy the approver opened.

The rules read the version **as stored** (`get_doc_before_save()`, which v16 loads `FOR UPDATE`),
never the copy in memory, and the copy being submitted must have the same content. Marketing's own
`approval_problems` is not reused: it hard-codes Marketing Manager.

**Content** (`validate`, on every save):

- presentation is stripped from the body (`content.strip_presentation`);
- content changes only while the stored version is a **Draft** (`workflow.content_edit_problem`). No
  flag lets code past this: nothing the Knowledge Base does changes the text of a version that has
  left Draft;
- a save that changes content is scanned for secrets and refused on a finding, and records the saver
  in `contributors` (one user id per line). A save that changes no content (a state change by the
  KB's own actions) is neither scanned nor recorded, so a version whose text predates a stricter scan
  can still be sent back; approval scans it again.

A change is judged the way a reader would see it: `None` and `""` are equal, the review interval
compares as a number, and a body compares after presentation is stripped, so re-colouring a word in
review is not an edit.

**KB numbers** (`workflow.next_kb_number`): `KB-{block}{01..99}`, one more than the highest number
already in the block, never the lowest gap (a vanished number may still be cited). `{block}00` is the
block's index and is never allocated. A full block raises `BlockFullError` with a message that says
so. `kb_number_prefix` gives PR 3 the `KB-06` it binds as `"KB-06%"` in its `SELECT ... FOR UPDATE`.

**Review dates** (`workflow.review_by`): the start date plus `review_interval(months)` calendar
months, clamped to a shorter month's end (31 August + 6 = 28 February, or 29th in a leap year). An
interval that is blank, 0, negative or unreadable is `constants.DEFAULT_REVIEW_EVERY_MONTHS` (6,
POL-0001).

**Presentation** (`content.strip_presentation`): removes every `style` declaration except
`text-align` (v16's Text Editor stores alignment as a style), and the `ql-color-*`, `ql-bg-*`,
`ql-size-*` and `ql-font-*` classes. Indent (`ql-indent-N`), direction, lists and tables survive.
Only the start tags that change are rewritten; every other byte comes back as it was. An image's
float or size set as `style` by the resize handles is dropped too; its `width` attribute is kept.
Scripts, comments and unknown tags are left to v16's own `sanitize_html`, which runs on every Text
Editor save after `validate`.

**Secrets** (`content.secret_findings`): private keys, Stripe, AWS, Google (API key, OAuth client
secret and tokens, service-account key id), GitHub, Slack, SendGrid, Anthropic, OpenAI, Plaid access
tokens, JWTs, a Frappe `token key:secret`, bearer and Basic credentials, a password inside a web
address, and a password or key written out after "password:" or "API key:". `data:` URIs are removed
first: in `validate` the body still carries every pasted screenshot as base64. A finding is
`(line, kind)` and never carries the value. Ordinary KB prose ("the password is kept in 1Password",
Drive links, ERPNext document names) is pinned as not a finding.

**The content hash** (`content.content_hash`): SHA-256 over the title, summary, keywords and body,
treating as equal what nobody can see (line endings, Unicode spelling, space at the ends and runs of
spaces in single-line fields, keyword order and case, stripped presentation). Metadata such as the
version number is not in it. PR 3 computes it, and `body_md` (v16's `frappe.utils.to_markdown`), at
publish, from the stored body, **never in `validate`**.

## Files

`files.py`, registered in `hooks.py`. Both hooks run for every File on the site, so each returns
after one attribute read for a File not attached to the Knowledge Base.

- **`force_private`** (`doc_events["File"]["before_insert"]` and `["before_validate"]`). A
  `doc_events` handler runs after File's own method of the same name, and `File.before_insert` has
  already written a public upload into `public/files`, where nginx serves it to anyone with the URL.
  So on insert the hook re-saves the content through `File.save_file` as private and deletes the
  public copy **only if this insert wrote it** (`flags.new_file`, and no other File row uses the URL).
  On an update, setting `is_private` is enough: `File.validate` moves the bytes itself.
- **`file_has_permission`** (`has_permission["File"]`) refuses **delete** on a File attached to a
  Knowledge Article, unless KB code sets `flags.kb_action`. v16 protects attachments only on a
  submitted document, and an Article is never submitted, so otherwise a File's owner could delete an
  image out of approved text. It is a permission hook and not `on_trash`, because `File.on_trash`
  deletes the bytes before any `doc_events` handler runs. Every other right returns `True` exactly: a
  falsy answer denies on v16. Code running with `ignore_permissions`, and Administrator, are not
  asked, as for every doctype.

## Roles, and how a person gets one

`patches/seed_knowledge_base_roles.py` (`[post_model_sync]`) creates **KB Author** and
**KB Approver**, both `desk_access = 1`, in the shape of `seed_training_roles`. Not
`fixtures/role.json`: fixtures import in alphabetical filename order, so `custom_docperm.json` lands
before `role.json`. Model sync usually creates both roles first anyway, from the DocPerm rows
(`make_module_and_roles`), and the patch is what the module relies on.

The same patch creates a **"KB Approvers" Role Profile** that carries only the KB Approver role and
has no members. It is named in the plural like the other one-role profiles ("PO Approvers" holds
the "PO Approver" role). On this site a user who holds any Role Profile has `roles` rebuilt from the union of
their profiles on every save, so a role granted to them directly is wiped. Such a user can only get
KB Approver through a profile of its own.

Granting is a Desk step, and only a System Manager can do it:

- **A user with no Role Profile** (check the User form's Role Profiles field first; James, Nik and
  Parker had none when WI-066 checked on 2026-07-28): add the role directly on the User. **Never**
  give such a user a Role Profile to do this; it regenerates their roles from the profile and wipes
  System Manager and everything else they hold directly.
- **A user with a Role Profile**: add "KB Approvers" as an additional profile. Lisa Symanski, the
  fourth approver (approved by James on 2026-09-25), holds "Finance Team", so this is her route.
- **Revoking** is the same step in reverse. The fast rollback of the whole Knowledge Base is to
  remove KB Approver from everyone: nothing can publish, and published articles stay readable.

## File map

| Path | What it is |
|---|---|
| `constants.py` | The fixed vocabulary: article statuses, review states, the POL-0000 department blocks, and `DEFAULT_REVIEW_EVERY_MONTHS` (6: POL-0001 mandates a review every six months), the default of `review_every_months` on both doctypes. Standard library only. Every Select option on both doctypes comes from here, and the schema test asserts the JSON matches. `department_block` stores a blank first option, because v16 defaults a Select to its first option and `reqd` would otherwise never fire: a draft nobody placed would be published into block 00 |
| `doctype/knowledge_article/` | The published snapshot. Controller `KnowledgeArticle`: refuses every write without `flags.kb_action`, and every delete and rename |
| `doctype/knowledge_article_version/` | Drafts and history, submittable, `KBV-.#####`. Controller `KnowledgeArticleVersion`: refuses a submit without `flags.kb_publish` or that breaks an approval rule, and every cancel, amend, delete and rename; applies the content rules on save |
| `workflow.py` | The approval rules, content-edit and contributor rules, KB numbers and review dates (PR 2). Standard library only, plus `signed_in_browser` from Marketing |
| `content.py` | Presentation stripping, the secret scan and the content hash (PR 2). Standard library only |
| `files.py` | The two `File` hooks: private Files, bytes included, and no deleting an article's image (PR 2) |
| `module_def/knowledge_base.json` | The `Module Def`. Documentation only: `module_def` is not in v16's `IMPORTABLE_DOCTYPES`, so the module is installed by its DocTypes and `refresh_module_map` (see `tests/test_module_installability.py`) |
| [`../patches/seed_knowledge_base_roles.py`](../patches/seed_knowledge_base_roles.py) | The two roles and the one-role "KB Approvers" Role Profile. Insert-only; cannot raise |
| [`../assistant_tools/_gate.py`](../assistant_tools/_gate.py) | `DENYLIST_DOCTYPES`, `DENYLIST_REASONS` and `NEVER_EXEMPT` carry the KB entries |
| [`../tests/test_knowledge_base_schema.py`](../tests/test_knowledge_base_schema.py) | Flags, the DocPerm matrix, fields, Select options, controller refusals, the seed patch. Its own CI step |
| [`../tests/test_ai_gate_denylist.py`](../tests/test_ai_gate_denylist.py) | The Version doctype refused on every gate path; the published doctype not refused. On the AI-gate CI step |
| [`../tests/test_knowledge_base_rules.py`](../tests/test_knowledge_base_rules.py) | `workflow.py` and `content.py`, every branch, with no stub (and a fresh-interpreter check that they import no frappe). Its own CI step |
| [`../tests/test_knowledge_base_hooks.py`](../tests/test_knowledge_base_hooks.py) | `files.py` (the fast path, the byte move, the delete refusal, registration) and the Version controller's content and approval gates. Its own CI step: it stubs `frappe` |

## What arrives later

In order, one PR at a time, each verified on prod before the next merges (see WI-080):

- **PR 3**: the actions (`api/knowledge_base.py`), the one-transaction publish, image re-attach,
  review ToDos and the form buttons. What PR 2 leaves it to do:
  - set `flags.kb_publish` **and** `flags.kb_opened_modified` (the `modified` the approver's page
    sent) before `submit()`, and ask `workflow.approval_problems` itself first so the button can
    explain a refusal before anything is written;
  - allocate with `next_kb_number` over the rows its `SELECT ... FOR UPDATE` returns for
    `kb_number_prefix(block) + "%"`;
  - compute `body_md` (`frappe.utils.to_markdown`) and `content_hash` at publish from the stored
    body, and set `review_by` with `workflow.review_by`;
  - enforce one open version per article in `start_revision`;
  - never modify a version's content at publish: the controller refuses a submit whose content
    differs from the stored row.
- **PR 4**: the `Knowledge Base` workspace, the Desk tile, the Help menu item, and two reports
  (Due for Review; the Integrity report, which holds the integrity query the denylist refuses over
  MCP).
- **PR 4a** (only if the Google Docs Markdown export keeps pictures and tables): Markdown import.
- **PR 5 and 6**: in-app search and the two read-only AI tools, `search_company_knowledge` and
  `fetch_knowledge_article`.
- **PR 7**: the one-way Drive copy for Gemini and outages.

**Hold:** none of this merges before the QuickBooks and Workforce cutover is finished
(~2026-11-02).
