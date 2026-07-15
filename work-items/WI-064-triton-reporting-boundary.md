# WI-064: Triton management-reporting integration (non-statutory only)
**Phase:** 2   **Type:** CONFIG   **Size:** S
**Blocked by:** WI-035 (real books exist to report on)   **Blocks:** nothing

## Why
Triton (the in-house AI reporting/assistant layer) already has read surfaces into ERPNext (analyze_business_data, generate_report, 30+ assistant tools) and a wildcard sync feed. The brief's rule is absolute: **statutory and audited numbers come from native ERPNext reports; Triton is a management/exploratory layer only.** That boundary must be operational — written, configured, and trained — or it will erode the first time an AI-generated number looks convenient.

## Native-first check
The statutory layer IS the native feature set: the 16 verified native Script Reports (Balance Sheet, P&L, General Ledger, Trial Balance, Cash Flow, AR/AP, Sales Register, etc.). Triton adds exploratory analysis on top and replaces nothing. Verdict: CONFIG + policy; zero build. Any Triton-produced artifact presented as a statutory number is a defect by definition.

## Preconditions
- WI-035 signed off (the native reports have real content).
- WI-050 completed: `ai_write_gating_enabled` = 1 on prod (AI-initiated writes route through the AI Pending Action confirmation flow).

## Scope
- A one-page written policy (wiki, linked from the Finance workspace): statutory/tax/lender/CPA numbers come only from the named native reports; Triton output is labeled management-analysis and never enters the close package, tax filing, or financial statements.
- Verify and keep `ai_write_gating_enabled` = 1; confirm the finance close checklist (WI-049's Month-End Close task list) contains no AI-generated artifact.
- Curate which Triton read tools the finance team actually uses (e.g. analyze_business_data for ad-hoc questions) and note them in the policy so usage is deliberate.
- Train the finance team + CEO on the boundary with one concrete example (native P&L vs a Triton revenue analysis of the same month — same underlying data, different standing).

## Acceptance criteria
- Policy page exists and is acknowledged (sign-off recorded) by the close owner and the CEO.
- `SELECT value FROM tabSingles WHERE doctype='ERPNext Enhancements Settings' AND field='ai_write_gating_enabled'` = 1 on prod.
- The first two post-cutover close packages contain zero AI-authored statutory artifacts (spot-audited against the policy).

## Rollback
None needed (policy + a settings flag; the flag's OFF state is the pre-existing behavior).

## Explicitly NOT in this work item
Any Triton feature development; disabling AI tools wholesale; changes to the wildcard sync hook (WI-050 owns that hazard); KPI dashboard work (separate, already-built module).
