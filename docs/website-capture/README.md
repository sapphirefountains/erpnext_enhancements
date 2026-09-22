# Website capture — the WordPress half of lead attribution

The ERPNext half of this integration has existed since v1.241.0 and has never been called.
These files are the missing half: the thing that puts a campaign on a Lead.

Nothing here is Frappe code. It installs on **WordPress (WP Engine, behind Cloudflare)** at
`www.sapphirefountains.com`. The payload contract, the response codes and the enable/disable
switch live in [`../attribution-runbook.md`](../attribution-runbook.md) — read that first; this
document is the installation and wiring procedure.

| File | What it is |
|---|---|
| `sf-attribution.php` | mu-plugin: inlines the script, ships the honeypot's concealing CSS |
| `sf-attribution.js` | captures campaign parameters into a first-party cookie, fills the hidden fields |

`sf-attribution.js` is tested in this repo's CI (`scripts/test_sf_attribution.js`) even though
it never runs here. Edit it in the repo, not on WP Engine, or the next paste overwrites the fix.

> **v1.501.0 changed the contract.** The secret now travels in an **`X-Web-Lead-Secret`**
> header. The original `Authorization: Bearer` could never have worked: Frappe v16 rejects any
> `Authorization` header it cannot authenticate itself, with a 401, before the endpoint runs.
> Nothing had been installed yet, so there is nothing on the WordPress side to migrate.

---

## Why this shape

The site's forms are **Fluent Forms Pro**, which ships a native webhook integration with
custom request headers — so no PHP has to POST anything and no secret ever reaches the
browser. WordPress captures and stores; Fluent Forms sends, server-side, with the secret held
in its own settings.

That leaves exactly one job for the browser: know which campaign brought this person here,
and still know it three pages later when they finally fill the form in.

### What gets credit

- **First touch within a visit.** Once a visit has a value for a key, nothing later in that
  visit overwrites it. A visit ends after **30 minutes** without a pageview.
- **A new visit that arrives with campaign tags starts a new touch.** Campaign tags means any
  of `utm_*`, `gclid`, `gbraid`, `wbraid` or `msclkid`. The whole stored touch is replaced,
  landing page and referrer included, so a paid click never inherits the referrer of an
  earlier organic visit.
- **A visit without tags changes nothing.** Someone who found you through an ad and types the
  address in a week later still carries the ad for the cookie's 90 days.

Why not pure first touch: an ad click that lands on someone who once visited organically would
get no credit, and the spend report would understate every campaign that re-engages people.
Pure first touch is one constant away (`NEW_TAGGED_SESSION_REPLACES = false` in the script).
Within ERPNext, first touch across records is unchanged: a Lead's attribution is never
overwritten.

---

## The trap that wastes an afternoon

**Only fields that exist in the Fluent Forms builder, and that are mapped in the webhook, reach
ERPNext.**

The webhook serialises Fluent Forms' own submission data, not the raw HTTP POST. A hidden
input injected into the DOM by JavaScript is submitted to WordPress and then dropped, because
it is not in the form's schema. And a builder field that is not listed in the webhook's
*Selected Fields* never leaves WordPress. The symptom either way is a Lead with every
attribution field blank while the cookie is visibly correct in the browser.

So the hidden fields are **created in the builder** (step 2), **filled by the script** (step
1), and **mapped in the webhook** (step 3). All three are required.

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
beside it is inert; the PHP reads it and inlines it. (Code Snippets would also work, as a PHP
snippet that echoes the script in `wp_footer`, but a snippet has an off switch in the admin
screen. Prefer the mu-plugin.)

**Verify:** load any page with `?utm_source=install-test&utm_id=123` appended, open the console
and run `document.cookie`. An `sf_attr` entry should contain `"utm_source":"install-test"` and
`"utm_id":"123"`. Load a second page *without* the parameters and confirm the values survive.
On a WP Engine staging host (`*.wpengine.com`) the cookie is host-only; on
`www.sapphirefountains.com` it is set for `.sapphirefountains.com`.

---

## 2. Add the hidden fields to every form

In the Fluent Forms builder, for **each** form that should create a Lead, add one **Hidden
Field** per row below. The *name* is what matters; the label is for your own benefit. **Leave
every default value empty.** A default such as `{get.utm_source}` reads the URL at submit time,
which is last touch, and the script never overwrites a field that already has a value.

