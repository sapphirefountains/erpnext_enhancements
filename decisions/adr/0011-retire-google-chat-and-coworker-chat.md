# 0011. Retire the Google Chat mirror and the coworker chat product; the Triton widget is the only chat surface

- **Status:** Accepted
- **Date:** 2026-09-13
- **Supersedes:** [`0009-erpnext-google-chat-triton.md`](0009-erpnext-google-chat-triton.md)
  and both its addenda,
  [`0009-A1`](0009-addendum-1-phase-5-decisions.md) and [`0009-A2`](0009-addendum-2-phase-6-decisions.md).
  The two appendices ([A](0009-appendix-a-widget-behavior-inventory.md),
  [B](0009-appendix-b-implementation-plan.md)) are references to that record and go with it.

> ADR 0009 is immutable and stays exactly as written. It is now history rather than
> instruction: everything it specifies was built, most of it worked, and it is being removed
> for a product reason rather than an engineering one. A reader who finds 0009 first should
> read this record second and stop there.

## Context

Nikolas's call, 2026-09-13: *"I'd like to remove the Google Chat integration from this. It's
buggy and a troublesome system. I'd like the widget and other chat related functions to not
have Google Chat anymore but just chatting with Triton."*

**The buggy half is real and is on the record.** `CHANGELOG` 1.340.1/1.340.2 records that
**no subscription renewal this app ever issued had succeeded** — sixteen consecutive failures
apiece on both live subscriptions, whose `last_renewed` still equalled their create date — and
four subscriptions subsequently found in state `DELETED` with nothing in the app able to
recreate them, so the affected coworkers stayed uncovered while the hourly job reported clean.
(Those same two entries correct the actual inbound outage to **~1.5 hours, not ~24**, and this
record quotes the corrected figure deliberately: the larger number is the one that circulated.)
It is a failure mode 0009 itself called out as "silent, total and permanent" and built a
reconciliation sweep to survive rather than prevent. 1.286.2 records the Chat app rendering
Triton's replies as `chatbot@sapphirefountains.com` in the native client. The mirror has more
moving parts than anything else in this app: an outbox relay with a state machine and a
circuit breaker, a Pub/Sub puller on a one-minute cron, per-coworker Workspace Events
subscriptions with their own renewal and reconciliation passes, an echo-suppression ladder, a
drift census, and provisioning in three modes.

**But the decisive argument is not that it was buggy — it is who the product was for.**
0009's own Context states the tension it existed to resolve: *the work lives in ERPNext and
the people live in Google Chat.* The mirror was the answer. Remove the mirror and the
ERPNext-side chat product does not become a smaller chat product; it becomes one with no
audience, because the audience was always reachable only through the mirror. Staff have the
Google Chat app on their phones and will keep using it. So the choice was never
"mirror or no mirror" — it was "mirror, or no ERPNext chat".

That is why this record retires the coworker product too, and not just the transport.

**Chat was never enabled on production.** Confirmed by Nikolas, 2026-09-13, and consistent
with how it shipped: `enabled = 0`, `dry_run_mode = 1`, `restrict_to_whitelist = 1`,
`google_sync_enabled = 0`, both relay switches off.

**That is a statement about the switches, not a row count**, and the distinction matters
because the `DROP TABLE` pass is irreversible. The tables could not be counted from here: the
chat retrieval gate refuses every chat doctype to the generic tools, by design, and routing
around a security control to source a figure for a decision record is not a trade worth
making. So the teardown patch **prints the row counts it finds immediately before it drops
anything**, and the deploy log is the record. The expectation is a row of zeros; if it is not,
the log says so before the tables are gone rather than after.

**What is NOT in question is Triton.** The floating Desk widget is a separate surface with a
separate server side — `triton_chat.py` and `triton_attachments.py`, 1,913 lines at the app
root that import nothing from `chat/`. The import direction was always one-way: `chat/`
reached into the widget's bridge, never the reverse. It keeps working, unchanged.

## Decision

**1. The `chat/` module is deleted in full.** All of it: the Google transport (`gchat/`), the
sync engine (`sync/`), the SPA's HTTP surface (`api/`), notifications and Web Push
(`notifications/`), governance and audit (`governance/`, `audit.py`), the gated retrieval
module and the semantic index (`retrieval/`, `indexing/`), the `@triton`-in-a-room handler
(`invoke/`), and all twenty-three DocTypes. ~58,000 lines of Python.

**2. The coworker browser code goes with it** — the SPA at `/chat`, its bundle and stylesheet,
the four `www/chat*` shells, the push service worker, the route rule, and
`chat_surface.js`: the coworker half of the floating bubble.

**3. The bubble becomes single-surface again.** The tab switch, the unread badge, the expand
control and the bubble↔SPA handoff writer are removed from `triton_widget.js`. Opening the
bubble opens Triton. ADR 0009's decision #8 — *extend the existing widget rather than adding
a second one* — is the one decision from that record that survives intact, because obeying it
is precisely why there is nothing to untangle now.

