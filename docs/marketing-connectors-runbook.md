# Ad-spend connectors — runbook

Nightly, read-only pulls of spend, clicks and impressions from **Google Ads, Meta and
LinkedIn** into ERPNext (TASK-2026-01476, v1.503.0). The code is `erpnext_enhancements/marketing/`
(see its [README](../erpnext_enhancements/marketing/README.md)); this is how a person sets it up,
checks it, and turns it off.

Everything ships **off**. Nothing runs until **Marketing Settings → Enabled** *and* a platform's
own switch are ticked, *and* that platform shows **Connected** on **Marketing Connections**.

---

## What it can and cannot do

- **It reads, and only reads.** Every request goes through one transport that refuses any method
  and path not on a short allowlist: account lists, campaign lists, daily reports, Google's click
  report. There is no code path that can create, pause, edit or fund anything, and a test fails the
  build if one appears.
- **Google Ads has no read-only permission scope** (its one scope is full access), so read-only is
  also enforced by *who* connects: a Google user whose access level in the Ads account is
  **Read only**. Even a bug could not then change spend.
- Meta connects with `ads_read`; LinkedIn with `r_ads` and `r_ads_reporting`. Both are read-only
  by definition.

What lands in ERPNext:

| DocType | One row per | Notes |
|---|---|---|
| Ad Account | ad account | Discovered on every run. Untick *Enabled* on one to stop pulling it. |
| Ad Campaign | campaign | Name, objective, status. |
| Ad Daily Metric | campaign × day | Impressions, clicks, spend (account currency), platform-reported conversions. |
| Ad Click | Google click | gclid → campaign, for Leads that arrive with a gclid and no `utm_id`. Kept 180 days unless a Lead carries it. |
| Marketing Sync Log | run × platform | Window, counts, errors. |
| Marketing Raw Payload | API response | System Manager only, tokens redacted, pruned after `raw_payload_retention_days`. |

**Each run restates the last `restate_days` (default 7)**, because the platforms revise closed days.
Rows are named from (campaign, date), so a re-pull updates them in place. An account's cursor moves
forward only when its whole run succeeded, so a failed night is simply pulled again the next night.

---

## The redirect URI

Every platform app needs this one URL registered **exactly** (the Marketing Connections form shows it
too):

```
https://erp.sapphirefountains.com/api/method/erpnext_enhancements.marketing.api.oauth_callback
```

The callback requires you to be logged in to ERPNext as a System Manager in the same browser, and it
only accepts a connection you started in the last ten minutes.

---

## Google Ads

**Needs first (Phase 0):** a Google Ads developer token with at least **Basic Access**, issued to the
manager (MCC) account ([approvals](marketing-platform-approvals.md), gate 7).

1. **Google Cloud console** → the project → **APIs & Services** → enable **Google Ads API**.
2. **OAuth consent screen:** user type *Internal*; the scope `https://www.googleapis.com/auth/adwords`.
3. **Credentials** → *Create credentials* → **OAuth client ID** → *Web application* → add the redirect
   URI above. Copy the client ID and secret.
4. **Google Ads** → the ad account → **Admin → Access and security** → invite the person who will click
   Connect with access level **Read only**. (Use that user's Google login when you connect, not an
   admin's.)
5. **Marketing Connections → Google Ads:** client ID, client secret, developer token, and the manager
   account's customer ID if that user reaches the ad account through the MCC. **Save.**
6. **Connect** → sign in as the Read-only user → approve. You land back on the form showing
   *Connected*. **Test** lists the accounts it can read; manager accounts are skipped.

Google's refresh token does not expire unless revoked or unused for six months; the connector refreshes
the access token on every run.

## Meta

**Needs first:** Meta Business Verification, and App Review for `ads_read` (Advanced Access)
([approvals](marketing-platform-approvals.md), gates 1–2).

1. **Meta for Developers** → the app (type *Business*) → add **Facebook Login for Business** and
   **Marketing API**.
2. **Facebook Login → Settings → Valid OAuth Redirect URIs:** the redirect URI above.
3. **Marketing Connections → Meta Ads:** App ID and App Secret. **Save.**
4. **Connect** → sign in as someone with access to the ad account in Business Manager → approve.

**Meta's token lasts about 60 days and cannot be renewed without a person.** The form shows the expiry
date and an orange warning in the last 10 days. Click **Reconnect** before it lapses. If it does lapse,
the next run marks Meta *Auth Failed* and the other platforms carry on.

## LinkedIn

**Needs first:** the **Advertising API** (Marketing Developer Platform) product on a LinkedIn app,
verified against the company page ([approvals](marketing-platform-approvals.md), gate 5). If it never
clears, enter LinkedIn spend by hand in **Marketing Spend** instead.

1. **LinkedIn Developer Portal** → the app → **Auth** → **Authorized redirect URLs:** the redirect URI
   above.
2. **Marketing Connections → LinkedIn Ads:** client ID and client secret. **Save.**
3. **Connect** → sign in as a user with a role on the ad account → approve.

The access token lasts 60 days and is refreshed automatically within its last 7 days, using a refresh
token that lasts a year (LinkedIn issues refresh tokens to Marketing Developer Platform apps). Expect to
reconnect once a year.

---

## Turning it on

1. Connect each platform as above; **Test** should list your accounts.
2. **Marketing Settings:** tick **Enabled** and each connected platform's switch. Leave the dials at
   their defaults (restate 7 days, first backfill 90 days).
3. **Marketing Connections → Sync now.** It queues one background job; the first run backfills 90 days.
4. Check **Marketing Sync Log**: one row per platform, *Completed*. Then **Ad Daily Metric**, filtered to
   yesterday, and compare one campaign's spend with the platform's own UI. They should match to the cent,
   in the account's currency.
5. From then on it runs nightly at **03:25** (site time). A second trigger within 20 hours of the last is
   skipped.

## Verify from the bench

```
bench --site erp.sapphirefountains.com execute frappe.get_all --kwargs "{'doctype': 'Marketing Sync Log', 'fields': ['name','platform','status','window_from','window_to','updated_count','failed_count'], 'order_by': 'creation desc', 'limit_page_length': 6}"
```

## Turning it off

Untick the platform's switch, or **Enabled** in Marketing Settings for all of them. Nothing already
pulled is deleted. **Disconnect** on Marketing Connections forgets the stored tokens (and revokes
Google's).

---

## When something is wrong

| What you see | What it means |
|---|---|
| Credentials show **Auth Failed** | The token was revoked, expired (Meta), or the Google user lost access. Reconnect. The platform is skipped until you do, so there is no nightly error storm. |
| Sync Log *Failed* with `HTTP 400`/`404` on every call, after months of working | The pinned API version was retired. Bump `GOOGLE_ADS_API_VERSION`, `META_API_VERSION` or `LINKEDIN_API_VERSION` in `marketing/core/constants.py` (checked 2026-09-22: v25, v26.0, 202608). |
| `refused … not on the read-only allowlist` | Code tried to call something that is not a read. That is the guard working; the call never left ERPNext. It is a bug to fix, never a guard to loosen. |
| Spend differs from the platform UI for recent days | Expected until the platform finalizes them. The next runs restate them. |
| A Google account missing from Test | It is a manager (MCC) account, or the connecting user cannot see it. |
| *Google returned no refresh token* | The consent was granted before; Connect always asks again (`prompt=consent`). Revoke the app at myaccount.google.com → Security and reconnect. |
