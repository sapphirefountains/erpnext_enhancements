# `ai_governance/` — human-in-the-loop for AI writes, and the Triton contract

The DocTypes behind the AI write-confirmation gate, plus the Triton assistant's settings,
allowlist, and the inbound call-routing rules the Triton voice gateway reads. The gate's *logic* lives in [`../assistant_tools/`](../assistant_tools/README.md);
this module holds the records it creates and the configuration it reads.

## What the gate does

When **ERPNext Enhancements Settings → AI Governance → Require Confirmation for AI Writes**
is on (default **off** — it ships dormant), `assistant_tools/_gate.py` wraps
`BaseTool._safe_execute`, the single choke point both Frappe Assistant Core execution paths
converge on. A mutating tool then does not execute: an **AI Pending Action** is recorded, a
desk notification goes out, and the model receives an anti-fabrication envelope telling it
the action has **not** run and how a human confirms it.

## Confirmation is desk-only, on purpose

There is deliberately **no MCP-exposed confirm tool**. A model-callable confirm would reduce
the human-in-the-loop guarantee to a convention that prompt injection walks straight through.
Confirm and Cancel happen only through the whitelisted endpoints in
`assistant_tools/gating_api.py`, which the `AI Pending Action` form's buttons call by dotted
path. The model can only *read* the outcome afterwards, via `check_ai_pending_action`.

Do not add a confirm tool. Do not relax the direct-status-edit block on `AI Pending Action` —
blocking desk status edits is what keeps the lifecycle honest.

## DocTypes

| DocType | Role |
|---|---|
| `AI Pending Action` | A proposed AI mutation awaiting human confirmation. Created by the gate; transitions only via `gating_api`. Direct status edits in the desk are blocked |
| `AI Action Log` | Append-only record of AI actions |
| `AI Model Usage` | Model usage accounting |
| `AI Confirmation Exempt Doctype` | Doctypes exempted from the confirmation requirement |
| `Triton Settings` | Single — connection settings for the Triton assistant |
| `Triton Assistant Settings` | Assistant behaviour configuration |
| `Triton Allowed User` | Per-user access to the assistant |
| `Triton Chat Attachment` | Anchor row for one file attached to a Triton chat turn. Owner-scoped (`permissions.py`, wired under both `permission_query_conditions` and `has_permission`). It exists so the uploaded `File` has something to be attached *to*: an orphan private File is readable only through `File.has_permission`'s `doc.owner == user` shortcut — an accident rather than a policy — and nothing in core ever garbage-collects one. Attached here, the file inherits this row's hook and is deleted with it. Behaviour lives in [`triton_attachments.py`](../triton_attachments.py) |
| `Training Insight` | Captured insights for assistant tuning |
| `Call Routing Settings` | Single — the fallback inbound calls land on when no rule matches: default forward number, ring duration, Holiday List, voicemail wording. Its kill switch is `paused`, not `enabled`, for the never-saved-Single reason in `CLAUDE.md` |
| `Call Routing Rule` | One "when a call looks like this, ring these people" clause. Ordered by `priority`, **first match wins**. Conditions: menu selection, known/unknown/matching caller, days and times |
| `Call Routing Target` | Child table — one Employee, softphone user, account manager, or voicemail box on a rule |

## Inbound call routing

Configured here, executed by Triton. A System Manager writes rules in the desk; the gateway
matches each incoming call against them and builds the `<Dial>`.

| File | Role |
|---|---|
| [`call_routing_match.py`](call_routing_match.py) | The decision, as a pure function. **Standard library only** |
| [`call_routing.py`](call_routing.py) | The Frappe binding: read the records, compile them into dial legs, explain the result |
| [`../api/telephony.py`](../api/README.md) | `get_telephony_routing` carries the compiled payload; `preview_call_routing` backs the "Test Routing" button |
| [`../tests/data/call_routing_vectors.json`](../tests/data/call_routing_vectors.json) | Shared test vectors, committed identically in Triton |

**Why the decision is made over there.** Triton owns the Twilio webhook, and it already
fetches `get_telephony_routing` on a 60-second cache with a prefetch that fires while the
caller is still listening to the phone menu — built that way so the dial decision never
waits on us. Making ERPNext decide per call would put a blocking HTTP request inside a live
call, which is the failure Triton's own `CLAUDE.md` records as having frozen its event loop.
So we compile and it matches.

**Why the compile step is not laziness.** Resolving Employees to E.164 numbers here means
the gateway needs no lookup of its own, *and* it means an unreachable target is reported
while somebody is editing the rule instead of being discovered as a phone that did not ring.
That matters more than it sounds: on 2026-09-11, **2 of 20** Employee records had a
`cell_number` at all, and those stored it as bare digits. `compile_rules` surfaces every
dropped target in the payload, the form, and the preview.

**The cost, and the defence.** The matcher exists in two repos that deploy independently, so
both load the same vectors file. Change behaviour on one side without the other and the
other side's CI goes red — which is the only cheap alarm for a bug whose symptom is a phone
that does not ring, for one caller in ten, noticed weeks later.

Three behaviours worth knowing before editing rules:

- **A matching rule is authoritative.** If it were additive no rule could ever *narrow*
  anything. The safety valve is per-rule — `also_ring_softphones` defaults on, so a rule
  written without thinking about it still rings the desk.
- **No match means today's behaviour**, not silence: every softphone plus the default
  forward number. Same for `paused`, an empty rule set, a payload from a newer ERPNext, and
  anything that raises. A configuration mistake must never send every caller to voicemail.
- **A time window that ends earlier than it starts wraps past midnight**, and the day filter
  still applies to the day the call arrives. "Weekdays 17:00-08:00" covers Monday-Friday
  evenings *and* Monday-Friday early mornings; it does not stretch Friday evening into
  Saturday.

## Relationship to Triton

Triton confirmation-gates writes in its own chat UI (`PendingAction` + `IntegrationAuditLog`);
this module gates Frappe Assistant Core tool execution at the MCP layer. **There is no
overlap and both stay** — they cover different entry points into the same data. See Triton's
`docs/convergence.md`.

## Tests

```bash
python -m unittest \
  erpnext_enhancements.tests.test_ai_gate_unit \
  erpnext_enhancements.tests.test_ai_gating_integration -v
```

`test_ai_gate_unit` is bench-free and runs in CI — it is the guard on a security boundary, so
keep it green and keep it in the CI list.

```bash
python -m unittest erpnext_enhancements.tests.test_call_routing -v
```

Bench-free and **stub-free**, because `call_routing_match` imports nothing but the standard
library. If that suite ever needs a `frappe` stub, something frappe-shaped has leaked into
the matcher and belongs in `call_routing.py` instead.
