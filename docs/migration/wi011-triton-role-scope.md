# Scoping the Triton service account — TASK-2026-01583

**Status:** **wave 1 applied 2026-08-15 17:31 UTC** (see §7). Wave 2 pending a clean week.
**Account:** `triton@sapphirefountains.com` — enabled System User, no Role Profile, **90 roles at
analysis time, 88 now**.

Supersedes the deferral in [`wi011-apply-runbook.md`](wi011-apply-runbook.md) line 41
(*"kept as-is (active service account, 72 roles; per direction, not scoped)"*, 2026-07-24). It held
72 roles then and 90 now — **+18 in three weeks**. "Kept as-is" is not a stable state; the set
grows because nothing stops it growing.

---

## 1. The finding that reframes the task

**This account is not the identity the product runs as.** Triton's own client documents two modes
(`triton/backend/app/core/frappe.py:105-122`):

> **Per-user mode** — "Uses the user's OAuth2 access token so writes are attributed to that user."
> **System mode** — "Uses the shared `FRAPPE_API_KEY`/`FRAPPE_API_SECRET`. **Reserved for
> background jobs with no user context (sync, webhooks).** Writes go in as the system account, so
> do not use this on user-initiated actions."

It is enforced, not aspirational: `frappe.py:177-181` **raises** `FrappeAuthRequired` rather than
falling back to the shared key. Production agrees — the OAuth bearer tokens for the Triton client
are held by **eight human users** and this account has **zero**.

`Assistant Audit Log`, 8,904 rows, is the clincher:

| user | tool calls | window |
|---|---:|---|
| nikolas.bradshaw@ | 8,671 | 2026-05-18 → 2026-08-15 |
| logan.penrod@ | 146 | 2026-08-10 → 2026-08-13 |
| **triton@** | **86** | **2026-05-14 → 2026-05-25 only** |
| clegg.mabey@ | 6 | 2026-05-27 |

So the 90 roles are not buying the product its reach. When someone asks Triton a business
question, it runs under *their* roles. The service account has made no MCP tool call in 82 days.

**But the credential is live.** `last_active` was today, from `34.173.81.55` (GCP), and there are
Call Log rows as recent as 16:36 today. Anyone concluding "dormant, strip it" from the tool audit
alone would be wrong.

## 2. What it actually does

Two consumers, and neither needs anything like 90 roles.

**(a) Telephony webhooks — need zero roles.** `erpnext_enhancements/api/telephony.py` and
`api/call_intelligence.py` are `allow_guest=True`, authenticated by Twilio signature or a webhook
secret, and then call `frappe.set_user("triton@sapphirefountains.com")` at ten sites. That
`set_user` is for **attribution, not authority**: every write is `ignore_permissions=True`, and
every read is `frappe.get_all`, which v16 documents as *"Will **not** check for permissions"*
(`frappe/__init__.py:1384`, read from `origin/version-16`). Strip every role and this path is
unaffected.

**(b) REST calls under the shared key — permission-checked, and this is the whole risk surface.**
Grep-verified call sites: `core/voice_crm.py:33-36`, `api/v1/endpoints/voice.py:169,1213,2070`,
`core/portfolio_sync.py:449`, `core/sales_sync.py:704`, `core/sync_engine.py:34`,
`core/wiki_sync.py:295,374`. The sync loops run on a 15-minute cadence.

## 3. The measurement

Every earlier attempt at this — including two in this analysis — got the loss table wrong by
querying `tabDocPerm` / `tabCustom DocPerm` by hand. Three ways that fails, each producing a
**false clear**:

1. **Custom DocPerm replaces standard DocPerm wholesale** for a doctype. Read the wrong table and
   the grant set you compute does not exist.
2. **The `All` role is implicit** and in no `Has Role` row. Drop it from both sides and you invent
   losses. `ToDo` reads as "System Manager only" from a joined query and is in fact granted to
   `All` — that single mistake drove a wrong conclusion about System Manager twice.
