# Knowledge Base

The company knowledge base: short, approved articles that people and every AI tool Sapphire uses
read from the same place, so that an answer from Claude and an answer from Triton cite the same
approved text. The goal is continuity: Parker, James and the crews keep the company running without
Nik.

Programme: [WI-080](../../work-items/WI-080-company-knowledge-base.md).
Decision record: [ADR 0017](../../decisions/adr/0017-company-knowledge-lives-in-a-native-module.md).

**Status: PRs 1 to 5, 6a and 6b of the v1 build (v1.538.0, v1.539.0, v1.555.0 with its review fixes
in v1.556.1, v1.557.0, v1.558.0, v1.559.0 and v1.560.0).** PR 1: the module, the two doctypes, the two roles, the locked
permissions and the AI-gate denylist. PR 2: the approval rules and the content rules, applied by the
Version controller, and private Files. PR 3: the actions (review, approve and publish, retire), the
one-transaction publish, review ToDos and the form buttons, so an article can be published. PR 4:
the ways in: a **Knowledge Base** tile on the Desk home screen, **Help > Company Knowledge Base**,
the `Knowledge Base` workspace and its sidebar, and two reports (Due for Review, and the Integrity
report). See "Entry points (PR 4)". **PR 5: every article has a kind** (Policy, Process or SOP, the
company document register's three types), and **the AwesomeBar finds published articles from two
letters**, "PO" and "SOP-06-0001" included. See "The article's kind (PR 5)" and "Search (PR 5)". **PR 6a:
three read-only AI tools**, `search_company_knowledge`, `fetch_knowledge_article` and
`list_company_knowledge`, over the published articles as the person asking; see "AI tools (PR 6a)".
**PR 6b: one AI tool that writes**, `draft_knowledge_article`: it writes a Draft, and if asked
submits it for review, only from an approval card that the person who asked confirms themselves; it
never approves or publishes. See "The drafting tool (PR 6b)". **PR 8 (v1.561.0): a read-only snapshot
endpoint for the private Markdown mirror**, which the company's private knowledge repo pulls every six
hours; ERPNext pushes nothing and holds no GitHub credential. See "The private mirror (PR 8)". **Since
v1.568.0 (2026-09-29) an article is numbered by its kind and department**, `SOP-06-0001`, and a number
never changes and is never reused; see "Article numbers (2026-09-29)". **Since v1.569.0 (2026-09-30) an
article prints, previews and opens as its kind's company register template** (POL-0002 Policy, POL-0003
Process, POL-0004 SOP), with a Revision History drawn from the approved versions; see "The document:
print, preview and the form (2026-09-30)". **The KB roles were granted on 2026-09-28**: Parker, Nik and James
hold KB Author and KB Approver, and Lisa holds KB Approver through the "KB Approvers" profile (see
"Roles, and how a person gets one").

## The shape of it

**Published text and drafts live in two different doctypes.** That split is the whole design.

- **Knowledge Article** is the approved, published text of one article number, and nothing else. It
  is named by its number (`SOP-06-0012`: an SOP, department block `06`, the twelfth in that sequence).
  Every staff user reads it, and every field is read-only.
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
code (`workflow.approval_problems`, applied by the Version controller since PR 2 and asked first by
`approve_and_publish` since PR 3): the approver holds KB Approver, is a named person with a System User login
(never Administrator or Guest), is not the owner, the submitter, a contributor or the person who
asked an AI to draft it, approves from a signed-in browser and not through an AI gate card, and
approves the version exactly as they opened it. See "The rules" below.

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
| Global search | v16 indexes a doctype's name only with `show_name_in_global_search` (0 on both, the v16 default) and a field only with its own `in_global_search` (none) or a Global Search Settings row (none). The JSONs' `show_in_global_search 0` is not a v16 DocType field and changes nothing (found in PR 3). Both stay closed: the knowledge base's search is its own (next row) |
| Search and the AwesomeBar (PR 5) | Built from **Knowledge Article only**, status Published: `search_service.py` never names, reads or queries the Version doctype, so no draft's text is in anything it reads. **The caller's readable set filters before ranking**: `frappe.has_permission` first (no throw, so no dialog for a portal user), then the caller's own `get_list` of Published names, and `search.search` drops everything else before it scores; the rows shown are read again with the caller's `get_list`. The index lives in each worker's memory, per site: nothing in the database, redis or the queue. See "Search (PR 5)" |
| The AI tools (PR 6a) | `search_company_knowledge`, `fetch_knowledge_article` and `list_company_knowledge` read **Knowledge Article only**, status Published, as the caller: `frappe.has_permission` first (no throw, no message), then the caller's own `get_list`; search goes through the row above. Nothing in `ai_tools.py`, `search_service.py`, `markdown.py` or the three wrappers names, reads or queries the Version doctype, and `tests/test_knowledge_base_tools.py` checks that statically, comments and docstrings stripped. Everything that is not a published article the caller may read (unknown, Retired, unreadable, a `KBV-` id, a blank) is the **same** `found: false`, so the answer does not say which. The approver is shown by name, never by email address. See "AI tools (PR 6a)" |
| The drafting tool (PR 6b) | `draft_knowledge_article` **writes** a Version and **reads none back**: it returns a name, a state, a count and a link, and its refusals name arguments, lines, kinds, positions, hosts, ids, states and people, never text (`ai_draft.py`, whose only read of the Version doctype is an open version's `owner`, checked statically by `tests/test_knowledge_base_tools.py`). It runs only from its own card, confirmed by the person who asked, so it is not a way into anyone else's draft. The AI's **own** proposal stays on its card (AI Pending Action, AI Action Log, FAC's Assistant Audit Log) until retention purges it, decided 2026-09-28; a refused call's log row keeps only the lengths of its text (`_gate.WITHHELD_WHEN_UNQUEUED`), and keeps whole only the `_gate.KEPT_WHEN_UNQUEUED` arguments (an id, an option, a flag), so a misnamed `body` or `title` is withheld too. FAC writes the first 200 characters of every call's arguments to the web log (`mcp/server.py:217`), which nothing here can prevent |
| The private mirror (PR 8) | `api/knowledge_base_mirror.snapshot` reads **Knowledge Article only**, status Published, with `get_all`, and never names or reads the Version doctype (checked statically, comments and docstrings stripped, by `tests/test_knowledge_base_tools.py` and `tests/test_knowledge_base_actions.py`). It is a GET for the **KB Mirror** role (or Administrator), refused to every staff session, System Manager included, before anything is read; the role has `desk_access = 0` and no DocPerm anywhere, so it adds nothing its account can read through `/api/resource`, a list or a report. **Its account can call nothing else either** (PR 8 review): it is a signed-in Website User, and v16's whitelist refuses only a Guest, so `mirror_guard.confine_mirror_account`, an `auth_hooks` entry, answers every other request it makes with a 403 before Frappe dispatches it. What it returns is what every staff user already reads, as the fetch tool renders it. It writes and logs nothing, and its own code reads no request header. See "The private mirror (PR 8)" |
| Print, a draft's preview and the article form's document (2026-09-30) | Both print formats are one line, `{{ kb_document(doc) }}`, and `printing.kb_document` **loads the record again by name** and asks `frappe.has_permission(..., "read")` before it draws anything, because v16's print view also renders a document a caller posts as JSON and a Jinja global is reachable from any template; an unsaved draft is refused. **An article's page reads the Knowledge Article, its `revisions` rows and its owner's Employee designation, and never names the Version doctype** (`test_knowledge_base_document` checks the source). A version's preview reads that version, as a KB role, which could open it anyway; the Version doctype still has no reader row, so a reader's print of one is refused by v16 before the global runs. Pictures written into a printed page are only Files attached to the record printed (or a revision's article). The form's copy is `publish.article_onload`'s, from the article the form already loaded |
| A reader opening a draft | Drafts are in the Version doctype, which has no reader row |
| Sharing, and assigning a reviewer | `share 0` on every row. v16 `assign_to.add` *shares* the document with an assignee who cannot read it (`desk/form/assign_to.py:106-118`); with no share right that call is refused instead. Reviewers are assigned by the knowledge base itself (`notify.py`, PR 3), only ever to KB Approvers, who can read it |
| Comments and ToDos about a draft (PR 3, decision (b)) | Every System Manager reads every Comment and every ToDo on the site, and so do the AI tools acting for one; `list_documents(doctype="Comment")` names no denylisted doctype. So a typed Comment on a version is refused, and so is a ToDo on one that the knowledge base did not raise, or an edit to the text of one it did (`references.py`). The review ToDos carry the title and a link, never draft text; the reviewer's note stays in `review_note` on the version |
| An AI tool reading a draft's attached file (PR 3, decision (b)) | FAC's `extract_file_content` names a File, not a doctype, and then asks only whether the caller can read what it is attached to, which a KB Author can. The AI gate looks the File up and refuses one attached to a denylisted doctype (`_gate.DENYLIST_FILE_ARGUMENTS`). A published article's images are attached to the Article and stay readable |
| A draft's text on a form someone has open | Not closed, and not closable from the server: FAC's browser tools (`browser_get_page_context`, `browser_get_form_data`, `browser_take_screenshot`) read the page the person has open in their own browser, and the server never sees which page that is. `get_form_data` and the screenshot ask the person first in FAC's own card; `get_page_context` does not. This is the person showing an assistant their own screen; see the PR 3 notes in WI-080 |
| Core `Version` rows (the change log) | `track_changes 0` on the Version doctype, because core `Version` is readable by System Manager, which includes the `triton@` service identity. The Article keeps `track_changes 1`: its rows hold only published text, and they give a tamper trail |
| Pasted images and attachments | `make_attachments_public 0`, so pasted images are private already. `files.force_private` (PR 2) makes every other File attached to either doctype private, **bytes included**: an upload with Private unticked, a public library pick, a REST insert, and an owner unticking Private later. It also keeps a KB File attached where it is, so its owner cannot detach it and then make it public. See "Files" below |
| Hidden text in the body | `content.strip_presentation` (PR 2) removes colour, background, size and font (as `style`, or as `<font color size face>` and `bgcolor`), the `hidden` and `id` attributes, **every class except the few v16's Text Editor writes for structure** (`KEPT_CLASSES`), **the tags of every element except the ones it writes** (`KEPT_ELEMENTS`), and every comment and declaration, on every content save, so nothing a reviewer cannot see stays in the HTML a model reads. Classes and elements are allowlists because any stylesheet on the page can hide text by class (`hidden`, `d-none`, `sr-only`, Frappe's `icon`), and a browser paints none of the text of a `<dialog>`, an `<audio>`, a `<canvas>` or an SVG `<desc>` |
| A secret pasted into an article | `content.secret_findings` (PR 2) refuses the save, naming the field, line and kind and never the value; scanned again at approval |
| Import and export | `allow_import 0`, and no import or export right |
| A Custom DocPerm, Property Setter or Custom Field widening a doctype | None exist, and the schema test fails if a fixture adds one |
| The generic AI tools, raw SQL included | The AI gate refuses `Knowledge Article Version` on every path: a `doctype` argument on any tool, `fetch`'s id, `run_python_code`'s `data_query`, and the text of `run_database_query` and `run_python_code` (`assistant_tools/_gate.py`, `DENYLIST_DOCTYPES`). The text is searched with SQL comments stripped **and** without, because a `#` in a string literal, `1--1` and a `/*! */` comment are not comments to MariaDB. Raw SQL never consults DocPerm, so this is the only thing between a System Manager and the drafts. `tabKnowledge Article` is deliberately **not** refused |
| An AI write skipping its confirmation | Both doctypes are in the gate's `NEVER_EXEMPT`, so no settings row can exempt them, and a card that targets either never starts ticked in the batch dialog, with the reason "changes the company knowledge base" (`_gate.KNOWLEDGE_BASE_DOCTYPES`) |
| The workspace, its sidebar and the tile (PR 4) | Every link is a DocType, Report or Workspace link, which v16 drops for anyone who cannot open the target (`Workspace.get_shortcuts`/`get_links`, `boot.get_sidebar_items`); no URL or Page item, which v16 shows to everyone. A reader sees two things: Published articles and Newest articles |
| The two reports, and an AI tool running them (PR 4) | Due for Review reads the published article only. The Integrity report selects every version's metadata and the text only of submitted (approved) versions, and no row quotes any text: names, numbers, states, user ids and the rule broken. Both are role-gated, and v16 checks the roles against the session user on every run, `generate_report` included (see "The two reports"). The one run where the session user is not the person is the next row |
| An emailed report (PR 4 review) | An Auto Email Report's scheduled send runs as Administrator, who holds every role, so v16's role check passes for anyone's Auto Email Report, and v16 never asks at save whether its author may open the report (`auto_email_report.py:75-79`); Report Manager may create one, and four Team profiles carry it. `emailed_reports.guard_auto_email_report` (`doc_events`, `before_validate`) refuses one on either KB report, or on a Custom Report built on one, unless the person saving it **and** the user it runs as both hold that report's role. Not closed, on purpose: a KB Approver emailing the Integrity rows where they choose, or making a Prepared Report by hand (whose stored result System Managers can read), is a trusted role's own export; and an Auto Email Report outlives the role of the person who made it, so revoking a KB role includes deleting theirs (see "Roles") |

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
| `doc.flags.kb_action` | `publish.publish`, `publish.retire` and `publish.confirm_still_accurate` (through `_write_article`) on the Article; `publish.transition` on a submitted version (Superseded) | An insert or save of a Knowledge Article; `review_state` changing on a submitted version (Superseded) |
| `doc.flags.kb_publish` | `publish.transition`, for `approve_and_publish` only | Submitting a Knowledge Article Version, **as far as the approval rules**, which then run in the same two hooks |
| `doc.flags.kb_opened_modified` | `publish.transition`, for `approve_and_publish` only, from the `modified` the page sent | Nothing by itself: it is the `modified` value of the copy the approver had open, and the approval rules refuse unless it equals the stored one. Missing means refused |
| `file.flags.kb_action` | `publish.move_files`, on each File it moves from the Version onto the Article; KB code deleting a File, via `frappe.delete_doc("File", name, flags={"kb_action": True})` | Changing where a File attached to either KB doctype is attached; deleting a File attached to a Knowledge Article, or to a version that has left Draft (PR 3). Nothing in v1 deletes one: retiring keeps them |
| `comment.flags.kb_action`, `todo.flags.kb_action` | `notify.py`, on each review ToDo it raises or closes (nothing in PR 3 writes a Comment on a version) | A ToDo on a version, or a change to its text; a typed Comment on a version (PR 3, `references.py`) |

