# 0009 Addendum 2. Phase 6 decisions

**Status:** Accepted 2026-08-13. Amends [ADR 0009](0009-erpnext-google-chat-triton.md) and
[Appendix B](0009-appendix-b-implementation-plan.md) §8.

Phase 6 is governance, audit, retention, hardening and rollout. Its prompt was written before
Phases 1–5 were implemented, so it disagrees with the shipped code in twelve places; those are
catalogued in [`docs/chat-phase6-plan.md`](../../docs/chat-phase6-plan.md) and resolved in the
ADR's favour. This addendum records the decisions that go the other way — where Phase 6 changes
something the ADR had settled, or settles something it left open.

---

## A2.1 — `Chat Audit Log` is created, reversing X-1

**Appendix B §13 recorded X-1: the ADR considered a `Chat Audit Log` DocType and rejected it.**
Phase 6 reinstates it. Approved by Nikolas 2026-08-13.

**Why X-1 was right about the principle and wrong about the shape.** The principle — two audit
tables are acceptable, two *definitions of a read* are not — still holds and is not weakened
here. What X-1 did not have was a concrete list of the events Phase 6 needs to record:

| Event | Why `Chat Retrieval Audit` cannot hold it |
|---|---|
| Oversight role granted / revoked | No room, no `seq` range, no participation. Every load-bearing field of that schema would be null |
| A tombstone expanded to its original content | Has a room, but the record's subject is *the act*, not the range read |
| What a retention run destroyed | Counts and ranges across many rooms; `actor_type` is a scheduler |
| An export requested / downloaded | The artefact, not the read |
| Chain verification failed | About the log itself |

`Chat Retrieval Audit` is read-shaped all the way down: required `accessed_by` / `actor_type` /
`purpose`, a child row per *room read* with a `seq` range, and `was_participant` as the field
that gives the table its value. Storing a role grant there means a row with every one of those
null, and the compliance query — *"child rows where `was_participant = 0`"* — stops being one
line.

**The two do not overlap, and that is the test of whether X-1's principle survived.** Nothing
that returns message content is recorded in `Chat Audit Log`; nothing recorded there returns
message content. The unified `Chat Access Report` reads both.

**One rule the new table has that its sibling does not:** *message text must never appear in any
field.* A retention run records how many rows it destroyed and over which `seq` range, never what
they said. A log of what was deleted that contains what was deleted has not deleted anything —
it has moved it somewhere with weaker permissions and called that an audit trail.

---

## A2.2 — The gate has exactly two doors, and the employee app is not one

**`membership_filter_sql` now takes `allow_oversight`, defaulting to `False`.** Only
`chat/retrieval/gate.py` passes `True`. Approved by Nikolas 2026-08-13.

It previously short-circuited on the caller's **roles**, so an oversight-role holder received the
unrestricted `1 = 1` fragment on every query they made — including their own ordinary scrollback,
which wrote a `Chat Retrieval Audit` row about a person reading their own conversations. That is
noise in the one table whose signal is `was_participant = 0`.

**The function now agrees with its own Python twin.** `visible_room_names` has never
short-circuited for the oversight role, on stated grounds: *"every room in the system is a
different query with a different audit obligation, and it must be written where that obligation
is visible."* That reasoning was right; the SQL fragment was doing the opposite for thirty call
sites.

**This is what makes §4.D.2's mandatory `reason` implementable rather than aspirational.** A
reason can be collected once per oversight session by the surface built for it, and there is now
nowhere else an oversight read can happen. What an auditor loses is cross-room search from the
ordinary chat UI; they get it back through the audited viewer, with a reason attached.

**Still open, deliberately.** `gate.py`'s own two doors — `retrieve()` and
`retrieve_for_oversight()` share private query helpers, so an auditor's ordinary Triton turn is
still unrestricted. Splitting them belongs with the oversight read path, where
`retrieve_for_oversight` gets its first caller and its behaviour can be tested rather than
assumed. It has never been called.

---

## A2.3 — The oversight role grants read, and `require_room` has two intents

`require_room` passed `ptype="read"` for every caller including writers, and called the
permission hook **directly** rather than through `frappe.has_permission` — so `Chat Room`'s
read-only DocPerm, which the hook's own docstring named as the thing refusing writes "above us",
was never consulted on that path.

`intent="read"` asks *may this identity see the room*; `intent="write"` asks *is this identity in
the room*. They are different questions, not stricter shades of one. All four `has_permission`
hooks are `ptype`-aware on the same grounds: `read` and `select` only, never `report` — a report
over chat content is the bulk-extraction path the audited viewer exists to replace.

---

## A2.4 — Governance policy, answered by the human

Recorded here because §11 forbids the agent from deciding any of them, and because a decision
nobody wrote down is one that gets re-made.

| Decision | Answer |
|---|---|
| Retention length | **Keep forever.** `message_retention_days = 0`, `retention_mode = Disabled`. The job, dry run and survives-a-purge table are still built and tested |
| `audit_survives_purge` | **On** |
| "Who read my messages" view | **Enabled, naming the admin.** Enabled by Nikolas as a rollout step, not in the build PR |
| Reason shown to the subject | **A category**, via a `reason_category` Select alongside the free-text reason |
| Investigation exemption | **Not built.** The only shapes on offer were "with a second approver and a mandatory expiry" or "not at all", and not-at-all is the one that cannot rot |
| Triton's reads in that view | **Shown, in a separate tab**, admin reads defaulting |
| Export includes deleted content | **Off by default** |

**Open, and not to be inferred from silence:** the off-box append-only audit copy; import-mode
back-fill (recommendation is *no*); and who owns the matching Google Vault retention rule, which
is console work this system cannot reach and is currently unowned.

---

## A2.5 — Findings the hardening pass produced

Full list in [`docs/chat-phase6-plan.md`](../../docs/chat-phase6-plan.md) §2. The ones that
changed a decision rather than a line:

- **Frappe v16 force-downloads four private-file extensions; `develop` has fourteen.** The seven
  in the gap were served **inline from our own origin**. Fixed in `monkeypatches.py` rather than
  nginx, because `bench setup production` regenerates the nginx config on every boot. *Public*
  files never reach Python at all, which is why chat attachments are `is_private = 1` without
  exception.
- **A `DocShare` row is ORed past a `permission_query_conditions` hook** —
  `where_condition |= table.name.isin(shared_docs)`, and `assign_to.add` creates one whenever an
  assignee lacks permission. Unreachable on `Chat Message` (zero DocPerm); **reachable on
  `Chat Room`**, which carries a read DocPerm. Open.
- **`on_trash` is skippable by flag.** `ignore_on_trash=True` and `for_reload=True` both delete
  an audit row with the controller never consulted, and `for_reload` suppresses the
  `Deleted Document` copy too. An `after_delete` guard does not close it — it fires after the
  rows are gone. Layer 3 covers both *inside this repo*; layer 4 is what remains beyond it.
