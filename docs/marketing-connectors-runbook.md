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
| Credentials show **Auth Failed** | The token was revoked, expired (Meta), or the Google user lost access. Reconnect. The platform is skipped until you do, so there is no nightly error storm. Since v1.508.0 an expired Meta token lands here too (Meta reports it as HTTP 400, code 190, which was read as an ordinary failure before), and a token server that is merely *down* no longer does (it used to mark a good connection Auth Failed). |
| Sync Log *Failed* with `HTTP 400`/`404` on every call, after months of working | The pinned API version was retired. Bump `GOOGLE_ADS_API_VERSION`, `META_API_VERSION` or `LINKEDIN_API_VERSION` in `marketing/core/constants.py` (checked 2026-09-22: v25, v26.0, 202608). |
| `refused … not on the read-only allowlist` | Code tried to call something that is not a read. That is the guard working; the call never left ERPNext. It is a bug to fix, never a guard to loosen. |
| Spend differs from the platform UI for recent days | Expected until the platform finalizes them. The next runs restate them. |
| A Google account missing from Test | It is a manager (MCC) account, or the connecting user cannot see it. |
| *Google returned no refresh token* | The consent was granted before; Connect always asks again (`prompt=consent`). Revoke the app at myaccount.google.com → Security and reconnect. |

---

## Publishing connections (Phase 2)

Since v1.508.0 (TASK-2026-01480) the same form connects the accounts that **posts** will go out
through: the Facebook Page (and the Instagram account linked to it), the LinkedIn Company Page, and
the YouTube channel. **Nothing publishes yet.** No publisher is installed, and every publishing switch
in Marketing Settings ships off. Connecting now proves the apps and permissions work before there is
anything to post.

These are **separate connections from the ad ones**, with their own Connect button, their own token
and their own section on the form, even where the vendor is the same. A publishing token cannot reach
ad spend: no spend-capable permission is ever requested, and Meta's Connect **refuses** a login that
carries one.

All three use the same redirect URI as the ad connectors (above).

### Meta Publishing: Facebook Page + Instagram

**Needs first:** Meta App Review for the publishing permissions: `pages_show_list`,
`pages_manage_posts`, `pages_read_engagement`, `read_insights`, `instagram_basic`,
`instagram_content_publish`, `instagram_manage_insights`, `pages_manage_engagement` and
`instagram_manage_comments` (the first comment, since v1.511.0), plus `ads_read`
([approvals](marketing-platform-approvals.md), gate 2). Until review clears, only people with a role on
the app can connect.

1. The same Meta app as Meta Ads is fine; its App ID and App Secret go in again under **Meta
   Publishing**. **Save.**
2. **Facebook Page ID:** leave it blank if the person connecting manages one Page. If they manage
   several, Connect lists them. Put the right ID here and Connect again.
3. **Connect**, as someone who can **create content** on the Page. Approve every permission. Unticking
   the Instagram ones connects Facebook alone.
4. **Test** shows the Page and the Instagram account.

What is kept: **the Page token only.** The login produces a user token that can act on every Page the
person manages; it is used once, to fetch the Page token, and never stored. A Page token does not
expire, but it dies if that person loses their role on the Page, changes their Facebook password, or
removes the app. The daily check notices, clears it and marks the connection *Auth Failed*.

**Instagram** posts through the Page it is linked to, so it needs an Instagram *professional* account
linked to that Page in Meta Business Suite. The form shows the linked account, or says why there is
none.

> **If Meta asks for `ads_management`.** Some of Meta's documentation says a person whose Page role
> comes through Business Manager also needs `ads_management` to publish to Instagram. Other pages say
> `ads_read` is enough, and this connection requests `ads_read`. If Instagram fails with a permission
> error naming `ads_management`, **do not add it**: it can change ad spend, and Connect will refuse the
> login. The fallback is Meta's *Instagram API with Instagram Login*, which needs no ads permission at
> all. It is a separate connection and a change to the code, so raise it rather than working around it.

**Login Configuration ID** is optional. If Meta requires a *Facebook Login for Business* configuration
for this app, create one with the permissions above (and nothing that touches ads beyond `ads_read`)
and put its ID here. Connect still checks what the login actually granted and refuses anything
spend-capable.

Disconnect forgets the Page token. To revoke it at Meta as well, remove the app under the person's
Facebook **Settings → Business integrations**.

