# Lead attribution — runbook

What it does, how to configure it, and how to turn it off. Ships in v1.241.0 (WP-1).

The code is `erpnext_enhancements/crm_enhancements/attribution.py` (capture, propagation,
the source gate) and `web_lead.py` (the website ingress endpoint). The design rationale —
in particular *why this does not use erpnext's own `utm_source`* — is in
[`crm_enhancements/README.md`](../erpnext_enhancements/crm_enhancements/README.md) and in
the module docstrings. This file is the operational half.

---

## The state this was built to fix

Measured against production on 2026-08-04:

| | |
|---|---|
| Opportunities | 815 |
| …with no `utm_source` | **814** |
| …with no `custom_lead_source` | **809** |
| Leads | 225 |

The marketing review's "370 of 815 (45%) have no source, and those win at 22% vs 48%"
reproduces exactly — but it was measured on `tabOpportunity.source`, a column that still
physically exists (frappe never drops columns) while having **no DocField** behind it since
erpnext v15 renamed the field to `utm_source`. Nothing reads or writes it and it is invisible
in the UI. So the real coverage was not 55%. It was ~0%.

---

## Turning it on

Everything is off by default. **ERPNext Enhancements Settings → Lead Attribution.** Nothing
in this app flips these switches; a person does, in the order below.

| Setting | Effect |
|---|---|
| `lead_attribution_enabled` | Master switch for the two parts that can **refuse or accept** data: the source gate below, and the website ingress. It does **not** gate capture or propagation — see the note after this table. |
| `require_lead_source_on_lead` | Block the save of a **new** Lead with no source. Needs the master switch. |
| `require_lead_source_on_opportunity` | Same for Opportunity. This is the one that matters for spend evaluation. Needs the master switch. |
| `web_lead_ingress_enabled` | Accept POSTs from the WordPress site. Needs the master switch. |
| `web_lead_default_owner` | The named triage owner for website submissions. When blank, Leads rotate across the `Sales Team` role instead — see [lead-triage-runbook.md](lead-triage-runbook.md). |
| `web_lead_shared_secret` | Shared secret for the ingress, sent in `X-Web-Lead-Secret`. At least 32 characters; fails closed when unset or shorter. |

> **The master switch alone changes nothing you can see.** Lead → Opportunity → Customer
> propagation (`propagate_to_opportunity`, `propagate_to_customer`,
> `backfill_opportunity_to_customer`) and the capture timestamp are hooks with no flag
> check: they have run on every save since v1.241.0. So the original advice here —
> "turn on `lead_attribution_enabled` alone and leave it for a week, capture and propagation
> start working" — would have produced a week with no change at all. Stage 1 below turns the
> master switch on **together with the ingress**, which is the capture that was actually
> missing. (Found 2026-09-22, TASK-2026-01472.)

### The staged checklist

Three stages, a week or more apart. Each has a check to run **before** moving on, and a
rollback that leaves the others alone. Every check reads the **Attribution Gaps** report
(CRM Enhancements → Reports), which sorts live gaps to the top:

| Gap | Meaning |
|---|---|
| **No source** (red) | Blank Lead Source on a record created since capture started. A live process failure. |
| **Unknown (new)** (red) | `Unknown (pre-Aug 2026)` chosen on a record created on or after 2026-08-01. That is the source gate's escape hatch, used today. It is a live gap to chase, not history. (Until v1.502.1 the report filed these as *Historical*, so every bypass of the gate was invisible. Two Opportunities from August and September 2026 were already sitting there.) |
| **Historical** | The backfill bucket on a record that predates capture. Expected; tick *Only live gaps* to hide it. |

#### Stage 0 — before anything is switched on

