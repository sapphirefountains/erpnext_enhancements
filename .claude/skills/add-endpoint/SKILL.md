---
name: add-endpoint
description: Add or change a whitelisted HTTP endpoint, background job, scheduler job, or MCP assistant tool in erpnext_enhancements. Use when writing an @frappe.whitelist() function, a doc_event hook, or a tool exposed to AI assistants.
---

# Adding an endpoint

## Where it goes

| Kind | Location |
|---|---|
| Whitelisted HTTP endpoint | `erpnext_enhancements/api/<area>.py` |
| Doc-event hook, background worker, scheduler job | alongside the endpoints in `api/`, or the owning module |
| MCP tool for AI assistants | `erpnext_enhancements/assistant_tools/<tool>.py` |

[`api/README.md`](../../../erpnext_enhancements/api/README.md) is the map — a table of every
file with its purpose, its whitelisted functions, what calls it, and which external services
it touches. **Add your row to it.** That table is how anyone finds your endpoint later.

## Indentation

`api/` is mixed. Most files are 4-space; `analytics.py`, `collab.py`, `comments.py`,
`user_drafts.py` and `integrations_health.py` are tabs. Match the file you're in. The README
carries the same warning at the top for a reason.

## Documentation density

`api/README.md` claims "every function is documented inline", and the good files earn it.
`api/pickup_routing.py` is the model: a module docstring that states what question the
endpoint answers, then an explicit list of the things the module is careful about —
including the ones that look like bugs. Match that. Explaining *why*
`Purchase Order.shipping_address` is deliberately excluded from an address chain is worth
more than restating what the code does.

## Security

- **Permission-check explicitly.** These are whitelisted functions reachable over HTTP by
  any logged-in user. Session permissions are not applied for you.
- **Validate anything client-supplied that reaches the query layer.** `api/gantt.py`
  validates a client-supplied config (doctype, field map, filters) against `frappe.get_meta`
  before use, and `tests/test_gantt_api.py` guards that contract. A raw fieldname from the
  client reaching a query is the bug class to avoid.
- **Webhook endpoints authenticate by provider signature**, not a session — see
  `api/telephony.py` (Twilio) and the Stripe handler, which hand-rolls signature
  verification because the app deliberately ships without the Stripe SDK.
- **Use an allowlist for writes** driven by client payloads, as `api/maintenance_visit.py`
  does for autosave fields, plus optimistic locking where concurrent edits are possible.

## Long-running work

Enqueue it. `api/maintenance_workflow.py` is the pattern — the endpoint returns and a
background worker does the stock/timesheet/warranty/invoice steps. Scheduler jobs are
registered in `hooks.py` under `scheduler_events`.

## If it's an AI-facing MCP tool

Tools in `assistant_tools/` are discovered by Frappe Assistant Core via the
`assistant_tools` hook. **Ours are read-only on purpose.** The write-confirmation gate in
`assistant_tools/_gate.py` wraps `BaseTool._safe_execute` so that, when AI write gating is
on, mutating tools record an `AI Pending Action` and return an anti-fabrication envelope
instead of executing.

Confirmation is **desk-only by design** — there is deliberately no MCP-exposed confirm tool,
because a model-callable confirm would collapse the human-in-the-loop guarantee to something
prompt injection walks straight through. Do not add one.

Adding a tool means updating `hooks.py`, `assistant_tools/README.md`, and the schema
contract test — see `tests/test_assistant_tools_schema.py` and the `run-tests` skill.

## Before you're done

1. Register anything hook-driven in `hooks.py` (annotated — keep it that way).
2. Add your row to `api/README.md` (or `assistant_tools/README.md`).
3. Add a bench-free test if the logic allows it, and wire it into `ci.yml` on the right
   step — see the `run-tests` skill, especially the unittest-vs-pytest trap.
4. Bump the version and write the changelog entry — see `release-prep`. A new endpoint is a
   **MINOR**.