### LinkedIn Publishing: Company Page

**Needs first:** the **Community Management API** product, on a **second LinkedIn app** of its own.
LinkedIn requires that product to be the only one on its app, so it cannot share the ads app, and an
app holding only it cannot carry any ads permission ([approvals](marketing-platform-approvals.md),
gates 3–4). The app must be verified by a super admin of the Company Page.

1. **Auth → Authorized redirect URLs:** the redirect URI above.
2. **Marketing Connections → LinkedIn Publishing:** that app's Client ID and Client Secret. **Save.**
3. **Company Page (Organization) ID:** leave blank if the person connecting is an administrator of one
   Company Page; otherwise the number from `urn:li:organization:NNN`.
4. **Connect** as an **administrator** of the Company Page. Permissions requested:
   `w_organization_social` (post), `r_organization_social` (read posts) and `rw_organization_admin`
   (find the Company Page, and post statistics later).

Tokens: a 60-day access token and a 365-day refresh token. The access token is refreshed automatically
inside its last 7 days. **The refresh token is not extended by refreshing**, so a year after connecting
someone must click **Reconnect**. The form turns orange 30 days before, and the daily check writes the
date into *Status*. If LinkedIn issues no refresh token at all, the 60-day access token is all there is,
and the same warning applies to it.

### YouTube Publishing

**Needs first:** a Google Cloud project with the **YouTube Data API v3** and **YouTube Analytics API**
enabled, and, before anything is posted publicly, the **YouTube API audit**
([approvals](marketing-platform-approvals.md), gate 6). **Until the audit passes, every video uploaded
through the API is locked private, and the lock cannot be appealed.** Test with throwaway videos only.

1. **APIs & Services → Credentials → OAuth client ID** (type *Web application*), with the redirect URI
   above. It can be the same OAuth client as Google Ads if it lives in the same project.
2. **OAuth consent screen.** This decides whether the connection lasts:
   - **Internal**, if the channel is owned by a **@sapphirefountains.com** Workspace account (or a
     Brand Account managed by one). There is no Google verification review, and no 7-day expiry.
   - **External** otherwise. While the app is in *Testing*, **Google expires the refresh token after
     7 days**, so the connection dies weekly. Publishing it to *In production* needs Google's
     verification review for these scopes.
   - **Open question (2026-09-22):** nobody has confirmed who owns the channel. Check YouTube Studio →
     Settings → Permissions before choosing.