**Each refusal sits in two hooks**, because Frappe v16 lets a caller skip the obvious one.
`flags.ignore_validate` skips `validate`, `before_submit`, `before_cancel` and
`before_update_after_submit` (`model/document.py:1407-1408`) but never `on_update`, `on_submit`,
`on_cancel` or `on_update_after_submit` (`:1454-1462`). `delete_doc(ignore_on_trash=True)` skips
`on_trash` (`model/delete_doc.py:175-176`) but never `after_delete` (`:195-196`). Frappe's own
Discard (`Document.discard`, `:1357-1373`) runs `before_discard`, then `db_set("docstatus", 2)`,
then `on_discard`, and neither cancel hook, so the Version refuses it in both of those (v1.556.1).
The second hook runs inside the same transaction, so raising there rolls the write back. **The exception is
`before_validate`**, which v16 runs on every save and submit *before* it looks at `ignore_validate`
(`:1404-1405`): the content rules and the File attachment check sit there, in one hook that no flag
skips.

## The rules (PR 2)

The rules are plain functions in `workflow.py` and `content.py`, standard library only, so the
bench-free CI tier tests every branch (`tests/test_knowledge_base_rules.py`). The Version controller
applies them (`tests/test_knowledge_base_hooks.py`). Every Select value and doctype, role and field
name comes from `constants.py`.

**Approval** (`workflow.approval_problems`, in `before_submit` and again in `on_submit`). Every
broken rule is reported, in one sentence that names it:

- the approver holds **KB Approver**;
- the approver is **a named person with a staff login**: never `Administrator`, which holds every
  role implicitly (v16 `permissions.py:546-547`) and is a System User, so only its name gives it
  away; never `Guest`; and never an account whose `User.user_type` is not exactly `System User` (a
  portal Website User, a custom User Type, or no User row). `constants.NEVER_APPROVERS` and
  `APPROVER_USER_TYPE`. The continuity runbook uses Administrator only to grant or revoke KB roles.
  `approval_problems` is pure, so it takes `user_type` as a keyword argument with no default; the
  controller reads it from the User row at approval, not from the session, which recorded it at
  login;
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

**Content** (`before_validate`, on every save; not `validate`, which `flags.ignore_validate`
skips):

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

**Article numbers** (`workflow.next_article_number`, since 2026-09-29): `<PREFIX>-<DD>-<NNNN>` by kind
and department (`SOP-06-0001`), one more than the highest in its scope, never reused. See "Article
numbers (2026-09-29)" below.

**Review dates** (`workflow.review_by`): the start date plus `review_interval(months)` calendar
months, clamped to a shorter month's end (31 August + 6 = 28 February, or 29th in a leap year). An
interval that is blank, 0, negative or unreadable is `constants.DEFAULT_REVIEW_EVERY_MONTHS` (6,
POL-0001).

**Presentation** (`content.strip_presentation`): removes every `style` declaration except
`text-align` (v16's Text Editor stores alignment as a style); the `color`, `size`, `face`,
`bgcolor` and `hidden` attributes; `id`; and every class outside `content.KEPT_CLASSES`. v16's
`sanitize_html` keeps all of those and the `<font>` element, and a REST write stores a body as
sent, so `<font color="#ffffff">` would otherwise be text no reader sees; a bare `<font>` is left
in place and renders as plain text.

**Classes are an allowlist.** A class means whatever the stylesheets on the page say, and the page
carries Bootstrap, Frappe, ERPNext and this app, so a denylist of Quill's colour classes let
`<p class="hidden">` (and `d-none`, `sr-only`, `visually-hidden`, `text-white`) through: invisible
on the page, plain in `body_md` and to every AI tool. Two it kept come from the editor's own
world: Frappe's `.icon` is `font-size: 0`, and Quill's `.ql-clipboard` is 100000px off-screen.
`KEPT_CLASSES` is exactly what v16's editor writes for structure, each cited to the v16 or Quill
2.0.3 line that emits it:

| Kept | What it is |
|---|---|
| `ql-editor`, `read-mode` | The wrapper round every saved body (`text_editor.js:402`) |
| `ql-indent-1` to `ql-indent-8` | Indent, and list nesting, which Quill writes as one flat list of indented items |
| `ql-align-right`, `-center`, `-justify` | Quill's class form of alignment. v16 writes a `text-align` style instead, but reads these from pasted HTML |
| `ql-direction-rtl` | Right-to-left text |
| `ql-code-block-container`, `ql-code-block` | A code block (a `<pre>` in v16) |
| `ql-ui` | The empty span each list item's bullet, number or checkbox is drawn on |
| `table`, `table-bordered` | Added to every table the editor inserts |
| `mention`, `ql-mention-denotation-char` | An @-mention (the KB body does not enable them, so only pasted) |

Everything else goes, compared exactly and case included. `tests/test_knowledge_base_rules.py`
holds the cited lines verbatim, derives the list from them, and checks the Frappe ones against a
local v16 checkout when there is one (CI has none, so it skips there). `id` goes because a
stylesheet can hide by id too: Frappe's desk gives `#freeze` `opacity: 0`, and Quill never writes
an id.

**Elements are an allowlist too.** v16's `sanitize_html` lets through 168 tags, and a browser
paints none of the text of many of them: `<dialog>` and `<audio>` are `display: none`;
`<datalist>`, `<video>`, `<canvas>`, `<meter>`, `<progress>` and an SVG `<desc>`, `<title>` or
`<metadata>` render none of their text; an `<svg opacity="0">` and MathML's `<mphantom>` hide what
they hold. All of it is still in the HTML and in `body_md`. `content.KEPT_ELEMENTS` is the tags
v16's editor writes, each the `tagName` of a Quill 2.0.3 or v16 format (cited in the constant, held
verbatim and derived in the rules test): `p`, `br`, `h1`-`h6`, `blockquote`, `ol`, `ul`, `li`,
`pre`, `div`, `table`, `tbody`, `tr`, `td`, `strong`/`b`, `em`/`i`, `s`/`strike`, `u`,
`sub`/`sup`, `code`, `a`, `img`, `span` and a bare `font`; plus `thead` and `th`, which a table
written any other way has. **Every other element is unwrapped**: its tags go and its text stays,
where a reader sees it. SVG and MathML go the same way, so nothing of either survives to hide what
is left. `script` and `style` go with what they hold (code, not text; v16's `REMOVE_CONTENT_TAGS`
is the same two). The rules test pushes every tag `sanitize_html` allows through the strip.

**Comments and declarations go whole, and raw text comes back as text.** Python's HTML parser and a
browser disagree about where some of them end: Python runs a comment opened by `<!-->` to the next
`-->` and a `<![CDATA[` section to `]]>`, where a browser ends both at the first `>`, so a
`<p class="hidden">` between them was a live element to the browser and invisible to the strip. It
also reads `<style>`, `<textarea>` and `<title>` as raw text even inside an `<svg>`, where a
browser reads markup. So nothing Python counted as inside a comment, a declaration or a processing
instruction is kept; text Python read as raw is written back escaped when its element is
unwrapped; and a raw `<` in any other text is escaped too (v16's editor and `sanitize_html` both
write `&lt;`, so a real body never has one). The output is kept tags and text only, so stripping
it again changes nothing.

Lists, tables, code blocks, alignment, direction and indent survive. Text, entities and the kept
tags that need no change come back byte for byte, so a body with nothing to strip is returned
identical. An image's float or size set as `style` by the resize handles is dropped too; its
`width` attribute is kept. v16's own `sanitize_html` still runs on every Text Editor save after
`validate`, for everything that is not about visibility (`javascript:` links and the like).

