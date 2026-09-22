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
separates two things that look identical in a list view:

- **No source** — blank. After the backfill this can only happen to a record created on or
  after 2026-08-01, so it is a *live* process failure. Shown in red, sorted to the top.
- **Historical** — the `Unknown (pre-Aug 2026)` bucket. Expected, nobody's fault, and
  hideable with one filter.

Ingress failures land in the Error Log under `Web Lead ingress: insert failed`.

---

## Known gaps

- ~~**The WordPress capture script does not exist yet.**~~ **Written 2026-08-13** — see
  [`website-capture/`](website-capture/) for the mu-plugin, the first-touch capture script, the
  Fluent Forms field list and the webhook mapping. It is **not installed yet**: until someone
  uploads it and wires the webhook, the ingress still works and nothing still calls it, and the
  only attribution arriving is whatever is typed by hand. The acceptance test ("a UTM-tagged
  link through to a submitted form produces a Lead carrying the full attribution set") is
  step 5 of that README and has not yet been run.
- **Google Search Console has never worked.** `Marketing Web Snapshot` has pulled nightly
  since 2026-06-26: GA4 succeeded 40/40 days, GSC failed 40/40 with `HTTP 403` on
  `searchconsole.googleapis.com`. Organic clicks and impressions have been 0 for the entire
  history of the dataset. This is a Google-side grant (the service account is not on the
  Search Console property, and/or it is a `sc-domain:` property being requested as a URL
  prefix), not a code bug — it cannot be fixed from this repository.
