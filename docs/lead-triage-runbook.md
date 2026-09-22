# Lead triage and speed to lead — runbook

How an enquiry becomes a Lead, who owns it, how fast it must be answered, and how it becomes
an Opportunity without losing the campaign that produced it. Ships in v1.502.0
(TASK-2026-01473). Code: `crm_enhancements/lead_triage.py`; working-time arithmetic:
`utils/business_hours.py`.

---

## Why the Lead stage exists at all

Between 2026-08-01 and 2026-09-22 this site created **1 Lead and 29 Opportunities**. Sales
works Opportunities directly. That makes three numbers impossible to compute:
lead-to-opportunity conversion, contact rate, and speed to lead. It also leaves attribution —
which campaign produced the enquiry — with nothing to attach to, because the website capture
writes it onto a Lead. The decision on 2026-08-13 was to **keep the Lead stage and fix the
process**.

The rule for sales is therefore short:

> **Every new enquiry starts as a Lead.** Website forms create it for you. A phone call, an
> email or a referral: create the Lead first, then qualify it into an Opportunity. Never
> create an Opportunity for somebody who has no Lead or Account yet.

---

## Stages

| Stage | Record | Status | Who moves it on |
|---|---|---|---|
| New enquiry | Lead | `Lead` | Arrives from the website, or typed in by sales |
| Contacted | Lead | `Lead` / `Replied` / `Interested` | Owner's first email, SMS or logged call. This stamps **First Response At** |
| Qualified | Lead → **Opportunity** | Lead becomes `Opportunity` | Owner, from the Lead form: **Create → Opportunity** |
| Not a fit | Lead | `Do Not Contact` | Owner. Add a comment saying why |
| Existing customer | Opportunity from the **Customer** | — | See the fountain-move exception below |