| Field name | Carries |
|---|---|
| `utm_source` | where the visit came from |
| `utm_medium` | cpc, paid_social, organic, email, … |
| `utm_campaign` | the campaign, as a human-readable slug |
| `utm_id` | **the ad platform's campaign ID** — the key that joins spend to this lead (see step 5) |
| `utm_content` | creative / placement |
| `utm_term` | keyword |
| `gclid` | Google Ads click ID; ERPNext can look it up in Google Ads to find the campaign |
| `gbraid` | Google Ads click ID on iOS. Not stored as a field: it marks the lead as paid and is kept in the submission comment |
| `wbraid` | as above |
| `msclkid` | Microsoft Ads click ID; same treatment as `gbraid` |
| `landing_page` | first page of the touch |
| `first_referrer` | external referrer, if any |
| `form_name` | set to a **static default** per form, e.g. `contact-us` (the one exception to "leave defaults empty") |

`form_name` is not filled by the script. It is what makes a flood attributable after the fact
and what tells you which form is producing junk.

Then add the honeypot as a **text input** (not a hidden field) named:

```
hp_company_url
```

Leave it empty with no default. The mu-plugin's CSS conceals it. It must be a real text input
because `type="hidden"` is exactly what a bot skips. The endpoint treats *present and
non-empty* as a bot and returns a fake success, so a human who somehow types in it is silently
discarded — the intended trade. Also turn on Fluent Forms' own **Global Settings → Security →
Honeypot**; it rejects bots before the webhook fires, and this field catches what it misses.

---

## 3. Configure the webhook

Fluent Forms → your form → **Settings & Integrations** → **Integrations** → **Webhook**.

| Setting | Value |
|---|---|
| Request URL | `https://erp.sapphirefountains.com/api/method/erpnext_enhancements.crm_enhancements.web_lead.submit_web_lead` |
| Request Method | `POST` |
| Request Format | `JSON` |
| Request Header | name `X-Web-Lead-Secret`, value `<web_lead_shared_secret>` |
| Request Body | **Selected Fields**, mapped as below |

> **Do not use an `Authorization` header**, not even `Bearer <secret>`. Frappe reads that header
> itself, cannot authenticate it, and answers `401 {"exc_type": "AuthenticationError"}` before
> the endpoint runs. That looks exactly like a wrong secret and is not one.

Map each key on the left to the form field on the right. Pick the right-hand side from the
field picker; the smartcodes below are what it inserts for a typical form. The `names` field
and the textarea's name differ per form, so check yours.

| Key | Field value |
|---|---|
| `first_name` | `{inputs.names.first_name}` |
| `last_name` | `{inputs.names.last_name}` |
| `email_id` | `{inputs.email}` |
| `mobile_no` or `phone` | `{inputs.phone}` |
| `company_name` | the company field, if the form has one |
| `city`, `state`, `country` | if collected |
| `notes` | the message textarea, e.g. `{inputs.message}` |
| `utm_source` … `msclkid` | `{inputs.utm_source}` … one row per hidden field in step 2 |
| `landing_page`, `first_referrer`, `form_name` | `{inputs.landing_page}`, `{inputs.first_referrer}`, `{inputs.form_name}` |
| `hp_company_url` | `{inputs.hp_company_url}` — **map it**, or the endpoint never sees the honeypot |

Anything not in the contract is dropped by the endpoint's allowlist, which is deliberate — see
`LEAD_FIELD_MAP` in `web_lead.py`. At least one of `email_id`, `mobile_no` or `phone` must be
present or the submission is rejected with `no_contact_method`.

> **Never send a key named `sid`.** Frappe pops it during auth to resume a *login* session,
> before the handler binds its arguments. A key called `sid` is silently swallowed and the
> request downgrades to Guest.

**The secret goes in the webhook header and nowhere else.** Not in a hidden field, not in page
source, not in the JS. It authorizes Lead creation.

---

## 4. Enable the ingress

In ERPNext Enhancements Settings, in this order:

1. `web_lead_shared_secret` — the same value as the webhook header, **at least 32 characters**.
   Generate one with `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`. A shorter
   one is treated as unset: every submission gets a 401, and the Error Log gets one row a day
   titled *Web Lead ingress: secret too short*.
2. `web_lead_default_owner` — or accept that submissions arrive unassigned. They still surface
   in **Attribution Gaps**.
3. `lead_attribution_enabled` = 1 — the master switch. The ingress checks this too.
4. `web_lead_ingress_enabled` = 1.

---

## 5. Tag the ads — `utm_id`

`utm_id` is how spend joins to a lead (TASK-2026-01570, decided 2026-09-22). Each platform
writes **its own campaign ID** into it with a dynamic macro, so it is exact and cannot drift
when somebody renames a campaign. ERPNext matches it against `Ad Campaign.external_id`.
`utm_campaign` stays free for a readable name.