- [ ] v1.502.1 or later is installed (`bench --site erp.sapphirefountains.com execute frappe.get_attr --args "['erpnext_enhancements.__version__']"`).
- [ ] The client address is real: step 3 of [Checks before enabling the ingress](#checks-before-enabling-the-ingress) reports `"proxy": 0`.
- [ ] Settings: `web_lead_shared_secret` set (32+ characters). **Website Lead Owner** = Brian,
      **Escalate Unanswered Leads To** = Nikolas (both set 2026-09-22).
- [ ] WordPress side installed and wired, ingress still off: [website-capture/README.md](website-capture/README.md)
      steps 1–3 and 5 (plugin, hidden fields, webhook, `utm_id` on the ads).
- [ ] **Record the baseline.** Attribution Gaps → *Created From* `2026-08-01`, *Only live gaps* ticked.
      On 2026-09-22 it read: Opportunities **18 No source + 2 Unknown (new)** of 29 created; Leads
      **1 No source** of 1 created. Write the day's numbers into TASK-2026-01472.

#### Stage 1 — capture on (week 1)

Tick **`lead_attribution_enabled`** and **`web_lead_ingress_enabled`** together. Optionally
the speed-to-lead SLA too ([lead-triage-runbook.md](lead-triage-runbook.md#turning-it-on)).
Nothing is blocked in this stage.

- [ ] **Same day:** run the acceptance test ([website-capture/README.md](website-capture/README.md), step 6).
- [ ] **Daily:** Attribution Gaps → *Created From* = the stage-1 date, *Only live gaps* ticked.
  - **No Lead owned by the website owner (Brian) may appear as No source.** Every website Lead
    gets at least *Website*, derived from its landing page, so a sourceless one means the
    webhook is not mapping the hidden fields (the README's "trap").
  - Opportunities keep appearing as **No source** at about the baseline rate (≈2.5 a week).
    Expected: nothing blocks yet. These are the people stage 2 will stop, so tell them now
    and show them the chart.
- [ ] **Converted website Leads carry their source.** Opportunity list → filter
      *Opportunity From* = Lead, *Created On* ≥ stage-1 date, *Lead Source* not set → must be
      empty. A row means propagation is not reaching the Opportunity.
- [ ] **Error Log:** no *Web Lead ingress: insert failed*, no *Web Lead ingress: secret too
      short*, no *Client IP derivation regressed*.

**Move on when**, after at least 7 days: the acceptance test passed, at least one real
website Lead arrived with its campaign, no website Lead was sourceless, and sales has been
told the stage-2 date.

**Roll back:** untick `web_lead_ingress_enabled`. WordPress keeps every entry (Fluent Forms is
the reconciliation source), so nothing is lost.

#### Stage 2 — require a source on new Opportunities (week 2)

Tick **`require_lead_source_on_opportunity`**.

Expect friction, and say so in advance: **18 of the 29 Opportunities created since
2026-08-01 (62%) would have been stopped at save**, about 2.5 a week. The message offers
`Unknown (pre-Aug 2026)` as a way out, deliberately, so nobody is blocked mid-deal.

- [ ] **Daily for the first week:** Attribution Gaps → *Created From* = the stage-2 date,
      *Only live gaps* ticked.
  - **Opportunities as No source: 0.** Any row means the gate is not firing. Check the master
    switch is still ticked. Imports, migrations and patches are exempt by design
    (`attribution._bulk_context`), so a row created by one of those is expected. Look at who
    created it.
  - **Opportunities as Unknown (new):** the escape hatch. Some are honest. If they are more
    than about a fifth of the week's new Opportunities, the gate has become a checkbox. That
    is a conversation with the owner on the chart, not a software change.
- [ ] **Automation:** anything that creates Opportunities through the API without setting a
      source now fails. Watch the Error Log and Triton's tool errors for *Lead Source is
      required* in the first days. The fountain-move conversion is unaffected: it stamps its
      own source (`fmr_lead_source`, default *Cactus & Tropicals*).

**Move on when** two consecutive weeks show zero *No source* Opportunities and *Unknown
(new)* is flat or falling.

**Roll back:** untick `require_lead_source_on_opportunity` only. **Not** the master switch,
which would also close the website ingress.

#### Stage 3 — require a source on new Leads (week 4 or later)

Tick **`require_lead_source_on_lead`**. Last, because website Leads already arrive with a
source; this stage only reaches the hand-typed ones (the phone call, the referral), which
[lead-triage-runbook.md](lead-triage-runbook.md) now asks sales to create.

- [ ] **Daily for the first week:** Attribution Gaps → *Created From* = the stage-3 date,
      *Only live gaps* ticked. **Leads as No source: 0**. **Leads as Unknown (new):** coaching,
      as in stage 2.
- [ ] Website Leads still arrive. The endpoint derives *Website* from the landing page before
      saving, so a web Lead should never hit the gate. If one does (a form not sending
      `landing_page`), WordPress gets a 500 and the Error Log gets *Web Lead ingress: insert
      failed*. Fix the form's mapping and re-send the entry from Fluent Forms.

**Roll back:** untick `require_lead_source_on_lead` only.

#### Steady state

Weekly: Attribution Gaps, *Only live gaps* ticked. Only **Unknown (new)** rows should remain,
and each one is a follow-up for its owner.

## Turning it off

**To unblock sales, untick the `require_lead_source_on_*` box that is in the way**, not the
master switch. It takes effect on the next save: no deploy, no restart, no cache to clear.
Unticking `lead_attribution_enabled` also works, but it closes the website ingress too.
Submissions then get a generic refusal, and they have to be re-sent from Fluent Forms' entry
list later.

Every value already captured is left intact either way, and the Attribution Gaps report keeps
working. The gate is a hook, not `reqd = 1` on the field, precisely so that one tickbox is the
fix.

---

## What runs where

| Trigger | Handler | What it does |
|---|---|---|
| `Lead.validate` | `stamp_capture_time`, `enforce_source` | Stamp capture time once; gate new records |
| `Opportunity.validate` | `propagate_to_opportunity`, `enforce_source` | Inherit from the Lead (or Customer); gate new records |
| `Opportunity.on_update` | `backfill_opportunity_to_customer` | Push attribution onto Customer-party deals |
| `Customer.validate` | `propagate_to_customer` | Inherit from `lead_name` |
| `bench migrate` | `patches.backfill_unknown_lead_source` | Bucket pre-Aug-2026 blanks |

Every handler is inert on a bench where the Custom Fields do not exist — they check
`frappe.db.has_column` first. That is not defensiveness for its own sake: these hooks fire
during erpnext's own test bootstrap, before fixtures are applied.

---

## The website ingress contract

**The public site is WordPress on WP Engine behind Cloudflare** (`www.sapphirefountains.com`).
ERPNext is a different host (`erp.sapphirefountains.com`). The capture script that reads the
UTM parameters runs on the WordPress side; its source, and the install and wiring procedure,
are in [`website-capture/`](website-capture/). This section is the contract between the two.

### Endpoint

```
POST https://erp.sapphirefountains.com/api/method/erpnext_enhancements.crm_enhancements.web_lead.submit_web_lead
X-Web-Lead-Secret: <web_lead_shared_secret>
Content-Type: application/json
```

**Not `Authorization: Bearer`.** That was the original contract (v1.241.0), and it could never
have worked: Frappe v16's `validate_auth` treats any two-part `Authorization` header as a
credential of its own, cannot authenticate it, and returns `401 {"exc_type":
"AuthenticationError"}` before the endpoint runs. Verified on production 2026-09-22. Fixed in
v1.501.0; nothing had ever called the endpoint, so nothing had to migrate.

### Body

```json
{
  "first_name": "Jane",
  "last_name": "Doe",
  "email_id": "jane@example.com",
  "mobile_no": "801-555-0100",
  "company_name": "Doe Landscapes",
  "notes": "Interested in a courtyard fountain",

  "utm_source": "google",
  "utm_medium": "cpc",
  "utm_campaign": "summer-2026",
  "utm_id": "21456789012",
  "utm_content": "hero-cta",
  "utm_term": "fountain installer",
  "gclid": "Cj0KCQ...",
  "landing_page": "/fountains/commercial",
  "first_referrer": "https://www.google.com/",

  "form_name": "contact-us",
  "hp_company_url": ""
}
```

Every field is optional except that **at least one of `email_id`, `mobile_no` or `phone`
must be present** — a lead nobody can contact is a row nobody can action.

- **`utm_id`** is the ad platform's own campaign ID, written by its dynamic URL macro. It equals
  `Ad Campaign.external_id` and is the spend-to-lead join key (TASK-2026-01570). Stored in
  `custom_utm_id` on Lead, Opportunity and Customer.
- **`gbraid`, `wbraid`, `msclkid`** are accepted too. They have no field of their own (only
  `gclid` can be resolved to a campaign), but any of them makes the Lead Source
  **Advertisement**, and their values are kept in the submission-context comment.

### Responses

| Status | Body | Meaning |
|---|---|---|
| 200 | `{"status": "accepted", "lead": "CRM-LEAD-…"}` | Created |
| 200 | `{"status": "accepted", "lead": null}` | Honeypot tripped. Deliberately indistinguishable from success — a bot that learns it was caught adapts. |
| 200 | `{"status": "rejected"}` | Ingress disabled |
| 400 | `{"status": "rejected", "reason": "no_contact_method"}` | No way to reach them |
| 401 | `{"status": "unauthorized"}` | Missing, wrong or too-short secret |
| 401 | `{"exc_type": "AuthenticationError"}` | Frappe's, not ours: an `Authorization` header was sent |
| 429 | — | Rate limit (120/hour) |

### Rules for the WordPress side

1. **First touch within the session.** Read `utm_*`, the click IDs, `document.referrer` and
   the landing path on *entry*, store them in a first-party cookie, and **never overwrite a
   non-empty value** within the session (30 minutes without a pageview ends it). A new session
   that arrives with campaign tags starts a new touch; one without tags changes nothing. The
   reasoning is in [`website-capture/README.md`](website-capture/README.md#what-gets-credit).
2. **Cookie, not `localStorage`.** It has to survive a subdomain hop and be readable if the
   form posts server-side.
3. **Never send a field named `sid`.** frappe pops it during auth to resume a *login*
   session, before the handler binds arguments. Anything called `sid` is silently swallowed
   and the request downgrades to Guest.
4. **Add a honeypot.** Send `hp_company_url` as a hidden, visually-concealed field. A
   non-empty value means a bot.
5. **Keep the secret server-side, in `X-Web-Lead-Secret`.** It authorizes Lead creation. It
   must never appear in page source or in a browser request, and never in `Authorization`.

### Rotating the secret

Change it in ERPNext Enhancements Settings and on the WordPress side **together**. There is
no grace window — the check is a single constant-time comparison, so a mismatch rejects
every submission with a 401 until both sides agree.

---

## Checks before enabling the ingress

1. `web_lead_shared_secret` set, **at least 32 characters**
   (`python3 -c "import secrets; print(secrets.token_urlsafe(32))"`). An unset or shorter
   secret authorizes nobody — the endpoint fails closed, which is correct but looks like a
   broken integration if you have not read this. A short one also writes one Error Log row a
   day, *Web Lead ingress: secret too short*.
2. `web_lead_default_owner` set, or accept that submissions arrive unassigned (they will
   still show up in Attribution Gaps).
3. **Confirm the client address is still real.** The IP-keyed rate limit (120/hour) is a
   per-caller control **only because of one nginx file on the VM**. The history, because an
   older revision of this step said the opposite (TASK-2026-01478):

   - **There is no Cloudflare in front of the ERP host.** `erp.sapphirefountains.com`
     answers with `via: 1.1 google` and `server: nginx/1.22.1` and no `cf-ray` — the chain is
     **GCLB → nginx → bench**. Only `www.sapphirefountains.com` (WordPress/WP Engine) is
     Cloudflare-fronted.
   - **2026-07-18 → 2026-08-02: the recorded address was the load balancer's.** Bench's nginx
     template forwards `$remote_addr`, which behind GCLB is a Google Front End in
     `35.191.0.0/16`. All 62 logins in that window were recorded from one; the three
     `Fountain Move Request` rows show `submitter_ip_claimed = 35.191.x`. Every IP-keyed rate
     limit was one global bucket.
   - **Fixed 2026-08-03** by `/etc/nginx/conf.d/00-realip.conf` (the realip module, trusting
     the front-end ranges and the load balancer's own address, `real_ip_recursive on`). 0 of
     143 logins since (to 2026-09-22) have been recorded from a proxy. The 2026-08-13 investigation that this
     step used to quote counted August 1–2 and read them as "still broken".
   - **Spoof-proof, verified 2026-09-22**: a Guest POST to this endpoint carrying a forged
     `X-Forwarded-For: 203.0.113.77` was keyed by the rate limiter on the caller's real
     address. nginx walks the header from the right and stops at the first untrusted entry,
     so a value the caller wrote — always to the left of what the load balancer appended — is
     never reached.

   The file's source of truth is [`infra/configs/nginx-realip.conf`](../infra/configs/nginx-realip.conf),
   installed on every boot by `startup_script.sh`. `utils/client_ip.check_client_ip_derivation`
   runs daily and writes an Error Log row titled **Client IP derivation regressed** the day
   logins start arriving from the load balancer again. Before enabling, run:

   ```
   bench --site erp.sapphirefountains.com execute erpnext_enhancements.utils.client_ip.check_client_ip_derivation
   ```

   A non-zero `"total"` with `"proxy": 0` means the 120/hour is per caller (`"total": 0` just
   means nobody logged in for three days — run `audit_login_ips` instead). A non-zero
   `"proxy"` means it is one bucket shared by the whole internet — fix nginx before turning the ingress on. The bearer secret stays
   the actual gate either way: an address is a rate-limit key, never an authentication factor.
4. Decide what the WordPress form does when ERPNext is unreachable. It should keep its own
   copy — this endpoint is not a queue.

---

## Monitoring

**Attribution Gaps** (CRM Enhancements; System Manager / Sales Manager / Sales User)
separates three things that look identical in a list view:

- **No source** — blank. After the backfill this can only happen to a record created on or
  after 2026-08-01, so it is a *live* process failure. Shown in red, sorted to the top.
- **Unknown (new)** — the `Unknown (pre-Aug 2026)` bucket on a record created on or after
  2026-08-01: the source gate's escape hatch, used today. Also red and sorted to the top. It
  is a live gap: somebody chose not to find out.
- **Historical** — the same bucket on a record that predates capture. Expected, nobody's
  fault, and hidden by *Only live gaps*.

Ingress failures land in the Error Log under `Web Lead ingress: insert failed`.

---

## Known gaps

- ~~**The WordPress capture script does not exist yet.**~~ **Written 2026-08-13, revised
  2026-09-22 (v1.501.0)** — see [`website-capture/`](website-capture/) for the mu-plugin, the
  capture script, the Fluent Forms field list, the webhook mapping and the per-platform
  `utm_id` tagging. It is **not installed yet**: until someone uploads it and wires the
  webhook, nothing calls the ingress, and the only attribution arriving is whatever is typed
  by hand. The acceptance test is step 6 of that README and has not yet been run.
- **Google Search Console has never worked.** `Marketing Web Snapshot` has pulled nightly
  since 2026-06-26: GA4 succeeded 40/40 days, GSC failed 40/40 with `HTTP 403` on
  `searchconsole.googleapis.com`. Organic clicks and impressions have been 0 for the entire
  history of the dataset. Half of it was the request: the setting holds the bare
  `sapphirefountains.com`, which Google reads as the one URL prefix
  `http://sapphirefountains.com/`, and v1.505.0 now tries every property form and keeps the
  one that answers. The other half is a Google-side grant (the service account is not a user
  on the property) that cannot be fixed from this repository. Once it is granted,
  `backfill_gsc_snapshots` repairs the history — see
  [Fixing the GSC 403](marketing-spend-runbook.md#fixing-the-gsc-403).
