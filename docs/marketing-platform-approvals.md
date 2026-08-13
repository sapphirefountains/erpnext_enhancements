# Marketing platform — Phase 0: platform access & approvals

The six external gates that stand between [`marketing-platform-plan.md`](marketing-platform-plan.md)
and a post reaching a public account. Every one has a lead time measured in weeks and none can be
compressed by writing code faster, which is why they are filed in parallel with Phase 1 rather
than after it.

Tracked as **TASK-2026-01460** under *Marketing Tools* (TASK-2026-00866) on **PRJ-00580**, one
subtask per gate.

> **Nothing in this document is submitted by an agent.** It is the material a human files:
> the scopes to ask for, the words to ask in, the screencast scripts, and what must be true
> before you press submit. Creating developer apps, accepting platform terms and entering
> credentials are human actions.

**Verified against vendor documentation on 2026-08-13.** Scope names, quotas and form
locations change without notice — the [re-verify list](#re-verify-before-you-submit) at the
end names the specific facts to re-check on the day, and two of them had already moved between
the plan being written and this document being written.

---

## The correction that changes the sequencing

The plan says these gates start on day one. Half of them cannot, and knowing which half is
the difference between a filed application and a burned one.

**Three of the six gates require a working, fully integrated application before you may
apply.** LinkedIn's Standard Tier review asks for a screencast of your app performing each use
case, and its documentation is explicit that the app must already be "fully integrated". Meta
tests your app during review and rejects the submission outright if reviewers cannot reach it.
The YouTube audit reviews a live integration.

So Phase 0 splits in two:

| Wave | Gate | Blocks on |
|---|---|---|
| **Now — no code required** | Meta Business Verification | company documents only |
| | Google Ads developer token + Basic Access | an MCC account |
| | Google Business Profile access request | a 60-day-old verified profile |
| | LinkedIn **Community Management, Development Tier** | a company page + a new app |
| **After Phase 2 has a demonstrable integration** | Meta App Review (Advanced Access) | a reachable test build |
| | LinkedIn **Community Management, Standard Tier** | screencast of the built app |
| | LinkedIn Marketing Developer Platform | an ads use case to show |
| | YouTube Data API audit | a live integration |

This is not a delay. The wave-one items are exactly the ones with the longest queues — Meta
Business Verification is the documented prerequisite for Advanced Access, and LinkedIn
Development Tier is the prerequisite for Standard Tier. **Filing wave one on day one is what
makes wave two possible at all.** What changes is the expectation: day one produces four filings
and four gates still open, not six submissions in flight.

---

## Status table

Update this as each gate moves. `Filed` and `Cleared` are dates; `State` is one of
*Not started / Blocked / Filed / In review / Rejected / Cleared*.

| # | Gate | Owner | Filed | State | Cleared | Notes |
|---|---|---|---|---|---|---|
| 1 | Meta Business Verification | | | Not started | | Prerequisite for #2 |
| 2 | Meta App Review — publishing + `ads_read` | | | Not started | | One submission, all six scopes |
| 3 | LinkedIn Community Management — Dev Tier | | | Not started | | **Must be a brand-new app.** File before #4 |
| 4 | LinkedIn Community Management — Standard Tier | | | Not started | | Needs built app + screencast |
| 5 | LinkedIn Marketing Developer Platform (ads) | | | Not started | | **Separate app** from #3 |
| 6 | YouTube Data API audit | | | Not started | | Uploads locked private until cleared |
| 7 | Google Ads developer token — Basic Access | | | Not started | | From the MCC's API Center |
| 8 | Google Business Profile API access | | | Not started | | Quota `0 → 300 QPM` signals approval |

Related but not an approval — a configuration grant, tracked in Phase 1 §1.4:

| — | Search Console property grant | | | Not started | | Fixes the standing 403 |

---

## Pre-flight checklist

Most important first. Every one of these has sunk a submission somewhere.

### 1. Is the Instagram account a **Business** account, linked to the Facebook Page inside Business Manager?

The single highest-risk item, because it is a ten-minute fix that is routinely not done and
that no amount of approval works around.

- The account must be a **professional** account. Instagram publishing requires it, full stop.
- Meta's content-publishing documentation describes both flavours of the Instagram API. We want
  **Instagram API with Facebook Login**, not Instagram Login, because the same token set must
  also cover Facebook Page publishing and `ads_read`. That flavour requires the IG account to be
  linked to a Facebook Page.
- The Page and the IG account must both sit inside the same Business Manager portfolio, and the
  person doing OAuth must hold a role on both.

**Verify by:** Business Manager → Accounts → Instagram accounts. The IG account should list the
Facebook Page as connected. If Instagram was linked from the *Instagram app* rather than from
Business Manager, the link often does not exist on the Business Manager side.

Historically the guidance was Business-not-Creator. Meta's current documentation says
professional accounts, which covers both — but confirm with the actual account before assuming
Creator is fine, because product behaviour has lagged the documentation here before.

### 2. Who holds LinkedIn **Page super admin**?

Not admin — **super admin**. LinkedIn requires a super admin of the Page to verify the
developer app against the organisation, and the Development Tier review checks for exactly that.
Without a named super admin who will click the verification link, the application cannot pass.

Also needed, and non-negotiable per LinkedIn's own list:

- A **business email address** on the company domain. Personal addresses fail vetting.
- Legal organisation name, registered address, website, and a **published privacy policy URL**.
- The app name must contain no part of "LinkedIn" or "Microsoft" — including the substrings
  "Linked" or "In".

### 3. Is the YouTube channel a **Brand Account**, and who owns it?

A channel owned by a personal Google account cannot be handed over or co-managed; a Brand
Account can. Establish which it is before building an OAuth flow around it, because converting
afterwards moves the channel ID.

Record the owning Google account. Whoever runs OAuth must have it.

### 4. Which MCC will hold the Google Ads developer token?

The token is issued from the **API Center of a Google Ads manager (MCC) account** — not a
customer account, not a test account. If there is no MCC, one must be created and the production
ad account linked to it.

Google also asks for an API contact email that is actively monitored, and rejects generic or
placeholder URLs.

### 5. Are the Google Business Profile locations verified?

Google requires that you have managed a **verified, active Business Profile for at least 60
days** before it will grant API access, and that the profile lists an official website. If any
location is unverified, start that clock now — it is a hard 60-day wait that no application can
shortcut.

### 6. Cross-cutting, needed by more than one gate

- A **privacy policy URL** that is live and describes the data each platform will ask about.
  Meta, LinkedIn and Google all check it.
- A **Terms of Service URL** and an app icon for the Meta app.
- Decide the OAuth **redirect URI** now and use the same one throughout. Per the plan this
  follows the QuickBooks pattern: a guest `oauth_callback` on the production host. Changing it
  after review means re-review.
- A **test account** each reviewer can use, where the platform asks for one.

---

## Order of operations

Two orderings are load-bearing. Getting either wrong costs weeks.

**LinkedIn: Community Management first, on its own app.** LinkedIn's FAQ states that the
Community Management Development Tier request is only available on **new developer applications
that do not have access to other API products** — the option is greyed out otherwise. If the
Advertising API lands on the app first, recovering means creating a second app, taking Dev Tier
there, filming the Standard Tier screencast against it, then requesting Community Management on
the original ads app using the second app's client ID, and discarding the throwaway. That is the
documented workaround, and it is entirely avoidable by filing in the right order.

> Plan for **two LinkedIn apps from the start**: one for Community Management (organic
> publishing), one for the Marketing Developer Platform (ads reporting). This costs nothing and
> sidesteps the trap.

**A LinkedIn rejection burns the app.** Their documentation is explicit for both tiers: if your
application is rejected, you must create a **new app** and submit a new request — you cannot
re-apply with the existing one. Treat the first submission as the only cheap one.

**Meta: Business Verification before App Review.** Advanced Access requires a verified business.
Filing App Review first means waiting for verification anyway, with the review clock already
spent.

---

## Submission packets

### Gate 1 & 2 — Meta: Business Verification, then App Review

**Where:** Meta Business Manager → Business settings → Security Centre (verification), then
developers.facebook.com → your app → App Review → Permissions and Features.

**Scopes to request — all in one submission.** A second round costs weeks, and the ads and
publishing scopes are reviewed by the same process.

| Permission | For | Access level |
|---|---|---|
| `pages_manage_posts` | create/edit/delete Facebook Page posts | Advanced |
| `pages_read_engagement` | read Page content and engagement | Advanced |
| `pages_show_list` | list the Pages the user manages | Standard — no review, but it is the dependency the others hang off |
| `instagram_basic` | read the IG professional account and its media | Advanced |
| `instagram_content_publish` | publish organic IG feed photo/video posts | Advanced |
| `instagram_manage_insights` | IG insights for the analytics surface | Advanced |
| `ads_read` | Ads Insights API — spend and campaign reporting | Advanced |

`instagram_basic` and `pages_show_list` are not in the plan's list but are required
dependencies of the publishing flow; add them. Do **not** request `ads_management` — decision 3
is that ads are read-only, and asking for a write scope invites questions you have no use case
to answer. Do not request `business_management` unless a reviewer asks for it.

**Prerequisites before submitting:**

- Business Verification **complete**, not pending.
- App has icon, privacy policy URL, terms URL, and a configured platform.
- The IG-account-to-Page link from pre-flight §1 actually exists.
- Reviewers can reach a working build. Meta's documentation says a submission is rejected
  outright if they cannot access the app to test it — this is the most common failure mode.

**Use-case description.** Meta asks per-permission how the app uses the data. Write it in their
voice: concrete, first-party, no marketing language.

> Sapphire Fountains designs, builds, services and rents architectural fountains. This app is
> our internal marketing tool, used only by our own employees to manage our own Facebook Page
> and Instagram professional account. It is not offered to third parties and has no external
> users.
>
> **Publishing (`pages_manage_posts`, `instagram_content_publish`, `instagram_basic`,
> `pages_show_list`).** Our staff draft a post — typically a photograph of a fountain we have
> just completed — inside our ERP system. A second employee approves it. On approval, or at a
> scheduled time, the app publishes that post to our own Facebook Page and our own Instagram
> professional account. Employees can see what is scheduled and cancel it before it goes out.
> Nothing is published without a named employee approving it first.
>
> **Engagement and insights (`pages_read_engagement`, `instagram_manage_insights`).** After a
> post is published, the app reads back its reach, impressions and engagement counts and stores
> them against the post record, so we can see which of our projects generate interest. This is
> aggregate performance data about our own content. We do not collect or store the profiles of
> people who engage with our posts.
>
> **Advertising reporting (`ads_read`).** We run paid campaigns for our own business through our
> own ad account. The app reads campaign names and daily spend, impressions, clicks and
> conversion counts, and joins them to the sales pipeline already in our ERP, so we can measure
> cost per enquiry and return on ad spend against booked revenue rather than platform-reported
> conversions. This is read-only. The app makes no changes to campaigns, budgets or bids.
>
> All data is stored in our own single-tenant ERP instance and is visible only to our employees.
> It is not sold, shared or transferred to any third party.

**Screencast.** One report suggests Meta dropped the screen-recording requirement in a May 2026
developer update; that is a single third-party source and the submission form is the authority.
Assume it is required until the form says otherwise — see the [screencast scripts](#screencast-scripts).

**Timeline.** Officially 2–7 business days. Third-party trackers put 2026 turnaround nearer 20
days, and a rejection restarts the clock. Business Verification is separately reported to stall
for weeks. Budget six weeks and be pleasantly surprised.

---

### Gate 3 & 4 — LinkedIn Community Management API (organic publishing)

**Where:** developer.linkedin.com → create app → Products → request access.

**Scopes:** `w_organization_social` (post as the organisation), `r_organization_social` (read
posts, comments, reactions). Add the organisation and follower/share statistics products if the
analytics surface needs them.

Do **not** request `r_member_social` — LinkedIn's FAQ states it is a **closed permission** and
they are not accepting requests. Nothing in this plan needs it; we post as the organisation, not
as members.

**Two tiers, filed separately:**

*Development Tier* — file this on day one. Reviewed against:

- an approved use case,
- a **verified business email address** (check spam; the verification mail routinely lands
  there),
- verified organisation, website and domain,
- the app **verified by a super admin** of the matching LinkedIn Page.

Dev Tier rate limits, recently raised: **500 requests per app** and **100 per member** per day.
That is enough to build and test against.

*Standard Tier* — the production upgrade, filed once the integration works. Requires a company
and product overview, the use-case description, **test credentials for LinkedIn's reviewers**,
and a screencast per the script below.

**Use-case description:**

> Sapphire Fountains is a fountain design, construction, service and rental company. We have
> built a marketing module inside our own ERP system, used exclusively by our own employees to
> manage our own LinkedIn Company Page. It is an internal tool with no external customers.
>
> **Page Management.** Employees draft posts about completed projects in our ERP. A second
> employee approves each one. On approval the app publishes it to our Company Page, immediately
> or at a scheduled time. We read back comments and reactions on our own posts so the team can
> see engagement without leaving the tool and can respond promptly.
>
> **Page Analytics.** We read follower, page and share statistics for our own Page and display
> them alongside the post that produced them, so we can tell which projects generate
> professional interest.
>
> We do not store personal data about members who engage with our posts beyond what is needed
> to display a comment and its author's name and headline in our interface, and we do not
> combine LinkedIn data with data from other sources about the same individual. Everything is
> held in our own single-tenant instance, visible only to our employees, and is never sold,
> shared or transferred.

**Timeline.** Dev Tier is typically days to a couple of weeks. Standard Tier is the slow one,
and the screencast is where submissions fail.

---

### Gate 5 — LinkedIn Marketing Developer Platform (ads reporting)

**Where:** developer.linkedin.com → **a second app** → Products → Advertising API.

**Scopes:** `r_ads` (read campaign structure) and `r_ads_reporting` (retrieve reporting for ad
accounts). **Do not request `rw_ads`** — it is the read-write variant and decision 3 is
read-only. Asking for write access on a reporting use case is a rejection risk with no upside.

**Historically the slowest of the set,** and reported to be partner-gated — access may be tied
to a commercial programme rather than granted on merit. File early and treat clearing it as
uncertain: if it does not clear, Meta and Google Ads still cover most of the spend, and LinkedIn
spend can be entered as offline `Marketing Spend` rows until it does. That fallback is why the
plan keeps `Marketing Spend` alive for offline spend.

**Use-case description:**

> We advertise our own fountain design and construction business on LinkedIn. This internal
> tool reads our own ad account's campaign structure and daily performance — impressions,
> clicks, spend and conversions per campaign per day — and joins it to the sales pipeline in our
> ERP. That lets us measure cost per qualified enquiry and return on ad spend against revenue we
> have actually booked, instead of platform-reported conversions.
>
> Access is read-only. The app does not create, modify or pause campaigns and cannot change
> budgets or bids. The data is used only by our own staff, stored in our own instance, and is
> never resold or shared.

---

### Gate 6 — YouTube Data API audit

**Where:** the *YouTube API Services — Audit and Quota Extension Form*, linked from
[Quota and Compliance Audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits).

**This gate has a trap that destroys work rather than merely delaying it.** Videos uploaded
through an unaudited API project are **locked as private, and the lock cannot be appealed**.
The only remedies are to re-upload through an audited project, or to upload through the YouTube
app or site by hand. So:

> **Do not bulk-upload a back catalogue before the audit clears.** Every one of those videos
> will be locked private and will have to be uploaded again. Test with throwaway content only.

This is why the plan gates YouTube publishing on the audit rather than shipping it dormant and
hoping — a dormant feature someone switches on early does real damage here.

**What the audit asks for:** a demonstration that the project complies with the YouTube API
Services Terms of Service and the Developer Policies. Expect questions on what you upload, who
can trigger an upload, what data you store, and how long you keep it. Note that audits are also
run **periodically** after approval, and a change of project ownership requires re-filing.

**Use-case description:**

> Sapphire Fountains uploads video of our own completed fountain installations to our own
> YouTube channel. Our employees record the video on site, upload it into our internal ERP
> system, and a second employee approves it. On approval our application uploads that video to
> our channel with a title, description and tags entered by our staff.
>
> We upload only our own original content, filmed by our own employees, of our own work. We do
> not upload third-party content and we do not host, download or redistribute YouTube content.
> We read back view and engagement counts for our own videos to display alongside the project
> the video documents. All of it is used internally by our staff and is never sold or shared.

---

### Gate 7 — Google Ads developer token (Basic Access)

**Where:** Google Ads **manager (MCC) account** → Tools → API Center.

**Steps:**

1. Sign in to the **MCC**, not a customer account. The token is issued at manager level.
2. API Center → complete the token application. Company name and website URL are required;
   generic or placeholder URLs are rejected. An individual developer would use "Individual" and
   a real profile URL, but this application is from the company.
3. Set the **API contact email** to a monitored address — Google uses it for compliance notices,
   and an unread mailbox is how tokens get suspended.
4. Most applicants land on **Explorer Access** automatically, which permits production calls
   with restrictions. This is a change from the older default of test-account-only access.
5. Then apply for **Basic Access**: API Center → the dropdown beside *Access level* → *Apply for
   Basic Access*.

**Limits:** Basic Access allows 15,000 operations per day against test and production accounts —
far beyond a nightly campaign-and-daily-metrics pull for one advertiser. Standard Access
(unlimited) is not needed and should not be requested.

**Timeline:** Basic Access review is documented at around 5 business days. Brand verification is
optional and can expedite it.

**Use-case description:**

> We are a single advertiser reporting on our own Google Ads account. Our internal ERP pulls
> campaign structure and daily performance metrics — impressions, clicks, cost and conversions
> per campaign per day — once nightly, and joins them to our sales pipeline to calculate cost
> per enquiry and return on ad spend against booked revenue. The integration is read-only: it
> issues search queries against the reporting API and performs no mutate operations. It is used
> only by our own staff and manages no third-party accounts.

---

### Gate 8 — Google Business Profile API access

**Where:** the **GBP API contact form**, choosing *Application for Basic API Access*.

**Prerequisites:**

- You have managed a **verified, active Business Profile for 60+ days**.
- The profile lists an official website.
- A Google Cloud project exists — you will need its **Project Number** (not the project ID).
- Submit from an email address that is an **owner or manager on the profile**.

**Approval signal:** check the API quota in Cloud Console. **0 QPM means not yet approved; 300
QPM means approved.** There is no other reliable indicator, and the approval email is easy to
miss.

**A wrinkle that shapes what you can build.** Google split the monolithic API into purpose-built
ones — Account Management, Business Information, Performance, Q&A, Notifications, Verifications,
Place Actions, Lodging, Business Calls. **Reviews and local posts are not among them.** Both
still live on the legacy **Google My Business API v4.9** (`accounts.locations.reviews`,
`accounts.locations.localPosts`), which Google's reference marks as legacy and states requires
**additional allowlisting** beyond the standard grant.

Reviews are the primary reason this gate is on the list at all, so **say so explicitly in the
access request** and confirm after approval that v4.9 quota is non-zero — approval to the modern
APIs does not by itself grant it. Third-party posts circulate claiming the legacy endpoints have
been removed entirely; Google's own reference documentation still describes them as of
2026-08-13. Verify against live quota before building on them, and treat GBP reviews as
provisional until you have made one successful v4.9 call.

**Use-case description:**

> Sapphire Fountains operates its own verified Business Profile locations. This internal tool
> reads reviews left on our own locations so that our staff are alerted to a new review promptly
> and can draft and publish a reply from inside the system they already work in. Replies are
> written or approved by an employee before they are sent; nothing is published automatically.
> We also publish local posts about our own completed projects to our own locations.
>
> We manage only our own locations and do not offer this tool to other businesses. Review
> content and reviewer display names are stored in our own single-tenant system, shown only to
> our employees, and are never resold, redistributed or used for any purpose other than
> responding to the customer.

---

## Screencast scripts

Two are needed. Both are recordings of the **built** integration, which is why they belong to
wave two.

General requirements, taken from LinkedIn's list and safe to apply to both: high resolution,
downloadable, **only your application's screens visible** (close everything else), narration
recommended, and every use case named in the access request demonstrated on screen. If a
requested capability does not exist in your app, say so aloud in the recording rather than
skipping it silently.

### Meta — publishing and ads reporting

1. **Log in** to the ERP as an ordinary marketing employee. Show the `/marketing` interface.
2. **OAuth.** Click Connect, walk the full Facebook login and permission dialog, show every
   permission being granted, and return to the connected state. Show the Page and Instagram
   account now listed.
3. **Compose.** Create a post — a project photograph plus caption. Show the per-network preview.
4. **Approval.** Log in as a second employee, show the approval queue, approve the post. State
   in narration that publishing is impossible without this step.
5. **Publish to Facebook** (`pages_manage_posts`). Show the post appearing on the Page.
6. **Publish to Instagram** (`instagram_content_publish`, `instagram_basic`). Show it on the
   IG account.
7. **Engagement** (`pages_read_engagement`, `instagram_manage_insights`). Show reach,
   impressions and engagement counts read back onto the post record. Say plainly that this is
   aggregate performance data for our own content.
8. **Ads reporting** (`ads_read`). Show the campaign spend report — campaign, day, spend,
   clicks, conversions — and the cost-per-enquiry figure. Say plainly: read-only, no mutation
   of campaigns, budgets or bids.
9. **Close** on where the data lives and who can see it.

### LinkedIn — Community Management, Standard Tier

LinkedIn publishes explicit test cases. Follow their order; reviewers check against the list.

*Page Management:*

1. An application user approving access to their LinkedIn page data via the **complete OAuth
   flow** — show the whole consent screen.
2. A user **posting to their LinkedIn page** through the app.
3. **How a comment on that post by a member is displayed** in the app.
4. **Exactly which personal data fields from the commenter's profile** are displayed — name,
   headline, photo. Enumerate them on screen; this is the item reviewers scrutinise.
5. Any other core functionality using member personal data.

*Page Analytics:*

6. How **post performance** — reactions, impressions, clicks — is displayed.
7. Which personal data fields from members who engage with posts are displayed. If the answer is
   none beyond what step 4 showed, say so.

Narrate the approval gate: a post reaches the Page only after a second employee approves it.

---

## Quota reference sheet

These constrain the **design**, not just the runtime. Two of the three numbers in the plan of
record were out of date by the time this document was written — which is the strongest possible
argument for the rule at the bottom of this section.

### Instagram content publishing

| | |
|---|---|
| **Current documented limit** | **100** API-published posts per rolling 24 h, per account |
| Plan of record said | 25 |
| Complication | Meta's own documentation gives **both 100 and 50** in different places; the widely-cited 25 does not appear in the current docs at all |
| Carousels | count as **one** post |
| Window | rolling — capacity returns 24 h after each publish, not at midnight |

**Do not hardcode any of these numbers.** Read `GET /<IG_USER_ID>/content_publishing_limit`
before publishing and to render remaining quota in the calendar. The endpoint is authoritative,
the documentation demonstrably is not, and a limit that has moved from 25 to 100 (or 50) once
will move again. The plan's requirement that the calendar surface remaining quota stands — it
just has to source that number from the API rather than a constant.

### YouTube Data API

The quota model has changed shape since the plan was written. It is no longer one pool.

| Bucket | Allocation |
|---|---|
| `videos.insert` | **100 calls/day** — its own dedicated bucket |
| `search.list` | **100 calls/day** — its own dedicated bucket |
| Everything else | **10,000 units/day** combined |

A video upload costs **1 unit** against its own bucket, not 1,600 against the shared pool.

Two consequences:

- **The upload ceiling is ~100/day, not ~6.** The plan's "roughly six uploads per day for the
  whole app" is obsolete. Uploads must still be queued and quota-aware — the outbox does that
  anyway — but upload volume is no longer the binding constraint on design.
- **`search.list` at 100 calls/day is now the tight one.** Any feature tempted to poll YouTube
  search will exhaust it. Read known video IDs with `videos.list` (1 unit from the large pool)
  and never build a discovery feature on `search.list`.

Quota above the default requires the compliance audit — which is gate 6, and is required anyway
for the privacy lock.

### Meta rate limiting

Meta returns **`X-App-Usage`** and **`X-Business-Use-Case-Usage`** response headers.

Treat them as the authoritative signal and back off on them rather than guessing. This is the
same standing rule the chat module already follows: **the bucket is an optimisation, backoff is
the correctness mechanism.** Never retry a 4xx other than 429 — a 403 is a configuration fault,
and retrying it turns a fast, legible failure into a slow, confusing one.

### LinkedIn

| Tier | Per app/day | Per member/day |
|---|---|---|
| Community Management, Development | 500 | 100 |
| Community Management, Standard | quoted at approval | quoted at approval |

Dev Tier limits were recently raised from 100/10. Confirm Standard Tier limits when they are
granted rather than assuming.

### Google Ads

Basic Access: **15,000 operations/day**, test and production. A nightly pull for one advertiser
does not come close.

> **The rule.** Every number on this page is a snapshot taken on 2026-08-13 and two of them had
> already moved since the plan was written three weeks of platform-time earlier. Re-verify at
> submission, prefer an API that reports live quota over any documented constant, and treat
> these as the shape of the constraint rather than a contract.

---

## Re-verify before you submit

The specific facts most likely to have moved, and the ones where being wrong is expensive:

1. **Instagram's publishing limit** — 100 vs 50 vs 25. Resolve against
   `content_publishing_limit` on the live account, not the docs.
2. **The YouTube bucket model** — confirm `videos.insert` is still a dedicated 100/day bucket
   rather than 1,600 units from the shared pool. This one flipped recently.
3. **Meta's screencast requirement** — reportedly dropped in May 2026; the submission form is
   the authority.
4. **Whether GBP reviews still work on v4.9**, and whether the standard grant now includes them.
   Confirm with live quota and one successful call.
5. **Google Ads default access level** — "Explorer Access" is recent; older guides say
   test-account-only.
6. **LinkedIn's new-app-only rule** for Community Management Dev Tier, and the exact scope names
   in the current API version. LinkedIn versions its Marketing APIs by `YYYYMM` header and
   sunsets old versions on a schedule.
7. **Meta's Instagram API flavour naming** — "with Facebook Login" vs "with Instagram Login"
   carries different scope names entirely (`instagram_content_publish` vs
   `instagram_business_content_publish`). Confirm which flavour the app is configured for.

---

## What happens when a gate clears

Record the date in the [status table](#status-table), then:

- **Meta clears** → Phase 2 Facebook and Instagram publishing unblocks, and Meta Ads reporting
  unblocks in Phase 1 §1.5.
- **LinkedIn CMA Standard clears** → LinkedIn publishing unblocks.
- **LinkedIn MDP clears** → LinkedIn ads reporting unblocks. If it does not clear, LinkedIn
  spend goes in as offline `Marketing Spend` rows and the report still balances.
- **YouTube audit clears** → uploads stop being locked private. Anything uploaded before this
  must be re-uploaded.
- **Google Ads Basic Access clears** → the largest spend channel starts reporting; this is the
  one that makes §1.6 — cost per won project, ROAS against booked revenue — real.
- **GBP clears** → reviews and local posts unblock in Phase 3, with no dependency on any ad
  approval.

Phase 2 starts as soon as the **first** gate clears, per the plan. It is not gated on all six.

---

## References

- [Instagram Content Publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing/) · [Meta Permissions Reference](https://developers.facebook.com/docs/permissions) · [Meta App Review](https://developers.facebook.com/docs/app-review/) · [Business Verification](https://developers.facebook.com/docs/development/release/business-verification)
- [LinkedIn Community Management Overview](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/community-management-overview) · [Community Management App Review](https://learn.microsoft.com/en-us/linkedin/marketing/community-management-app-review)
- [YouTube Quota and Compliance Audits](https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits) · [YouTube Quota Costs](https://developers.google.com/youtube/v3/determine_quota_cost) · [Videos locked as private](https://support.google.com/youtube/answer/7300965)
- [Google Ads Developer Token](https://developers.google.com/google-ads/api/docs/get-started/dev-token) · [Google Ads Access Levels](https://developers.google.com/google-ads/api/docs/access-levels)
- [GBP Prerequisites](https://developers.google.com/my-business/content/prereqs) · [Business Profile APIs Reference Overview](https://developers.google.com/my-business/ref_overview)