3. **Only losses matter, at `(doctype, ptype)` grain.** "Grants read on 58 doctypes" says nothing
   about whether anything else also grants them.

[`scripts/role_permission_diff.py`](../../scripts/role_permission_diff.py) does it with
`frappe.get_meta`, which is what applies rule 1, and adds `All` to both sides.

A fourth trap, found while writing this: **run it over all eight ptypes, not six.** An earlier pass
omitted `report` and `export` and reported a loss table missing five entries. Those two are the
least interesting permissions on a service account and the easiest to leave out of a tuple.

Across **2,856 reachable grants**:

| removal set | roles | total measured loss |
|---|---:|---|
| Script Manager + Device Manager | 2 | `Server Script` — **nothing else** |
| the 65 individually-free roles | 65 | Delivery Trip, Request for Quotation, Subcontracting Order, Supplier Quotation |
| **recommended set (below)** | **60** | the 10 doctypes in §4 |

**65 of the 90 roles are individually free** — every permission they carry is carried by another
held role. Only 25 are the sole grant for anything, and that map is the real structure of the
account:

`Script Manager`→Server Script · `Fleet Manager`→Vehicle · `Workspace Manager`→Workspace ·
`Support Team`→Issue · `Chat User`→Chat Room · `Quality Manager`→Quality Inspection ·
`HR User`→Designation · `HR Manager`→Holiday List · `PO Creator`→Purchase Order ·
`Knowledge Base Editor`→Help Article · `Newsletter Manager`→Newsletter · `Item Manager`→Item/Brand/UOM …

Note what this kills: **"System Manager covers everything" is false on this site.** Custom DocPerm
means System Manager holds no row at all on Opportunity, Project, Issue and many others.

## 4. Recommendation — remove 60, keep 30

Remove the 65 individually-free roles **minus six structural keeps**, **plus `Script Manager`**.

**The six structural keeps are roles the DocPerm diff says are free and that are not**, because
their gate is Python, not permissions — this is the category that makes a clean diff dangerous:

- **`System User`** — `User.set_system_user()` recomputes `user_type` from whether *any* held role
  has `desk_access`. Lose the last one and the account silently becomes a Website User and
  `on_update` calls `clear_sessions(force=True)`.
- **`Assistant User`, `Assistant Admin`** — FAC gates assistant access on
  `{System Manager, Assistant Admin, Assistant User}` in `utils/permissions.py:83`, a role-literal
  check no DocPerm analysis can see.
- **`Raven Admin`, `Raven User`** — Raven's app was not opened; it is a chat app and this account
  is a live chat participant.
- **`Projects Manager`** — in `sapphire_maintenance` `VIEW_ALL_ROLES`, which is a
  `permission_query_condition`. Those *narrow silently*: the sync just returns fewer rows.

### The complete measured cost of removing those 60

Verified against the live site, all eight ptypes, 2,856 grants — this is the whole list:

| doctype | what is lost |
|---|---|
| `Server Script` | read, write, create, delete, report, export |
| `Delivery Trip` | everything |
| `Subcontracting Order` | everything |
| `Supplier Quotation` | everything |
| `Request for Quotation` | everything **except read** |
| `Operation` | report only |
| `Delivery Note`, `Purchase Order`, `Material Request`, `Cost Center` | export only |

The four full losses appear **nowhere** in the account's enumerated call surface. `Operation:report`
and the four `export` losses are desk capabilities — a REST client neither runs a query report nor
clicks Export. `Server Script` is the point of the exercise: that capability is what the 2026-08-02
intruder used, `server_script_enabled` is `0`, and this repo's own `chat/invoke/triton_link.py`
already lists `Script Manager` in `UNSAFE_BOT_ROLES`.

**Reports are a second permission system and must be counted separately.** A Report carries its own
`roles` child table, and `Report.is_permitted()` has **no System Manager short-circuit** — so a
report can become unrunnable for an account that still holds every DocPerm it needs. Counted
directly: of 219 reports (211 role-gated), this account can run **210 before and 200 after**. The
10 lost:

