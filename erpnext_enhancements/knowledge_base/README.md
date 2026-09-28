# Knowledge Base

The company knowledge base: short, approved articles that people and every AI tool Sapphire uses
read from the same place, so that an answer from Claude and an answer from Triton cite the same
approved text. The goal is continuity: Parker, James and the crews keep the company running without
Nik.

Programme: [WI-080](../../work-items/WI-080-company-knowledge-base.md).
Decision record: [ADR 0017](../../decisions/adr/0017-company-knowledge-lives-in-a-native-module.md).

**Status: PRs 1 to 3 of the v1 build (v1.538.0, v1.539.0, v1.555.0).** PR 1: the module, the two
doctypes, the two roles, the locked permissions and the AI-gate denylist. PR 2: the approval rules
and the content rules, applied by the Version controller, and private Files. **PR 3: the actions**
(review, approve and publish, retire), the one-transaction publish, review ToDos and the form
buttons, so **an article can now be published**. There is still **no workspace, no report and no AI
tool** (PRs 4 to 6): people reach a version or an article from its list (`/desk/knowledge-article`,
`/desk/knowledge-article-version`) or from a review ToDo. **Nobody holds a KB role on prod yet**:
granting them is a Desk step for Nik after PR 3 deploys (see "Roles, and how a person gets one").

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
| Global search and the AwesomeBar | v16 indexes a doctype's name only with `show_name_in_global_search` (0 on both, the v16 default) and a field only with its own `in_global_search` (none) or a Global Search Settings row (none). The JSONs' `show_in_global_search 0` is not a v16 DocType field and changes nothing (found in PR 3). Search arrives in PR 5, as the caller |
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
then `on_discard`, and neither cancel hook, so the Version refuses it in both of those (v1.555.1).
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

**KB numbers** (`workflow.next_kb_number`): `KB-{block}{01..99}`, one more than the highest number
already in the block, never the lowest gap (a vanished number may still be cited). `{block}00` is the
block's index and is never allocated. A full block raises `BlockFullError` with a message that says
so. `kb_number_prefix` gives PR 3 the `KB-06` it binds as `"KB-06%"` in its `SELECT ... FOR UPDATE`.

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
| `submit_for_review(version)` | POST | Draft -> In Review; review ToDos raised | KB Author, KB Approver | no |
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
people pressing it at once get the same draft. **An article keeps its department**: its KB number
carries the block, so a revision in another department is refused at submit and at publish.

**Publishing is one transaction** (`publish.publish`), in this order: (1) a first version gets its
KB number from `SELECT name FROM tabKnowledge Article WHERE name LIKE %s FOR UPDATE`, with
`kb_number_prefix(block) + "%"` as the bound parameter, and `next_kb_number` over what it returned;
a revision locks its article instead; (2) the article is inserted or saved under
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

### Fixed after PR 3's review (v1.555.1)

