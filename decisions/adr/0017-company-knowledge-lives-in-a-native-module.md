# 0017. Company knowledge lives in a native Knowledge Base module

- **Status:** Accepted (2026-09-25)
- **Date:** 2026-09-24 (proposed); accepted 2026-09-25, when Nik decided "we do our own"
- **Work item:** [WI-080](../../work-items/WI-080-company-knowledge-base.md)

> **On acceptance (2026-09-25).** Nik chose the native build, so Frappe Wiki is dropped rather than
> held as a fallback, and Parker's Phase 0 editor test no longer decides between the two: it decides
> only whether the Markdown import (WI-080 PR 4a) is built. The fourth KB Approver is Lisa Symanski,
> approved by James; because she holds a Role Profile, she gets the role through a one-role
> "KB Approvers" profile. The text below was edited where those facts appear (the roles bullet in §2,
> the Quill consequence, the follow-ups and the revisit list) and is otherwise as proposed.

> **Amendment (2026-09-28, WI-080 Slice 3 redesigned AI-first).**
> - **Frozen tool names:** `search_company_knowledge`, `fetch_knowledge_article`, `list_company_knowledge` (new, a table of contents) and `draft_knowledge_article` (new).
> - **The three read tools** run as the caller, over published articles only. Search first takes the caller's readable set, so permissions still apply before ranking; idf is computed over the whole published corpus.
> - **`draft_knowledge_article`** is the dedicated drafting tool that §2 deferred. It is an app write in `APP_MUTATING`, in the Medium risk band:
>   - every call that passes its precheck becomes an AI Pending Action card; the rest are refused with no card. The tool refuses to run outside a confirmed card, and runs only when the person confirming it is the person who asked;
>   - it creates a **Draft**, either a new article or a revision with nothing open, with `ai_drafted = 1` and `ai_requested_by`;
>   - with `submit_for_review`, the same card then submits that Draft for review, through the same rules and the same write as the Submit for Review button. The requester is recorded as its submitter, so `approval_problems` refuses them as its approver, as it refuses every approval made under a gate card;
>   - it never approves, publishes, requests changes, withdraws, discards, retires or confirms, and never returns draft text.
>
>   The denylist is unchanged. No text a person wrote in a draft reaches an assistant. The AI's own proposal stays in the gate's records until retention purges them.
> - **Articles gain a `kind`: Policy, Process or SOP**, the company document register's three types. It is required to submit, not by the schema. The result-type field that §3 and §6 called `kind` is renamed **`result_type`** (`"article"`). No consumer existed.
> - **Triton** is offered the three read tools, not the drafting tool.
> - **Published articles are also mirrored as Markdown into the company's private knowledge repo** (WI-080 Slice 6), pulled by that repo from a read-only endpoint every 6 hours. **ERPNext holds no GitHub credential.**

> **Amendment (2026-09-29, numbering by kind).** Nik: "Also I feel like KB-#### is too limiting"; he chose
> "SOP-06-0001 by kind".
> - **The format** is `<PREFIX>-<DD>-<NNNN>`: the kind's prefix (`POL` Policy, `PRO` Process, `SOP` SOP), the
>   two-digit department block (`00` to `09`), and a zero-padded sequence from `0001`. The number says what
>   the document is, as the Drive register's numbers do; the two are separate series (the register's
>   `SOP-0601` is never read as an article number).
> - **The scope** is `(prefix, department)`: each counts on its own, one more than the highest number taken,
>   never the lowest gap, never `0000`. "Taken" is every article's name **and every number a version still
>   names as its article**, so an article removed past the ORM does not give its number away again. The
>   articles are read `FOR UPDATE`, as before; no naming series (`tabSeries` is shared across doctypes and
>   resettable from the Desk).
> - **A published article's number never changes, and a number is never reused.** The number carries the
>   kind and the department, so neither changes after the first publish. To reclassify or move an article,
>   a new article is published and the old one retired with a pointer to it, so old citations still
>   resolve (until a "Replaced by" field lands, the pointer is the retire reason). The freeze holds in four
>   places with one message: the form (read-only on a revision), the version's save, submit and approve,
>   and the Article row itself, whatever flag is set.
> - **The retired `KB-` format maps to nothing.** No number in it was issued (prod had no article and no
>   version), and it holds no kind. Fetch answers it with the same `found: false` as every other miss; that
>   message now says what a number looks like, for every miss, so it leaks nothing. Search adds a
>   `problems` hint for it.
> - **The frozen names and payload keys are unchanged** (`kb_number` included): only the values' format
>   changed, which no consumer had seen. The tools' descriptions and examples changed, so Triton's frozen
>   snapshot is regenerated after the deploy. The mirror's `schema` stays 1.
>
> §1 and §3 are edited in place for the format; §6's "cite KB numbers" now means article numbers.