**4. Three browser modules are re-homed rather than deleted**, to
`public/js/triton/`: `citations.js` (decision #7's inline `[[ref:N]]` rendering),
`markdown.js` (the widget's only markdown path since `frappe.markdown` was removed as an
unsanitised `innerHTML` sink), and `keys.js` — the single surviving function of `chat/dom.js`.

**5. A one-shot patch deletes the database residue.**
[`patches/delete_chat_module.py`](../../erpnext_enhancements/patches/delete_chat_module.py):
twenty-two tables, the `tabSingles` rows, both Roles, both Notification Types, the
`Logs To Clear` rows and the `Module Def`.

**6. The Google-side estate is torn down by hand**, per
[`docs/google-chat-teardown.md`](../../docs/google-chat-teardown.md). None of it is under
Terraform — `grep -ril chat infra/ modules/` returns nothing — so deleting the code changes
nothing at Google and `terraform plan` shows no drift afterwards.

**7. We do not rebuild `/chat` as a Triton page.** Today `/chat/triton` is a signpost back to
the Desk bubble, not a Triton client, so "make `/chat` a Triton assistant" would be new
construction wearing a removal's clothes. The route is deleted. If a full-page Triton
surface is wanted later it is a feature with its own record.

## Consequences

**Capabilities genuinely lost, stated plainly rather than buried.** `@triton` can no longer be
asked a question *in a conversation* and answer with room context — the gated retrieval module,
the semantic index over transcripts, the rolling room and thread digests, and the citation
manifest that came with them are all gone. The widget's Triton conversation never had any of
that; it was the in-room path that did. `Triton Invocation Log` and the `Triton Cost` report go
too, so per-turn token accounting for `@triton` ends. Nothing replaces these. If Triton needs
to cite ERPNext history again, that is a new design over documents rather than over messages.

**Three near-misses are now guarded, and all three were silent.** They are the reason
[`tests/test_chat_module_retirement.py`](../../erpnext_enhancements/tests/test_chat_module_retirement.py)
spends more assertions on what survived than on what went:

| Trap | Why it is silent |
|---|---|
| `triton_widget.js` imported three modules from `public/js/chat/` | It ships in the **global Desk bundle**, and the deploy runs `bench migrate && bench build`. An unresolvable import fails the build *after* the migrate has committed. |
| ~60 lines of `.ee-citation` / `.triton-source` CSS sat **inside** the "PHASE 3" block that ran to end-of-file | Deleting the block in one cut strips inline-citation and sources-chip styling off every Triton answer. Nothing fails; it just looks wrong. |
| `scripts/fuzz_url_safety.mjs` imports `isSafeUrl` from `citations.js` | A CI step with no connection to chat, broken by a tidy-up of chat. |

**Two framework behaviours decide the teardown patch, and neither is the intuitive one.**
Verified against `origin/version-16`, not the sibling `develop` checkouts:

- `remove_orphan_doctypes()` runs in `post_schema_updates` — *after* all patches — and deletes
  the DocType rows on its own. Its docstring is explicit that this is "supposed to be
  non-destructive": **it never drops a table.** Without the patch, twenty-two `tabChat*` tables
  survive with no DocType describing them, invisible to the desk and to any fixture audit.
- Deleting the `Chat Settings` **DocType** does not clear its `tabSingles` rows.
  `delete_from_table` clears Singles only under `doctype != "DocType" and doctype == name`,
  which is true when deleting the *document* and false when deleting the *DocType*.

**The obvious safety step was the dangerous one.** A pre-removal
`set_single_value("Chat Settings", "google_sync_enabled", 0)` would, on a site that never
materialised the Single, **create** the row and write `None` into every other field —
`dry_run_mode` to 0 and `restrict_to_whitelist` to 0, the unsafe direction for both. A patch
written to make the integration safer would have made it live, on the deploy removing it. The
patch therefore disables nothing and only deletes.

**Removing `Chat` from `modules.txt` and deleting the package are one commit, not two.**
`sync_for()` calls `frappe.get_module(app + "." + module)` for every entry, so a stale
`modules.txt` line raises `ModuleNotFoundError` inside `sync_all()` and aborts `bench migrate`
— which on this repo is the deploy, at exactly the half-applied point v1.395.0 reached.

**The AI gate's chat denylist is removed with the tables it protected.** It was the only
control stopping `run_database_query` and `run_python_code` reading chat data, because raw SQL
never consults DocPerm. It is safe to remove *only* because the patch drops the tables: a
denylist naming twenty-three DocTypes that no longer exist protects nothing, and its own test
asserted set equality against `chat/doctype/*/*.json`, so it would have failed the build
against an empty directory. The mechanism is documented in place in `assistant_tools/_gate.py`
for whoever needs it next — the refusal must sit above the settings check, and it must refuse
on contact rather than try to parse SQL.

**What would have to change to revisit this.** The premise is that the people are in Google
Chat and the work is in ERPNext. If staff ever move to a chat client ERPNext owns, or Google
Workspace stops being the company's messaging platform, the tension 0009 describes returns and
a new record should start from 0009's Context — which remains a good statement of the problem,
whatever one thinks of its answer.