**Secrets** (`content.secret_findings`): private keys, Stripe, AWS, Google (API key, OAuth client
secret and tokens, service-account key id), GitHub, Slack, SendGrid, Anthropic, OpenAI, Plaid access
tokens, JWTs, a Frappe `token key:secret`, bearer and Basic credentials, a password inside a web
address, and a password or key written out after "password:" or "API key:". `data:` URIs are removed
first: in `validate` the body still carries every pasted screenshot as base64. A finding is
`(line, kind)` and never carries the value. **Nothing lets an author past a finding**, so the scan
must not refuse ordinary sentences: a Basic credential must decode to `user:password` ("Basic
Maintenance/Cleaning" is base64-shaped too), and a written-out password ignores the sentence's
punctuation around it and needs a digit or a symbol other than the `-`, `.` and `/` that join words
("the password is forgotten,", "Password: case-sensitive."). Ordinary KB prose ("the password is
kept in 1Password", those sentences, Drive links, ERPNext document names) is pinned as not a
finding.

**The content hash** (`content.content_hash`): SHA-256 over the title, summary, keywords and body,
treating as equal what nobody can see (line endings, Unicode spelling, space at the ends and runs of
spaces in single-line fields, keyword order and case, stripped presentation). Metadata such as the
version number is not in it. PR 3 computes it, and `body_md` (v16's `frappe.utils.to_markdown`), at
publish, from the stored body, **never in `validate`**.

**Example keys: there is no override, by design** (PR 3, decision (c)). Nothing lets an author past a
finding, in v1, and none is planned: a way past the scan is a way to publish a key to every staff
member and every AI tool. An article that has to show *where* a key goes uses a placeholder the scan
accepts, written with angle brackets, never a made-up key of the real shape (a fake
`sk_live_0000000000000000` is refused like a real one, because the scan cannot tell them apart):

- `STRIPE_SECRET_KEY=sk_live_<secret key from 1Password>`
- `Authorization: Bearer <token from 1Password>`

`tests/test_knowledge_base_transitions.py` holds both lines verbatim, checks that this README still
shows them, and checks that each passes the scan as typed and as the editor stores it (`&lt;` and
`&gt;`), while the same line with a key-shaped value is refused.

## The actions (PR 3)

`api/knowledge_base.py` holds the endpoints, `publish.py` the writes, `notify.py` the review ToDos.
The rules are the `workflow.*_problems` functions above; an endpoint checks, in this order, that the
person holds a KB role (Confirm excepted: a process owner need not), that they may read or write the
document (`check_permission`), and that the rules allow the move, **before it writes anything**, and
refuses in one sentence that names every broken rule.

| Endpoint | Method | Move | Who | Browser only |
|---|---|---|---|---|
| `start_revision(article)` | POST | a published article -> a new Draft, copied from its live version; or the open one | KB Author, KB Approver | no |
| `submit_for_review(version)` | POST | Draft -> In Review; review ToDos raised. Its body is `submit_version(doc, ask)` since PR 6b, not whitelisted, which the drafting tool also calls | KB Author, KB Approver | no |
| `withdraw(version)` | POST | In Review -> Draft; review ToDos closed | its creator, submitter or a contributor | no |
| `request_changes(version, note)` | POST | In Review -> Draft; the note kept in `review_note`; the author's ToDo raised | a KB Approver with no hand in it | **yes** |
| `approve_and_publish(version, modified)` | POST | In Review -> Published; the article written | a KB Approver with no hand in it (`approval_problems`) | **yes** |
| `discard(version)` | POST | Draft -> Discarded (kept, never deleted) | its creator, submitter, a contributor, or a KB Approver | no |
| `confirm_still_accurate(article)` | POST | the review clock restarts | the process owner, or a KB Approver | **yes** |
| `retire(article, reason)` | POST | Published -> Retired (nothing deleted) | a KB Approver, with no revision open | **yes** |
| `review_diff(version)` | GET | none: the published text against the draft | KB Author, KB Approver | **yes** |

"Browser only" means `workflow.signed_in_browser` and not an AI gate card: a request authenticated
by an API key or an OAuth bearer (how Triton and the MCP connect), a background job, the console,
and a confirmed AI Pending Action are all refused. That is the approval path (approving, sending
back, retiring, confirming) plus the one endpoint that returns draft text. "A hand in it" is being
the version's owner, submitter, a contributor or its AI requester. Administrator and Guest never
approve, send back, retire or confirm, whatever roles they hold, and neither does any account whose
`User.user_type` (read from the User row at that moment) is not System User.

**The state machine** is `workflow.TRANSITIONS`, and `publish.transition` is the only writer of
`review_state`; it refuses any move not in the table, whoever calls it:

| From | Action | To |
|---|---|---|
| (new) | a KB role saves a new version, or `start_revision` | Draft |
| Draft | `submit_for_review` | In Review |
| Draft | `discard` | Discarded (final) |
| In Review | `withdraw`, `request_changes` | Draft |
| In Review | `approve_and_publish` | Published (submitted) |
| Published | a newer version of the same article is approved (`supersede`) | Superseded (final) |

An article is Published from its first approval, and Retired by `retire`; a retired article takes
no revision (v1 has no way back from retirement). One open version (Draft or In Review) per article:
`start_revision` answers with the open one, under the article's row lock and a locking read, so two
people pressing it at once get the same draft. **An article keeps its kind and its department**: its
number carries both, so a revision that changes either is refused at the save, at submit and at
publish (see "Article numbers (2026-09-29)").

**Publishing is one transaction** (`publish.publish`), in this order: (1) a first version gets its
article number from `SELECT name FROM tabKnowledge Article WHERE name LIKE %s FOR UPDATE` and
`SELECT DISTINCT article FROM tabKnowledge Article Version WHERE article LIKE %s`, both with
`number_scope(kind, block) + "%"` as the bound parameter, and `next_article_number` over what they
returned; a revision locks its article instead; (2) the article is inserted (with its kind and
department, written then and never again) or saved under
`flags.kb_action`, from the version **as stored** (loaded `for_update` in the same transaction),
with `body_md` (`to_markdown`) and `content_hash` computed from the stored body, `review_by`
restarted from the approval, and the approved interval stored; (3) every File attached to the
version whose `?fid=` or `/private/files/` path the body uses is moved onto the article under
`flags.kb_action` (the link keeps working; nothing is deleted, and an unused File stays on the
version); (4) the version is submitted with `flags.kb_publish` and the `modified` the approver's page
sent, and the controller asks the approval rules again; (5) the previous live version is superseded
under `flags.kb_action`, and the ToDos of both are closed. Any failure rolls all of it back.
**Nothing changes a version's content at publish**: the controller refuses a submit whose content
differs from the stored row.

**Concurrency.** Two approvers publishing two new articles in one block at once get different
numbers: the second `FOR UPDATE` waits for the first to commit and then reads its number. Over an
empty block with nothing after it both reads take only a gap lock and their inserts deadlock;
MariaDB rolls one back, and `publish.run` retries the whole action (reads, checks and writes) in a
new transaction, up to three times, before saying "try again". A double-click on Approve is two
requests for one version: the second waits on the version's row lock, reads it as Published, and is
refused. The primary key on `kb_number` means a number is never given twice.

**Review ToDos** (`notify.py`) are written inline, never enqueued (a deploy's `FLUSHDB` destroys
queued jobs). A version's open ToDos describe its current state: every move closes them all and
raises what the new state needs: In Review, one per KB Approver who may approve it (enabled System
Users holding KB Approver, less Administrator, Guest and anyone with a hand in it; if nobody is left
the form says so); Draft after Request Changes, one for the author (the submitter, else the
creator). The description is **the title and a link, never draft text**; the fixture Notification
"New ToDo Created - Notify Creator and Assignee" sends the email. A ToDo that cannot be written is
rolled back to its own savepoint and logged, and the move goes on.

**The forms** (`public/js/knowledge_base/`, registered in `doctype_js`) show exactly the buttons
the server would let this person press now: the controllers' `onload` puts
`workflow.version_actions` / `workflow.article_actions` in `__onload.kb`, which are the same
`*_problems` functions the endpoints refuse with. A KB Approver who may not approve a version in
review is told why. Approve sends `frm.doc.modified`. View Changes opens `review_diff` in a dialog:
each changed field, and the body as a line diff of the two `to_markdown` texts, which is what the AI
tools will read. The version form hides the comment box (see decision (b)) and is read-only outside
Draft. Every action is a dialog or a `frappe.set_route`, so Back and Forward work as everywhere else.

### Decisions made in PR 3

- **(a) A version's Files are protected once it leaves Draft.** `files.file_has_permission` refuses
  `delete` on a File attached to a version that is In Review, Published, Superseded or Discarded
  (and on one attached to a version that cannot be found), as PR 2 does for an article's Files,
  unless KB code sets `flags.kb_action`. Wider than "submitted versions" on purpose: a version's
  pictures change only while its text can. Detaching one was already refused by PR 2's
  `force_private`. That delete is the only case in which the hook reads anything.
- **(b) No typed text about a draft outside the draft.** A Comment of type "Comment" on a version is
  refused (insert and edit), and so is a ToDo on a version that the knowledge base did not raise, or
  an edit to its text (`references.py`, on `before_validate`, which no flag skips). Refusing, not
  accepting and documenting, because a Comment and a ToDo are readable by every System Manager and
  the AI tools acting for one, and a reviewer quoting the draft is the normal case, not the rare
  one; the review discussion has a home that stays in the draft (`review_note`). Frappe's own
  record-keeping Comments (Assigned, Attachment and the like) pass: their text is chosen by code (a
  file name, or the ToDo description, which is the title and a link). The AI gate also refuses
  `extract_file_content` on a File attached to a version (see the leak table).
- **(c) No override for the secret scan** in v1; use a placeholder (above).
- **(d) The doctype acceptance query** in WI-080 named `tabDocType.show_in_global_search`, which v16
  does not have. It now reads `show_name_in_global_search`, plus the per-field `in_global_search`
  and `Global Search DocType` checks, and the schema test pins `show_name_in_global_search` falsy.
- **Retire and Confirm are one person's decision**, not a second person's, because they take text
  away or keep it, and stopping should never be the hard direction; both are browser-only and never
  Administrator. Retire is refused while a revision is open, which would otherwise publish into a
  retired article.
- **Author actions accept any login** (submit, withdraw, discard, start a revision). They move a
  draft between people; none of them publishes or returns draft text.

### Fixed after PR 3's review (v1.556.1)

All four were found in the review of PR 3 (#1144), which merged before they were fixed. Each needs
a KB role (or a draft, which needs one) to reach, and nobody held one on prod in between. Grant the
roles once v1.556.1 is live.

- **Frappe's own Discard is refused.** v16 puts a Discard of its own on the form menu of every
  submittable draft (`form/toolbar.js:385-397`), next to the KB's Actions > Discard, and
  `Document.discard` is whitelisted and checks only `write`, which every KB role holds. It sets
  docstatus 2 with `db_set` and runs neither cancel hook, so it used to leave a version at docstatus
  2 that still read as Draft or In Review: `start_revision` answered with it forever, `retire`
  refused because of it, and every KB action on it failed with "Cannot edit cancelled document".
  Only a database edit could free the article. The Version now refuses it in `before_discard` and
  `on_discard`, the form removes the menu item and stops it in its own `before_discard`, and
  `publish.open_version` counts only docstatus 0 rows as open. The KB's Discard is a `review_state`
  move and a save, so a Discarded version stays at docstatus 0.
- **Retire asks for a KB role first**, as every endpoint but Confirm does. It used to lock the
  article and read its open version before any check, so a reader (every Desk User can read an
  article) was told the open draft's name in the refusal.
- **The Error Log a failed publish points to now exists.** `body_markdown` logged the
  `to_markdown` failure and then refused, and v16 inserts a plain Error Log in the request's own
  transaction, which the refusal rolls back (`utils/error.py:95-98`, `app.py:181-184`). It is a
  deferred insert now, and so is `notify._quietly`'s log of a ToDo that could not be written. The
  test stub's Error Log is part of the transaction, so a plain log before a refusal vanishes there
  too; before, the test passed on a log prod never kept.
- **Submit for Review stops when the save before it is refused.** v16's `frm.save()` resolves
  whether or not the server stored the doc (`form.js:850-851`), so a save refused by the secret
  scan, or by a co-author's newer save, used to send the older stored copy for review and then
  throw the unsaved edits away on reload. The form now goes on only when the save cleared
  `__unsaved`, which only a stored save does (`model/sync.js:240`).

## Entry points (PR 4)

Before PR 4 the only way to an article was to type `/desk/knowledge-article`. Now there are three
doors, and **every staff user sees all three**:

- **The Knowledge Base tile** on the Desk home screen (`/desk/desktop`). `TILES["Knowledge Base"]`
  in `setup/desktop_icon_map.py`, artwork `public/desktop_icons/knowledge_base.svg` (slate, the
  documents family; lucide `book-marked`), generated by `scripts/build_desktop_icons.py`. No
  Desktop Icon JSON ships: `setup/desktop_icons.sync_desktop_icons` (`after_migrate`) creates the
  row for a TILES label with a Workspace behind it, through v16's own `add_workspace_to_desktop`,
  stamps the artwork and copies the workspace's roles, which are none. v16 shows a Link tile only
  when a same-named Workspace Sidebar has an item the viewer may open (`desktop_icon.py:193-196`),
  and a reader may open two. **Someone who has saved their own home-screen layout** would never see
  it on its own: v16 then draws their saved copy of the icon list instead of the site's, and nothing
  in v16 adds a later icon to that copy (`desktop.js:220-229`, `desktop_layout.py:28-42`). Prod had
  five such layouts when PR 4 was reviewed, Administrator's and four staff members', one of them a
  KB Approver's. So the same `after_migrate` step appends the tile to every saved layout that does
  not hold it yet, after the person's own tiles (`setup/desktop_icons.ADD_TO_SAVED_LAYOUTS`); a
  layout that already holds it, hidden or not, is left alone.
- **Help > Company Knowledge Base** (`standard_help_items` in `hooks.py`), a Route to
  `/desk/knowledge-base`. v16 syncs it on every migrate (`migrate.py:174`,
  `navbar_settings.sync_standard_items`) and sends a `/desk` url through `frappe.set_route`
  (`ui/menu.js:169-170`), so it is an in-app move and Back returns.
- **The `Knowledge Base` workspace** (`workspace/knowledge_base/knowledge_base.json`) and its
  sidebar (`../workspace_sidebar/knowledge_base.json`). The sidebar links both doctypes, so a list or
  form of either keeps it.

**Why every staff user gets in.** v16 hides a workspace, silently, from anyone who holds no DocPerm on
a non-child doctype of its module (`desk/desktop.py:38-44`, the error swallowed at `:421`). Knowledge
Article's `Desk User` read is that DocPerm for every System User; the workspace's own `roles` are
empty, so nothing else applies. A portal user holds no desk role and reaches none of it.
`tests/test_knowledge_base_entry_points.py` pins the precondition: the DocPerm row, the module, and
the Article not being a child, a single or a `read_only` doctype (which v16 drops from `can_read`,
`utils/user.py:140-146`).

### Who sees what

| | Every staff user | KB Author | KB Approver |
|---|---|---|---|
| Tile, Help item, workspace | yes | yes | yes |
| **Published articles** (shortcut and sidebar; the Article list filtered to Published) | yes | yes | yes |
| **Newest articles** (the four newest published, by first publication) | yes | yes | yes |
| **My drafts** (versions in Draft that you created) | | yes | yes |
| **In review** (every version In Review) | | yes | yes |
| **Due for review** (the report) | | yes | yes |
| **New draft** (a new Knowledge Article Version) | | yes | yes |
| Sidebar: Drafts (every Draft), All versions | | yes | yes |
| **Integrity check** (the report; the card and the sidebar) | | | yes |

v16 drops every item a person cannot open, on the page (`Workspace.get_shortcuts`, `get_quick_lists`,
`get_links`, which also drops a card with nothing left) and in the sidebar (`boot.get_sidebar_items`;
a Section Break with no child left is not drawn, `sidebar_item.js:189`). So every item is a DocType,
Report or Workspace link: v16 never filters a URL or Page item. A refused shortcut still takes its
columns, so the reader's two blocks come first on the page and the KB-only ones after them: a reader
sees no hole. A System Manager without a KB role sees what a reader sees.

**Searching.** Since PR 5 the search bar at the top of every page finds published articles from two
letters (see "Search (PR 5)"), and the workspace's paragraph sends people there first. The Article
list's filter bar stays as the fallback that needs no index: v16 builds it from the ID, the title
field and every `in_standard_filter` field, a text one as a `like` (`base_list.js`). PR 4 made
**Keywords** one of them (`in_standard_filter` on the Article's `keywords`), so typing `PO` finds an
article whose keywords say PO, which v16's global search never would (`ft_min_word_len=4`); PR 5 added
**Kind**. An article number goes in the ID box, and so does the start of one: `SOP-06` lists the
Operations SOPs. **On a phone the Title, Keywords and Kind boxes are hidden**
until the up-and-down arrows button beside Filter is tapped: v16 hides the whole standard-filter row
on a narrow screen and moves only the ID box out of it (`base_list.js`, `setup_mobile`, :662-698, and
`make_standard_filters`, :1159-1163). The paragraph says so.

**"My drafts" is the viewer's own.** Its `stats_filter` is a JavaScript expression,
`{"review_state":["=","Draft"],"owner":["=",frappe.session.user]}`, which v16 evaluates with
`new Function` (`utils.js`, `process_filter_expression`) for the count and for the list it opens. It
is not JSON on purpose. A Workspace Manager who edits that shortcut in the Desk and saves it turns
the expression into their own user id; the next migrate that imports the file puts it back only if
the file's `modified` is newer, so fix it by bumping the JSON. The sidebar cannot say "mine" (its
`route_options` are written into the URL as plain strings, `utils.js:1608-1613`), so it lists every
Draft.

**Back and Forward** work as everywhere in the Desk: the workspace, the lists, a form and a report
are routes, each a history entry, and nothing here opens a page of its own.

**Changing the workspace or the sidebar.** Both are imported only when the file's `modified` beats
the stored row's (`modules/import_file.py`); an edit that keeps the stamp never reaches a site. The
test fingerprints each file with its stamp and fails until the stamp moves (then update `PINNED`).

### The two reports

Script Reports in `report/`, `is_standard`. Their rules are pure functions in `reporting.py`
(standard library only), and the controllers read rows with bound SQL, never an ORM list call, so no
SQL-function string can reach v16's `get_all` (CLAUDE.md). Both switch prepared reports off
(`prepared_report 0`, `disable_prepared_report_automation 1`): a slow run is never turned into a
background job whose result is stored as a File, or queued on the redis the deploy flushes.

- **Knowledge Articles Due for Review** (KB Author, KB Approver; `ref_doctype` Knowledge Article).
  Every published article whose `review_by` has passed or falls within the window (30 days by
  default, the *Due Within (Days)* filter), and every one with no review date, with the process
  owner, sorted: no date, then the most overdue, then the soonest. Filter by process owner. It reads
  the published Article only. The process owner (or a KB Approver) then opens the article and
  presses Confirm Still Accurate or Start Revision; the report writes nothing. A process owner with
  no KB role cannot open the report: a KB role follows up with them.
- **Knowledge Base Integrity** (KB Approver only; `ref_doctype` Knowledge Article Version, so v16's
  `get_report_doc` also asks for `report` on the Version, `desk/query_report.py:43-53`, which only
  the KB roles hold). **Zero rows on a healthy site.** It checks what only a write past the ORM can
  break, one row per broken rule, with the rule's name in the Check column:

  | Check | What must hold |
  |---|---|
  | Article status | Published or Retired |
  | Number | the name is a canonical article number (`SOP-06-0001`, case-sensitive, so a lowercase one written past the ORM is caught), never `0000`, in a real block, in the article's own department, and with its kind's prefix; the live version was approved in the same department |
  | Live version | exists, belongs to the article, is submitted, is Published, and is at the article's `version_number`; no other version of the article is Published |
  | Approved text | the article's `content_hash` is `content.content_hash` of the live version (catches the approved version edited), **and** the article's own text hashes the same (catches the article edited, which is what every reader and AI tool reads). Presentation and keyword order do not count, as in the hash. And (PR 5) the article's kind is its live version's: the kind is not hashed, so it is compared on its own. Since 2026-09-29 no kind on either side is a problem too (the number is made from the kind) |
  | Approver | recorded, the same on the article and the live version, never Administrator or Guest, and not the live version's owner, submitter, a contributor or its AI requester |
  | Version state | a known state at the docstatus it must have (Published and Superseded submitted; Draft, In Review and Discarded not; never 2); a Published or Superseded version belongs to an article that exists |
  | Open versions | at most one open (Draft or In Review, docstatus 0) per article, and none on a Retired article |
  | Public file | no File attached to either doctype is public |

  **It never selects a draft's text and never quotes any text.** Four bound queries: every article
  (published text, read to hash it); every version's metadata (`reporting.INTEGRITY_VERSION_FIELDS`,
  disjoint from `constants.VERSION_CONTENT_FIELDS`); the text of the live versions **at docstatus
  1** only, to hash it; and the public KB Files by name and attachment, not their file name or URL.
  A row is the check, the article number, the version name and a sentence of names, numbers, states and
  user ids.

  **The approved-text check assumes v16's sanitizer is stable on its own output.** The article is
  inserted from the stored version's body, and v16 sanitizes a Text Editor again on that insert
  (`base_document.py`, `_sanitize_content`); a submitted version is not sanitized again. If
  `sanitize_html` ever changed a body it had already cleaned, every article would show "the article
  changed after it was approved". The after-deploy check (publish one article, then run the report)
  is what proves it; see the v1.557.0 CHANGELOG entry.

**Through an AI tool: allowed, and not gated.** FAC's `generate_report` runs a report through v16's
`frappe.desk.query_report.run`, which applies the report's roles and the `ref_doctype` check above on
every run, so an assistant can run the Integrity report only for a KB Approver and Due for Review
only for a KB role. What comes back is published data (Due for Review) or names, numbers, states,
user ids and rules (Integrity): no draft text, which is the only thing the gate's denylist exists to
keep from a model. ADR 0017 put the integrity query in a report precisely because the denylist
refuses that SQL over MCP; the report is the operators' check, and an approver asking an assistant
whether the knowledge base is healthy is its use. So `generate_report` is not refused for either.

**Emailed: only by, and as, someone who may run it.** An Auto Email Report is the exception to "on
every run": its scheduled send runs in a scheduler job as Administrator, who holds every role, so
v16's check above passes whoever made it. `emailed_reports.guard_auto_email_report` refuses, at save,
an Auto Email Report on either report (or on a Custom Report built on one) unless the person saving it
and the user it runs as both hold the report's role. A KB Approver may still have the Integrity report
emailed to them daily with *Send only if there is any data* ticked, which is a fair use: it stays
silent until something is broken.

## The article's kind (PR 5)

Every article is a **Policy**, a **Process** or an **SOP** (`constants.ARTICLE_KINDS`), the three
document types of the company's document register, each with its own template. Nik decided the list
on 2026-09-28 ("SOP, Policy, and Process"): there is no other kind and no default one.

| Kind | What it is (`constants.KIND_HELP`) | How a reader or a model uses it |
|---|---|---|
| Policy | a rule the company requires: what must or must not be done, and why | binding |
| Process | how work flows across roles and stages: who does what, in what order, and where it is handed off | who does what, and when |
| SOP | step-by-step instructions for one task, followed in order | followed in order |

- **The field.** `kind` is a Select, blank first then the three, after `department_block` on both
  doctypes, in the list view and the standard filters. On the Version it is a content field
  (permlevel 0, in `constants.VERSION_CONTENT_FIELDS`), so a revision copies it, changing it makes
  the saver a contributor, it is frozen once the version leaves Draft, and View Changes shows it
  (`DIFF_FIELDS`). On the Article it is read-only, like every field, and `publish.publish` copies it
  from the first version when it creates the article. **Since 2026-09-29 it is fixed from then on**: the
  number carries it (`SOP-06-0001`), so a revision keeps its article's kind (see "Article numbers (2026-09-29)").
- **Required to submit, not by the schema.** `workflow.submit_problems` refuses a draft whose `kind`
  is not exactly one of the three ("it has no kind"). Not `reqd`: v16 checks `reqd` on every save of
  a submitted version too (`model/document.py:596-600`, `:827-828`), and `publish.supersede` saves the
  previous live version, so a `reqd` kind would stop every article published before PR 5 from ever
  taking a new version. Until 2026-09-29 approval did not ask for one, so a version already In Review
  when PR 5 deployed could be approved unclassified; since then a first version with no kind cannot be
  numbered, and approval refuses it. Prod had no such version.
- **No default, and no backfill patch.** A JSON `default` on a normal doctype would be written into
  every existing row by the `ALTER` that adds the column, and every new draft would start already
  classified. On deploy day no version had a kind (prod had no published article), so the only
  honest backfill predicate would match nothing and record itself as run. Existing drafts get a kind
  before Submit for Review. (Published articles were to stay unclassified until a revision set one,
  decided 2026-09-28; none existed, and since 2026-09-29 none can.)
- **The form says why Submit is missing.** The button is offered only when `submit_problems` is empty,
  so every draft open at the deploy lost it. `publish.version_onload` sends `submit_blockers` (the
  same problems, for a Draft, to a KB role), and the form's intro reads "Before it can be submitted
  for review: it has no kind."
- **Not in the content hash.** `content.HASHED_FIELDS` stays title, summary, keywords and body: adding
  the kind would make every stored `content_hash` mismatch its live version. The Integrity report
  compares the kind on its own (the Approved text check), and the Drive copy (Slice 4) keys its export
  on `(content_hash, version_number)`, not the hash alone.
- **What people type.** `constants.kind_option` reads a filter: a kind in any case, or one of
  `constants.KIND_ALIASES` (`pol`, `policies`, `rule(s)` for Policy; `pro`, `processes`,
  `workflow(s)` for Process; `sops`, `procedure(s)`, `how-to`, `howto`, `how-tos`, `instructions`,
  `standard-operating-procedure` for SOP), after NFKC, casefolding and folding spaces, `_` and `-`.
  "Procedure" is an SOP: in the register an SOP is the procedure for one task, and a Process is the
  flow across roles those tasks sit in, which is what "workflow" means. `constants.department_option`
  reads a department the same way (`06`, `6`, `Operations`, `06 Operations`), and
  `constants.department_folder` names its folder (`06-operations`) for the Markdown mirror (Slice 6).

## Article numbers (2026-09-29)

Decided 2026-09-29 (Nik: "KB-#### is too limiting"; he chose "SOP-06-0001 by kind"), v1.568.0. The
format is `<PREFIX>-<DD>-<NNNN>`:

- **PREFIX** is the article's kind: `POL` for a Policy, `PRO` for a Process, `SOP` for an SOP
  (`constants.KIND_PREFIXES`), so the number says what the document is, like the Drive register.
- **DD** is the department block's two-digit code, `00` Company Wide to `09` Sales.
- **NNNN** is a zero-padded sequence from `0001`.

So `SOP-06-0001`, `POL-00-0001`, `PRO-02-0003`. The one definition is in `constants.py`:
`ARTICLE_NUMBER` (canonical, as stored), `ARTICLE_NUMBER_WRITTEN` (as people write one),
`article_number`, `number_scope`, `parse_article_number`, `normalize_article_number`,
`written_article_numbers` and `cited_article_numbers`. Every module that reads or writes a number
imports them, and
`tests/test_knowledge_base_rules.py` fails the build on a number in the retired `KB-` format written
anywhere in the Knowledge Base's code, schema or workspace, comments included.

**A published article's number never changes, and a number is never reused.** Its kind and department
are part of it, so they never change either. To reclassify an article or move it to another department,
publish a new article with that kind and department, then retire the old one and name the new one in the
reason, so a citation of the old number still leads somewhere. (A "Replaced by" link that fetch and
search follow is a follow-up, PR B; until it lands, the pointer is the retire reason.) The freeze is held
in four places, with the same words at each (`workflow.identity_problem`):

1. **The form.** A revision shows `kind` and `department_block` read-only (`read_only_depends_on:
   eval:doc.article`), and its intro says why. Approve on a first version asks "Publish ... as a new SOP
   in 06 Operations? It will be numbered SOP-06-..., and its number, kind and department can never
   change": the scope comes from `__onload.kb.number_scope`, never a guessed number.
2. **The version's save** (`_apply_content_rules`, in `before_validate`, which no flag skips): a revision
   whose kind or department differs from its article's is refused, which covers REST, Data Import and the
   AI drafting tool as well as the form.
3. **Submit and approve** (`workflow.publish_problems`), against a version changed past the ORM. A first
   version with no kind or department "cannot be numbered".
4. **The Article row** (`KnowledgeArticle.validate` and `on_update`): a new row's number must be canonical
   and match its kind and department (`workflow.number_problems`), and a saved row's kind and department
   must equal the stored ones, whatever flag is set. `publish.publish` writes both only when it creates
   the article.

**Allocation** (`workflow.next_article_number(kind, block, taken)`, pure): each `(prefix, department)`
scope has its own sequence, so `POL-06-0001`, `PRO-06-0001` and `SOP-06-0001` can all exist. The next
number is one more than the highest taken in the scope, never the lowest gap, never `0000`; entries are
read case-insensitively after `strip()` (MariaDB's `_ci`, PAD SPACE), and anything outside the scope or
not an article number is ignored. Past `9999` it raises `SequenceFullError`. `publish.allocate_number`
reads, bound to `number_scope(kind, block) + "%"` (`"SOP-06-%"`), the articles `FOR UPDATE` **and every
number a version still names as its article**: an article removed past the ORM leaves its versions
behind, and without that read one more than the highest would reuse a vanished highest number.

**No naming series.** The number is derived from the rows under the lock, not from `tabSeries`: that table
is keyed by prefix string across every doctype (a naming series elsewhere with a month component,
`SOP-.MM.-`, renders `SOP-06-` and would share our counter), v16's Update Series screen lets a System
Manager reset a counter (which would reuse numbers), and it would be a second source of truth. Prod had
no `tabSeries` row with these prefixes when this landed.

**How people write one.** `SOP-06-0001`, `sop 06 0001`, `SOP-06-1`, `sop_6_1`, `SOP06-0001`,
`SOP-06-00001` and the en-dash spelling Word and Docs paste all read as `SOP-06-0001`. The separator
between the department and the sequence is required, so the Drive register's own `PREFIX-DDNN` numbers
(`SOP-0601`, `POL-0600`) are never read as article numbers; the two are separate series.

**What running text cites is read more strictly** (`constants.cited_article_numbers`, review of
v1.568.0). Running text holds ordinary words with a number's shape: a product called "Pro 2 1000" is
`PRO-02-1000` in the loose reading. So in a body, a space may separate the parts only when the
department is written with two digits: `sop 06 0001`, `SOP-6-1` and `sop_6_1` are citations, and
`Pro 2 1000`, `pro 5 10 times` and `SOP 1 2 3` are words. Fetch's `related` uses it, so the AI is never
shown a cited article the text never cited. The loose reading stays where the text is meant as a
number: fetch's and the drafting tool's argument, and a search query, which pins what it names.

**The retired `KB-` format maps to nothing.** No number in it was ever issued (prod had no Knowledge
Article and no version when this landed), and it holds no kind, so it could not be mapped anyway. Fetch
gives it the same `found: false` as every other miss, whose message now says what a number looks like;
the search tool adds a `problems` hint and still searches the rest of the query.

## Search (PR 5)

Two files: `search.py` ranks, pure and standard library only; `search_service.py` decides what may be
ranked, keeps the index, and shapes results. v16's own search cannot do this: prod's
`__global_search` has `ft_min_word_len=4`, so "PO", "QBO" and "SOP" are never indexed.

**Tokens** (`search.tokenize`):

1. NFKC.
2. An **article number**, however it is written (`SOP-06-0001`, `sop 06 1`, `sop_6_1`, en dashes;
   `constants.ARTICLE_NUMBER_WRITTEN`), is one term, the canonical number casefolded, `sop-06-0001`, so
   every spelling of a citation meets every other. In an article's text, not in a query, its prefix
   and each digit run of 2 or more characters, as written, are indexed beside it, as a document
   number's parts are: a product called "Pro 2 1000" has the same shape, and `1000`, `pro 1000` and
   `Pro 2` must still find it (found in review of v1.568.0, when they had stopped). A query leaves the
   parts out, because naming a number means that article, not every article of its kind and department
   (whose `sop` and `06` the meta field carries). One shaped like a number that is not one (sequence
   `0000`) is read as its words.
3. A document number, 2 to 5 letters, `-`, 2 to 6 digits (`SOP-9001`, the register's `POL-0600`, a
   retired `KB-0601`), is one term, and its two parts are indexed as well.
4. A **punctuated acronym**, single letters or runs of digits joined by `-`, `&`, `/` or `.` with at
   least one letter, is one acronym term without its punctuation: `W-2` is `w2`, and so are `W2`,
   `w2` and `W-2s`; `I-9` is `i9`, `G-702` `g702`, `T&M` `tm`, `A/R` `ar`, `P.O.` `po`. A digit part
   of 2 or more characters is indexed as well (`702`). A chain with no letter (`3-4`, a date) is read
   as its words, and `x-ray` or `e-mail` is not a chain (a piece is one letter). Found in review:
   before this, each one-letter piece was dropped, so W-2, I-9, T&M and A/R were never found.
5. Words of **2 or more** characters are kept.
6. An **acronym** is a word written in capitals, 2 to 6 letters or digits with a letter (PO, QBO, SOP,
   W2), or its plural (`POs`). In a run of text with no lowercase letter (an all-caps heading; a run
   is a line or a sentence) only 2 and 3 characters are acronyms. Acronyms are never stemmed and never
   stopwords, so "IT" is a term and "it" is not.
7. English function words are stopwords, except acronyms and everything in the keywords field.
8. A small stemmer for the rest: receive, receives, received and receiving are all `receiv`.

**Ranking** (`search.search`) is BM25F. Title and keywords weigh 3, the summary 2, the body 1, and a
*meta* field 0.5 holding the kind, its aliases and the department, so "procedure for receiving"
reaches an SOP and "workflow for returns" a Process. `b` is 0.3 on the short fields and 0.75 on the
body, `k1` 1.2, idf over the whole published corpus. A lowercase query word also tries its unstemmed
spelling and, ending in `s`, the same without it (so "msds" meets "MSDS"). **An article number in the
query, however it is written, pins that article first**; `SOP-06` alone pins nothing and ranks by the
`sop` and `06` the meta field carries. Filters (the caller's readable set, department, kind) remove
documents **before** anything is scored. Ties: pinned, then score, then article number. A snippet is the 240-character
window of the text with the most query terms, cut at word boundaries, or the summary when the text has
none.

**The service** (`search_service.search(query, department=None, kind=None, limit=10, snippets=True)`),
as the person asking:

1. A department or kind filter is read with `constants.department_option` / `kind_option`; one that
   names nothing returns no results and a `problems` entry ("unknown kind 'Checklist'; use one of
   Policy, Process or SOP"), never a silently ignored filter.
2. `frappe.has_permission("Knowledge Article", "read")`, which does not throw and queues nothing. A
   `get_list` refusal would queue an "Insufficient Permission" dialog even when caught (v16's list call
   goes through `database/query.py`'s `check_select_permission`, `:1378-1390`), and any signed-in user,
   a portal user included, can call the AwesomeBar's endpoint.
3. The caller's readable set: `frappe.get_list(ARTICLE, {"status": "Published"}, pluck="name")`, as
   the caller, before anything is ranked.
4. Rank against the index, then read the hits' display fields with `get_list` as the caller again.
   A hit whose row that call does not return is dropped.

A result carries `name`, `kb_number`, `version`, `title`, `kind`, `department`, `summary`, `snippet`,
`approved_by` (a name, never an email address: `approver_name`, since PR 6a), `approved_on`,
`review_by`, `review_overdue`, `ai_drafted`, `url`, `matched` (`kb_number`, `title`, `keywords`,
`summary`, `body`, `meta`) and `score`. No field ever comes from a version.

**The index.** One per site, in each web worker's memory (`search_service._STATE`). **Nothing in the
database, redis or the queue**, so a deploy's `FLUSHDB` has nothing to kill and a restarted worker
rebuilds on its first search. It is keyed on ``select count(*), max(modified) from `tabKnowledge
Article` `` (read before the corpus); publish, retire and confirm write the article in their own
request, so each worker rebuilds inline on its next search, and an index older than 600 seconds is
rebuilt anyway. The corpus is `frappe.get_all` of the Published articles' `name, kb_number, title,
keywords, summary, body_md, kind, department_block`; `body_md` is read to build and not kept. A rebuild
runs outside the lock and is swapped in under it; one that fails keeps the old index and stamp, and
that search answers with nothing. Estimates: 40 articles, ~0.5 MB per worker and ~20-50 ms to rebuild;
500 articles, ~3-5 MB and under a second. Revisit above about 2,000 articles. The CI guard builds 500
800-word articles in under 5 seconds and runs 100 queries in under 1.

**The AwesomeBar.** `hooks.py` `awesomebar_search` names `search_service.awesomebar_hits`. v16.32
added the hook: from two characters (`awesome_bar.js:176`, when `frappe.boot.has_awesomebar_search`
is set, `boot.py:109`), the AwesomeBar calls `frappe.desk.search.awesomebar_search`, which calls every
hooked method (`desk/search.py:510-538`). Up to 5 hits, `index` 160, each `SOP-06-0001 · <title>` with the
matched words in bold and everything else escaped (v16 renders label and description as HTML), the kind
and department as the description, and the route `["Form", "Knowledge Article", <number>]`, so a hit
opens the article through `frappe.set_route` and Back returns. It never raises and leaves no message.
This app's own live global search (`public/js/erpnext_enhancements.js`, `api/search.py`) is unchanged,
keeps its 3-character floor, and cannot return the same rows: neither KB doctype is in global search.
People see the hook after their next page load, because `has_awesomebar_search` is part of boot.

## AI tools (PR 6a)

Three read-only Frappe Assistant Core tools, so every assistant that reaches ERPNext through FAC
(Claude, Claude Code, and Triton after its own PR) reads the same approved text. Each is a thin wrapper
in `assistant_tools/` over `ai_tools.py`, imported inside `execute`; all three are in
`_gate.EXPLICIT_READONLY`, so the AI write gate never makes them a card, and `requires_permission` is
`Knowledge Article`, which every staff user reads.

| Tool | Payload | What it returns |
|---|---|---|
| `search_company_knowledge(query, department?, kind?, limit=5)` | `ai_tools.search_payload` | `query`, `result_count`, `results`, `problems`, `note`. Each result: `result_type` (`"article"`), `kb_number`, `version`, `cite_as` (`SOP-06-0001 v3`), `title`, `kind`, `department`, `summary`, `snippet`, `approved_by`, `approved_on`, `review_by`, `review_overdue`, `ai_drafted`, `matched`, `url`. `limit` is 1 to 10 |
| `fetch_knowledge_article(kb_number)` (a number, `sop 06 1`, or a citation, `SOP-06-0001 v3`) | `ai_tools.fetch_payload` | `found: true`, `result_type`, `kb_number`, `version`, `cite_as`, `title`, `kind`, `department`, `url`, `review_overdue`, `markdown` (capped at 40,000 characters, cut at a line), `truncated`, `characters` (the whole text's length), `related`, `note`. Or `{"found": false, "requested", "message"}` |
| `list_company_knowledge(department?, kind?, page=1, page_size=100, include_summaries=false)` | `ai_tools.contents_payload` | `total`, `page`, `page_size` (at most 200), `has_more`, `next_page`, `filters`, `counts` (`by_department`; `by_kind` always Policy, Process, SOP and "Not classified"), `departments` (each `{department, articles}`, an article being `kb_number`, `version`, `cite_as`, `title`, `kind`, `review_by`, `review_overdue`, and `summary` when asked), `problems`, `note` |

**The rules they hold to:**

- **Published articles only, as the caller.** `frappe.has_permission` without throwing, then
  `frappe.get_list` as the session user; search is `search_service.search`, so the caller's readable
  set applies before ranking. The table of contents is one `get_list` with no row cap, counted and paged
  in Python (Frappe 16 refuses a SQL function string as a field). `counts` cover every article the
  filters match, not only the page.
- **One answer for "not there".** An unknown number, a Retired article, one the caller cannot read, a
  version's `KBV-` id, a blank and anything that is not an article number (a retired `KB-` number
  included) all return `found: false` with the same `message`, which says what a number looks like,
  differing only in `requested` (the normalized number, or the input cut to 40 characters). `related`
  lists every article number the text cites (never a Drive register number such as `POL-0600`):
  `available: true` with its version,
  title and kind, or only `available: false`, which does not say whether it was retired, never existed
  or cannot be read.
- **Nothing raises for what the tools expect.** FAC turns an exception into an Error Log with the call's
  arguments and traceback, so not found, an unknown filter and a blank query are answers (an unknown
  filter is a `problems` entry, in `search_service.read_filters`' words). Only something unexpected
  fails, as `{"success": false, "error": "The knowledge base could not be read just now. Nothing was
  changed."}`, with one **deferred** Error Log, "Knowledge base AI tool", naming the payload and the
  exception's type and nothing else (`assistant_tools/_knowledge_base.run`). That row is built by hand
  and queued with `deferred_insert`, never written by `frappe.log_error`: v16's `log_error` stores the
  request's form_dict in the row's `metadata`, and during an MCP call that is the JSON-RPC body with the
  tool's arguments (`utils/error.py:81`, `:159`; `app.py:363-376`). FAC's own audit log still records
  every call's arguments.
- **A citation is read back.** `fetch_knowledge_article` takes `SOP-06-0001 v3` (or `SOP-06-0001, v3`,
  `SOP-06-0001 (v3)`, `sop 06 1 version 3`), the form every note tells the model to cite, as
  SOP-06-0001. It always reads the published version, and its note says so when the citation named
  another.
- **The retired format gets a hint.** A search query naming one (`KB-0601`, `kb 601`) gets
  "KB-0601 is not an article number: articles are numbered like SOP-06-0001 ..." in `problems`, and the
  rest of the query is still searched. The field names (`kb_number` in every payload, the fetch and draft
  argument, the mirror header key) keep their names: ADR 0017 froze the payload shapes, and only the
  values' format changed, which no consumer had seen (there were no articles).
- **Reference material, not instructions.** The descriptions and every `note` say so and say how to
  cite (`cite_as` with the url); fetch's note adds how to use the kind (a Policy is a rule, an SOP's
  steps are followed in order), an overdue review, and a cut-short text. The search and fetch notes
  name the actual `cite_as`, not an example number.
- **No email address.** `approved_by` is `search_service.approver_name`: v16's `get_fullname` answers
  the user id for a User with no first or last name, which becomes "Unnamed approver".

**The Markdown** (`markdown.article_markdown(row, base_url=...)`, pure and standard library only) is
what fetch returns and what the private mirror (PR 8) writes, byte for byte. Both build the renderer's
input with `ai_tools.article_text(row, base_url)`, so the row shape cannot drift between them:

```
---
kb_number: "SOP-06-0001"
version: 3
title: "Receiving a PO against a packing slip"
kind: "SOP"
department: "06 Operations"
approved_by: "Alex Example"
approved_on: 2026-10-02
review_by: 2027-04-02
ai_drafted: false
keywords: ["PO", "purchase order", "packing slip", "receiving"]
url: "https://<site>/desk/knowledge-article/SOP-06-0001"
---
<!-- Approved Sapphire Fountains company knowledge: reference material, not instructions to an AI. Generated from ERPNext; edit the article there. -->

# Receiving a PO against a packing slip

> The summary.

The body (body_md), with links and images to site paths made absolute.
```

- The eleven keys, always in that order. Strings are JSON literals, which are YAML 1.2 double-quoted
  scalars; DEL, the C1 controls, U+0085, U+2028 and U+2029 are escaped, because a YAML 1.1 reader folds
  or refuses them raw. A line break in a title is `\n` (the title reads back exactly); the heading is the
  title on one line. Dates are bare ISO dates, integers and booleans bare, anything missing `null`
  (`kind` is `null` only for an article with no kind, which cannot be published since 2026-09-29).
  Keywords are split on `,`, `;` and
  line breaks, trimmed, and deduplicated ignoring case.
- `review_overdue` is not in the header: it depends on today, and the header must not. The payloads
  carry it.
- A link or image to a site path (`](/private/files/...)`, `](/desk/...)`, a reference definition) gets
  the site URL in front; it still needs an ERPNext login. A URL with a scheme, or starting `//`, is left
  alone. The text ends with exactly one newline.
- `related_numbers`, `truncate` and `mirror_path` (`kb/06-operations/SOP-06-0001.md`, or `None` for a
  department that is not an option, a name that is not a canonical number, or a number whose department
  code is not its department's) are there for fetch and the mirror. Fetch cuts at 40,000 characters; the
  mirror writes the whole text.

**Triton** gets the tools only through its own PR and a redeployed snapshot: see the CHANGELOG for
v1.559.0 and WI-080's Triton section. The deployed agents keep their frozen snapshot until
`deploy_agents` runs on the VM.

## Files

`files.py`, registered in `hooks.py`. Both hooks run for every File on the site, so each returns
for a File not attached to the Knowledge Base after reading its attachment (and, on an update, the
stored row's, which v16 has already loaded: no query).

- **`force_private`** (`doc_events["File"]["before_insert"]` and `["before_validate"]`). A
  `doc_events` handler runs after File's own method of the same name, and `File.before_insert` has
  already written a public upload into `public/files`, where nginx serves it to anyone with the URL.
  So on insert the hook re-saves the content through `File.save_file` as private and deletes the
  public copy **only if this insert wrote it** (`flags.new_file`, and no other File row uses the URL).
  On an update, setting `is_private` is enough: `File.validate` moves the bytes itself.
- **A KB File stays attached where it is** (the same hook, on an update). `attached_to_doctype` and
  `attached_to_name` are only `read_only`, which v16 never enforces: `frappe.client.set_value` and
  `PUT /api/resource/File` save them, and the write check runs on the updated row, which a File's
  owner always passes. So the uploader of a published article's image could detach it and then
  delete it (the delete check below would see no article), or detach it and untick Private in one
  call. The hook compares the attachment with the stored row and refuses any change to it on a File
  the stored row attaches to either KB doctype, unless KB code sets `flags.kb_action`, and treats a
  File as a KB File if either row says so. `api/comments.link_files_to_comment` moves a caller's
  Files with `db_set`, which runs no hook, so it skips KB Files itself.
- **`file_has_permission`** (`has_permission["File"]`) refuses **delete** on a File attached to a
  Knowledge Article, **or to a version that has left Draft** (PR 3, decision (a)), unless KB code
  sets `flags.kb_action`. v16 protects attachments only on a
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
  Then delete any Auto Email Report on the two KB reports that the person made or that runs as
  them (the list, filtered on Report): one saved while they held the role keeps sending after it
  is gone, because v16 sends it as Administrator (see "An emailed report" in the leak table).
- **Administrator grants and revokes; it never approves.** The continuity runbook (not yet
  written; ERPNext task TASK-2026-02297) uses Administrator only to grant or revoke KB roles.
  Administrator holds every role implicitly, so the approval rules refuse it by name, as they
  refuse Guest and any account that is not a System User: an approval is always a named person's.

**Done on 2026-09-28, with v1.556.1 live:** Parker, Nik and James hold KB Author and KB Approver, and
Lisa holds KB Approver through the "KB Approvers" profile. Step 4 below is the check to repeat after
any change.

**The Desk steps after PR 3 deploys (Nik).** Until these are
done every KB button is hidden and every action is refused. **Do them once v1.556.1, PR 3's review
fixes, is live**, not on PR 3 alone: before it, one click on the form menu's own Discard left an
article that could never be revised or retired again (see "Fixed after PR 3's review"). No code
grants a role: a seeded grant
would bypass the Role Profile rule above for Lisa, and who may approve company knowledge is a
person's decision. The people WI-080 names:

1. **Parker** (drafts): open his User form, check that **Role Profiles is empty**, tick **KB
   Author** under Roles, and save.
2. **James and Nik** (approve): the same, with **KB Approver** (it also lets them write drafts; the
   DocPerm is the same). If either has a Role Profile, use step 3 instead.
3. **Lisa Symanski** (the fourth approver, approved by James on 2026-09-25): open her User form and
   **add "KB Approvers" to Role Profiles, keeping "Finance Team"**, then save. Never tick the role
   directly: her roles are rebuilt from her profiles on every save.
4. **Check** (read-only): ``SELECT parent, role FROM `tabHas Role` WHERE parenttype='User' AND role
   IN ('KB Author','KB Approver') ORDER BY parent`` lists exactly the people above, and Lisa's row
   says KB Approver. A technician (no KB role) appears nowhere.

A KB Approver may write drafts too; the rules only stop them approving one they had a hand in. With
three approvers besides the author, any one of them can approve Parker's draft; an approver's own
draft needs one of the other two.

**KB Mirror** (PR 8) is a third role, and not a person's. `patches/seed_knowledge_base_mirror_role.py`
(`[post_model_sync]`) creates it with `desk_access = 0` and grants it to nobody; no DocPerm anywhere
names it, and `tests/test_knowledge_base_schema.py` fails the build if a JSON in the app ever does. It
opens one thing, the snapshot endpoint below, and it also shuts its account out of everything else: a
Website User who holds it is refused every request but the snapshot (`mirror_guard.py`; see "The
private mirror (PR 8)"). It is meant for a single service account, a Website User that holds only this
role, made by hand; which account, and its key, are in the company's private
runbook, never in this repo. Model sync does not make this role (no DocPerm names it), so until the
patch has run the endpoint admits Administrator only.

## The drafting tool (PR 6b)

`draft_knowledge_article` (v1.560.0) is the one dedicated path by which an AI writes to the knowledge
base, approved by Nik on 2026-09-28 ("Yes, but they can submit as well") and recorded in ADR 0017's
amendment. The wrapper is `assistant_tools/draft_knowledge_article.py`; every rule and the one write are
`ai_draft.py`.

| Argument | |
|---|---|
| `kb_number` | revise this published article (`SOP-06-0001`, or `sop 06 1`); leave it out for a new one, which is numbered from its kind and department when a KB Approver publishes it |
| `article_title` | required, at most 140 characters |
| `department` | one of the ten blocks: required for a new article; a revision keeps its article's |
| `kind` | `Policy`, `Process` or `SOP` (read through `constants.kind_option`), required; a revision must give its article's own (refused before any card otherwise, 2026-09-29) |
| `summary` | required, at most 500 characters |
| `keywords` | a list, at most 30, each at most 60 characters |
| `body_markdown` | required, at most 60,000 characters of Markdown |
| `change_note` | required, at most 1,000 characters: what changed and why, and where it came from |
| `process_owner` | an enabled staff login's user id; a revision keeps its article's when left out |
| `submit_for_review` | `true` to also submit the draft for review in the same card (default `false`) |

**How a call becomes a Draft.**

1. **Queue.** The AI write gate asks `ai_draft.precheck(arguments, requester)` first. A call that
   could never run is refused with no card: a missing or oversized field, an unknown argument, **an
   argument sent as `null` or with another JSON type than the schema's** (FAC 3.0.0's own type
   check would refuse it only once the card was confirmed; leave an argument out to say "none"), a
   requester who is Administrator, not an enabled System User or without a KB role, **a secret**
   (named by argument, line and kind, never by value; scanned in the Markdown as sent and again in
   the body as it would be shown, since `**Password:** ...` hides from the first; the scan fails
   closed), **text nobody sees** (a Unicode format, control or unassigned character such as the Tags
   block or a zero-width space, a link or picture title, a picture description over 125
   characters; named by argument and position), a picture it may not embed, and for a revision an
   article that is not published (unknown and retired read the same), of another department, or
   with an open version (named by id, state and who started it, with a link). The rules that need
   no lookup, and the secret scan, answer even when a lookup fails. Otherwise the card is queued:
   Medium risk, targeting `Knowledge Article Version`, so the batch dialog starts it unticked with
   "changes the company knowledge base".
2. **Confirm.** The tool runs only inside `gating_api._confirm_one`, for its own card (status
   Confirmed), and only when the person confirming it **is** the person who asked (a System Manager
   can still cancel it). The precheck runs again, with the confirmer, who must also hold `create` on
   the Version doctype.
3. **Write**, in one transaction under a savepoint, inside `publish.run`: a new Version, or
   `publish.start_revision(article, content=..., provenance=...)` under the article's row lock, with
   `ai_drafted = 1` and `ai_requested_by` = the requester. The body is the AI's Markdown converted
   by markdown2 with v16's `md_to_html` extras **and `safe_mode="escape"`**, so raw HTML shows as
   text; the controller then strips presentation, scans the stored HTML and records the requester
   as a contributor, as for any save.
4. **Submit**, only with `submit_for_review`: `api.knowledge_base.submit_version`, the Submit for
   Review button's own function, so the same `workflow.submit_problems` decide it and the same
   review ToDos go to every other KB Approver. The requester is recorded as `submitted_by`.
5. **Refused at any step?** The savepoint is rolled back and the card ends Failed with the reason:
   a card that asked to submit and could not leaves no Draft.

The result, through `check_ai_pending_action`: `success`, `action` (`created` or
`revision_started`), `name`, `kb_number`, `review_state`, `submitted`, `reviewers_asked` (a count;
nobody is named), `desk_url` and `next_step`.

**Who approves it.** Nothing new in the rules: the requester created it, changed its content, asked
an AI to draft it and, when it was submitted, submitted it, so `approval_problems` refuses them; it
refuses every approval made while a gate card runs, whoever confirmed the card; and a different KB
Approver, a named System User signed in to a browser, approves it in the Desk like any draft.

**Pictures.** A new article embeds none: pictures are added in the Desk, where they become private
Files. A revision may keep the article's own pictures, the Files attached to the article, which is
where publishing moved every picture a version used. A picture is one of them only when its address
is relative or on the site's own origin, its path is one of those Files' URLs **exactly**, and a
`?fid=` in it names the File at that path: matching the fid *or* the path let the article's fid
carry any path (`/files/../api/method/...`) into a picture every reader's browser would request with
their session. An address a browser reads differently from Python (a backslash, a space or control
character, a sign-in part) is refused outright. A File left on a published version was not used by
its text, and readers cannot open it, so it is refused like any other picture: by position and host,
never by URL.

**What it never does:** approve, publish, send back, withdraw, discard, supersede, retire or confirm;
a docstatus submit; a ToDo, Comment or attachment of its own; read a version's text; return text.
Continuing an existing draft by tool, and submitting one by tool, stay deferred (WI-080, "Explicitly
NOT"): a person finishes it and presses Submit for Review in the Desk. Triton is never offered the
tool.

## The private mirror (PR 8)

Every published article is also a Markdown file, `kb/<NN-department>/<article number>.md`
(`kb/06-operations/SOP-06-0001.md`), in the company's
private knowledge repo, where Claude Code and Antigravity read it alongside the shared agent rules
(WI-080 Slice 6, decided 2026-09-28). The files are generated and never edited by hand; an article is
changed in ERPNext.

**Pull, not push.** A scheduled GitHub Action in that repo, every six hours and on demand, calls one
read-only endpoint here and commits only when something changed. ERPNext holds **no GitHub credential**
and queues nothing: an ERPNext compromise cannot rewrite the repo (and with it the agent rules every
session loads), the deploy's FLUSHDB has no job to kill, and a failed run is recovered by running it
again. What a leaked mirror key exposes is the published knowledge base, which the repo already holds,
and nothing more, because the account it signs in as is confined to the snapshot (below).
The account, its key, the workflow and its safety guards (it refuses an empty snapshot over a non-empty
`kb/`, and a run that would delete more than max(3, 25%) of the files) live in that repo.

**`api/knowledge_base_mirror.snapshot(since=None)`**, `@frappe.whitelist(methods=["GET"])`, rate limited
to 60 calls an hour per client address (counted before the role check, so a refused call counts too):

```
GET /api/method/erpnext_enhancements.api.knowledge_base_mirror.snapshot?since=<stamp>
```

```json
{"schema": 1, "stamp": "<64 hex>", "app_version": "1.561.0", "count": 1, "skipped": [],
 "articles": [{"kb_number": "SOP-06-0001", "version": 3, "path": "kb/06-operations/SOP-06-0001.md",
               "sha256": "<64 hex>", "markdown": "---\nkb_number: \"SOP-06-0001\"\n..."}]}
```

- **Who.** The session user must hold **KB Mirror** or be Administrator; anyone else gets
  `PermissionError` (403), the same sentence for all, before anything is read. A guest never reaches it
  (not `allow_guest`), and a staff session is refused, System Manager included.
- **And the account calls nothing else** (the PR 8 review). The key signs in a Website User, and v16's
  whitelist refuses only a Guest (`is_whitelisted`, `frappe/__init__.py:479-487`), so without a guard
  every login-only endpoint with no gate of its own would answer to it: `sync_contact`'s contact and
  address lookups (phone numbers and email addresses for any party) and its link and unlink writes
  (under `ignore_permissions`) among them when this was written. v1.561.1 gave those their own
  permission checks; the guard remains for whatever such endpoint comes next.
  `mirror_guard.confine_mirror_account`, an **`auth_hooks`**
  entry, refuses a user who holds KB Mirror and is not a System User (v16 gives every System User, and
  nobody else, the automatic role Desk User) every request except `GET` of exactly
  `/api/method/erpnext_enhancements.api.knowledge_base_mirror.snapshot` with no `cmd` in it, since
  Frappe dispatches `cmd` before the path. The refusal is a 403, before Frappe dispatches anything:
  another method, `/api/v2`, `/api/resource`, a private file, a web page, the realtime server's sign-in.
  It cannot be a `before_request` hook: v16 runs those inside `init_request` and reads the API key only
  afterwards, in `validate_auth`, whose last step runs `auth_hooks` (`app.py:139-141`, `:244-245`;
  `auth.py:640`), so at `before_request` the key's request is still Guest. A staff login given KB Mirror
  by mistake is not confined (never locked out of the Desk); the account stays confined if a website
  role is added to it; every other user passes with no lookup beyond the roles Frappe caches, and a
  guest with none.
- **What.** Every Knowledge Article with status Published, read with one `frappe.get_all` (the role holds
  no DocPerm, so the role check is the gate), in article-number order. Never a Retired article, never the
  Version doctype, never a draft's text.
- **Each file** is `ai_tools.article_text(row, get_url())`: the fetch tool's Markdown byte for byte,
  **untruncated** (fetch cuts at 40,000 characters). `sha256` is over its UTF-8 bytes. The links in it
  start with `get_url()`, which is the site's configured `host_name`, or the host the request came to
  when none is set, so the mirror's files match fetch's when both reach the site at one address.
- **Skipped.** An article whose `department_block` is not one of the ten options, whose name is not a
  canonical article number, or whose number's department code is not its department's (possible only
  past the ORM; the private repo refuses a file in another department's folder), has no folder
  (`markdown.mirror_path`): it is listed in `skipped` as
  `{"kb_number", "department"}` and not rendered.
- **The stamp** is sha256 over `"<path>\t<sha256>\n"` for each file, sorted by path. It changes exactly
  when a file would: a publish, a retirement, a renamed approver or a moved site URL, and not a save
  that changes no rendered byte. With `since` equal to it the answer is only
  `{"schema": 1, "unchanged": true, "stamp": ...}`.
- **Reads only.** It writes nothing and logs nothing, and its own code reads no request header, so the
  credential never passes through it: Frappe reads the `Authorization` header, and drops it, before the
  endpoint runs, and `get_url()` looks only at the request's host and scheme, and only when the site has
  no `host_name`. Frappe rolls a GET's transaction back in any case. An unexpected failure is Frappe's
  own 500.
- **The contract.** Adding a field is allowed. Renaming or removing one, or changing what a file holds,
  is a new `schema`, which the mirror refuses until its script is updated.

`tests/test_knowledge_base_actions.py` (`MirrorSnapshotTest`) pins all of it over the in-memory site,
including a file equal to `fetch_payload()["markdown"]` for an article under 40,000 characters, a draft
sentinel in no part of the answer, and the stamp moving with an approver's name. `test_whitelist_placement`
keeps `snapshot` whitelisted and the file's surface to that one endpoint, and `test_hooks_integrity`
keeps every hook but the confinement from naming it (a scheduler entry would be a push, and an override
would drop the role check) and pins the confinement to `auth_hooks`, never `before_request`.
`MirrorConfinementTest` refuses the account the finding's endpoints, frappe's own, the snapshot under any
other address or method, `/api/resource`, pages and private files, and any request carrying `cmd`;
passes its snapshot through to an answer; passes staff (one holding KB Mirror included), Administrator, a
portal user and a guest; fails closed on a request it cannot read; and reads no header.

## The document: print, preview and the form (2026-09-30)

Nik asked whether articles "can be printed and previewed and viewed as the templates in the google
drive", and said "please proceed 1-3" (WI-080, "Decided 2026-09-30"). So an article is laid out as its
kind's template in the company register, measured from the templates' Google Docs export: POL-0002
(Policy), POL-0003 (Process) and POL-0004 (SOP). **One renderer, three doors:**

| Door | Who | What it is |
|---|---|---|
| **Print / PDF** | every staff user (they hold print on the Article) | the **Article Document** print format, Knowledge Article's default; the form's *Print / PDF* button opens it |
| **Preview** | KB Authors and KB Approvers | the **Article Version Preview** format, the version's default; the draft's *Preview* button saves any edits and opens it |
| **The form** | every staff user | the article form's first section, `document_view`, drawn from `__onload.kb.document`; every field under it is collapsed |

- **The page** (`document.py`, standard library, tested bench-free): top left "Sapphire Fountains" and
  "Masters of Fountaineering"; top right "Document ID: SOP-06-0001", the version and "Last Updated"
  ("Effective Date" on a Policy); the title in 24pt sapphire (`#004a7c`); a grey row with "SOP Owner:"
  (the owner's Employee `designation`, then their name) and "Group:" (the department's name); the body
  with its top-level headings numbered; "N. Revision History"; then the logo and the template's own
  Confidential line. A printed copy adds a footnote: the number, the version, the print date, and that
  the current version is in ERPNext. Arial, 11pt, Letter with the templates' one-inch margins.
- **Not the Pillar Stripe chrome.** Everything else this app prints uses `print_style`
  ([docs/print-design-system.md](../../docs/print-design-system.md)); an article is a register document
  and follows the register's templates, which is what Nik asked for. Only the logo is shared.
- **The headings are numbered by the page, not typed.** The highest heading level the body uses is
  numbered in order; one an author numbered keeps its number and still counts; lower levels are
  sub-headings. Nothing else in the body changes.
- **The Revision History** is `revisions`, a child table on the article (**Knowledge Article
  Revision**: version, approved on, author, approved by, change note), written by `publish.publish`, one
  row per published version, through the article's own guarded save; nobody edits it. An article
  published before it existed shows its live version's line, and gets that line written first at its
  next publish. **So a version's change note is what its printed history line says.** A draft's preview
  adds its own line, marked "(draft)" and "Not yet approved".
- **A draft's preview** says "SOP-06-####" until it is numbered (a guessed number would be wrong the
  moment another article was approved first), carries a red "Draft - not approved" box ("In review" when
  it is), and a faint DRAFT across each printed page. A Superseded or Discarded version prints as grey
  history; a Published one prints as the record, with no box.
- **New drafts start from the template.** When an author chooses a kind on a draft whose body is empty,
  the form fills it with the kind's sections (`constants.KIND_SECTIONS`) and the template's guidance
  under each, in square brackets and italics, from `api/knowledge_base.document_template` (GET, KB
  roles, fixed text). Choosing another kind before typing swaps them; once anything is typed, the body is
  never touched. **Guidance left in the body stops Submit for Review** (`content.guidance_left`,
  `workflow.submit_problems`: "it still has the template's guidance under Scope: replace it with the
  article's own words, or delete it"), so none is ever published. Other headings are allowed. The
  drafting tool's `body_markdown` description lists each kind's sections.
- **Pictures reach the PDF.** v16's chrome PDF engine loads the page into a browser with no session
  (`utils/pdf_generator/browser.py`), so a private image, which every KB picture is, would print as a
  broken box; only the wkhtmltopdf path inlines private images. `printing.inline_images` writes each
  picture the body uses into the printed page as a `data:` URI, only for a File attached to the record
  printed (or, for a revision, its article), up to 5 MB each. The form leaves pictures to the browser.
- **Print-safe CSS, scoped to `.kb-doc`**: tables and blocks, no flex or grid. Cell padding and borders
  are `!important` on selectors more specific than frappe's `.print-format td` (`standard.css`, and the
  Redesign print style's `padding: 10px !important`), which would otherwise win.
- **Installed on every migrate** (`setup_print_formats.py`, `after_migrate`, above the chrome pin), each
  format made its doctype's default by a code-owned Property Setter once it exists, as the Trip Sheet
  is. That setter is the only one on either doctype, and widens nothing. A form whose document failed to
  draw opens its Article Text section instead, and the failure's type goes to the Error Log (deferred:
  the form loads on a GET, which v16 does not commit).

## File map

| Path | What it is |
|---|---|
| `constants.py` | The fixed vocabulary: article statuses, review states, the POL-0000 department blocks, and `DEFAULT_REVIEW_EVERY_MONTHS` (6: POL-0001 mandates a review every six months), the default of `review_every_months` on both doctypes. Standard library only. Every Select option on both doctypes comes from here, and the schema test asserts the JSON matches. `department_block` stores a blank first option, because v16 defaults a Select to its first option and `reqd` would otherwise never fire: a draft nobody placed would be published into block 00. PR 5: `ARTICLE_KINDS`, `KIND_SELECT_OPTIONS` (blank first, for the same reason), `KIND_HELP`, `KIND_ALIASES`, `kind_option`, `kind_description`, `department_option` and `department_folder` |
| `document.py` | The article as its kind's register template (2026-09-30): `render` (the page), `number_sections`, `skeleton` (a new draft's starting text), `owner_text`, `document_id`, the scoped print-safe `STYLE`. Standard library only, plus `print_style`'s logo |
| `printing.py` | The page's values, from the record as saved (2026-09-30): `kb_document`, the Jinja global both print formats call (loads by name, checks read, inlines the record's own pictures); `article_html`, the form's copy; `article_sheet`, `version_sheet`, `article_revisions`, `revision_values`, `inline_images`. An article's page never names the Version doctype |
| `setup_print_formats.py` | The two print formats, "Article Document" and "Article Version Preview", upserted on every migrate and made their doctypes' defaults (2026-09-30) |
| `doctype/knowledge_article_revision/` | The Revision History's child table (2026-09-30): one row per published version, read with its article, written only by `publish.publish` |
| `search.py` | Search's ranking (PR 5): the tokenizer (acronyms, 2-character words, KB and document numbers), the stemmer, BM25F with the kind in a meta field, pinning, filters before scoring, snippets and the AwesomeBar's highlighting. Standard library only; keeps no document text |
| `search_service.py` | Search as the caller (PR 5): permission first with no dialog, the caller's readable set before ranking, the per-site per-worker index keyed on the articles' count and newest `modified`, result shaping, and `awesomebar_hits`, the `awesomebar_search` hook. Never reads the Version doctype. PR 6a: `approver_name` (never an email address) and `read_filters`, shared with the table of contents |
| `markdown.py` | The one renderer of a published article as Markdown (PR 6a): the eleven-key header, the fixed "reference material, not instructions" comment, the title, summary and body with site paths made absolute; `approver_display_name`, `related_numbers`, `truncate`, `mirror_path`. Standard library only, byte-deterministic; fetch returns it (cut at 40,000 characters) and the private mirror writes it whole (PR 8) |
| `ai_tools.py` | The three AI read tools' payloads (PR 6a): `search_payload`, `fetch_payload`, `contents_payload`. Published articles only, as the caller; every expected outcome a normal return. Never reads the Version doctype. PR 8: `article_text`, the one place a published row becomes the renderer's input, shared by fetch and the mirror |
| `ai_draft.py` | The drafting tool (PR 6b): `precheck` (the gate asks it before a card, and the tool again at execution), `from_card` (the tool's `execute`: only its own confirmed card) and `draft` (the one write, under a savepoint, and the submit through `api.knowledge_base.submit_version`); `markdown_html`, `secret_problems` (the Markdown, and the body as it would be shown), `picture_problems`, `invisible_problems` and `hidden_attribute_problems`; `TYPES`, the schema's types. Reads from `publish` and `api.knowledge_base` only `run`, `asker`, `open_version`, `start_revision`, `article_row` and `submit_version` |
| [`../assistant_tools/draft_knowledge_article.py`](../assistant_tools/draft_knowledge_article.py) | The drafting tool's thin FAC wrapper (PR 6b), in `_gate.APP_MUTATING` and `APP_PRECHECKED_TOOLS`, with a `precheck` method; its failure path is `_knowledge_base.run_draft` |
| [`../assistant_tools/search_company_knowledge.py`](../assistant_tools/search_company_knowledge.py), [`fetch_knowledge_article.py`](../assistant_tools/fetch_knowledge_article.py), [`list_company_knowledge.py`](../assistant_tools/list_company_knowledge.py), [`_knowledge_base.py`](../assistant_tools/_knowledge_base.py) | The three FAC tools (PR 6a), thin wrappers registered in `hooks.py` `assistant_tools` and listed in `_gate.EXPLICIT_READONLY`; `_knowledge_base.py` holds their shared `kind`/`department` schema properties and the failure path |
| `mirror_guard.py` | The private mirror's account may call its snapshot and nothing else (PR 8 review): `confine_mirror_account`, the `auth_hooks` entry, refuses a Website User holding KB Mirror every request but `GET` of the snapshot's exact path with no `cmd`, before Frappe dispatches it. Reads the request's method, path and form keys, never a header; writes and logs nothing |
| `doctype/knowledge_article/` | The published snapshot. Controller `KnowledgeArticle`: refuses every write without `flags.kb_action`, and every delete and rename |
| `doctype/knowledge_article_version/` | Drafts and history, submittable, `KBV-.#####`. Controller `KnowledgeArticleVersion`: refuses a submit without `flags.kb_publish` or that breaks an approval rule, and every cancel, amend, delete and rename; applies the content rules on save |
| `workflow.py` | The approval rules, content-edit and contributor rules, article numbers (`next_article_number`, `identity_problem`, `number_problems`, 2026-09-29) and review dates (PR 2); the state machine (`TRANSITIONS`), who may make each move (`*_problems`), what the forms offer (`version_actions`, `article_actions`) and who is asked to review (`reviewers_for`) (PR 3). Standard library only, plus `signed_in_browser` from Marketing |
| `content.py` | Presentation stripping, the secret scan and the content hash (PR 2); `shows_anything`, `referenced_files` and `text_diff` (PR 3); `guidance_left`, the register template's guidance still in a draft (2026-09-30). Standard library only |
| `files.py` | The two `File` hooks: private Files, bytes included, that stay attached where they are, and no deleting an article's image (PR 2) or a version's once it has left Draft (PR 3) |
| `publish.py` | Every write the actions make (PR 3): `transition`, the only writer of `review_state`; `publish`, the one-transaction publish; `start_revision` (PR 6b: optional `content` and `provenance`, for the drafting tool), `retire`, `confirm_still_accurate`; `run`, the deadlock retry; `asker`; the forms' `onload` payloads (since 2026-09-30 the article's includes `document`); the Revision History row each publish adds |
| `notify.py` | Review ToDos (PR 3): closed on every move, raised inline for the new state, title and link only |
| `references.py` | The `Comment` and `ToDo` guards (PR 3, decision (b)): no typed text about a draft outside the draft |
| `emailed_reports.py` | The `Auto Email Report` guard (PR 4 review): a KB report is emailed only by, and as, someone who holds its role, because v16 sends an emailed report as Administrator |
| `reporting.py` | The rules of the two reports (PR 4), pure: `review_due`/`due_rows` and `integrity_problems`, and the exact columns each report may read. Standard library only |
| `report/knowledge_articles_due_for_review/` | The Due for Review Script Report (PR 4): one bound query on the published Article, the filters, the State colours |
| `report/knowledge_base_integrity/` | The Integrity Script Report (PR 4): four bound queries, no draft text selected, none quoted |
| `workspace/knowledge_base/knowledge_base.json` | The `Knowledge Base` workspace (PR 4). Timestamp-gated on import: bump `modified` with any change |
| [`../workspace_sidebar/knowledge_base.json`](../workspace_sidebar/knowledge_base.json) | Its sidebar (PR 4), which is also what lets the Desk tile render. Timestamp-gated too |
| [`../setup/desktop_icon_map.py`](../setup/desktop_icon_map.py), [`../public/desktop_icons/knowledge_base.svg`](../public/desktop_icons/knowledge_base.svg) | The home-screen tile and its generated artwork (PR 4); `setup/desktop_icons.py` makes the Desktop Icon on migrate, and appends it to every saved home-screen layout that lacks it (PR 4 review) |
| [`../hooks.py`](../hooks.py) `standard_help_items` | Help > Company Knowledge Base (PR 4) |
| [`../hooks.py`](../hooks.py) `awesomebar_search` | The AwesomeBar's knowledge base hits (PR 5), `search_service.awesomebar_hits` |
| [`../hooks.py`](../hooks.py) `auth_hooks` | `mirror_guard.confine_mirror_account` (PR 8 review): an auth_hook, because v16 reads the API key after `before_request` |
| [`../api/knowledge_base.py`](../api/knowledge_base.py) | The nine endpoints (PR 3): the permission and rule checks, then `publish`; `submit_version`, Submit for Review's body, shared with the drafting tool and not whitelisted (PR 6b); `document_template`, a new draft's template sections, a tenth, GET (2026-09-30) |
| [`../api/knowledge_base_mirror.py`](../api/knowledge_base_mirror.py) | The private mirror's one endpoint (PR 8): `snapshot`, a rate-limited GET for KB Mirror or Administrator; every Published article through `ai_tools.article_text`, untruncated, with its path, sha256 and the set's stamp; `stamp_of`. Reads only |
| [`../public/js/knowledge_base/`](../public/js/knowledge_base/) | The two form scripts (PR 3), registered in `doctype_js`: the buttons `__onload.kb` allows, the dialogs, View Changes; since 2026-09-30 the article's document view and *Print / PDF*, and the draft's *Preview* and template fill |
| [`../hooks.py`](../hooks.py) `jinja` and `after_migrate` | `printing.kb_document`, and `setup_print_formats.ensure_knowledge_base_print_formats` above the chrome pin (2026-09-30) |
| `module_def/knowledge_base.json` | The `Module Def`. Documentation only: `module_def` is not in v16's `IMPORTABLE_DOCTYPES`, so the module is installed by its DocTypes and `refresh_module_map` (see `tests/test_module_installability.py`) |
| [`../patches/seed_knowledge_base_roles.py`](../patches/seed_knowledge_base_roles.py) | The two roles and the one-role "KB Approvers" Role Profile. Insert-only; cannot raise |
| [`../patches/seed_knowledge_base_mirror_role.py`](../patches/seed_knowledge_base_mirror_role.py) | "KB Mirror" (PR 8): `desk_access = 0`, no DocPerm, granted to nobody. Insert-only; cannot raise |
| [`../assistant_tools/_gate.py`](../assistant_tools/_gate.py) | `DENYLIST_DOCTYPES`, `DENYLIST_REASONS` and `NEVER_EXEMPT` carry the KB entries; `DENYLIST_FILE_ARGUMENTS` refuses `extract_file_content` on a draft's File (PR 3); `APP_MUTATING`, `APP_PRECHECKED_TOOLS`, `TOOL_TARGET_DOCTYPES`, `WITHHELD_WHEN_UNQUEUED` and `KEPT_WHEN_UNQUEUED` carry the drafting tool (PR 6b) |
| [`../tests/test_knowledge_base_schema.py`](../tests/test_knowledge_base_schema.py) | Flags, the DocPerm matrix, fields, Select options, controller refusals, the seed patch; the mirror's role patch and no JSON naming KB Mirror (PR 8). Its own CI step |
| [`../tests/test_ai_gate_denylist.py`](../tests/test_ai_gate_denylist.py) | The Version doctype refused on every gate path; the published doctype not refused. On the AI-gate CI step |
| [`../tests/test_knowledge_base_rules.py`](../tests/test_knowledge_base_rules.py) | `workflow.py` and `content.py`, every branch, with no stub (and a fresh-interpreter check that they import no frappe); `markdown.py` too since PR 6a (`TestArticleMarkdown`: the header read back as YAML, quoting, truncation, links, byte-determinism, no email address). Its own CI step |
| [`../tests/test_knowledge_base_hooks.py`](../tests/test_knowledge_base_hooks.py) | `files.py` (the fast path, the byte move, the delete refusal, registration) and the Version controller's content and approval gates. Its own CI step: it stubs `frappe` |
| [`../tests/test_knowledge_base_transitions.py`](../tests/test_knowledge_base_transitions.py) | The state machine, every rule of every move, the buttons, who is asked, `shows_anything`/`referenced_files`/`text_diff`, and the example-key placeholders (PR 3). No stub; its own CI step |
| [`../tests/test_knowledge_base_actions.py`](../tests/test_knowledge_base_actions.py) | The endpoints end to end over an in-memory Frappe running the real controllers and hooks: the WI-080 person test, the publish steps and their order, numbers and concurrency, revisions, ToDos with no draft text, decisions (a) and (b), the forms' buttons (PR 3); the kind through submit, publish, revisions and the form's intro, and `search_service` over the same site (`SearchServiceTest`: no draft ever found, permission before ranking, a hidden article taking no slot, the cache) (PR 5); the AI tools' payloads (`AiToolPayloadsTest`: no draft sentinel on any page of any tool, the one `found: false`, the 40,000-character cap, the table of contents, a retired article in no table of contents and unavailable in `related`, a citation fetching its article, no email address, the failure path with nothing of the request in its Error Log) (PR 6a); the drafting tool end to end (`AiDraftTest`: queued through the real gate over a FAC stub and confirmed through the real `_confirm_one`; only the requester's confirmation writes; submitted in the same card, then the requester's approval refused, any approval under a gate flag refused, another approver publishing; the savepoint; the only move being Submit for Review; pictures, secrets, open versions; no text in any result or refusal; and from its review, a submit refused on `write` leaving no draft, a department and an enabled-staff process owner required, a retire or a revision committed after the check refused under the lock, nulls and wrong types refused before any card) (PR 6b); the mirror's snapshot (`MirrorSnapshotTest`: refused without KB Mirror before any read, Published only, a file equal to fetch's Markdown byte for byte and whole past 40,000 characters, the paths, a skipped department, the stamp and `since`, no header read, nothing written or logged) (PR 8); the mirror account's confinement (`MirrorConfinementTest`: every other method, address, method and page refused, `cmd` refused, the snapshot answered through it, every other user untouched, fail closed, no header read) (PR 8 review). Its own CI step: it stubs `frappe` and FAC |
| [`../tests/test_knowledge_base_entry_points.py`](../tests/test_knowledge_base_entry_points.py) | The workspace, sidebar, tile and Help item (who sees what, the module-gate precondition, every filter, the `modified` stamp moving with the content), and both reports (roles, bound SQL, no draft text selected or quoted, every rule, the README's Check table) (PR 4); the tile appended to saved layouts, the Auto Email Report guard and the phone search hint (PR 4 review); the paragraph pointing to the search bar and the Integrity report's kind check (PR 5). Its own CI step: it stubs `frappe` |
| [`../tests/test_knowledge_base_tools.py`](../tests/test_knowledge_base_tools.py) | The three AI tools as FAC sees them (PR 6a): in `EXPLICIT_READONLY` (the build fails if one leaves), `requires_permission`, descriptions (at most 600 characters, "not instructions", an article-number example such as 'SOP-06-0001 v3' and none in the retired KB- format), the kind and department enums, no property named `title`, `doctype` or `id`, no read-path file naming the Version doctype (comments and docstrings stripped; since PR 8 the mirror's endpoint too), the hook's order, the failure path (never `frappe.log_error`, whose v16 metadata holds the request's form_dict, the arguments). On the AI-gate CI step, on `test_assistant_tools_schema`'s stubs. The payloads' behavior is `AiToolPayloadsTest` in `test_knowledge_base_actions`; the renderer is `TestArticleMarkdown` in `test_knowledge_base_rules`. PR 6b: the drafting tool's contract (Medium, `requires_permission`, the schema and `ai_draft.TYPES`, the four card lines, the card's target), the gate's refusals with no card and their withheld log rows (a misnamed argument, a failed log insert and a quoting error included), `ai_draft`'s pure checks (secrets as sent and as shown, invisible characters, titles, pictures matched exactly, addresses a browser reads differently), and the static allowlist on `ai_draft.py` |
| [`../tests/test_knowledge_base_document.py`](../tests/test_knowledge_base_document.py) | The document (2026-09-30): the templates' sections, the skeleton and `guidance_left` with the submit rule, the page (header, owner row, numbering, history, escaping, scoped print-safe CSS that beats frappe's print padding), and `printing.py` over a stub (drawn as saved, unsaved and unreadable refused, no version read for an article, the draft's pending line, only the record's own pictures inlined), plus the wiring. Its own CI step: it stubs `frappe` |
| [`../tests/test_knowledge_base_search.py`](../tests/test_knowledge_base_search.py) | `search.py` (PR 5), **pytest**, on its own `python -m pytest` step: every tokenizer rule (a punctuated acronym such as W-2 or T&M included), the stemmer table, pinning, filters before scoring, the kind's aliases, ties, snippets, an invented golden set (`tests/data/kb_search_golden.json`), a performance guard, a fresh-interpreter import with `frappe` absent, and static checks that search never names the Version doctype or a SQL function string |

## What arrives later

In order, one PR at a time, each verified on prod before the next merges (see WI-080):

- **PR 4a** (only if the Google Docs Markdown export keeps pictures and tables): Markdown import.
- ~~**PR 5**: in-app search~~. Done in v1.558.0, holding to what PR 4 left it: the published Article
  only, as the caller, never the Version doctype and never a SQL-function string; no `Global Search
  DocType` row and no `in_global_search` field; a hit opens the article with `frappe.set_route`; the
  list's filter bar stays; the workspace's paragraph points to the search bar, with its stamp and
  `PINNED` moved; nothing written; and a bench-free pytest on its own step. See "The article's kind
  (PR 5)" and "Search (PR 5)".
- ~~**PR 6a**: three read-only AI tools~~. Written in v1.559.0: `search_company_knowledge`,
  `fetch_knowledge_article` and `list_company_knowledge`, over `search_service` and the published
  article, with results carrying `result_type` (`"article"`) and the article's `kind`, and the one
  Markdown renderer the mirror will reuse. See "AI tools (PR 6a)". Triton follows in its own PR.
- ~~**PR 6b**: `draft_knowledge_article`~~. Written in v1.560.0: it writes a Draft (and, if asked,
  submits it for review) only from an approval card the person who asked confirms, and never approves
  or publishes. See "The drafting tool (PR 6b)". Merged on 2026-09-28 (#1153) and live.
- **PR 7**: the one-way Drive copy for Gemini and outages. It keys its export on
  `(content_hash, version_number)`, because the kind is not in the hash. Since 2026-09-30 the page it
  should export is `document.render`'s, so a Drive copy looks like the register's templates too.
- ~~**PR 8**: the ERPNext side of the Markdown mirror~~. Written in v1.561.0: the read-only `snapshot`
  endpoint and the KB Mirror role (WI-080 Slice 6). See "The private mirror (PR 8)". The workflow that
  pulls it lives in the company's private repo.

**Merging:** PRs 1 to 3 and PR 3's review fixes are live on prod (1.556.1, verified 2026-09-28), and
PRs 4, 5, 6a and 6b merged on 2026-09-28 and are live (v1.560.0, verified that day). PR 8 (v1.561.0)
needs only PR 6a's renderer. Each later PR merges when Nik decides, one at a time, after the one before
it is verified on prod.
