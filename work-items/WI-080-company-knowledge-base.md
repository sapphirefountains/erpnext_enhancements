# WI-080: Company knowledge base (native)

**Phase:** 2   **Type:** APP_CODE   **Size:** L (v1 in eight PRs, S to M each; v1.1 M; the training slice S)
**Blocked by:**
- Slice 0: nothing.
- Slices 1–3: the QBO + Workforce cutover (~2026-10-21), so build starts no earlier than ~2026-11-02. Also Parker's Phase 0 test (below), and a named 4th KB Approver before slice 1's roles are granted.
- Slice 4: the Google setup.
- Slice 5: its content trigger.

**Blocks:** nothing
**Decision record:** [ADR 0017](../decisions/adr/0017-company-knowledge-lives-in-a-native-module.md)

## Why

Nik wants a company knowledge base that people *and* every AI tool Sapphire uses read from the same place, so that Parker, James and the crews can keep the company running without him. On 2026-09-24 he chose ERPNext as its home. After comparing Frappe Wiki v3, the recommendation is to build a narrow native module (ADR 0017).

Why now, in numbers (verified 2026-09-24 against prod, read-only):

| Fact | Value |
|---|---|
| Company knowledge in ERPNext | **0** articles. Help Article 0 rows; Frappe Wiki and Helpdesk not installed (`tabInstalled Application` = erpnext, frappe, erpnext_enhancements, payments, telephony, frappe_assistant_core, newsletter) |
| Knowledge the AI tools can reach today | FAC Skills: 31 rows, `use_count` 0 in total. They are served only as MCP resources, which claude.ai and Triton never read |
| Can the Desk's own search find "PO", "QBO", "SOP"? | No. `__global_search` has `ft_min_word_len=4`, so 2–3-letter terms are never indexed |
| Who can grant roles | Nik only (System Manager: Nik, triton@, Administrator; no User Manager holder) |
| Staff | 19 enabled System Users. All get `Desk User` automatically (v16 `permissions.py:35`, `:559-562`) |
| Controlled documents in Drive | ~17 written POL/PRO/SOP in the POL-0000 register (last edited 2026-06-22), plus ~25 unregistered procedure Docs and 10–12 staff-grade repo runbooks |
| Training | 7 published courses with 107 live lessons. **0 of the 107 mention a `KB-\d{4}` number.** 6 completions ever, by 2 real people |

## Native-first check

Checked against Frappe and ERPNext `version-16` (`git show origin/version-16:…`), Frappe Wiki `version-3` at v3.2.1, and prod, 2026-09-24.

