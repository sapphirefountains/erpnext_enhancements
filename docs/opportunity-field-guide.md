# Opportunity & Lead Field Guide

**Status: the layout is frozen.** Agreed in the Projects/Invoice-processing meeting
(Jun 9, 2026): the Opportunity page has been reshuffled for the last time — from here
on, what goes where is defined below, and changes go through a PR to this file plus
the fixtures, not through Customize Form. The same definitions ship as field
descriptions on the form itself (see `fixtures/custom_field.json`), so this page and
the form can't drift apart silently.

Process context: PRO-0204 "Won Opportunity Hand-Off" (Step numbers below refer to it).

## Leads — quick capture

A lead is **name + phone + what they want**. That's the whole bar. The Lead list's
**+ Add Lead** button opens a quick-entry dialog with exactly: First Name, Phone,
Lead Details ("what do they want?"), Lead Source, and Status (pre-filled). Fill it in
while the caller is still on the phone — no paper, no memory.

- Don't create an Opportunity for a "how much is a rental?" call. Log the lead and
  move on; nine out of ten go nowhere, and that's fine — the lead record is the metric.
- Convert to an Opportunity only when it turns real. Conversion marks the Lead
  "Converted" automatically.

## Opportunity — who fills in what

| Field | Who | What goes in it |
|---|---|---|
| **Opportunity Summary** | Account Executive | One plain-English line answering "what is this?" — e.g. *"Sapphire hardware access + VNC for controller"*. The detail lives in Scope, not here. |
| **{Build/Design/Service/Rent} Customer Requests** | Account Executive | The customer's ask, **in their own words**, as the request comes in. One row per ask, or everything in one row — both fine. **Don't break it down**; that's not Sales' job. |
| **{Build/Design/Service/Rent} Deliverables** | PM / Design | The internal breakdown of what we'll actually deliver, translated from the Customer Requests (Step 6). Never filled in by Sales. |
| **Scope / Schedule / Budget Rank** | Account Executive | Required (1/2/3 each) before the status can become Closed Won — enforced by the system. |
| **Comments** | Anyone | Things that *happen*: "just got off a call, they asked about X", a pasted email, "gate code is 4471". If it changes what we're delivering, it must ALSO be reflected in Requests/Deliverables — a comment is a record, not a spec. |

The rule of thumb from the meeting: **requests are the customer's words, deliverables
are our breakdown, comments are the running story.** If requirements change mid-flight,
update the fields — don't let the latest truth live only in a comment thread.

## Estimates / quotes — revision naming (QuickBooks, for now)

Until accounting moves into ERPNext: never edit an estimate the customer has already
seen. **Duplicate it** and number the copy — *"Candy estimate 2026-06-09 R1"*, *"… R2"* —
and mark superseded ones rejected/closed so the highest number is always the live one.
(Estimate numbers also increment on their own: bigger number = newer.) When quotes move
to ERPNext, this convention becomes a "Duplicate as Revision" button.

## What happens automatically (so you don't have to)

- **Closed Won** → the configured team list gets an SMS with a link to the
  opportunity (Step 1 auto-alert). Finance can start QuickBooks setup immediately —
  no waiting for the project number.
- **Won but no Project yet** → after 24h (configurable), a daily reminder SMS lists
  the stragglers (the Step 1 → Step 2 gap).
- **Payment Received ticked on the Project** (Budget tab) → timeline comment + SMS to
  the PM and the AE: financially cleared to proceed (Step 5).

Recipient lists live in **ERPNext Enhancements Settings → Status Change SMS Alerts**.