> Item-wise Purchase History · Procurement Tracker · Product Bundle Balance · Purchase Analytics ·
> Purchase Order Analysis · Purchase Order Trends · Subcontract Order Summary · Subcontracted Item
> To Be Received · Subcontracted Raw Materials To Be Transferred · Supplier Quotation Comparison

The same procurement/subcontracting corner as the DocPerm losses, which is the coherence check:
both fall out of dropping `Purchase Manager`, `Purchase User`, `Stock User` and `External
Contractor`. In practice this costs nothing, because reports are run through the MCP
`generate_report` tool **as the asking human**, and this account has not made an MCP call since
May. But it is a real capability change and it is listed here rather than discovered later.

Also verified zero, each with a direct query, because these are what "approver" roles would have
meant: `Employee.leave_approver`/`expense_approver` naming this account (0), open ToDos allocated
to it (0), Assignment Rule membership (0), Notification recipients (0), Workflow Document States
requiring `Expense Approver`/`Leave Approver`/`PO Approver` (0).

### Kept (30)

`System User`, `System Manager`, `Assistant User`, `Assistant Admin`, `Raven Admin`, `Raven User`,
`Chat User`, `Support Team`, `Sales User`, `Sales Manager`, `Sales Master Manager`,
`Accounts User`, `Accounts Manager`, `Projects User`, `Projects Manager`, `Maintenance User`,
`Maintenance Manager`, `HR Manager`, `HR User`, `Item Manager`, `Stock Manager`,
`Purchase Master Manager`, `PO Creator`, `Manufacturing User`, `Quality Manager`,
`Website Manager`, `Knowledge Base Editor`, `Newsletter Manager`, `Fleet Manager`,
`Workspace Manager`.

`System Manager` stays **for now** and is the obvious next target: it is what gates FAC's
`run_database_query` and `run_python_code`, it disables FAC field redaction
(`core/security_config.py:406`) and it bypasses the per-tool role allowlist. Removing it means
building a purpose-named role with explicit DocPerms first. That is a separate change.

## 5. Cutover

**Direct `Has Role` deletion, not a Role Profile.** A profile is one-shot and all-or-nothing:
`User.populate_role_profile_roles` (v16 `user.py:277`) does
`self.roles = [r for r in self.roles if r.role in new_roles]` on **every save**, so attaching one
drops everything not in it, permanently, and thereafter role edits via the API are silently
discarded. Direct deletion is per-role, incremental, and reversible one row at a time. This
account has no profile today, which is what makes that available.

1. **Snapshot first.** `select role from "tabHas Role" where parent = <account>` → keep the 90.
   The rollback is re-adding rows; there is nothing else to restore.
2. **Wave 1 — today, 2 roles:** `Script Manager`, `Device Manager`. Highest security value, and
   both fail **loudly** (an interactive `PermissionError`) rather than by silently narrowing a
   background query. Ship alone so any incident in 72h is unambiguously attributable.
3. **Wave 2 — after a clean week, 58 roles:** the remainder of the set above.
4. **Watch:** Error Log for `PermissionError` naming the account; Call Log and Contact row counts
   staying non-zero on the usual cadence; the portfolio/sales sync loops. The dangerous failure is
   a `get_list` returning **fewer** rows, which raises nothing — so watch counts, not errors.

### Removed (60)

Wave 1 (2): `Script Manager`, `Device Manager`.

