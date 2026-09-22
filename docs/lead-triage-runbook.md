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
| Qualified | Lead → **Customer** → Opportunity | Lead becomes `Converted` | Owner, from the Lead form: **Create → Customer**, then **+ Opportunity** from that Customer |
| Not a fit | Lead | `Do Not Contact` | Owner. Add a comment saying why |
| Existing customer | Opportunity from the existing **Customer** | Lead → `Converted` | Set the Lead's **Customer** field first, then create the Opportunity from that Customer |

**Qualified means**: we know what they want (service interest and a rough scope), where it
is, and that they want a price or a visit. If you are about to quote, visit or design, it is an
Opportunity. ERPNext's own **Qualification Status** field on the Lead (`Unqualified` / `In
Process` / `Qualified`) may be used on the way there, but converting the Lead is the step that
counts.

### Converting, and what happens to attribution

**Never use Create → Opportunity on the Lead.** That makes an Opportunity whose party is the
*Lead*, and on this site a won Opportunity must have a *Customer* as its party. The Closed-Won
hand-off copies `party_name` into `Project.customer`, a Lead id fails that link, and the
failure is caught and logged, so **the deal is marked won and no Project is ever created**.
Drive folders are also only provisioned for Customer-party deals. (An earlier revision of this
runbook, v1.502.0, said to use Create → Opportunity. It was wrong, and corrected in v1.504.0
before any training used it.)

The path is **Lead → Customer → Opportunity**:

1. On the Lead: **Create → Customer**. ERPNext builds the Customer from the Lead and sets
   `Customer.lead_name`, which is the link attribution follows. The Lead moves to
   `Converted` by itself, so it leaves the triage queue.
2. On the new Customer: **+ Opportunity** (or the Opportunity list with *Opportunity From* =
   Customer). This is the shape 168 of the 172 Opportunities created in 2026 already have.
3. If the enquiry is from an **existing** customer, do not create a second Customer: set the
   Lead's **Customer** field to the account, mark the Lead `Converted`, and create the
   Opportunity from that Customer.

What happens to the campaign on the way:

- `propagate_to_customer` (on the Customer's save) copies the Lead's attribution onto the
  Customer through `lead_name`: `custom_lead_source`, every `custom_utm_*` field **including
  `custom_utm_id`**, `custom_gclid`, landing page, referrer and capture time.
- `propagate_to_opportunity` (on the Opportunity's save) copies it from the Customer onto the
  Opportunity. Both fill blanks only, so a value typed by hand is never overwritten. Tested in
  `tests/test_lead_triage.py::QualificationPathTests`, both paths.
- The spend report ([Ad Spend ROAS](../erpnext_enhancements/marketing/report/ad_spend_roas/),
  TASK-2026-01477) reads the attribution *on the Opportunity*, so a deal is traced to its ad
  whichever way it was created.

This is the chain the spend report walks: Ad Campaign → Lead → Customer → Opportunity →
Project → Sales Invoice. A Lead skipped here is a paid click that can never be traced to
revenue.

**Fountain moves already work this way.** The Cactus & Tropicals flow creates the Customer
first and the Opportunity from it, for exactly the hand-off reason above.

---

## Ownership

A website Lead always gets an owner the moment it arrives:

1. **`web_lead_default_owner`** (ERPNext Enhancements Settings → Lead Attribution) — the one
   named person who triages website Leads. Set this.
2. If it is blank, or that user is disabled: **round-robin** across enabled users holding
   **Triage Rotation Role** (default `Sales Team`: 7 people on prod). Not `Sales User`: 16
   of 19 users hold that, so as a pool it means everybody. Service accounts are never in the
   pool: `triton@` holds Sales Team on prod and is a Google Group, not a person. The rotation
   remembers who was last (`DefaultValue` key `lead_triage_last_owner`).
3. If nobody qualifies, the Lead arrives unowned. It still shows in the Speed-to-Lead widget
   and in Attribution Gaps, and the SLA reminder goes to the escalation user.

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
| Escalate Unanswered Leads To | *blank* | **Required to enable the SLA.** One named person. No role fallback: every Sales Team member here also holds Sales Manager, so a role would page the whole team |
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
- At the escalation time, the **escalation user and the owner** are told, and SLA Alert
  becomes `Escalated`. Nothing further happens after that. If the escalation user has since
  been disabled, only the owner hears, and the Error Log gets one row a day saying so.
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
   The Settings page refuses to enable the SLA without the second.
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
