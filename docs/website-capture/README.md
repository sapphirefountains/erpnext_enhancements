# Website capture — the WordPress half of lead attribution

The ERPNext half of this integration has existed since v1.241.0 and has never been called.
These two files are the missing half: the thing that puts a campaign on a Lead.

Nothing here is Frappe code. It installs on **WordPress (WP Engine, behind Cloudflare)** at
`www.sapphirefountains.com`. The payload contract, the response codes and the enable/disable
switch live in [`../attribution-runbook.md`](../attribution-runbook.md) — read that first; this
document is the installation and wiring procedure.

| File | What it is |
|---|---|
| `sf-attribution.php` | mu-plugin: inlines the script, ships the honeypot's concealing CSS |
| `sf-attribution.js` | first-touch capture into a first-party cookie, fills the hidden fields |

---

## Why this shape

The site's forms are **Fluent Forms Pro**, which ships a native webhook integration with
custom request headers — so no PHP has to POST anything and no secret ever reaches the
browser. WordPress captures and stores; Fluent Forms sends, server-side, with the bearer
token held in its own settings.

That leaves exactly one job for the browser: know which campaign brought this person here,
and still know it three pages later when they finally fill the form in.

---

## The trap that wastes an afternoon

**Only fields that exist in the Fluent Forms builder reach ERPNext.**

The webhook serialises Fluent Forms' own submission data, not the raw HTTP POST. A hidden
input injected into the DOM by JavaScript is submitted to WordPress and then dropped, because
it is not in the form's schema. The symptom is a Lead that arrives with every attribution
field blank while the cookie is visibly correct in the browser.

So the hidden fields are **created in the builder** and **filled by the script**. Both halves
are required. This is why step 2 below comes before step 3.

---

## 1. Install the plugin

Upload **both files** to `wp-content/mu-plugins/`:

```
wp-content/mu-plugins/sf-attribution.php
wp-content/mu-plugins/sf-attribution.js
```

Must-use plugins load automatically and have no activation step and no deactivate button —
deliberate, so attribution cannot be switched off from the plugins screen by someone tidying
up. WordPress only auto-loads top-level `.php` from that directory, so the `.js` sitting
beside it is inert; the PHP reads it and inlines it.

**Verify:** load any page on the site with `?utm_source=install-test` appended, open the
console and run `document.cookie`. An `sf_attr` entry should be present containing
`utm_source: "install-test"`. Load a second page *without* the parameter and confirm the value
survives — that is first-touch working.

---

## 2. Add the hidden fields to every form

In the Fluent Forms builder, for **each** form that should create a Lead, add one **Hidden
Field** per row below. The *name* is what matters; the label is for your own benefit.

| Field name | Carries |
|---|---|
| `utm_source` | where the visit came from |
| `utm_medium` | cpc, organic, email, … |
| `utm_campaign` | the campaign |
| `utm_content` | creative / placement |
| `utm_term` | keyword |
| `gclid` | Google Ads click ID |
| `gbraid` | Google Ads click ID on iOS — a campaign reading only `gclid` loses this traffic |
| `wbraid` | as above |
| `msclkid` | Microsoft Ads click ID |
| `landing_page` | first page of the visit |
| `first_referrer` | external referrer, if any |
| `form_name` | set to a **static default** per form, e.g. `contact-us` |

`form_name` is not filled by the script — give it a static default value in the builder, unique
per form. It is what makes a flood attributable after the fact and what tells you which form
is producing junk.

Then add the honeypot as a **text input** (not a hidden field) named:

```
hp_company_url
```

Leave it empty with no default. The mu-plugin's CSS conceals it. It must be a real text input
because `type="hidden"` is exactly what a bot skips; the endpoint treats *present and
non-empty* as a bot and returns a fake success, so a human who somehow types in it is silently
discarded — which is the intended trade.

---

## 3. Configure the webhook

Fluent Forms → your form → **Settings & Integrations** → **Integrations** → **Webhook**.

