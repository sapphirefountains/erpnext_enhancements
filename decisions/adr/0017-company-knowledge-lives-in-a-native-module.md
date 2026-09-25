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

**Knowledge Article** is the approved, published text and nothing else. It is named by KB number (`KB-{block}{01..99}`, following the POL-0000 department blocks, with `00` reserved for each block's index). Every field is read-only and changes only under a publish-action flag. All staff read it through `Desk User`, which v16 grants to System Users only. It has no web view, no Guest access, and is not in global search.

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

**AI can only draft, and in v1 it cannot even do that through a dedicated tool.**

### 3. Two read-only FAC tools, with frozen names

`search_company_knowledge` and `fetch_knowledge_article` are the contract. Their names are frozen, so the storage behind them stays swappable.

**How they run:**
- Both run as the caller over `frappe.get_list`, so permissions apply before ranking.
- Search is an in-app BM25 that keeps two-letter tokens and jumps to an exact KB number.
- Both are in `EXPLICIT_READONLY`, with a test that fails if they are not.

**What they return:**
- **Search results always carry `kind`**, which is `"article"` in v1. A later result type is therefore an added field, not a changed contract.
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

**The integration tier adopted is "Training-lite" (T1), and it is trigger-gated.** It is built when at least one published course cites three or more published articles. Until then (T0), search results carry `kind`, and course authors cite KB numbers in lesson text.

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