Wave 2 (58): `AI Auditor`, `Academics User`, `Accounts Receivable`, `Agent`, `Agent Manager`,
`Analytics`, `Auditor`, `Blogger`, `Call Center Supervisor`, `Customer`, `Dashboard Manager`,
`Delivery Manager`, `Delivery User`, `Design Team`, `Dispatch User`, `Executive Team`,
`Expense Approver`, `External Contractor`, `Finance Team`, `Fulfillment User`, `HR Team`,
`Helpdesk Contact`, `Inbox User`, `Insights Admin`, `Insights User`, `Interviewer`,
`Inventory Clerk`, `Knowledge Base Contributor`, `Leave Approver`, `Maintenance Supervisor`,
`Manufacturing Manager`, `Marketing Manager`, `Marketing Team`, `Operations Team`, `PO Approver`,
`Prepared Report User`, `Product Engineer`, `Production Team`, `Project Manager`, `Project User`,
`Purchase Manager`, `Purchase User`, `Report Manager`, `Sales Team`, `Stock User`, `Supplier`,
`TP Agent`, `TP Manager`, `Training Author`, `Training Learner`, `Training Manager`, `Translator`,
`Travel Coordinator`, `Wall Display`, `Water Engineer`, `Wiki Approver`, `Wiki Manager`,
`Wiki User`.

Note `Customer` and `Supplier` in that list: portal roles on a System User, which is a category
error rather than a permission. Also `Expense Approver` and `Leave Approver` — per the zero-counts
above, nothing on the site names this account as an approver, so they granted an authority no
workflow ever routed to it.

## 6. What was not checked

Stated plainly, because a role nobody examined is not a cleared role:

- **Wiki, Insights and Helpdesk app internals.** Their roles are in the removal set and the diff
  says their DocPerm access survives via retained roles — but none of those apps was opened to
  look for role literals. `wiki_sync.py` runs in **system mode**, so Wiki is the one to check.
- **`Chat Settings.alert_post_as`.** Unreadable through the generic tools — the chat denylist
  refused the query, correctly. If it names this account, `Chat User` is load-bearing:
  `governance/alert_delivery.py:53` deliberately runs **without** `ignore_permissions`. `Chat User`
  is kept regardless, so this blocks nothing.
- **Whether `poseidon-voice-gateway` shares this key.** It reads `FRAPPE_API_KEY` from its own
  environment and hits only `allow_guest=True` telephony endpoints, so it needs no roles either
  way — but which account that env var resolves to was not confirmed.


## 7. Wave 1 — applied 2026-08-15 17:31 UTC

`Script Manager` (`Has Role` row `0mkpoh1mbv`) and `Device Manager` (`qsq81fpl9n`) removed via a
User save in patch mode. **90 → 88.**

Verified immediately after, against the 90-role snapshot taken before the change:

| check | result |
|---|---|
| roles removed | exactly `Script Manager`, `Device Manager` |
| roles unexpectedly added or lost | **none** |
| capability lost (all 8 ptypes, 2,856 grants) | `Server Script` r/w/c/d/report/export — **and nothing else** |
| `enabled` / `user_type` / `role_profile_name` | `1` / `System User` / empty — unchanged |
| `api_key` / `api_secret` still set | **yes, both** |
| Error Log rows since the change | 0 |
| permission errors naming the account today | 0 |

**`last_active` moved to 17:31:27 — twenty seconds after the change.** The credential
authenticated successfully post-revoke, which is the strongest immediate evidence available that
nothing broke.

`Device Manager` cost **literally nothing**, as predicted: the MDM webhook path runs as
`mdm@sapphirefountains.com`, not this account.

The site holds 30 `Server Script` documents. This account can no longer read, write or create any
of them — which was the point.

**Before saving the User, `on_update` was checked on v16 rather than assumed.** Two things that
would have made this an outage do not happen: `clear_sessions(force=True)` fires only
`if self.has_value_changed("user_type")`, and `user_type` is unchanged because `System User` and
many other desk roles remain; and `api_key`/`api_secret` are never touched by a save — key
generation lives in the separate `generate_keys()` method. Both were confirmed above.

**Rollback:** re-add the two roles. The account has no Role Profile, so direct `Has Role` rows
stick. The full 90-role snapshot was captured before the change.

**Wave 2 gate:** a clean week — no `PermissionError` naming the account, and Call Log / Contact
creation continuing at its usual cadence. Watch counts, not errors: the dangerous failure mode is
a `get_list` returning **fewer** rows, which raises nothing.