| Setting | Value |
|---|---|
| Request URL | `https://erp.sapphirefountains.com/api/method/erpnext_enhancements.crm_enhancements.web_lead.submit_web_lead` |
| Request Method | `POST` |
| Request Format | `JSON` |
| Request Header | `Authorization: Bearer <web_lead_shared_secret>` |
| Request Body | *Select fields* — map each form field to the payload key |

Map your visible fields onto the contract's names: `first_name`, `last_name`, `email_id`,
`mobile_no`, `phone`, `company_name`, `website`, `city`, `state`, `country`, and the free-text
enquiry to `notes`. Anything not in that list is dropped by the endpoint's allowlist, which is
deliberate — see `LEAD_FIELD_MAP` in `web_lead.py`.

At least one of `email_id`, `mobile_no` or `phone` must be present or the submission is
rejected with `no_contact_method`. A lead nobody can contact is a row nobody can action.

> **Never send a field named `sid`.** Frappe pops it during auth to resume a *login* session,
> before the handler binds its arguments. A key called `sid` is silently swallowed and the
> whole request downgrades to Guest — which presents as a baffling 401 against a token you can
> see is correct.

**The secret goes in the webhook header and nowhere else.** Not in a hidden field, not in page
source, not in the JS. It authorises Lead creation.

---

## 4. Enable the ingress

In ERPNext Enhancements Settings, in this order:

1. `web_lead_shared_secret` — the same value as the webhook header. Unset authorises nobody:
   the endpoint fails closed, which is correct and looks exactly like a broken integration.
2. `web_lead_default_owner` — or accept that submissions arrive unassigned. They still surface
   in **Attribution Gaps**.
3. `lead_attribution_enabled` = 1 — the master switch. The ingress checks this too.
4. `web_lead_ingress_enabled` = 1.

---

## 5. Acceptance test

The end-to-end test the runbook has never been able to run:

1. Visit `https://www.sapphirefountains.com/?utm_source=acceptance&utm_medium=cpc&utm_campaign=wiring-test&gclid=TEST123`.
2. Navigate to a different page — *do not* re-add the parameters.
3. Submit the form with a real contact method.
4. In ERPNext, open the newest Lead. It should carry `custom_utm_source = acceptance`,
   `custom_utm_campaign = wiring-test`, `custom_gclid = TEST123`, and a landing page of `/`.
5. Confirm two Comments: the enquiry text, and the submission context.

Step 2 is the part that matters. If the Lead arrives with a blank campaign, the cookie is not
surviving navigation and the value being sent is whatever was on the URL at submit time —
which is last-touch wearing first-touch's clothes.

---

## When ERPNext is unreachable

**This endpoint is not a queue and Fluent Forms does not retry.** A failed webhook leaves the
form entry in Fluent Forms and nothing in ERPNext.

That copy is the fallback, and it is a real one — Fluent Forms keeps every submission
regardless of integration outcome. The operational consequence is that **the Fluent Forms
entry list is the reconciliation source**: after any ERPNext outage, compare its entries for
the window against Leads created and re-send by hand what is missing. Nothing does this
automatically, and pretending otherwise is how enquiries get lost quietly.

---

## Known weakness: the rate limit is not per-client

`submit_web_lead` carries `@rate_limit(limit=120, seconds=3600)`, which keys on
`frappe.local.request_ip`. As of 2026-08-13 that address is **unreliable on this
infrastructure** — it frequently records the Google load balancer rather than the caller, so
the limit behaves as a single global budget shared by every source, and a caller who sets
their own `X-Forwarded-For` can very likely land in a bucket of their choosing.

This does not undermine the ingress itself: `web_lead.py` treats the IP as advisory, never
decides on it, and gates on the bearer secret. But do not read the 120/hour as a per-client
control, and do not add one that depends on the IP.

The investigation is on **TASK-2026-01478**; the topology finding it corrects — `erp` is
**GCLB → nginx → bench with no Cloudflare in front**, unlike `www` — is recorded there and in
the attribution runbook.