| Candidate | Verdict |
|---|---|
| **Frappe Wiki v3** (v3.2.1, 2026-09-17, MIT; needs frappe ≥ 16.31.0, prod is 16.35.0) | **Rejected, kept as the fallback.** It has five unsafe defaults: a space with no roles is readable by portal users (`permissions.py:88-114`); uploads are public; the Wiki User role gives portal users Desk access; wiki routes can shadow site pages; a frontend build runs on every deploy. A writer can approve and merge their own change in one click, and System Manager and Wiki Manager Desk/API edits publish with no review (`wiki_document.py:987-999`). There is no way to browse or restore history (issue #622). Upgrades need server shell access, and the test VM is down (503). The two AI tools and the Drive copy are needed either way, so the Wiki saves only the model, editor and reader, about 4–6 days. **If Parker rejects the Quill editor in Phase 0, reopen this.** |
| **Core Help Article / Help Category** (Website module) | **Rejected.** 0 rows on prod. `allow_guest_to_view=1`, so a published article is served to guests by `web_search` and listed in `sitemap.xml`; `login_required` on the page does not close either path. It has no draft/published split, no approver, and no KB number, owner or review-date fields. It relies on the acronym-blind global search. Its roles (Knowledge Base Contributor/Editor) are held by 22 users directly and 5 Role Profile rows, which is a latent leak with no content behind it. |
| **Helpdesk knowledge base (HD Article, frappe/helpdesk v1.30.1)** | **Rejected.** Not installed. Installing it means a whole ticketing app for a customer-portal knowledge base. It has no two-person approval and no dedicated MCP tool; its AI integration is undocumented (helpdesk#3632, open). |
| **Drive only** (Google Docs in a shared drive as the canonical store) | **Rejected as canonical, adopted twice as a copy.** Nik chose ERPNext as home. Drive enforces no second approver (it is a human process only), and Claude and Triton would still need a mirror plus the two tools. It stays as (a) the v1.1 one-way copy for Gemini in Workspace, which cannot call a custom MCP server on a work account, and for outages, and (b) the Restricted Drive for continuity material. |
| **FAC Skills** (`data/assistant_skills.json`) | **Rejected.** 31 rows, 0 uses. FAC 3.0.0 serves them only as MCP resources (`fac://skills/<id>`), which claude.ai never shows the model and Triton does not implement. There is no approval step. Knowledge an agent must use has to be reachable through a **tool**. |
| Frappe `Note` | **Used only for Parker's Phase 0 editor test.** A private Note is owner-only (`note.py:74-82`), and `Desk User` can create one (`note.json`). It is the same Text Editor v1 uses. |
| Frappe v16 SQLiteSearch / InnoDB FULLTEXT | **Rejected for retrieval.** SQLiteSearch's first build is queued on redis `:11000`, which the deploy flushes, and it applies no row permissions. FULLTEXT's minimum word length drops "PO". |

## Preconditions

- **The cutover is done.** Build starts no earlier than ~2026-11-02. Only slice 0 and the zero-code prep go earlier.
- **Parker's Phase 0 test passes.**
  1. In a private Note, paste a real SOP from Google Docs, a screenshot and a table, then view it on his phone.
  2. Run File → Download → Markdown on SOP-0030 and on one image-bearing Doc. Record whether the images arrive as embedded `data:` URIs and whether lists inside table cells survive.
  3. If the editor is unworkable, stop and reopen the Wiki. If the export fails, drop PR 4a and budget for manual re-pasting.
- **A 4th KB Approver is named.** If that person has a Role Profile, the grant goes through a profile: a direct grant is wiped on their next save, and profile propagation is queued, so the deploy's FLUSHDB can kill it.
- **The Restricted Drive runbook has a step:** "grant or revoke KB roles as Administrator".
- **The POL-0000 register is reconciled before any POL/PRO/SOP is imported.** Fix SOP-0030 vs SOP-0105, the titles typed into the Review Date column, and POL-0004 existing as both a Doc and a Sheet.
- **Slice 4:** Nik creates the `erpnext-kb-export@erpnext-465317` service account and its key. James creates the "Sapphire Knowledge Base" shared drive, with sharing changes limited to James and Nik.
- **Slice 5:** at least one published course cites three or more published articles.

## Scope

Every PR bumps `__init__.py` and `package.json` together and adds a CHANGELOG entry. PRs merge one at a time, and each is verified with the read-only queries below before the next merges.

### Slice 0: Decision record [S, 0.5 d]

- This file, ADR 0017, a row in `decisions/adr/README.md`, and a PATCH bump.
- No `.py` or `.json` under `erpnext_enhancements/` changes, except `__init__.py`.

### Slice 1: Model and workflow (v1a, PRs 1–4) [M, 4.25–5.25 d]

**PR 1: module, doctypes, roles, locked schema, gate.**
- `modules.txt` += `Knowledge Base`, plus `knowledge_base/` with a README.
- **`Knowledge Article`**, class `KnowledgeArticle`:
  - `autoname: field:kb_number`, `allow_rename 0`, `track_changes 1`.
  - Every field is read-only: kb_number, title, department_block, status (Published/Retired), summary, keywords, `body` (Text Editor), `body_md` (hidden), content_hash, version_number, live_version (Data), change_note, author, approved_by/on, first_published_on, process_owner, review_every_months, review_by, last_reviewed_on/by, ai_drafted, retired_on/by/reason.
  - `validate` refuses any change without `flags.kb_action`. `on_trash` refuses.
- **`Knowledge Article Version`**, class `KnowledgeArticleVersion`:
  - Submittable, `KBV-.#####`, `track_changes 0`.
  - Fields: article, version_number, base_version, review_state (Draft / In Review / Published / Superseded / Discarded), the editable content fields, reviewer, submitted_by/on, approved_by/on, review_note, contributors, ai_drafted, ai_requested_by, and `source_url`/`source_drive_file_id`/`source_modified`/`imported_on` for slice 2.
  - `before_submit` **and** `on_submit` refuse without `flags.kb_publish`, because `flags.ignore_validate` skips `before_submit` (v16 `document.py:1391-1416`) but never `on_submit` (`:1445-1457`).
  - `before_cancel`, `on_trash` and amend all refuse.
- **Both doctypes:** `has_web_view 0`, `allow_guest_to_view 0`, `show_in_global_search 0`, `make_attachments_public 0`, `allow_import 0`.
- **DocPerm:**
  - Article: `Desk User` read/report/print, System Manager read.
  - Version: KB Author and KB Approver read/create/write/print/report.
  - **`share 0` everywhere.** v16 `assign_to.add` shares the document with an assignee who cannot read it (`assign_to.py:106-118`).
  - No submit, cancel, amend, delete, export, import or email for anyone.
  - No System Manager, `Desk User`, All or Guest row on Version.
- **Roles:** `patches/seed_knowledge_base_roles.py` under `[post_model_sync]`, in the shape of `seed_training_roles` (`patches.txt:44`, `:371`; `hooks.py:2071-2073`). It is insert-only, with `desk_access=1`.
- **Gate (`_gate.py`):**
  - `DENYLIST_DOCTYPES` += `Knowledge Article Version`. `NEVER_EXEMPT` += both doctypes.
  - The refusal message becomes a per-doctype reason map.
  - Fix `assistant_tools/README.md:125`, which says there is no denylist.
- **Tests:**
  - `tests/test_knowledge_base_schema.py` (unittest, own ci.yml step) pins the flags, the exact DocPerm matrix, the class names and the seed patch.
  - `tests/test_ai_gate_denylist.py` (unittest, appended to the "AI gate + assistant-tool contract" step) covers:
    - the Version doctype refused for every tool;
    - raw SQL on `` `tabKnowledge Article Version` `` refused, including comment and backtick variants;
    - `tabKnowledge Article` **not** refused;
    - `Triton Chat Attachment` still refused.

**PR 2: pure rules and content hygiene.**
- `knowledge_base/workflow.py` (standard library only; imports `signed_in_browser` from `marketing/publish/workflow.py:64-74`). Marketing's `approval_problems` is **not** reused, because it hard-codes Marketing Manager (`:122-142`). The module's own `approval_problems` refuses when:
  - the approver lacks KB Approver;
  - the request is not from a browser;
  - `ai_gate_pending` or `ai_gate_bypass` is set;
  - the version is not In Review;
  - the approver is the owner, the submitter, a contributor or the AI requester;
  - `modified` is not the value the approver opened.
- It also holds `next_kb_number(block, taken)` (`KB-{block}{01..99}`, with `00` reserved as the block index) and `review_by`.
- `knowledge_base/content.py` (standard library only):
  - `strip_presentation` removes `style` and the `ql-color`/`ql-bg`/`ql-size`/`ql-font` classes, and keeps tables, lists, alignment and indent.
  - `secret_findings` runs on the text **after** `data:` images are removed, because v16 extracts images only after `validate` (`document.py:594` → `:835`). It returns line and kind, never the value.
  - `content_hash`.
- **`body_md` and `content_hash` are computed at publish, from the stored body. Never in `validate`.**
- `knowledge_base/files.py` forces `is_private=1` on any File attached to either KB doctype (`doc_events["File"]["before_insert"]`).
- Tests are unittest and pure. Secret fixtures are built by concatenation, because push protection refused an `sk_live_` fixture before.

**PR 3: actions.**
- `api/knowledge_base.py` (tabs; POST-only, with explicit permission checks; token-authenticated requests are refused on the approval path). Endpoints:
  - `start_revision`, which returns the open draft if there is one (one open draft per article);
  - `submit_for_review`, `withdraw`, `request_changes`, `approve_and_publish`, `discard`, `confirm_still_accurate`, `retire`;
  - GET `review_diff`: the live vs draft `html2text` diff, KB roles only.
- `knowledge_base/publish.py` does the following in one transaction:
  1. Allocates the number with `SELECT … FOR UPDATE` (the `%` goes inside the bound parameter).
  2. Writes the Article under `flags.kb_action`.
  3. Moves every `?fid=` File that is attached to this Version and used in its body onto the Article. It never deletes.
  4. Supersedes the previous version and closes its ToDos.
- `knowledge_base/notify.py` handles review ToDos in the shape of `training/notifications._raise_todo`, but **inline, not enqueued**. The description holds the title and link, never draft text. The enabled fixture Notification "New ToDo Created – Notify Creator and Assignee" sends the email.
- Form scripts go in `public/js/knowledge_base/*.js`, registered in `doctype_js`.

**PR 4: entry points.**
- The `Knowledge Base` workspace: Published, My drafts, In review, Due for review.
- `TILES["Knowledge Base"]`, plus the generated `public/desktop_icons/knowledge_base.svg`.
- `standard_help_items` += "Company Knowledge Base" → `/desk/knowledge-base`.
- Script Reports:
  - `Knowledge Articles Due for Review`, for KB roles;
  - `Knowledge Base Integrity`, for KB Approver only. It holds the integrity query, because the denylist refuses it over MCP.

### Slice 2: Markdown import (PR 4a) [S, 1.5–2 d; only if Phase 0's export test passes]

- A KB Author drops a `.md` file onto a Draft Version, either a Google Docs Markdown download or a repo `docs/*.md`.
- The server then:
  1. parses the POL-0002/0004 template header into fields;
  2. moves the revision-history table into `change_note` and drops the "Confidential" footer;
  3. converts with `frappe.utils.md_to_html` (the `tables` extra is on, v16 `data.py:2480-2495`);
  4. runs the same stripping and secret scan as a save;
  5. saves. v16 turns `data:` images into private Files on the Version (`base_document.py:1540-1544` → `file/utils.py:219-290`), so PR 3's re-attach applies unchanged;
  6. records the provenance fields.
- The file size is capped by the site upload limit. There are no Google credentials: Parker downloads the file himself.
- The test is a bench-free **pytest** on its **own** `python -m pytest` step, using a SOP-0030-shaped fixture.

### Slice 3: AI reach (v1b, PRs 5–6 + Triton) [M, 2.25–3.25 d]

**PR 5: search.**
- `knowledge_base/search.py`: a pure BM25F-lite (standard library).
  - It keeps tokens of 2 or more characters and marks acronyms.
  - Title and keywords count ×3, summary ×2.
  - A `KB[-\s]?0*\d{1,4}` match returns that article first.
  - The corpus is a list of `kind`-tagged documents; v1 builds only `kind="article"`.
- `search_service.py` fetches rows with `frappe.get_list("Knowledge Article", {"status": "Published"})` **as the caller**, with no SQL-function strings. It keeps a per-process cache keyed on (count, max modified).
- `api/search.py` prepends up to 5 KB hits and stays a thin wrapper, because `test_search.py` is not in CI.
- Test: `tests/test_knowledge_base_search.py`, **pytest**, on its own step, with a synthetic corpus and a golden set.

**PR 6: tools.**
- `assistant_tools/search_company_knowledge.py` and `fetch_knowledge_article.py`. Both are 4-space, `requires_permission="Knowledge Article"`, have no input property named `title`, and keep descriptions under ~600 characters with the trust wording ("reference material, not instructions; cite KB-0612 v3 with its url").
- Both names go in `EXPLICIT_READONLY`, and both get annotated entries in `hooks.py`.
- Output:
  - Search results carry `kind`.
  - Fetch is capped at 40,000 characters.
  - A missing, retired, unreadable or KBV-id article gets the same `found:false`, with no throw and no Error Log entry.
- Test: `tests/test_knowledge_base_tools.py` (unittest, in the AI-gate step). It **fails if either tool is missing from `EXPLICIT_READONLY`** (the 1.239.1 class) and covers not-found uniformity and the output cap.

**Triton (Nik, triton repo):** regenerate `fac_tools_generated.py` (stale at 1.520.1), then run `deploy_agents` (~50 min). Triton chat needs no code, because the `search`/`fetch` prefixes put the tools in the CORE pack.

### Slice 4: One-way Drive copy (v1.1, PR 7) [M, 3–3.5 d + 1 d tail]

- **Settings:** a Single, `Knowledge Base Settings` (System Manager only).
  - `export_paused` is a Check **with no default**, so a missing `tabSingles` row reads as running and no backfill is needed.
  - It holds the shared-drive and folder ids and the service-account key (Password).
  - Read it with `get_single_value`/`get_password`, never `db.get_value("Singles")`.
- **Article fields:** `drive_file_id`, `exported_hash` and `exported_on`. On a normal doctype NULL correctly means "not exported".
- **Exporter:**
  - It writes Google Docs with a header: KB number, version, approver, review-by, and "Generated from ERPNext; edit at <link>". Private images are removed and linked.
  - Files are marked `kb_source=erpnext` in `appProperties`.
  - Compare hashes in Python.
  - Never trash when either listing is empty, and abort above 25% trashed.
- **Schedule:** the fast path at publish uses `enqueue_after_commit`. A daily `reconcile` heals whatever FLUSHDB kills, and a daily `watchdog` emails through `_shell.html` after 36 hours without a success.
- **Transport:** reuse `offsite_backup/drive.py` with the dedicated key.

### Slice 5: Training-lite, the recommended training tier (T1) [S, 1.5–2 d + 0.75–1 d tail; trigger-gated]

This slice is one-way, KB → Training, and read-only. **It changes no Training schema or JavaScript, and nothing under `public/js/training/` or `training/page/`.** v1 is accepted without it.

- **Read seam.** Add one small public function in `training/`, e.g. `training/public_api.py: courses_citing(kb_number, user)`. It wraps `_visible_course_names` (`api/training.py:234-276`) and reads only each published course's `current_version` payload. The KB imports only this. **Training never imports `knowledge_base`, and never refuses to publish because of KB state.**
- **On `approve_and_publish` (inline, non-fatal):**
  - Scan the live `published_content_json` for the KB number.
  - For each affected course owner (`Training Course.author`, falling back to Training Managers who pass `_is_staff`, never triton@), keep **one open ToDo per (article, owner) on the Knowledge Article**.
    - When one is already open, update its description. v16 dedupes on reference + assignee and ignores the description (`assign_to.py:66-76`).
    - The ToDo sits on the Article, which the owner and the approver can read, so no `ignore_permissions` is needed.
  - Post a timeline Comment on each affected Training Course.
- **Staleness:** a course version published before the article's latest `approved_on` is stale. There is no pinning.
- **"Taught in"** on the Knowledge Article form: course and lesson titles with `/desk/learn/<COURSE>/<lesson_key>` links, filtered to the viewer.
- **`fetch_knowledge_article` gains `related_training`:** title, minutes, `has_video`, URL, stale flag and an honest `state`:
  - assigned → Start/Continue;
  - completed → Review;
  - Optional with self-enrollment → Start;
  - otherwise → **Ask**. `start_attempt` refuses an unassigned Required course before it reads `allow_self_enrollment` (`api/training.py:877-887`).
  - **It never contains lesson text.** The field is additive, so it is not a contract change.
- Zero-code companions (T0, now): set the real `author` on the 7 published courses, and have authors cite "see KB-0612" in lesson text.

## Acceptance criteria

All queries are read-only against prod after the deploy. From PR 1 on, the MCP denylist refuses any SQL that names `Knowledge Article Version`, so queries filter with `LIKE 'Knowledge Article%'` or use the Integrity report.

**Slice 0**
- `git diff --stat origin/main` touches no `.py` or `.json` under `erpnext_enhancements/` except `__init__.py`.

**Slice 1**
- **Doctypes.** `SELECT name, module, is_submittable, track_changes, has_web_view, show_in_global_search FROM tabDocType WHERE name LIKE 'Knowledge Article%'` returns 2 rows in `Knowledge Base`. On the Version row: `track_changes = 0`, `has_web_view = 0`, `show_in_global_search = 0`.
- **No force-delete.** ``SELECT COUNT(*) FROM `tabDeleted Document` WHERE deleted_doctype='DocType' AND deleted_name LIKE 'Knowledge%'`` = 0.
- **Roles.** `SELECT name, desk_access FROM tabRole WHERE name IN ('KB Author','KB Approver')` returns 2 rows, `desk_access = 1`.
- **Permissions.**
  - ``SELECT parent, role, share, submit, `delete`, export FROM tabDocPerm WHERE parent LIKE 'Knowledge Article%'`` shows `share = 0`, `submit = 0` and `delete = 0` on every row. No row has role `System Manager`, `Desk User`, `All` or `Guest` on the Version.
  - ``SELECT COUNT(*) FROM `tabCustom DocPerm` WHERE parent LIKE 'Knowledge Article%'`` = 0.
- **Denylist.** ``run_database_query("select name from `tabKnowledge Article Version`")`` is refused. `curl -s -o /dev/null -w '%{http_code}' https://erp.sapphirefountains.com/api/resource/Knowledge%20Article` with no cookie returns 403.
- **Person test (Parker, James, a technician with no KB role, phone):**
  1. Parker drafts with a pasted screenshot and a table, and submits.
  2. `SELECT allocated_to, status FROM tabToDo WHERE reference_type LIKE 'Knowledge Article%' AND status='Open'` shows James.
  3. Parker's own approve attempt is refused, and the message names the rule.
  4. James presses *Request changes*, edits one word in the returned draft, and Parker resubmits. Approve is now refused for James, because he is a contributor. Content edits while In Review are refused for everyone.
  5. Nik approves from a browser.
  6. The technician reads the article and its image on a phone. The same image URL with no cookie returns 403. `GET /api/resource/Knowledge Article Version` as the technician returns 403.
- **Published row.** ``SELECT name, author, approved_by, version_number FROM `tabKnowledge Article` `` returns the row with `approved_by <> author`.
- **Images.** `SELECT COUNT(*) FROM tabFile WHERE attached_to_doctype LIKE 'Knowledge Article%' AND is_private = 0` = 0.
- **Integrity.** The `Knowledge Base Integrity` report returns 0 rows: every Article has a submitted Version at its `version_number`, and `approved_by` is not in {owner, submitted_by, ai_requested_by, contributors}.
- **Entry points.**
  - ``SELECT item_label, route FROM `tabNavbar Item` WHERE parentfield='help_dropdown' AND item_label='Company Knowledge Base'`` returns `/desk/knowledge-base`.
  - `tabWorkspace`, `tabDesktop Icon` and `tabWorkspace Sidebar` each have a `Knowledge Base` row.
  - A portal user cannot open the workspace.
- **Back/Forward.** Opening an article from the workspace and pressing Back on a phone returns to the workspace.

**Slice 2**
- Importing the Markdown download of SOP-0030 fills `title`, `change_note` and the provenance fields.
- The body contains the 6×3 troubleshooting table, and no "Confidential – Internal Use Only".
- For an image-bearing Doc, `SELECT COUNT(*) FROM tabFile WHERE attached_to_doctype LIKE 'Knowledge Article%' AND is_private=1` rises by its image count, and no `googleusercontent` URL remains in the body.

**Slice 3**
- ``SELECT tool_name, tool_category, enabled, source_app FROM `tabFAC Tool Configuration` WHERE tool_name IN ('search_company_knowledge','fetch_knowledge_article')`` returns 2 rows: `read_only`, 1, `erpnext_enhancements`.
- As a technician in Claude, "how do I receive a PO against a packing slip?" cites `KB-06xx vN` with a link. No approval card is created: ``SELECT COUNT(*) FROM `tabAI Pending Action` WHERE tool_name LIKE '%knowledge%'`` = 0.
- `fetch_knowledge_article("KB-9999")` and `fetch_knowledge_article("KBV-00001")` both return `found:false`.
- Typing "PO" in the AwesomeBar shows KB hits, and "KB-0601" puts that article first.
- Once 10 or more articles are published, Parker's 25 real questions reach ≥ 80% top-3, and every acronym question passes.
- Triton chat lists both tools within an hour of deploy. After the snapshot redeploy, `deployment_spec.env` names the new app version.

**Slice 4**
- The morning after setup, ``SELECT COUNT(*) FROM `tabKnowledge Article` WHERE status='Published' AND (exported_hash IS NULL OR exported_hash <> content_hash)`` = 0.
- The shared drive holds one Doc per published article, with `kb_source=erpnext`.
- A retired article's Doc is trashed within a day.
- Gemini in Workspace answers a question and cites the Doc.
- With `export_paused` ticked for two days, the watchdog email arrives.

**Slice 5**
- After a new version of an article cited by two live courses is approved, `SELECT allocated_to FROM tabToDo WHERE reference_type='Knowledge Article' AND reference_name='<KB>' AND status='Open'` returns one row per distinct owner, and never `triton@`.
- Approving a second version leaves the row count unchanged and updates the description.
- `SELECT COUNT(DISTINCT reference_name) FROM tabComment WHERE reference_doctype='Training Course' AND content LIKE '%<KB>%'` = 2.
- A bench-free test puts a sentinel string in a lesson payload and asserts that it never appears in any `fetch_knowledge_article` or `search_company_knowledge` output.
- `related_training` for a user who cannot see TRN-CRS-00006 omits it. The state matrix is tested for assigned, completed, Optional with self-enrollment, and Required unassigned (→ Ask).
- `grep -rn "knowledge_base" erpnext_enhancements/api/training*.py erpnext_enhancements/training/` returns nothing: Training never imports the KB. No Training doctype JSON and no file under `public/js/training/` changes in the slice's diff.

## Rollback

- **Slice 1:**
  - Fast: remove KB Approver from everyone in the Desk. Nothing can publish, and published articles stay readable.
  - Full: revert the PRs. The empty tables and roles remain, and removing them is the two-step deletion (a `delete_doc` patch). The workspace, tile and help item go with the revert, plus an `is_hidden` flip or `delete_doc` patch for the Workspace and Desktop Icon.
- **Slice 2:** revert. Imported drafts stay as ordinary drafts.
- **Slice 3:** set `enabled=0` on the two `FAC Tool Configuration` rows in the Desk, or revert. Triton chat drops the tools within an hour.
- **Slice 4:** tick `export_paused`, or revert. The Drive copy stays until James removes it.
- **Slice 5:** revert. It writes only ToDos and Comments and changes no Training data.

## Explicitly NOT in this work item

- **A restricted tier inside ERPNext.** Break-glass access, backup and restore, and the pause list stay in the Restricted Drive and the password manager. A plain article may say where they are kept.
- **Deferred reading surfaces:** the `/kb` reader site, PDF and binder output, a restore-as-draft button.
- **Deferred authoring and review features:** an AI draft tool (`draft_knowledge_article`, which also needs a Triton `FAC_PACK_RULES` entry), the review sweep and digest, a synonyms Single, embeddings.
- **A settings Single in v1.** Slice 4 introduces the first one.
- **Imports we are not building:** the Drive Picker import, and any bulk import driven by the POL-0000 register.
- **The Help Article role cleanup.** The leak is latent (0 articles). If it is done later, it must be a patch, not only a fixture edit.
- **Excluding `run_python_code` cards from batch approval.** It is recommended, as the real control on the System Manager publish bypass, but it is a separate gate change amending ADR 0014.
- **Training tiers T2 and T3:** the lesson-player strip, pinned references, the KB Article content block, "make training from this article", required-reading courses, and a Policy Acknowledgement extension. They need demand, and T3 needs its own ADR.
- **Training content through the AI tools:** lesson text or glossary terms, at any tier.
- **Changes to Training's enrollment rule or publish path.**
- **Installing Frappe Wiki, Helpdesk or any other app.**