## Context

**The goal is continuity.** Nik holds most of how Sapphire runs in his head, and he is the only engineer. Parker, the purchasing agent and inventory clerk, is to become his backup. Parker is not an engineer, and James approves. The knowledge base therefore has to be something:
- a non-engineer can write;
- a second person approves;
- every staff member can read on a phone;
- every AI tool Sapphire uses reads from the same place, so that an answer from Claude and an answer from Triton cite the same approved text.

**Some material is excluded from that goal on purpose:** break-glass access, backup and restore, and the pause list. It exists for the day ERPNext is down or compromised, so it cannot live in ERPNext. It stays in the Restricted Drive and the password manager.

The constraints that shaped the decision, measured 2026-09-24 (prod read-only, Frappe `origin/version-16`, FAC 3.0.0):

| Constraint | Fact |
|---|---|
| **AI reach: FAC** | Claude (web, desktop, mobile), Claude Code and Triton chat reach ERPNext only through the FAC MCP server, as the signed-in user. Knowledge an agent must use has to be a **tool**. FAC Skills are served only as MCP resources; all 31 have `use_count` 0. claude.ai drops MCP server instructions, so trust wording has to live in tool descriptions |
| **AI reach: Gemini** | Gemini in Workspace **cannot call a custom MCP server** from a work account. Google documents custom apps as personal-account-only. It reads Drive natively |
| **AI reach: Triton agents** | Deployed agents use a frozen tool snapshot, now at 1.520.1 while prod runs 1.534.1. A new tool reaches them only after Nik regenerates it and runs `deploy_agents` (~50 min). Triton chat picks up `search*`/`fetch*` tools within its 1-hour cache, with no code (`tool_packs.py:113-116`) |
| **Search** | Prod `__global_search` has `ft_min_word_len=4`, so "PO", "QBO" and "SOP" are never indexed. FAC's own `search`/`fetch` ride on it |
| **Gate** | AI writes become AI Pending Action cards (ADR 0006, 0014, 0016), and **Nik batch-approves every pending card.** So no control may depend on a person rejecting a card |
| **Who can grant roles** | 19 enabled System Users, one human System Manager (Nik). No one holds User Manager |

**Options considered:**

- **Drive-canonical** (Google Docs in a shared drive, plus two FAC tools over a mirror). This was the first recommendation: Gemini reads it natively, and Parker already drafts in Docs. It lost when Nik chose ERPNext as home. Drive enforces no second approver, and Claude and Triton would read a mirror of it anyway.
- **Frappe Wiki v3** (v3.2.1). It has the best in-ERPNext editor and a page tree. Against that:
  - five unsafe defaults: a roleless space is portal-readable, uploads are public, Wiki User gives portal users Desk access, routes can shadow site pages, and a frontend build runs on every deploy;
  - self-merge, and Desk/API edits that skip review;
  - no history browsing or restore;
  - upgrades need server shell access.
  
  The two tools and a Drive copy are needed either way, so it would save about 4–6 days.
- **Confluence Cloud.** It is the only SaaS KB that Gemini in Workspace can read (through the Rovo connector). But native Google Docs already give Gemini that access, and Confluence adds a vendor account Parker would inherit and a second register to reconcile.
- **FAC Skills.** Never read (see above), and no approval step.
- **Core Help Article.** 0 rows. It has `allow_guest_to_view=1`, so published articles leak to guests through `web_search` and `sitemap.xml`. It has no draft/published split, no approver and no KB metadata. Frappe Helpdesk's HD Article is the same shape inside an app that is not installed.
- **Build our own**, narrowly. About 3–4 days more than the Wiki up front; private by construction, review enforced in code, deployed through CI.

Training is the nearest thing Sapphire has to structured knowledge today: 7 published courses and 107 live lessons. **None of those lessons cites a KB number.** Every live version was self-published by one person, and Training records no lesson-level AI provenance.

## Decision

### 1. A native module with a published snapshot and a separate version doctype

A new `Knowledge Base` module holds two doctypes.

