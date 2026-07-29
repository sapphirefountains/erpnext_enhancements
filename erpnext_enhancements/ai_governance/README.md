# `ai_governance/` — human-in-the-loop for AI writes

The DocTypes behind the AI write-confirmation gate, plus the Triton assistant's settings and
allowlist. The gate's *logic* lives in [`../assistant_tools/`](../assistant_tools/README.md);
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
| `Training Insight` | Captured insights for assistant tuning |

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
