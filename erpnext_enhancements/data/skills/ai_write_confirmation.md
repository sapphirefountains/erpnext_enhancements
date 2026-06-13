# AI Write Confirmation Workflow

This site can gate AI-initiated writes behind human confirmation ("AI
Governance"). When the gate is on, mutating tools (`create_document`,
`update_document`, `delete_document`, `submit_document`, `run_workflow`,
`run_python_code`, dashboard creation) — and this app's own write tools such as
`create_followup_task` — do **not** execute immediately.

## What a gated response looks like

Instead of a result, the tool returns:

```json
{
  "status": "awaiting_user_confirmation",
  "executed": false,
  "output": null,
  "action_id": "AI-PA-2026-00001",
  "summary": "Create ToDo",
  "risk": "low",
  "expires_at": "2026-06-11 15:04:00"
}
```

## The rules — follow them exactly

1. **The action has NOT run.** There is no document, no docname, no output.
   Never fabricate, summarize, or describe a result that does not exist.
2. **Do not chain.** Skip any follow-up step that depends on this action
   until it has really executed.
3. **Tell the user to confirm in ERPNext.** They received a desk notification;
   the pending action is at *AI Governance → AI Pending Action* (or the link
   `/app/ai-pending-action/<action_id>`). Only they (or a System Manager) can
   click **Confirm & Execute** — you cannot confirm it yourself, and there is
   deliberately no tool that can.
4. **Fetch the real outcome afterwards** with `check_ai_pending_action`
   (`action_id` from the envelope). Statuses:
   - `Pending` — still waiting; remind the user, don't poll in a tight loop.
   - `Executed` — the real result is included; continue from it.
   - `Failed` — the error is included; explain it, don't retry blindly.
   - `Cancelled` / `Expired` — the human declined or waited too long; ask
     before proposing again.
5. **Identical retries are deduplicated.** Re-sending the same mutation while
   one is Pending returns the same `action_id`, not a new card — so don't
   spam retries to "make it work".
6. Actions expire (default 1 hour). If expired, propose again only when the
   user still wants it.

## When the gate is off

Mutations execute immediately (and are still audited by FAC). You can tell
the difference by the response shape: a gated call always carries
`"status": "awaiting_user_confirmation"`.
