# 0006. AI writes are confirmed in the desk, never by the model

- **Status:** Accepted
- **Date:** 2026-07-29 (recorded retroactively)

## Context

Frappe Assistant Core exposes this site's data to AI assistants over MCP, including tools
that create, update, delete and submit documents and run workflows. Those tools reach real
accounting and real customer records.

An AI client acting on a misread instruction — or on instructions embedded in a document it
was asked to summarise — can submit an invoice. There is no undo that restores the
surrounding context.

The tempting design is a "confirm" tool the model calls after checking with the user. It
keeps the whole interaction in one place and feels natural. It also reduces the entire
human-in-the-loop guarantee to a convention that the model is trusted to follow, which prompt
injection walks straight through: text that can persuade the model to act can persuade it to
confirm.

## Decision

When `ai_write_gating_enabled` is on, `assistant_tools/_gate.py` wraps
`BaseTool._safe_execute` — the single choke point both FAC execution paths converge on — so a
mutating tool does not execute. An **AI Pending Action** is recorded, a desk notification is
sent, and the model receives an anti-fabrication envelope telling it the action has **not**
run and how a human confirms it.

**Confirmation is desk-only. There is deliberately no MCP-exposed confirm tool.** Confirm and
Cancel exist only as whitelisted endpoints in `assistant_tools/gating_api.py`, which the
`AI Pending Action` form's buttons call. The model can only *read* the outcome afterwards,
through the read-only `check_ai_pending_action` tool.

## Consequences

- **Never add a model-callable confirm.** It is the one change that would defeat the whole
  mechanism while appearing to be a usability improvement.
- **Direct status edits on `AI Pending Action` are blocked** so the lifecycle stays honest.
  Keep that block.
- The gate is patched at `_safe_execute` rather than `execute_tool` for a concrete reason:
  `api/fac_endpoint` calls `_import_tools()` on every MCP request *before* dispatch, so a
  class-level wrap applied from `assistant_tools/__init__` is in place before any tool
  executes in a fresh worker — and `tool_adapter` bypasses `execute_tool` entirely. Moving
  the patch point reopens a path.
- **It ships dormant** (`default: 0`), which per ADR
  [0003](0003-repo-is-source-of-truth-for-customizations.md) means existing installs need a
  patch to turn it on, or it silently enforces nothing.
- The anti-fabrication envelope matters as much as the block. Without it the model reports
  success for an action that did not run, which is worse than refusing.
- `run_database_query` is exempt (FAC enforces read-only SQL) and our own tools are
  explicitly read-only. The six MDM device tools carry a hard safety net even on un-annotated
  builds — a tool that can wipe a phone does not get to rely on advertised annotations.