All four were found in the review of PR 3 (#1144), which merged before they were fixed. Each needs
a KB role (or a draft, which needs one) to reach, and nobody held one on prod in between. Grant the
roles once v1.555.1 is live.

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
- **Administrator grants and revokes; it never approves.** The continuity runbook (not yet
  written; ERPNext task TASK-2026-02297) uses Administrator only to grant or revoke KB roles.
  Administrator holds every role implicitly, so the approval rules refuse it by name, as they
  refuse Guest and any account that is not a System User: an approval is always a named person's.

**The Desk steps after PR 3 deploys (Nik).** Nobody holds a KB role on prod yet, so until these are
done every KB button is hidden and every action is refused. **Do them once v1.555.1, PR 3's review
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

## File map

| Path | What it is |
|---|---|
| `constants.py` | The fixed vocabulary: article statuses, review states, the POL-0000 department blocks, and `DEFAULT_REVIEW_EVERY_MONTHS` (6: POL-0001 mandates a review every six months), the default of `review_every_months` on both doctypes. Standard library only. Every Select option on both doctypes comes from here, and the schema test asserts the JSON matches. `department_block` stores a blank first option, because v16 defaults a Select to its first option and `reqd` would otherwise never fire: a draft nobody placed would be published into block 00 |
| `doctype/knowledge_article/` | The published snapshot. Controller `KnowledgeArticle`: refuses every write without `flags.kb_action`, and every delete and rename |
| `doctype/knowledge_article_version/` | Drafts and history, submittable, `KBV-.#####`. Controller `KnowledgeArticleVersion`: refuses a submit without `flags.kb_publish` or that breaks an approval rule, and every cancel, amend, delete and rename; applies the content rules on save |
| `workflow.py` | The approval rules, content-edit and contributor rules, KB numbers and review dates (PR 2); the state machine (`TRANSITIONS`), who may make each move (`*_problems`), what the forms offer (`version_actions`, `article_actions`) and who is asked to review (`reviewers_for`) (PR 3). Standard library only, plus `signed_in_browser` from Marketing |
| `content.py` | Presentation stripping, the secret scan and the content hash (PR 2); `shows_anything`, `referenced_files` and `text_diff` (PR 3). Standard library only |
| `files.py` | The two `File` hooks: private Files, bytes included, that stay attached where they are, and no deleting an article's image (PR 2) or a version's once it has left Draft (PR 3) |
| `publish.py` | Every write the actions make (PR 3): `transition`, the only writer of `review_state`; `publish`, the one-transaction publish; `start_revision`, `retire`, `confirm_still_accurate`; `run`, the deadlock retry; `asker`; the forms' `onload` payloads |
| `notify.py` | Review ToDos (PR 3): closed on every move, raised inline for the new state, title and link only |
| `references.py` | The `Comment` and `ToDo` guards (PR 3, decision (b)): no typed text about a draft outside the draft |
| [`../api/knowledge_base.py`](../api/knowledge_base.py) | The nine endpoints (PR 3): the permission and rule checks, then `publish` |
| [`../public/js/knowledge_base/`](../public/js/knowledge_base/) | The two form scripts (PR 3), registered in `doctype_js`: the buttons `__onload.kb` allows, the dialogs, View Changes |
| `module_def/knowledge_base.json` | The `Module Def`. Documentation only: `module_def` is not in v16's `IMPORTABLE_DOCTYPES`, so the module is installed by its DocTypes and `refresh_module_map` (see `tests/test_module_installability.py`) |
| [`../patches/seed_knowledge_base_roles.py`](../patches/seed_knowledge_base_roles.py) | The two roles and the one-role "KB Approvers" Role Profile. Insert-only; cannot raise |
| [`../assistant_tools/_gate.py`](../assistant_tools/_gate.py) | `DENYLIST_DOCTYPES`, `DENYLIST_REASONS` and `NEVER_EXEMPT` carry the KB entries; `DENYLIST_FILE_ARGUMENTS` refuses `extract_file_content` on a draft's File (PR 3) |
| [`../tests/test_knowledge_base_schema.py`](../tests/test_knowledge_base_schema.py) | Flags, the DocPerm matrix, fields, Select options, controller refusals, the seed patch. Its own CI step |
| [`../tests/test_ai_gate_denylist.py`](../tests/test_ai_gate_denylist.py) | The Version doctype refused on every gate path; the published doctype not refused. On the AI-gate CI step |
| [`../tests/test_knowledge_base_rules.py`](../tests/test_knowledge_base_rules.py) | `workflow.py` and `content.py`, every branch, with no stub (and a fresh-interpreter check that they import no frappe). Its own CI step |
| [`../tests/test_knowledge_base_hooks.py`](../tests/test_knowledge_base_hooks.py) | `files.py` (the fast path, the byte move, the delete refusal, registration) and the Version controller's content and approval gates. Its own CI step: it stubs `frappe` |
| [`../tests/test_knowledge_base_transitions.py`](../tests/test_knowledge_base_transitions.py) | The state machine, every rule of every move, the buttons, who is asked, `shows_anything`/`referenced_files`/`text_diff`, and the example-key placeholders (PR 3). No stub; its own CI step |
| [`../tests/test_knowledge_base_actions.py`](../tests/test_knowledge_base_actions.py) | The endpoints end to end over an in-memory Frappe running the real controllers and hooks: the WI-080 person test, the publish steps and their order, numbers and concurrency, revisions, ToDos with no draft text, decisions (a) and (b), the forms' buttons (PR 3). Its own CI step: it stubs `frappe` |

## What arrives later

In order, one PR at a time, each verified on prod before the next merges (see WI-080):

- **PR 4**: the `Knowledge Base` workspace, the Desk tile, the Help menu item, and two reports
  (Due for Review; the Integrity report, which holds the integrity query the denylist refuses over
  MCP). What PR 3 leaves it, and what it must hold to:
  - **The workspace's lists are the states**: "In review" is versions with `review_state = 'In
    Review'`; "My drafts" is `owner = me` and Draft; "Due for review" is articles with `status =
    'Published'` and `review_by` before today (the article's field, which `publish` and
    `confirm_still_accurate` restart). Links to the Version doctype are for KB roles only; a reader
    reaches the workspace through the Article's `Desk User` DocPerm (the module gate needs one
    non-child doctype the reader can read).
  - **The Integrity report** (KB Approver only) checks what only a write past the ORM can break:
    every Article's `live_version` exists, is submitted, is Published, and has the Article's
    `version_number`; the Article's `content_hash` equals `content.content_hash` of that version
    (so a `frappe.db.set_value` on the body shows); `approved_by` is not the live version's owner,
    submitter, AI requester or a contributor; every version with an `article` other than the live
    one is Superseded, Discarded or open, and at most one is open; no open version sits on a
    Retired article; no File attached to either doctype is public. It reads the Version doctype, so
    it runs as a Script Report (the denylist refuses that SQL over MCP, on purpose).
  - **Nothing in PR 4 writes.** Every state change stays in `publish.transition` and the article
    writes in `publish.py`; a report or workspace button that needs a move calls the PR 3
    endpoints, which check everything again.
  - **The review ToDos already carry the link** people follow today. The workspace adds lists, not
    a second notice.
- **PR 4a** (only if the Google Docs Markdown export keeps pictures and tables): Markdown import.
- **PR 5 and 6**: in-app search and the two read-only AI tools, `search_company_knowledge` and
  `fetch_knowledge_article`.
- **PR 7**: the one-way Drive copy for Gemini and outages.

**Merging:** PRs 1 and 2 are live on prod (installed 1.549.1, verified 2026-09-28). Each later PR
merges when Nik decides, one at a time, after the one before it is verified on prod.
