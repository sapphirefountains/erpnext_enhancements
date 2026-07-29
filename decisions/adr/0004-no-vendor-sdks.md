# 0004. Integrations are hand-rolled on `requests`; no vendor SDKs

- **Status:** Accepted (forced by the platform)
- **Date:** 2026-07-29 (recorded retroactively)

## Context

The production host is a managed server where PyPI packages cannot be installed. That is not
a preference we could trade against developer convenience — it is the deployment target.

Three integrations need HTTP clients: QuickBooks Online, Stripe, and Plaid. Each vendor ships
an official SDK, and each SDK would be the obvious choice anywhere else.

## Decision

Every vendor integration is hand-rolled on `requests`, which is already a Frappe dependency.
No vendor SDK appears in `pyproject.toml`. The comment there records why, so the absence
reads as deliberate rather than forgotten.

The three modules deliberately share a shape —
`core/{client,api,utils,constants,tasks}.py` — so learning one teaches all three:

| Module | Notes |
|---|---|
| `quickbooks_online/` | The first one; the others were built to match it |
| `stripe_payments/` | Also hand-rolls **webhook signature verification** (the `Stripe-Signature` `t=`/`v1=` scheme), since there is no SDK to do it |
| `plaid_banking/` | Plaid authenticates with `client_id` + `secret` in the JSON **body** of every POST, not a header |

## Consequences

- **We own the protocol details**, including the security-critical ones. Stripe's signature
  verification is ours to get right; an SDK would have done it. That code is not a candidate
  for casual refactoring.
- **API changes are ours to track.** No SDK version bump will tell us a field moved.
- **The parallel structure is the mitigation.** Because the three modules read the same way, a
  fix or a hardening applied to one is mechanically transferable. Preserve that symmetry when
  adding a fourth integration — the consistency is doing real work.
- Adding an SDK "just for this one thing" breaks the deployment, not just the convention. The
  failure will appear at install time on the managed host, not locally.
- Twilio is the exception that proves the rule: it *is* a declared dependency, bounded
  `>=8,<10`, because `api/telephony.py` imports it at module top for webhook signature
  validation and Voice access-token JWTs. Where a package can be installed and the crypto is
  non-trivial, use it.