| Platform | Where | Value |
|---|---|---|
| **Google Ads** | Admin → Account settings → Tracking → **Final URL suffix** (account level) | `utm_source=google&utm_medium=cpc&utm_id={campaignid}` |
| **Meta** | Ads Manager → each ad → Tracking → **URL parameters** | `utm_source=facebook&utm_medium=paid_social&utm_campaign={{campaign.name}}&utm_id={{campaign.id}}&utm_content={{ad.id}}` |
| **LinkedIn** | Campaign Manager → account or campaign → **tracking parameters** | `utm_source=linkedin&utm_medium=paid_social&utm_id={{CAMPAIGN_ID}}` |

Three things that silently break it:

- **Google: a lower-level suffix replaces the account one; it does not add to it.** If a
  campaign or ad group gets its own Final URL suffix, for example to set `utm_campaign`, that
  suffix must repeat `utm_id={campaignid}`. Keep auto-tagging on as well: `gclid` is the
  fallback join.
- **Meta sets URL parameters per ad.** Duplicated ads keep them; a new ad built from scratch
  does not. Bulk-edit when in doubt.
- **LinkedIn resolves macros only for Sponsored Content and Document Ads**, and renamed its
  macros in October 2025. Pick `CAMPAIGN_ID` from the macro list in the UI rather than typing it.

A lead from an ad with no `utm_id` and no resolvable `gclid` still arrives, classified as
*Advertisement*. The spend report shows it as **paid but unjoinable**, a coverage gap to fix
here, never as a free lead.

---

## 6. Acceptance test

The end-to-end test the runbook has never been able to run:

1. Visit `https://www.sapphirefountains.com/?utm_source=acceptance&utm_medium=cpc&utm_campaign=wiring-test&utm_id=TEST-ID&gclid=TEST123`.
2. Navigate to a different page — *do not* re-add the parameters.
3. Submit the form with a real contact method.
4. In ERPNext, open the newest Lead. It should carry `custom_utm_source = acceptance`,
   `custom_utm_campaign = wiring-test`, `custom_utm_id = TEST-ID`, `custom_gclid = TEST123`, a
   landing page starting `/?utm_source=acceptance`, and Lead Source **Advertisement**.
5. Confirm two Comments: the enquiry text, and the submission context
   (`form_name=…, caller_ip=…`). `caller_ip` is WP Engine's server, not your browser.

Step 2 is the part that matters. If the Lead arrives with a blank campaign, the cookie is not
surviving navigation, a field is missing from the builder or the webhook mapping, or a hidden
field has a default value. Delete the test Lead afterwards.

---

## When something is wrong

| What you see | What it means |
|---|---|
| `401 {"exc_type": "AuthenticationError"}` | An `Authorization` header was sent. Use `X-Web-Lead-Secret`. |
| `401 {"message": {"status": "unauthorized"}}` | Secret missing, different on the two sides, or under 32 characters (check the Error Log). |
| `200 {"message": {"status": "rejected"}}` | The ingress or the master switch is off. |
| `400 … "no_contact_method"` | No email or phone reached ERPNext. Check the webhook mapping. |
| `429` | More than 120 submissions in an hour from WP Engine's address. |
| A Lead with blank attribution | See [the trap](#the-trap-that-wastes-an-afternoon). |

---

## When ERPNext is unreachable

**This endpoint is not a queue and Fluent Forms does not retry.** A failed webhook leaves the
form entry in Fluent Forms and nothing in ERPNext.

That copy is the fallback, and it is a real one — Fluent Forms keeps every submission
regardless of integration outcome. The operational consequence is that **the Fluent Forms
entry list is the reconciliation source**: after any ERPNext outage, compare its entries for
the window against Leads created and re-send by hand what is missing. Nothing does this
automatically, and pretending otherwise is how enquiries get lost quietly. Note that every
merge to `main` restarts ERPNext for a minute or two.

---

## The rate limit is per caller — while one nginx file stays in place

`submit_web_lead` carries `@rate_limit(limit=120, seconds=3600)`, which keys on
`frappe.local.request_ip`. An earlier revision of this section (2026-08-13) called that
address unreliable. It had been fixed ten days before: `/etc/nginx/conf.d/00-realip.conf`
went onto the VM on **2026-08-03**, no login since has been recorded from a load-balancer
address, and on 2026-09-22 a request carrying a forged `X-Forwarded-For` was keyed on its real
caller, not on the forged value. `erp` is **GCLB → nginx → bench, with no Cloudflare in
front**, unlike `www`.

What that means for this integration: every submission arrives from **WP Engine's egress
address**, so the 120/hour is the WordPress site's own budget, and a stranger hammering the
endpoint without the secret spends their own bucket rather than the site's.

It holds only while that nginx file is on the VM. Its source is
[`infra/configs/nginx-realip.conf`](../../infra/configs/nginx-realip.conf); a daily check writes
an Error Log row titled **Client IP derivation regressed** if it goes missing. See step 3 of
the attribution runbook's pre-flight, and **TASK-2026-01478**.
