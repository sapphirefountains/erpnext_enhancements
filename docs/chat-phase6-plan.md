# Google Chat Phase 6 — divergence register, findings and plan

**Status:** opened 2026-08-13. Working document, not a decision record. When the governance
decisions below are answered and the work lands, its conclusions move into
`decisions/adr/0009-addendum-2-phase-6-decisions.md` and this file is deleted.

Phase 6 is governance, audit, retention, hardening and rollout — the phase that makes the chat
system safe to run on a real company. Its prompt (`PHASE_6_governance_audit_rollout.md`) was
written before Phases 1–5 were implemented, and §6 step 1 asks for exactly this document first:
*"write down the divergences between this prompt and the ADR, and resolve each in the ADR's
favour."*

There turned out to be twelve.

---

## 1. Where the prompt and the shipped code disagree

**The ADR and the code win; the prompt is corrected here.** Each row below would, if followed
literally, produce a duplicate, a dead read, or a second implementation of something load-bearing.

| # | The prompt says | The code says | Following the prompt would |
|---|---|---|---|
| D1 | `Chat Audit Log` exists (Phase 1) and §5 forbids adding a third audit DocType | It does not exist. ADR **X-1** rejected it on the record | Leave §4.A.4 (role grants), §4.E.4 (hard deletes) and §4.F (what was destroyed) with **no table to write to**. See [decision E-1](#3-decisions-only-a-human-can-make) |
| D2 | Phase 6 *adds* a `chain_hash`, its serialization, its computation and a verifier | `chat/audit.py` has all four, hardened through two production incidents, bench-free coverage green in CI | **Fork the chain.** Two writers signing the same head make `verify_chain` report permanent breaks across the whole log, indistinguishable from tampering. Consolidate and re-export; do not re-implement |
| D3 | Test T5-3 fences every SQL literal in the chat package into `gate.py` | That scan covers three *index* tables. `Chat Message` and `Chat Room Member` are explicitly excluded, and the addendum records that the sentence was never implementable | Assume a fence that is not there. The real fence is the raw-SQL guard, satisfiable with a justified triple |
| D4 | A user-facing delete **moves** the body off the live row and clears it | `is_deleted = 1` rows **retain** `text` (ADR §F.6.5, argued in five places) | Build the tombstone expand against a schema that does not exist |
| D5 | Audit the escape hatch from a permission hook | That design shipped in v1.268.0 and was withdrawn after four defects; a green CI test now **fails the build** if a hook writes an audit row | Turn a green test red while re-introducing all four defects |
| D6 | `deleted_origin` | `deletion_source` | Write a field that is not there |
| D7 | `Chat Settings.chat_enabled` / `max_attachment_bytes` | `enabled` / `attachment_byte_limit` | Read `None`, silently |
| D8 | Relay states `Queued / Sending / Retrying / Cancelled` | `Pending / In Progress / Failed / Dead / Skipped` — there is no `Retrying` | Alert on states that never occur, and miss the ones that do |
| D9 | Presence keys `chatpresence:*` | `ee_chat_presence:<user>`, one hash per user with a field per tab | Count zero presence keys and alert on it |
| D10 | `replied_message_details` is a stored-XSS carrier to review | Never built. Threading is `parent_message` / `thread_root` links | Review a surface that does not exist |
| D11 | The MCP denylist is "in `triton`" | It is `assistant_tools/_gate.py`, checked against the filesystem by set equality. **Triton has no denylist of any kind** | Add DocTypes to a constant in the wrong repo |
| D12 | `<app>.chat.ops.health.report`, `<app>.chat.invoke.webhook.handle` | `…chat.health.report`, `…chat.gchat.webhook.handle` | Document commands nobody can run |

**One correction is in the other direction, and it matters more than the rest.** §4.G.3 says child
tables *may* be exempted from the both-hooks rule. They **must** be: a `permission_query_conditions`
entry keyed on an `istable: 1` doctype is dead code, because the lookup uses the *parent*'s name
(`frappe/database/query.py:275`), and `frappe.has_permission` short-circuits child tables to the
parent before any hook runs (`frappe/permissions.py:120`). Write the exemption as a rule with a test,
not as an allowance.

---

## 2. What the hardening pass found

Thirteen findings. Four are live now; the rest become live the day `admin_oversight_role` is filled —
and the reason to fill it is the oversight viewer, which is this phase.

**The pattern is worth naming before the list, because it recurs:** in both the URL case and the
audit case, *the path that had been reviewed was the new one, and the path that was live was not.*

### Live today

| # | Finding | Status |
|---|---|---|
| **B-6** | `renderSources()` put model and tool output straight into `a.href` with no scheme check. This is the legacy sources row — the path taken on every turn with no citation manifest, i.e. the normal one. A `javascript:` URL in a tool result executed on click, on any Desk page, under the reader's session. The manifest row twelve lines below has called `isSafeUrl` since Phase 3 | **Fixed, v1.282.3** |
| **B-7** | `isSafeUrl` tested prefixes. `/\evil.example` starts with `/`, does not start with `//`, and every browser resolves it to `http://evil.example/`. Confirmed against Node's WHATWG parser, not inferred | **Fixed, v1.282.3** |
| **B-4** | `enroll_org_units` and `start_org_mirror` have no role gate, no `require_session`, no role test anywhere in the file. Any authenticated System User can create `Chat Room` rows and open a provisioning run; `dry_run` defaults to 1 but is caller-supplied, so real Google spaces are a query string away | Open |
| **B-5** | 42 of 43 chat endpoints declare no `methods=`, so every state-changer accepts GET — CSRF-reachable from any page | Open |

### Live the day the oversight role is configured

| # | Finding |
|---|---|
| **B-1** | The hatch is **`ptype`-blind**, and `require_room()` calls it directly rather than through the permission stack. The function's docstring says a write "is already refused above us" by `Chat Room`'s read-only DocPerm — true of the stack, false of the one caller that skips it. `send_message`, `mark_read`, `set_typing` and `prepare_upload` are gated only by `require_room`. §4.A.3's "read and nothing else" is violated on day one |
| **B-2** | `history.get_messages`, `get_thread` and `get_message_context` do not import `audit` at all. **I9 is false today** — an oversight holder, or Administrator, reads any room's full transcript with zero audit rows. The build-failing rule meant to catch this keys on the literal `"1 = 1"`, which `history.py` never writes, so the detector's blind spot is exactly where the reads are |
| **B-3** | The only privileged read reachable today writes `reason = NULL`, and the schema permits it. Every Admin search row already fails §4.D.2 |

### Found by reading Frappe v16 source rather than ours

| # | Finding |
|---|---|
| **F-1** | v16 force-downloads exactly **four** private-file extensions (`.svg .html .htm .xml`); develop has fourteen. `.xhtml`, `.svgz`, `.shtml`, `.mhtml`, `.xsl`, `.xslt` and `.swf` are served **inline from the site origin** — stored XSS with session cookies. `nosniff` does not help; the declared type is already scriptable, and Frappe sets `nosniff` nowhere in v16 |
| **F-2** | A `DocShare` row is **ORed past** a `permission_query_conditions` hook — `where_condition \|= table.name.isin(shared_docs)`, commented "shared docs trump all other restrictions" — and `get_shared` also matches `everyone = 1`. Zero DocPerm makes this unreachable on `Chat Message`; `Chat Room` carries a read DocPerm, so on **rooms it is reachable**. `assign_to.add` auto-creates a share whenever an assignee lacks permission |
| **F-3** | `on_trash` fires only when `not for_reload and not ignore_on_trash`. Both flags delete an audit row with the controller never consulted, and `for_reload` also suppresses the `Deleted Document` copy. An `after_delete` guard does **not** close this — it fires after the rows are gone. The child `Chat Retrieval Audit Room` has no guard at all, and the parent's docstring claim that reaching it needs "bypassing the ORM" is wrong |

### Corrected by the same reading

**B-8 is not an XSS.** The audit reported the bell subject as a stored-XSS vector. It is not:
`Notification Log.subject` is a plain `Text` field, so Frappe's generic `_sanitize_content()` runs
nh3 over it on every `insert()` (`frappe/model/base_document.py:1334`), and no `script`, `iframe`,
`style` or `on*` attribute survives. What does survive is `<b>`, `<img src>`, `<a href>` and
`<form>` — so a room titled `<b>Payroll</b>` renders as live markup in the bell and in the
notification email. That is markup injection: worth an `escape_html` call, not a vulnerability, and
it is listed here at its real severity rather than its first-reported one.

---

## 3. The governance decisions — answered by Nikolas, 2026-08-13

Phase 6 §9 says to lead with these rather than bury them, and §11 forbids the agent from deciding
any of them. Eight are settled; three are still open and are **not** to be inferred from silence.

### Settled

| # | Decision | Answer | What it means for the build |
|---|---|---|---|
| **D-6** | How long chat messages live (CQ-19, open since Phase 1) | **Keep forever** | `message_retention_days = 0`, `retention_mode = Disabled`. The job, the dry run and the survives-a-purge table are still built and still tested — a retention rule written after somebody notices the table is one written under pressure. It simply never runs until a future decision turns it on |
| **D-7** | Does the audit trail survive a purge | **Yes** | `audit_survives_purge = on`. Bodies go; the record that somebody read them stays. Moot while D-6 is "keep forever", and it is the answer that makes the purge safe to enable later without re-opening this |
| **D-1** | Is the "who read my messages" view enabled | **Yes** | Built, and enabled by Nikolas as a rollout step rather than in the build PR — §11 is explicit that flags get turned on one at a time with a look at the dashboard between each. The setting still *ships* `Disabled` |
| **D-2** | Name the admin, or "an administrator" | **Name them** | The employee sees who read, not that someone did |
| **D-3** | Reason verbatim, or a category | **Category** | **Schema:** a `reason_category` Select lands alongside the free-text `reason`. The free text stays for the compliance view; the subject sees the category. Answering this now is what saves the migration |
| **D-4** | An investigation exemption | **No** | Not built. `suppress_from_subject_view`, the second approver and the expiry are all absent, which is the clean outcome — the fields do not exist, so there is no half-implemented exemption for somebody to complete later without the approver |
| **D-5** | Are Triton's reads shown | **Yes, separate tab** | Admin reads are the default tab. Triton's reads are real and disclosed, and they do not bury the ones the view exists for |
| **D-9** | Export includes deleted content by default | **No** | `include_deleted_content` ships off, must be set deliberately, and the fact that it was set is itself audited |
| **E-1** | Create `Chat Audit Log`, reversing ADR X-1 | **Create it** | An explicit ADR amendment, not a silent addition. Governance events — role grants, hard deletes, retention outcomes — get a table shaped for them instead of `Chat Retrieval Audit` rows with every read-specific field null |

### Still open — bring back individually, do not infer

| # | Decision | Why it is still open |
|---|---|---|
| **D-10** | A nightly off-box append-only copy of the audit rows | Deliberately offered without a recommendation. The chain makes tampering **detectable, not impossible** — anyone with database write access or root on the VM can rewrite a row and recompute the tail. A bucket with object versioning and a retention lock is the only durable fix, and it is a real cost |
| **D-11** | Import back-fill of historical Chat content | The recommendation is **no** and it was not confirmed. Silence is not a no here, because the cost of guessing wrong runs both ways: it cannot be undone once done, and it cannot be done at all after 90 days |
| **D-8** | Who owns the matching Google Vault retention rule | Not a build decision — an acknowledgement. This job cannot purge Google's copies; they are governed by Workspace retention in a console this system cannot reach. Deleting from ERPNext while Chat keeps everything forever is a reporting gap, not a retention policy. Currently unowned |

---

## 4. Sequence

Roughly 24 focused changes, one PR each, each with its version bump and changelog entry.

1. **Pre-feature block** — this document; the four inventories; the findings in §2. Nothing here
   needs a governance answer, and B-4/B-5/F-1 should not wait for one.
2. **Audit immutability** — the hinge. *Nothing that writes an audit row merges before it.*
   Consolidate `chat/audit.py`, close the two `on_trash` holes or accept them by name, add nightly
   chain verification with an alert, and grant the auditor read on the audit tables — which they do
   not have today.
3. **The unified access report**, then the oversight read path, then the viewer, then export.
4. **Edit/delete trail completion.**
5. **Retention — deliberately last of the data-touching work.** Building the destroyer before the
   vault is sealed inverts the only safe ordering.
6. **Observability, drift, rollout, runbook**, then the final definition of done.

---

## 5. What cannot be done from a development machine

Stated up front, because a plan that pretends otherwise produces a checkpoint full of unverified
checkboxes.

- **15 of the 22 named tests need a real bench**, which CI does not have and will not get. Three
  bench suites from earlier phases have **never been executed**, so the authorisation half of the
  security model is unproven by execution rather than merely untested.
- **Production `gcloud`** — whether a Cloud Armor policy is attached to the live backend service has
  never been read, and two prior documents claim opposite things. One read-only command settles it.
- **A browser** — the hostile-input corpus with devtools open, an SVG attachment fetched with
  headers inspected, an export downloaded and its hashes verified.
- **A phone** — Web Push end to end, and what origin `frappe.utils.get_url()` returns behind the
  load balancer. That last one is not cosmetic: an `http://localhost` origin would be baked into a
  downloaded legal artefact.
- **A non-engineer** — the pilot checklist is only evidence if one of them runs it.
- **Two real users and 30 minutes of production** — the Chat-dark drill.
- `gitleaks` / `trufflehog` are not installed. `scripts/check_no_committed_secrets.py` already
  blocks in CI with full history and ran clean over 2,387 working-tree files and 8,316 history
  blobs. No rotation is indicated **by repo evidence**; production `site_config.json`, bench backups
  and Error Log bodies remain unexamined.