3. **Marketing Connections → YouTube Publishing:** Client ID and Client Secret. **Save.**
4. **Connect** and, on Google's account picker, choose the **channel's** account. A Brand Account
   appears there as its own entry. Permissions requested: `youtube.force-ssl` (upload, thumbnails,
   playlists, read the channel; decided 2026-09-22, so it can also manage the channel's videos) and
   `yt-analytics.readonly` (metrics later).

Kept: the refresh token. Google stops honoring a refresh token that goes unused for six months. The
daily check uses it, which also keeps it alive.

### The daily check (03:35)

Once **Marketing Settings → Enabled** is on, a job at **03:35** looks at every publishing connection
that shows *Connected*, and applies that connection's own rule: read the Page (Meta), refresh inside 7
days of expiry (LinkedIn), exercise the refresh token (YouTube). A credential the platform rejects is
**cleared and marked *Auth Failed*** with "Reconnect needed" in *Status*, so it is never retried every
night. A platform that is only down leaves the connection alone and records the error. The publishing
switches play no part: keeping a token alive while its network is switched off is what lets switching
it on work without reconnecting.

### When a publishing connection is wrong

| What you see | What it means |
|---|---|
| *refused: this Meta login grants …* | The person has granted this app a permission that can touch ad spend (for example `ads_management`), maybe for something else entirely. Remove it from the app's permissions (and from the Login Configuration, if one is set), have them remove and re-add the app, and connect again. |
| *Facebook publishing needs …, which was not granted* | A required permission was unticked on Meta's dialog, or App Review has not cleared it. Connect again and approve everything. |
| *this login manages N of them; set Facebook Page ID …* | The person manages several Pages. Copy the right ID from the message into **Facebook Page ID**, then Connect again. Same for LinkedIn and **Company Page (Organization) ID**. |
| *cannot post to … (no CREATE_CONTENT role)* | The person can see the Page but not post to it. Connect as someone with content access. |
| Instagram: *no professional account is linked* | Link the Instagram professional account to the Page in Meta Business Suite, then Reconnect. |
| *this Google account has no YouTube channel* | The wrong account was picked on Google's account picker. A Brand Account is a separate entry there. |
| YouTube dies after a week | The OAuth consent screen is External and in Testing. See step 2 above. |
| *Reconnect needed: …* in Status | The daily check found the credential dead: a password change, a removed role, a revoked app, or LinkedIn's year running out. Click **Reconnect**. |

---

## The publishing outbox

Since v1.509.0 (TASK-2026-01481) the records a post lives in exist, along with the machinery that
will publish it. **No post can go out yet:** no network has a publisher installed (TASK-2026-01483
to 01485), and nothing approves a post (TASK-2026-01486). Until then, a queued job just waits.

**The records:**

- **Social Account:** one per Facebook Page, Instagram account, Company Page or channel. Connect
  creates them. Untick **Enabled** to stop anything reaching that account, approved posts included.
- **Social Post:** the post. Once approved, its text, link, media, accounts and publish time are
  **locked**: what goes out is what was approved.
- **Marketing Media Asset:** a photo or video. Only one marked **Cleared for social** can be
  published. A photo of a client's fountain needs the client's approval first, and every asset
  starts as *Needs client approval*.
- **Social Publish Job:** the outbox, one row per post per account.
- **Social Post Metric:** engagement, filled in later (TASK-2026-01488).

**How a post goes out.** Approval writes one Social Publish Job per account, *Pending*, not before
its publish time. Every five minutes (:02, :07, …) the sweep claims the jobs that are due and whose
network may send, then hands each to a background worker. "May send" means all of these:

- **Enabled** in Marketing Settings;
- the network's publishing switch on;
- the connection *Connected*;
- the account enabled;
- a publisher installed.

A job for a network that is switched off simply waits. **A scheduled post waits in the database, not
in the job queue, so a deploy cannot lose it.**

| State | Means | What to do |
|---|---|---|
| Pending | Waiting for its time, or for its network to be able to send | Nothing |
| In Progress | A worker has it (for at most 30 minutes) | Nothing |
| Published | Live; the post's ID and link are recorded | Nothing |
| Failed | The network refused it (a 4xx), or five tries failed | Read *Last Error*; fix, then **Send it again** or **Cancel** |
| **Unconfirmed** | The request left ERPNext but no answer came back (a timeout, a 5xx, a worker that died). **The post may be live.** | **Check the account on the network first.** Then **It was published** (paste the link) or **Send it again** |
| Canceled | Stopped by a person | Nothing |

**Why Unconfirmed exists.** A queue that "just retries" would publish the same post twice, in public,
whenever a network timed out after accepting it. So a job that may have sent never goes back to
Pending on its own. Only a person who has looked can send it again. A job that provably sent nothing
(a throttle, a token server that was down, a worker that died before sending) retries by itself, 1,
5, 15, 60 and 240 minutes apart. A refused credential stops the job without using up a try: it waits
until someone reconnects.

The post's owner and approver get a notification when a job goes Failed or Unconfirmed. Resolving
takes a System Manager, from the job's form.

### Rate limits

Since v1.510.0 (TASK-2026-01482) the sweep asks a rate limiter before it claims a job. A job it
refuses stays **Pending**, and its *Available At* moves to when the quota comes back. *Last Error*
says why, for example "Instagram's 25 posts per 24 hours for this account is used up".

| Network | Limit | Resets |
|---|---|---|
| Instagram | Posts per rolling 24 hours, per account. The live figure once the Meta publisher has read it from Meta; **25** until then, the most conservative figure Meta has published | Rolling |
| YouTube | **100 uploads** a day, plus 100 units per upload (a thumbnail and a playlist add) from the 10,000-unit budget | Midnight **Pacific**, not site time |
| Facebook, LinkedIn | No local count: their limits are far above what the company posts | n/a |
| Any | A **429** pauses the whole connection for its *Retry-After* (a minute if none). Meta's usage headers pause Meta Publishing when any figure reaches 90%, or for as long as Meta says | When the pause runs out (never longer than a day) |

*Posts Remaining Today* on each Social Account shows what the limiter last saw. The limiter only
avoids asking for what would be refused. If it is wrong, the network's own 429 still pauses the
connection and the job retries later: nothing is lost either way.

### Facebook and Instagram (v1.511.0)

Since TASK-2026-01483 a Facebook Page or Instagram account can be published to. That happens once
all of these are true:

- **Enabled** in Marketing Settings, and the network's switch;
- Meta Publishing *Connected*;
- the post approved (TASK-2026-01486).

**Before approval: Network Check.** Every save of a Social Post fills **Network Check** with
whatever Facebook or Instagram would refuse. A post with anything there cannot be queued. The
checks use Meta's own figures:

- **Instagram:**
  - at least one photo or video, and at most 10;
  - a caption of at most 2,200 characters, 30 hashtags and 20 @mentions;
  - photos **JPEG only**, between 4:5 and 1.91:1;
  - videos MP4 or MOV, 3 seconds to 15 minutes.
- **Facebook:** text, a link or media; one video per post, never mixed with photos.
- **Both:** every file must be fetchable by Meta.

Instagram needs the width and height (and a video's duration) on the Marketing Media Asset to
check the ratio, so fill those in when you add the asset.

**Media must be reachable by Meta,** which fetches it from a URL when the post is made:

| Source on the asset | Works? |
|---|---|
| File, uploaded as **public** | Yes |
| File, **private** | No: re-upload it as public |
| Google Cloud Storage (`bucket/path`) | Yes: a link that expires after 6 hours, signed with the key in Training Settings. The bucket stays private |
| Google Drive | No: Meta cannot fetch it without sharing it with everyone |

**What goes out:**

- **Facebook:** text and link as a normal post. Photos are uploaded hidden first, then posted
  together as one post; the link goes into the text, because Facebook shows a link preview *or*
  photos. A single video posts as a video.
- **Instagram:**
  - one photo is a photo post;
  - one video is a **Reel**, also shown in the feed (Meta retired plain feed video in 2023);
  - several items make a **carousel**, and photos and videos may mix.
  - Links in Instagram captions are not clickable, so the post's link is not added.
- **First comment**, if set, is posted after the post.

**A timeout on Instagram is usually settled automatically.** If the final publish call times out
or errors, the publisher asks Instagram whether that post went live. **Yes:** it is recorded as
Published, with a note. **Provably no:** it is retried. Only when Instagram cannot say does the job
become **Unconfirmed**. Facebook has no such check, so a Facebook timeout goes to Unconfirmed as
described above.

**Things that fail after the post is live** never turn it into a failure. For example, the first
comment is refused because the permission was not granted. The job reads **Published**, and *Last
Error* says what did not happen.

**Instagram's daily limit** is read live from Instagram before each post, and handed to the rate
limiter. Meta's docs say 50 in one place and 100 in another.

### LinkedIn (v1.512.0)

Since TASK-2026-01484 the Company Page can be published to. It needs the same switches and
approval as Facebook, plus LinkedIn Publishing *Connected*.

**What goes out:**

- **Text:** as written, with hashtags.
- **A link, with no media:** a link post with a preview. **LinkedIn does not read the page to make
  the preview**, so fill in **Link Title** (and optionally **Link Description**) on the Social
  Post, or Network Check will say so.
- **One photo, or 2 to 20 photos:** JPG, PNG or GIF. A link goes into the text beside them.
- **A document**, on its own: PDF, PowerPoint or Word, up to 100 MB and 300 pages. The asset's
  title becomes the document's title on LinkedIn. Set the asset's **Type** to *Document*.
- **Video: not yet.** LinkedIn's video upload is a separate flow. Network Check refuses a LinkedIn
  video until it is built. Post the video to YouTube, or post to LinkedIn without it.

**Private files work for LinkedIn,** unlike for Meta: this app uploads the file itself instead of
LinkedIn fetching it. Google Drive does not; a file stored elsewhere on the web does not either.

**A timeout on LinkedIn:** the publisher looks for the post among the Page's latest ten. **Found:**
Published, with a note. **Not found:** Unconfirmed, and a person checks. Missing from that list is
not proof the post failed, and LinkedIn has no equivalent of Instagram's container to ask.

**The first comment may be refused.** LinkedIn's docs disagree on whether the permission we hold
covers comments. If it is refused, the post still publishes and *Last Error* says so.

**LinkedIn's Development tier allows 100 API calls per person per day,** across everything. A post
costs about three to six. The limit lifts at Standard tier.