**Qualified means**: we know what they want (service interest and a rough scope), where it
is, and that they want a price or a visit. If you are about to quote, visit or design, it is an
Opportunity. ERPNext's own **Qualification Status** field on the Lead (`Unqualified` / `In
Process` / `Qualified`) may be used on the way there, but converting the Lead is the step that
counts.

### Converting, and what happens to attribution

Use **Create → Opportunity** on the Lead. Do not make a blank Opportunity and type the name
in: the button is what links the two records.

- ERPNext maps the Lead into the new Opportunity with `opportunity_from = Lead` and
  `party_name = <the Lead>`. `lead_owner` becomes the Opportunity owner.
- On save, `attribution.propagate_to_opportunity` copies the Lead's attribution onto the
  Opportunity: `custom_lead_source`, every `custom_utm_*` field **including
  `custom_utm_id`**, `custom_gclid`, landing page, referrer and capture time. It fills blanks
  only, so a value typed on the Opportunity is never overwritten. Tested in
  `tests/test_lead_triage.py::QualificationPathTests`.
- When the Customer is later created from the Lead (Create → Customer, or the quotation
  flow), `propagate_to_customer` does the same via `Customer.lead_name`. An Opportunity
  created later against that Customer inherits from the Customer.
- The Lead's status moves to `Opportunity` by itself, so it leaves the triage queue.

This is the chain the spend report (TASK-2026-01477) walks: Ad Campaign → Lead →
Opportunity → Project → Sales Invoice. A Lead skipped here is a paid click that can never be
traced to revenue.

**Exception — fountain moves.** The Cactus & Tropicals flow creates a Customer first and an
Opportunity with `opportunity_from = Customer` on purpose (a Lead id there breaks the
closed-won hand-off). Attribution follows the Customer path instead. Leave it alone.

---

## Ownership

A website Lead always gets an owner the moment it arrives:

1. **`web_lead_default_owner`** (ERPNext Enhancements Settings → Lead Attribution) — the one
   named person who triages website Leads. Set this.
2. If it is blank, or that user is disabled: **round-robin** across enabled users holding
   **Triage Rotation Role** (default `Sales Team`, which 9 users hold). Not `Sales User`: 16
   of 19 users hold that, so as a pool it means everybody. The rotation remembers who was
   last (`DefaultValue` key `lead_triage_last_owner`).
3. If nobody qualifies, the Lead arrives unowned. It still shows in the Speed-to-Lead widget
   and in Attribution Gaps, and the SLA reminder goes to the escalation list.

The owner gets a **ToDo** ("First response to new Lead …", priority High, dated to the
deadline) and a notification, emailed per their own Notification Settings. The ToDo is made
directly rather than through the Assign To dialog, so the notification never reads "Guest
assigned you…" — website submissions arrive as Guest.

---

## Speed-to-lead SLA

**Settings → Lead Triage & Speed to Lead.** Off by default (`lead_sla_enabled`).

| Setting | Default | Meaning |
|---|---|---|
| Enable Speed-to-Lead SLA | off | Stamp deadlines and chase them |
| First Response Within | 60 | Working minutes from arrival to the owner's reminder |
| Escalate After | 240 | Working minutes from arrival to the escalation |
| Business Day Starts / Ends | 08:00 / 17:00 | Site time (America/Denver) |
| Escalate Unanswered Leads To | *blank* | A named user. Blank = users holding **both** `Sales Manager` and the triage role |
| Triage Rotation Role | Sales Team | Backup owner pool (above) |

**Working time** is Monday–Friday between those hours, skipping dates on the company's
default Holiday List. A Lead at 16:30 on Friday is due 08:30 Monday, not 17:30 Friday.

> ⚠️ The company Holiday List on production is **`Utah, USA Holidays 2025`**. It has no 2026
> dates, so 2026 holidays are treated as working days. Create a 2026 list and set it as the
> company default (Company → Sapphire Fountains → Default Holiday List).

**What counts as a response**: a **Sent** Communication of type **Communication** on the Lead
— an email sent from the Lead form, an SMS through the telephony gateway, or a logged outbound
Triton call. A comment does not count, and neither does an automated message. It stamps
**First Response At** on the Lead. The Speed-to-Lead widget uses the same rule from the same
constant (`lead_triage.RESPONSE_EXISTS_SQL`), so the widget and the alert cannot disagree.

**The chase** runs every ten minutes:

- At the deadline, the **owner** gets an in-app alert and an email. The Lead's **SLA Alert**
  becomes `Reminded`.
- At the escalation time, the **escalation recipients and the owner** are told, and SLA Alert
  becomes `Escalated`. Nothing further happens after that.
- Each fires at most once per Lead. A Lead that was not reminded before the escalation time
  (an outage, or the SLA switched on over a backlog) is escalated once, not reminded and then
  escalated ten minutes apart.

**Only inbound Leads are chased**: those created by the website ingress while the SLA was on.
A Lead that a salesperson types in after a phone call has already been answered, and chasing
it would teach people to ignore the alert. Another inbound channel opts in by calling
`lead_triage.prepare_inbound_lead` and `assign_inbound_lead`.

---

## The triage queue

Three views of the same thing:

1. **Your ToDo list.** One open ToDo per Lead assigned to you.
2. **Sales Dashboard → Speed to Lead** (enable the widget in Settings → Sales Dashboard
   Widgets). Unanswered Leads from the last 30 days, oldest first, with hours waiting. Its data
   now also carries the deadline and an overdue flag.
3. **Lead list**, filtered to *First Response At* is not set and *Status* is `Lead`, sorted by
   *First Response Due*.

**Who works it daily**: the named `web_lead_default_owner`. The rotation is a backup, not a
substitute for a person who owns the queue.

---

## Turning it on

In this order, after the website ingress is live (see the attribution runbook):

1. Set **Website Lead Owner** (`web_lead_default_owner`) and **Escalate Unanswered Leads To**.
2. Fix the Holiday List (above).
3. Tick **Enable Speed-to-Lead SLA**.
4. Submit a test enquiry through the website. The Lead should show **First Response Due**, and
   the owner should have a ToDo and a notification. Reply from the Lead form: **First Response
   At** is stamped and the Lead drops off the widget.

**Turning it off**: untick the SLA. Deadlines already stamped stay on their Leads; nothing is
chased. Ownership and ToDos continue, because an unowned website Lead is the failure this
exists to end.

---

## Measuring it

Every Lead that has been answered carries **First Response At**. Leads answered before
v1.502.0 are backfilled from their Communications on every migrate (`after_migrate`,
blanks only). Speed to lead for a period is `First Response At − creation` over the Leads
created in it. Contact rate is the share of those Leads with **First Response At** set.
