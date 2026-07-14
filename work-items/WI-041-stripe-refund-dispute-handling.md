# WI-041: Refund GL reversal Payment Entry + minimum-viable dispute handling
**Phase:** 1   **Type:** APP_CODE   **Size:** M
**Blocked by:** WI-005 (build/test), WI-039 (live validation)   **Blocks:** nothing (WI-055's compliance-checklist item 7 depends on this landing)

## Why
Verified (repo_payments): `charge.refunded` only sets `amount_refunded`/status on the Stripe Payment; `api.refund_payment`'s docstring says the GL reversal is manual; disputes are entirely unhandled (no `charge.dispute.*` anywhere). Manual reversals will be forgotten; disputes silently pull funds. Minimum-viable: automate the reversing Payment Entry for refunds; for disputes, alert + mark — no automated GL (the withdrawn funds and dispute fee flow through the payout body and are already handled by WI-040's payout JE).

## Native-first check
Payment Entry (native) is the reversal artifact; Notification (native, already a fixture type in this app — repo_app_inventory) is the alert vehicle. No native feature auto-reverses gateway refunds. Verdict: thin APP_CODE producing native documents.

## Preconditions
- WI-005 clearing account live; `Stripe Event` idempotency store operative (repo_payments).
- Webhook endpoint subscribed to `charge.dispute.created` and `charge.dispute.closed` (add in Stripe dashboard alongside WI-039/WI-040 events).

## Scope
In `erpnext_enhancements/stripe_payments/core/reconcile.py` (+ webhooks HANDLED set):
- `charge.refunded`: in addition to the existing `_on_charge_refunded` record update, create and submit a reversing Payment Entry — payment_type 'Pay', party_type Customer, `paid_from`='Stripe Clearing - SF' (settings.deposit_account), amount = the refund delta (handle partial refunds by diffing `amount_refunded`), linked back to the Stripe Payment (`payment_entry`-style link field or remarks), idempotent per refund id. When surcharging is later enabled, prorate the surcharge on partial refunds per docs/stripe_surcharging_compliance.md (full refunds return it automatically — repo_payments compliance summary).
- `charge.dispute.created`: mark the Stripe Payment (status flag/comment), create a Notification alert to Accounts Manager role with manual-JE guidance text (funds withdrawal + dispute fee will appear in the payout body → WI-040 JE); NO automated GL.
- `charge.dispute.closed`: comment the outcome on the Stripe Payment + notify.

## Acceptance criteria
- TEST: full refund of a test payment → a docstatus=1 Payment Entry of type 'Pay' exists with `paid_from`='Stripe Clearing - SF' and amount = original charge; partial refund → PE for exactly the delta; redelivered event creates no duplicate.
- TEST: simulated dispute → Stripe Payment carries the dispute marker and a Notification Log/email reached an Accounts Manager within 5 minutes.
- `SELECT COUNT(*) FROM \`tabStripe Payment\` WHERE status='Refunded'` rows each have a linked reversal PE (SQL join check).

## Rollback
Guard both handlers behind existing settings presence; unhandled events fall back to today's behavior (recorded + Ignored). Cancel erroneous PEs natively.

## Explicitly NOT in this work item
Automated dispute evidence submission or dispute GL automation; refund initiation UX changes (`api.refund_payment` exists); payout accounting (WI-040).
