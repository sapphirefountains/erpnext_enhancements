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

Everything is off by default. **ERPNext Enhancements Settings → Lead Attribution.**

| Setting | Effect |
|---|---|
| `lead_attribution_enabled` | Master switch. Nothing below does anything while this is off. |
| `require_lead_source_on_lead` | Block the save of a **new** Lead with no source. |
| `require_lead_source_on_opportunity` | Same for Opportunity. This is the one that matters for spend evaluation. |
| `web_lead_ingress_enabled` | Accept POSTs from the WordPress site. |
| `web_lead_default_owner` | Lead Owner for website submissions. Deliberately not guessed. |
| `web_lead_shared_secret` | Bearer token for the ingress. Fails closed when unset. |

Suggested order: turn on `lead_attribution_enabled` alone first and leave it for a week —
propagation and capture start working with nothing blocked. Then turn on
`require_lead_source_on_opportunity`. Leave `require_lead_source_on_lead` until last.

## Turning it off

Untick `lead_attribution_enabled`. It takes effect on the next save — no deploy, no restart,
no cache to clear. Every value already captured is left intact, and the Attribution Gaps
report keeps working.

**If the sales team is blocked mid-day, that single tickbox is the fix.** It is why the gate
is a hook and not `reqd = 1` on the field.

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
UTM parameters and forwards a submission lives on the WordPress side and **is not in this
repository** — only the ERPNext half is. This section is what whoever owns that site needs.

### Endpoint

```
POST https://erp.sapphirefountains.com/api/method/erpnext_enhancements.crm_enhancements.web_lead.submit_web_lead
Authorization: Bearer <web_lead_shared_secret>
Content-Type: application/json
```

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

### Responses

| Status | Body | Meaning |
|---|---|---|
| 200 | `{"status": "accepted", "lead": "CRM-LEAD-…"}` | Created |
| 200 | `{"status": "accepted", "lead": null}` | Honeypot tripped. Deliberately indistinguishable from success — a bot that learns it was caught adapts. |
| 200 | `{"status": "rejected"}` | Ingress disabled |
| 400 | `{"status": "rejected", "reason": "no_contact_method"}` | No way to reach them |
| 401 | `{"status": "unauthorized"}` | Bad or missing Bearer token |
| 429 | — | Rate limit (120/hour) |

### Rules for the WordPress side

1. **First touch wins.** Read `utm_*`, `gclid`, `document.referrer` and the landing path on
   *entry*, store them in a first-party cookie, and **never overwrite a non-empty value**
   within the session. The campaign that earned the lead gets credit, not whichever
   parameter happened to be on the URL when they finally converted.
2. **Cookie, not `localStorage`.** It has to survive a subdomain hop and be readable if the
   form posts server-side.
3. **Never send a field named `sid`.** frappe pops it during auth to resume a *login*
   session, before the handler binds arguments. Anything called `sid` is silently swallowed
   and the request downgrades to Guest.
4. **Add a honeypot.** Send `hp_company_url` as a hidden, visually-concealed field. A
   non-empty value means a bot.
5. **Keep the secret server-side.** It authorises Lead creation. It must never appear in
   page source or in a browser request.

### Rotating the secret

Change it in ERPNext Enhancements Settings and on the WordPress side **together**. There is
no grace window — the check is a single constant-time comparison, so a mismatch rejects
every submission with a 401 until both sides agree.

---

## Checks before enabling the ingress

1. `web_lead_shared_secret` set. An unset secret authorises nobody — the endpoint fails
   closed, which is correct but looks like a broken integration if you have not read this.
2. `web_lead_default_owner` set, or accept that submissions arrive unassigned (they will
   still show up in Attribution Gaps).
3. **Confirm the Cloudflare → GCLB → bench chain OVERWRITES `X-Forwarded-For` rather than
   appending.** `auth.py` takes the first entry unconditionally, so with an appending proxy
   every IP-keyed rate limit in the app is spoofable. This is the same pre-flight the
   fountain-move public form carries and it has never been formally verified.
4. Decide what the WordPress form does when ERPNext is unreachable. It should keep its own
   copy — this endpoint is not a queue.

---

## Monitoring

**Attribution Gaps** (CRM Enhancements; System Manager / Sales Manager / Sales User)
separates two things that look identical in a list view:

- **No source** — blank. After the backfill this can only happen to a record created on or
  after 2026-08-01, so it is a *live* process failure. Shown in red, sorted to the top.
- **Historical** — the `Unknown (pre-Aug 2026)` bucket. Expected, nobody's fault, and
  hideable with one filter.

Ingress failures land in the Error Log under `Web Lead ingress: insert failed`.

---

## Known gaps

- **The WordPress capture script does not exist yet.** Until it does, the ingress works but
  nothing calls it, and the only attribution arriving is whatever is typed by hand. The
  end-to-end acceptance test ("a UTM-tagged link through to a submitted form produces a Lead
  carrying the full attribution set") cannot be demonstrated until then.
- **Google Search Console has never worked.** `Marketing Web Snapshot` has pulled nightly
  since 2026-06-26: GA4 succeeded 40/40 days, GSC failed 40/40 with `HTTP 403` on
  `searchconsole.googleapis.com`. Organic clicks and impressions have been 0 for the entire
  history of the dataset. This is a Google-side grant (the service account is not on the
  Search Console property, and/or it is a `sc-domain:` property being requested as a URL
  prefix), not a code bug — it cannot be fixed from this repository.