**Knowledge Article** is the approved, published text and nothing else. It is named by its article number, `<PREFIX>-<DD>-<NNNN>` (`SOP-06-0001`: the kind's prefix, the POL-0000 department block, a sequence per kind and department; amended 2026-09-29, where it had been `KB-{block}{01..99}`). The number never changes, so neither do the article's kind and department. Every field is read-only and changes only under a publish-action flag. All staff read it through `Desk User`, which v16 grants to System Users only. It has no web view, no Guest access, and is not in global search.

**Knowledge Article Version** is submittable and holds drafts and the immutable history.
- Only **KB Author** and **KB Approver** can open it.
- No reader role and no System Manager has a DocPerm on it.
- `share` is 0 on every row, because v16 `assign_to.add` would otherwise share a draft with an assignee who cannot read it.
- `track_changes` is off, because core `Version` rows are readable by System Manager, which includes the `triton@` service identity.

**Drafts can never leak through a reader path because they are not in the reader's doctype.** Field-level hiding was rejected: FAC's `get_document` checks doctype permission, not permlevel. The Version doctype is also on the gate's denylist and in `NEVER_EXEMPT`, as defense in depth, not as the barrier.

**The body is one Quill Text Editor field**, with `body_md` derived for AI at publish. Training Content Blocks were rejected as the body:
- ~98% of published training blocks are Rich Text or Callout;
- no non-engineer has authored in the block grid;
- blocks would tie the KB schema to Training's vocabulary.

**On save and publish:**
- Style attributes and color/size classes are stripped, so hidden text cannot reach AI.
- Secret-shaped strings are refused, scanned after images are removed.
- Every attached file is private.
- Publishing moves the draft's image Files onto the Article, which keeps their links valid.

Nothing is ever deleted.

### 2. Approval is enforced in code, from a browser, by someone else

A version is published only by `approve_and_publish`. The server refuses unless all of the following hold:
- the approver holds KB Approver;
- the approver is **not** the owner, submitter, a contributor (anyone who changed content) or the person who asked an AI to draft it;
- the request comes from a signed-in browser session, not a token and not the AI gate;
- the version is In Review, and unchanged since the approver opened it.

**Where the checks run:**
- The checks live in the controller's `before_submit` **and** `on_submit`. The first is skipped by `flags.ignore_validate`; the second is not. This does not stop a determined System Manager: a batch-approved `run_python_code` card can set flags or write past the ORM.
- An **Integrity report** detects any Article whose approver is in the forbidden set or that lacks a matching submitted Version.
- Excluding `run_python_code` from batch approval is the real control, and is a follow-up to ADR 0014.

**Roles and notices:**
- Roles are granted in the Desk: directly for a user with no Role Profile, and through a one-role "KB Approvers" Role Profile for a user who has one, because this site rebuilds a profiled user's roles from their profiles on every save.
- They are seeded by a `post_model_sync` patch, not a fixture, and so is that Role Profile.
- Review notices are ToDos, raised inline, with the existing branded ToDo notification.

**An AI can draft, and submit its own draft for review, only through `draft_knowledge_article`, in a card the person who asked confirms. Approving and publishing stay a named person's act in a browser.** (amended 2026-09-28)

### 3. Two read-only FAC tools, with frozen names

`search_company_knowledge` and `fetch_knowledge_article` are the contract. Their names are frozen, so the storage behind them stays swappable.

**How they run:**
- Both run as the caller over `frappe.get_list`, so permissions apply before ranking.
- Search is an in-app BM25 that keeps two-letter tokens and jumps to an exact article number, however it is written (amended 2026-09-29).
- Both are in `EXPLICIT_READONLY`, with a test that fails if they are not.

**What they return:**
- **Search results always carry `result_type`**, which is `"article"` in v1; `kind` is the article's kind (amended 2026-09-28). A later result type is therefore an added field, not a changed contract.
- Results carry the version, approver and date, `ai_drafted`, `review_overdue` and a URL. They carry no "trusted" label.
- A missing, retired, unreadable or draft id returns the same `found:false`.

**Contract changes:** adding fields is allowed. Renaming or removing one is a breaking change that needs a Triton snapshot redeploy.

### 4. Gemini and outages read a one-way Drive copy (v1.1)

- **What gets written:** each published article is exported as a Google Doc into a dedicated "Sapphire Knowledge Base" shared drive. A dedicated service account writes it.
- **Healing:** a nightly reconcile keyed on the content hash heals what the deploy's FLUSHDB kills, and a watchdog emails when it stalls.
- **Direction:** ERPNext → Drive only. Edits happen in ERPNext, and only James and Nik may change the drive's sharing.

### 5. Restricted material stays out of ERPNext

There is no restricted tier in the module. A plain article may say where a restricted item is kept and who to ask.

### 6. Training integrates one way, as pointers, and only when there is something to point at

**The integration tier adopted is "Training-lite" (T1), and it is trigger-gated.** It is built when at least one published course cites three or more published articles. Until then (T0), search results carry `result_type` (amended 2026-09-28), and course authors cite article numbers (`SOP-06-0001`) in lesson text.

What T1 does when a new article version is approved:
- It scans live course payloads for the number.
- It keeps one open ToDo per (article, course owner) **on the Knowledge Article**.
- It comments on each affected course.
- The Article form shows "Taught in".
- `fetch_knowledge_article` returns `related_training` pointers (title, link, and an honest start/review/ask state).

The rules that hold at every tier:
- **The AI tools never return lesson text or glossary terms as company knowledge.** Lessons are single-publisher and may be AI-written, and a "not reviewed" label does not survive summarization.
- **Training never imports the KB and never refuses to publish because of KB state.** The KB reads Training through one small public function.
- **A KB change only ever produces a human decision**: never an auto-publish, never a publish card. A Material republish retakes the whole course for everyone.
- **If a lesson ever embeds article text, it is a snapshot taken at course publish, never a live view**, so completions keep meaning what was read. That tier (T3) needs its own ADR.

## Consequences

**Positive**
- **Private by construction and locked by tests.** A bench-free test pins the flags and the whole DocPerm matrix, so a permissive change fails the build.
- **Review is enforced, not requested.** A self-approval is refused, with a message that says why.
- **One answer everywhere.** People (AwesomeBar), Claude, Claude Code and Triton get the same ranking over the same approved text. Gemini gets the same text, a day behind, from Drive.
- **Nothing to install and nothing new to upgrade.** Everything ships through PR → CI → deploy, and any engineer with merge rights can maintain it. Upkeep is about 2–3 days a year.
- **The tool contract survives a change of backend.** If Sapphire later moves to the Wiki, the names and fields stay.

**Negative**
- **Parker writes in Quill, not TipTap.** There is no slash menu, callouts or autosave. This is the Wiki's real advantage. Nik accepted it on 2026-09-25; Parker's Phase 0 test now decides only whether the Markdown import is built.
- **More code for one engineer to own.** v1 is 8.5–11 engineer-days of build, 12.5–17.5 with the fix tail this repo sees on live features. The Drive copy adds 4–4.5. The repo's history suggests a ~50% chance that one of the first deploys breaks.
- **The riskiest behavior can only be tested on prod.** That covers private-image access after re-attach and the permission rows: CI has no bench job and the test VM is down. Every PR carries read-only verification queries.
- **The denylist has side effects.** It refuses any MCP SQL that names the Version doctype, including the operators' own integrity checks, which therefore live in a Script Report.
- **Approval continuity.** With approver ≠ author and three people, losing two stops publishing, and only Nik can grant roles.
- **Content dominates the cost.** The first 40 articles take about 46–62 person-hours, and James's review time sets the calendar.

**Follow-ups**
- Exclude `run_python_code` cards from batch approval, as an amendment to ADR 0014.
- ~~Name a 4th KB Approver.~~ Done 2026-09-25: Lisa Symanski, approved by James, through the "KB Approvers" Role Profile.
- Add "grant or revoke KB roles as Administrator" to the Restricted Drive runbook. The runbook does not exist yet, so the step is tracked as ERPNext task TASK-2026-02297 ("Continuity 3: write the restricted-access runbook") on PRJ-00580.
- Regenerate the Triton agent snapshot and run `deploy_agents` after the tools ship.
- Build the markdown import if Parker's export test shows that images arrive embedded and tables survive; skip it if they break.
- The Help Article role cleanup: the leak is latent (0 articles). If done, it must be a patch, because Role Profile propagation is queued and FLUSHDB kills it.
- **Revisit:**
  - if the scope grows toward the /kb site, digests, synonyms and deep Training embedding (~25 days), where the Wiki is cheaper;
  - ~~if Parker rejects the editor~~ (withdrawn on acceptance: the native build is decided);
  - if Gemini in Workspace gains custom-tool access, which would remove the Gemini reason for the Drive copy but not the outage reason.
