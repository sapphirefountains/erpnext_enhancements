# Google Cloud setup for Triton chat attachments (Drive Picker)

The console and Admin-console half of "attach a file to a Triton chat". Nothing here is a
deploy; every step is a click in a Google console, and most of them are irreversible only in
the sense that you can undo them the same way.

Written for a Workspace super-admin who already owns the projects. Menu paths are given
beside every URL on purpose — the URL is what rots, the menu path is what you click. That
convention, and the `knowledge.workspace.google.com` redirect that forced it, come from
[ADR 0009 §E.5.0](../decisions/adr/0009-erpnext-google-chat-triton.md).

---

## 0. The one fact everything depends on

**ERPNext never fetches the file.** The picker runs in the user's browser, hands back a Drive
*file id*, and **Triton** downloads the bytes using **that user's own stored Google OAuth
credential**. ERPNext is a courier for an id.

That matters because the instinct — "we already have a Google service account, use it" — is
wrong here and fails in a way that reads like a bug:

> `erpnext-drive@triton-497321.iam.gserviceaccount.com` is built with **no domain-wide
> delegation** (`erpnext_enhancements/google_drive/drive_utils.py:108` — `from_service_account_info(...)`
> with no `.with_subject(...)`). It can see exactly two Shared Drives, and Google returns
> **404, not 403**, for anything else. A user picking a file out of their own My Drive would
> get "file not found" from a service account that was never going to be able to read it.
> See [training-video-drive-runbook.md](training-video-drive-runbook.md), which is the
> written record of that exact failure.

So the service account is not in this feature at all. Section 7 says what it would cost if
someone tries to put it back in.

### Two projects, and they are not interchangeable

| Project | What it is | Role in this feature |
|---|---|---|
| `erpnext-465317` | The ERPNext infrastructure project — VMs, load balancers, SSL (`infra/prod.tfvars:8`, `infra/docs/GCP_SETUP.md`) | **None.** Do not create the key or client here |
| `triton-497321` | Triton's project — Vertex AI, the Triton VM, the Drive service account (`triton/deploy/.env.base:20`) | **Everything below happens here** |

Two reasons the credentials go in `triton-497321`:

1. Google requires the Picker's `setAppId` (a **project number**) and the OAuth **client ID**
   to be in the *same* Cloud project. See §5.
2. Triton's existing per-user Google OAuth client already lives there. Keeping the picker
   beside it means one project's consent posture to reason about, not two.

**Verify before you start** that Triton's OAuth client is in `triton-497321`. Go to
**Google Auth Platform → Clients** (`https://console.cloud.google.com/auth/clients?project=triton-497321`)
and look for the Web client whose authorized redirect URI is
`https://triton.sapphirefountains.com/api/v1/auth/google/callback` — that value is Triton's
`GOOGLE_REDIRECT_URI` default (`triton/backend/app/core/config.py:110`). If it is **not**
there, stop: every project number below is wrong, and §5 tells you how to recover.

### The identities in play

| Identity | Holds | Used for |
|---|---|---|
| The signed-in ERPNext user, in their browser | A ~1 hour GIS access token, scope `drive.file`, no refresh token | Opening the picker, nothing else |
| The same person's Triton account | An encrypted refresh token with `https://www.googleapis.com/auth/drive` (`triton/backend/app/api/v1/endpoints/auth.py:55`) | **Downloading the bytes** |
| `erpnext-drive@triton-497321...` | A service-account key, full `drive` scope, access to two Shared Drives | Project folders and training video. **Not this feature** |

---

## 1. Enable the APIs

Both APIs go in **`triton-497321`**.

**Console:** Menu → **APIs & Services** → **Library**, search and enable each:

| API | Service name | Why |
|---|---|---|
| Google Picker API | `picker.googleapis.com` | The picker library itself |
| Google Drive API | `drive.googleapis.com` | Triton's `files.get`/`get_media` download, and the API restriction on the key |

**gcloud:**

```bash
gcloud services enable picker.googleapis.com drive.googleapis.com --project=triton-497321
```

**Verify:**

```bash
gcloud services list --enabled --project=triton-497321 \
  --filter="config.name:(picker.googleapis.com OR drive.googleapis.com)"
```

Both must appear. Drive API is very likely already enabled — Triton has been reading Drive
per-user for a long time — but enabling an enabled API is a no-op, so run it anyway rather
than checking first.

---

## 2. The browser API key (the Picker "developer key")

This key is **public by design**. It ships to every desk browser. Its safety comes entirely
from the two restrictions below, and a key without them is a real finding.

**Console:** Menu → **APIs & Services** → **Credentials** → **+ Create credentials** →
**API key** → **Edit API key**.

Name it something that says what it is and where it goes — e.g.
`triton-drive-picker-browser-key`.

### 2a. Application restriction — HTTP referrers

Set **Application restrictions** = **Websites**, and add exactly these:

```
https://erp.sapphirefountains.com/*
https://beta.erp.sapphirefountains.com/*
https://docs.google.com/*
```

- `erp.sapphirefountains.com` is the production desk origin
  (`infra/docs/GCP_SETUP.md`, and the same value hardcoded as the training-media CORS default
  at `infra/variables.tf:905`).
- `beta.erp.sapphirefountains.com` is the spot/test desk on the same infra. Include it or the
  picker works in prod and silently fails on beta, which is the worst possible order to
  discover it in.
- **`https://docs.google.com/*` is not optional and is the single most-missed step.** Google's
  own integration guide states the picker "renders in an iframe hosted on `docs.google.com`",
  so requests carry *that* referrer, not yours. Omit it and the picker opens to a blank or
  erroring frame with nothing useful in the console.

Do **not** add `http://localhost:8000/*` to this key. A key that accepts localhost is a key
anyone can use from their own machine. If a developer needs the picker locally, mint a
**second, separate key** with the same API restriction and only the localhost referrer, and
put that one in the dev site's settings row — never in the prod row.

### 2b. API restriction

Set **API restrictions** = **Restrict key**, and select:

- Google Picker API
- Google Drive API (only if the browser ever calls Drive directly; the design here does not,
  so leaving Drive off is the tighter choice — add it only when something breaks that names it)

An unrestricted key is a key that can spend against every API enabled on the project.

### 2c. gcloud equivalent

```bash
gcloud services api-keys create \
  --project=triton-497321 \
  --display-name="triton-drive-picker-browser-key" \
  --allowed-referrers="https://erp.sapphirefountains.com/*,https://beta.erp.sapphirefountains.com/*,https://docs.google.com/*" \
  --api-target=service=picker.googleapis.com
```

Then read the key string back (creation prints the key *resource*, not the string):

```bash
gcloud services api-keys list --project=triton-497321 --format="table(uid,displayName)"
gcloud services api-keys get-key-string <KEY_UID> --project=triton-497321
```

> **Handling.** That last command prints a live credential to your terminal. It is a browser
> key, so this is not a disaster, but do not paste it into a chat, an issue, or an Error Log.
> Copy it straight into the settings field in §6.

### 2d. Where the existing Maps key lives, and why you should not reuse it

This app already ships a browser key — `Travel Settings.google_maps_api_key`, described in its
own field as *"restrict it by HTTP referrer to your ERPNext domain(s)"*
(`erpnext_enhancements/.../travel_settings.json:119`) and handed to any logged-in user by
`api/travel.py:346-353`. The same file's sibling in ERPNext Enhancements Settings spells out
the rule to follow here:

> *"Use a **SEPARATE** key from Travel Settings so a leak here can be rotated without breaking
> trip maps."* — `erpnext_enhancements_settings.json:868`

Follow that. One key per surface. If you want to know which project the Maps key is in,
`gcloud services api-keys list` in each of `erpnext-465317` and `triton-497321` — the repo does
not record it.

---

## 3. The OAuth 2.0 Client ID

**Console:** **Google Auth Platform → Clients** → **+ Create client** → Application type
**Web application**.

| Field | Value |
|---|---|
| Name | `Triton Drive Picker (ERPNext desk)` |
| Authorized JavaScript origins | `https://erp.sapphirefountains.com` and `https://beta.erp.sapphirefountains.com` |
| Authorized redirect URIs | **leave empty** |

Origins are **scheme + host only** — no trailing slash, no path, no wildcard. That is a
different grammar from the referrer patterns in §2, and mixing them up is a five-minute bug:
`https://erp.sapphirefountains.com/*` is rejected as an origin.

### Why there is no redirect URI

The browser uses the **GIS token model** —
`google.accounts.oauth2.initTokenClient({...}).requestAccessToken()`. That flow returns the
access token to a JavaScript callback in the page that opened the popup; nothing is ever
redirected back to a URL you control, so there is no redirect URI to register. Google
validates the **origin** of the page that asked, which is why the JavaScript origins entry is
the load-bearing one.

The consequence, which §8 revisits: the token model issues **no refresh token**. It is a
~1-hour token, in memory, and that is the whole credential the browser ever holds. This is a
feature — a browser that cannot hold long-lived Drive access is a browser that cannot leak it.

### Reuse Triton's existing login client instead? — No, and here is the trade

You *could* add `https://erp.sapphirefountains.com` to the JavaScript origins of Triton's
existing login client and skip creating anything. The user would then face no second consent,
because that client already holds `drive` + `drive.file` for them
(`triton/backend/app/api/v1/endpoints/auth.py:55-56`).

Rejected, on blast radius. That client is what every Triton login depends on; a fat-fingered
edit there takes sign-in down for everyone, and the failure mode of an OAuth client edit is
not gentle. A separate client costs one extra consent click, once per user, on a
**non-sensitive** scope inside an Internal app. That is a good trade. Ship the separate
client.

---

## 4. The consent screen (Google Auth Platform)

Since the 2024–25 console reorganisation the old *APIs & Services → OAuth consent screen* now
lives at **Google Auth Platform**, split into **Branding**, **Audience**, **Data Access** and
**Clients**. ADR 0009 flagged that rename as *"⚠ VERIFY against live console"* when it was
still a rumour; it is now the live layout, and this doc treats it as fact.

### 4a. Audience = Internal

**Google Auth Platform → Audience → User type: Internal.**

Internal restricts the app to `sapphirefountains.com` accounts, which is already the domain
Triton enforces on its own login (`hd: "sapphirefountains.com"`,
`triton/backend/app/api/v1/endpoints/auth.py:196`) and which the ERPNext bridge enforces on its
side (a non-`sapphirefountains.com` account is the documented `Bridge token failed: 403` in
[error-log-runbook.md](error-log-runbook.md)).

If the project is already **External**, do not flip it casually — switching audience resets
verification state and can invalidate existing grants. Check what Triton's own client is on
first; match it.

### 4b. The scope: `drive.file`, and nothing wider

Add exactly one scope under **Data Access**:

```
https://www.googleapis.com/auth/drive.file
```

Google classifies the Drive scopes like this, and the gap is the whole argument:

| Scope | Class | What it grants |
|---|---|---|
| `drive.file` | **Non-sensitive** | Per-file access to files the user creates with the app **or explicitly hands it through the Google Picker** |
| `drive.readonly` | **Restricted** | "View and download **all** your Drive files" |

`drive.readonly` would work, and would be indefensible: it is a restricted scope granting
standing read access to every document a person owns, in exchange for the ability to attach
one of them.

**What `drive.file` actually gives you after a pick.** The user picks `Q3 Budget.xlsx`; Google
records a per-file grant of that file to *this Cloud project's app*; the browser's token can
then read that file's metadata and bytes through the Drive API. The grant persists until the
user revokes it or the file is deleted — it is not a one-shot.

**What it does NOT give you, and each of these will be mistaken for a bug at least once:**

- **No listing.** `files.list` returns only the files already granted. There is no "browse the
  user's Drive from the server" — the picker *is* the browse.
- **No access to anything the user did not pick.** Siblings in the same folder, the folder
  itself, a linked spreadsheet — all invisible.
- **No access for a *different* project's app.** The grant is scoped to the Cloud project
  behind the token. This is exactly why §5's app id and the client ID must share a project.
- **No access after revocation.** Revoking at myaccount.google.com kills the grant for every
  file at once.
- **It does not narrow Triton.** This is the honest caveat. Triton's own stored per-user
  credential already carries full `https://www.googleapis.com/auth/drive`, so when Triton
  downloads the picked file it is not constrained by the picker's `drive.file` grant at all —
  it is constrained by the user's own Drive ACL. `drive.file` narrows the **browser** token,
  which is the token most likely to leak. Do not claim in a review that `drive.file` limits
  what Triton can read; it does not.

### 4c. Verification and the security assessment

**Neither applies, because the app is Internal.** Google's own wording: for apps used only
within your Workspace organization, scopes are not listed on the consent screen and use of
restricted or sensitive scopes does not require further review.

Two consequences worth writing down, because they are the reason to hold the line at Internal:

- Going **External** with `drive.file` alone would still avoid the CASA security assessment —
  `drive.file` is non-sensitive.
- Going External with **`drive.readonly`** would trigger full verification **and** a
  third-party security assessment (CASA), which is a multi-week, recurring-cost exercise. That
  is the practical, as well as the ethical, argument for `drive.file`.

---

## 5. The app id (project number)

`PickerBuilder.setAppId()` takes the **Cloud project number** — not the project id, not the
client id. Google requires the app id and the OAuth client ID to be in the **same** Cloud
project; that is the mechanism that ties the `drive.file` grant to your app.

`triton-497321` is the project **id**. The **number** is a different value and is not recorded
anywhere in either repo, deliberately — read it live:

**Console:** Cloud console **Dashboard** → *Project info* card → **Project number**.

**gcloud:**

```bash
gcloud projects describe triton-497321 --format="value(projectNumber)"
```

If the §0 verification found Triton's OAuth client in some *other* project, then either create
the picker client in that project instead and use *its* number, or accept that the picker's
grant is scoped to a project Triton does not act as — which is fine only because Triton holds
full `drive` scope, and stops being fine the moment anyone narrows Triton to `drive.file`.
Write down which one you chose.

---

## 6. Where each value goes in ERPNext

All three go on the **Triton Assistant Settings** Single (module AI Governance,
`erpnext_enhancements/ai_governance/doctype/triton_assistant_settings/`) — the widget's
behaviour settings. They do **not** go on `Triton Settings`, which owns the *connection* and
holds the gateway secret.

| Field | Fieldtype | Encrypted? | Value |
|---|---|---|---|
| `enable_attachments` | Check | — | Master switch for chat attachments — local uploads **and** Drive. Ships `1`, but reads `0` on every existing site until `backfill_triton_assistant_settings_defaults` runs, so the feature arrives off and turns on inside the same `bench migrate` |
| `triton_drive_picker_api_key` | **Data** | **No** | The browser key from §2 |
| `triton_drive_picker_client_id` | **Data** | **No** | The `...apps.googleusercontent.com` client ID from §3 |
| `triton_drive_picker_app_id` | **Data** | **No** | The project number from §5. Data, not Int — it is an identifier that happens to be digits, and Int would drop a leading zero |

**There is no separate `drive_picker_enabled` checkbox, deliberately.** `get_config` emits the
`drive_picker` block only when **all three** credential fields are non-blank, so leaving any one
of them empty *is* the kill switch — one fewer thing that can disagree with itself. Blanking the
API key is the rollback in §8.

### Why none of them is a Password field

Two reasons, and the second one is a live bug waiting to happen:

1. **They are public by design.** A referrer-restricted browser key and an OAuth client ID are
   both shipped to the browser by every app that uses them. Encrypting a value you are about to
   publish buys nothing. This app already says so twice, in the field descriptions of
   `Travel Settings.google_maps_api_key` and
   `ERPNext Enhancements Settings.fountain_move_maps_api_key`.
2. **A Password on a Single reads back as asterisks.** Frappe's `_save_passwords` replaces the
   in-memory value with `"*" * len(value)` before `update_single` re-inserts every field row,
   so `frappe.db.get_single_value(...)` and `get_cached_doc(...).field` both return `*********`.
   Only `doc.get_password(...)` returns the real thing. Get this wrong and the browser is
   handed a string of asterisks and the picker fails with no error anywhere — the same class of
   trap `google_drive/drive_utils.get_service_account_info` exists to prevent.

The counter-example that proves the rule: `Triton Settings.maps_api_key` **is** a Password,
because it is a server-side GCP key for Vertex, never a browser key.

**No client secret is created and none is stored.** The GIS token model does not use one. If a
review finds a client secret anywhere in this feature, something has gone wrong.

### What the config endpoint may hand the browser

`triton_chat.get_config()` is the only channel to the widget, and its docstring already states
the rule: *"Browser-safe config for the widget. Never returns the gateway secret."*

It may return the four values above. It must return them **only inside the access gate** — the
endpoint is `@frappe.whitelist()` with no role check, and today every key except `enabled` is
returned unconditionally to any logged-in user, whitelisted or not. Compute
`s["enabled"] and user_has_widget_access(s)` once and nest the picker block inside it, so the
key is not handed to users the whitelist exists to exclude.

It must **never** return `Triton Settings.admin_webhook_secret` or `Triton Settings.maps_api_key`.
Those stay behind `get_gateway_config()`, which is System Manager–gated.

### Referrer restriction is not visible from ERPNext

Nothing in the app can tell whether the key you pasted is restricted. That check lives in the
console and only in the console — which is why the instruction belongs in the field's
`description`, exactly as the two existing Maps key fields do it. Write it there.

---

## 7. Domain-wide delegation: not used, and what it would cost

**The recommended design uses no service account, so no DWD grant is required. Do not make
one.** This section exists so the next person who proposes it can read why.

DWD in this app today is real but narrow: `chat/gchat/auth.py` implements keyless delegation
for the Google Chat relay, and its scope tuple is frozen at three `chat.*` scopes with a
standing note — *"Do not add a scope here speculatively — Google's own DWD guidance is 'do not
give access to non-essential OAuth scopes'"* (`chat/gchat/auth.py:160-161`). There are **no
Drive scopes on it**, and the Drive service account is a different account entirely, with no
delegation at all.

If someone did add Drive to a delegation grant, this is the exact step:

> **Admin console** → **Security** → **Access and data control** → **API controls** →
> **Manage Domain Wide Delegation** → **Add new** → paste the delegation service account's
> **numeric OAuth client id** → **OAuth scopes**, comma-separated → **Authorize**.
> The scope string would be `https://www.googleapis.com/auth/drive.readonly`.
> Changes take **up to 24 hours** to propagate, and a wrong client id and a wrong scope produce
> the identical `401 unauthorized_client`.

Refuse it. That grant gives a hand-rolled JWT signer standing, silent, unconsented read access
to **every employee's entire Drive** — HR files, drafts, personal documents in a Workspace
account — to serve a feature about attaching one file. It is made in a console no code review
sees, and revoked only in that same console. The blast radius is the whole domain, permanently;
the benefit is one turn of convenience. The per-user OAuth path in §3 gives the same
functionality constrained by the permissions each person already has, and revocation is theirs.

The same asymmetry the chat module already wrote down applies here:
*"Copying the bytes into ERPNext re-homes somebody else's ACL decision inside ours and there is
no way to un-make that later"* (`chat/sync/attachments.py:51-55`). Referencing a file the user
re-authorizes on each fetch keeps that property; a delegated service account destroys it.

---

## 8. Operations

### Token lifetime and the re-consent UX

- The browser token is **~1 hour**, held in memory, with **no refresh token**. After an hour the
  next pick needs `requestAccessToken()` again.
- With `prompt: ''` on the token client, the user is prompted **only the first time** — after
  that, re-authorization is silent. Set it, or every attach becomes a consent dialog.
- **The popup is gesture-bound.** `requestAccessToken()` must be the first thing the click
  handler does, with no `await` before it. Warm the two Google scripts when the panel opens,
  never inside the click, or the browser eats the first popup as a blocked pop-up and the user
  learns the button is broken.
- **Bridge-provisioned users have no Triton Google credential.** Triton stores the Google
  refresh token only on the Google **login** callback
  (`triton/backend/app/api/v1/endpoints/auth.py:366-400`); a user auto-provisioned by the
  ERPNext bridge has an account and no credential. For them the picker will work and the
  download will silently return nothing. The fix is one-time and human: sign in once at
  `https://triton.sapphirefountains.com` with Google. The accounts match on email, so it
  attaches to the same Triton user.

  **Built in v1.375.0.** `triton_attachments.google_link_status()` probes
  `GET /api/v1/integrations/google/drive?limit=1` as the user and reports `connected` /
  `disconnected` / `unknown`; the widget renders an inline empty state on `disconnected` and
  fails **open** on `unknown`, so a Triton outage never tells the whole company to reconnect.
  The card names the expected account, because Triton's consent flow takes no `login_hint` —
  whichever Google account the browser is signed into is the one that gets linked, and
  consenting as the wrong identity writes the credential onto a different Triton row while
  this widget keeps running as the bridge-provisioned one. Naming the address is the only
  defence available from the ERPNext side; the real fix is a Triton-side `connect` route that
  passes a `login_hint` and refuses a mismatch. See the follow-ups at the end of this file.

### When a user revokes

At **myaccount.google.com → Data & privacy → Third-party apps** a user can revoke the picker
app. Effects, in order of when you notice:

1. The next `requestAccessToken()` shows consent again (recoverable, invisible, fine).
2. Every previously granted per-file `drive.file` grant is gone at once.
3. A **previously attached** file becomes unfetchable — and that is the correct behaviour, not
   a regression. It is the property the chat module protects by refusing to copy Drive bytes.
   Any design that caches the extracted text forever quietly re-breaks it.

Revoking Triton's *login* grant is a bigger deal: it takes Calendar, Gmail and Drive with it and
the user must sign in to Triton again.

### Quota worth knowing

Drive API quotas are counted in **quota units**, not calls: currently **1,000,000 units/minute
per project** and **325,000/minute per user per project**, with **400,000,000 units/day per
project** before charges. A download costs **200 units**, a read 5, a list 100.

At those numbers a human clicking an attach button is not a quota risk — you would need
thousands of downloads a minute. Two things do matter:

- **Google updated these quotas on 2026-05-01 and has stated new pricing takes effect later in
  2026.** Drive API being metered is a change to plan for, not a surprise to absorb. Re-read the
  limits page before assuming this stays free at volume.
- The **Picker API** itself has no meaningful quota — it is a JavaScript library, and the API
  enablement is a gate, not a meter.

Also: a Workspace account can upload only 750 GB/day across all drives. Irrelevant here — we
never write to Drive — but it is the number people reach for when a Drive limit is suspected.

### Security review checklist

A reviewer of this change should be able to tick every one of these:

- [ ] The API key in `triton_drive_picker_api_key` has **Websites** application restrictions listing
      the two desk origins **and** `https://docs.google.com/*` — checked in the console, not
      inferred from this doc.
- [ ] The same key has **API restrictions** set. An unrestricted key is a finding on its own.
- [ ] No `localhost` referrer on the production key.
- [ ] The OAuth client is a **new** client, not an edit to Triton's login client.
- [ ] Its authorized **redirect URIs are empty** — a redirect URI on a token-model client means
      someone built a different flow than the one reviewed.
- [ ] The requested scope is `drive.file` and only `drive.file`. Any `drive.readonly`,
      `drive`, or `drive.metadata` in the diff or the console is a blocker.
- [ ] Consent screen **Audience = Internal**, no verification banner.
- [ ] No client **secret** exists anywhere in the repo, the settings, or the browser bundle.
- [ ] All four new settings fields are **Data/Check**, not Password (see §6 for why Password is
      actively wrong, not merely unnecessary).
- [ ] `get_config()` returns the picker block **inside** the
      `enabled and user_has_widget_access()` gate, not beside it.
- [ ] No new Drive scope was added to `chat/gchat/auth.py`'s `RELAY_SCOPES`, and no DWD grant
      was created in the Admin console.
- [ ] No code path calls `google_drive.drive_utils.get_drive_service()` for a picked file. The
      service account must not appear in this feature.
- [ ] The Google access token is never persisted — not in a DocType, not in `frappe.cache()`,
      not in an Error Log. Note that a bare re-raise out of a background job publishes frame
      locals to the Error Log; anything holding a token must `raise ... from None`.
- [ ] The Drive button cannot appear until all three of `triton_drive_picker_api_key`,
      `triton_drive_picker_client_id` and `triton_drive_picker_app_id` are set, so a
      half-finished console setup ships inert.
- [ ] `triton_drive_picker_app_id` is the project **number** of the same project the OAuth
      client lives in. A wrong or missing one makes the pick succeed and the later download
      fail — the failure is remote, in Triton, and reads as a Triton bug.
- [ ] A Drive `file_url`/webViewLink rendered as a chip goes through `isSafeUrl`
      (`public/js/chat/citations.js:412`) — a `drive.google.com` https URL passes; a `blob:`
      preview URL does not and renders as inert text.

### Rollback

Blank `triton_drive_picker_api_key`. The `drive_picker` block then drops out of `get_config`
and the Drive button stops rendering — no deploy, no console change, effective on the next
widget boot. To kill local uploads as well, untick `enable_attachments`.
That is the whole reason it is a Check field and not an inference from "is the key filled in" —
the same pattern as `Travel Settings.use_routes_api`, which exists so a console-side problem can
be switched off without a release.

Deeper rollback, in order: delete the API key (immediate, breaks only this feature), then delete
the OAuth client (revokes every user's grant). Leave the enabled APIs alone; disabling
`drive.googleapis.com` would break Triton's Drive integration wholesale.

---

## Sources

Google documentation read while writing this, September 2026. Console layouts move; menu paths
are given above precisely because these URLs will not last:

- [Integrate the Google Picker into web apps](https://developers.google.com/workspace/drive/picker/guides/web-picker) — API enablement, the `docs.google.com` referrer, `setAppId`/`setDeveloperKey`/`setOAuthToken`
- [PickerBuilder.setAppId](https://developers.google.com/workspace/drive/picker/reference/picker.pickerbuilder.setappid) — app id is the Cloud project number; same project as the client ID
- [Drive API-specific authorization and scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth) — `drive.file` non-sensitive, `drive.readonly` restricted
- [Configure the OAuth consent screen and choose scopes](https://developers.google.com/workspace/guides/configure-oauth-consent) — Internal apps need no verification for sensitive/restricted scopes
- [Use the token model (GIS)](https://developers.google.com/identity/oauth2/web/guides/use-token-model) — ~1 hour token, no refresh token, `prompt: ''`
- [Drive API usage limits](https://developers.google.com/workspace/drive/api/guides/limits) — quota units, per-method costs, the 2026-05-01 change
- [gcloud services api-keys create](https://docs.cloud.google.com/sdk/gcloud/reference/services/api-keys/create) — `--allowed-referrers`, `--api-target`, `get-key-string`

---

## The Triton side (shipped in Triton v0.75.0)

These were filed here as follow-ups and are now built, in
[sapphirefountains/triton#352](https://github.com/sapphirefountains/triton/pull/352). They are a
**separate deploy**, so ERPNext degrades gracefully until it lands: the probe falls back to the Drive
read on a 404, and the connect link falls back to the plain login. Nothing here is an ordering
dependency.

1. **`GET /api/v1/auth/google/connect` with a `login_hint`** — *done*. The single real gap. Today's
   `/auth/google/login` takes no arguments and passes no hint, so the credential lands on whichever
   Google account the browser is signed into, and `google_callback` resolves by email without ever
   comparing against the ERPNext identity that sent the person there. The route should accept the
   caller's bridge JWT (or a signed hint), pass `login_hint` to Google, and **refuse** in the callback
   when the returned email does not match — instead of silently writing the credential onto another row.
2. **Preserve the stored refresh token when the exchange returns none** — *done*. `google_callback` writes
   `"refresh_token": refresh_token` unconditionally and replaces `encrypted_key` wholesale.
   `Credentials.from_authorized_user_info` checks key *presence*, not value, so a null refresh token
   constructs fine; and because no `expiry` is stored, the credential is already expired on load and the
   refresh branch is skipped for want of a refresh token. Net: a previously-working user who clicks
   "Connect Google" a second time can end up with a permanently dead credential, silently.
   `prompt=consent` makes that unlikely, not impossible, and nothing guards it.
3. **Land somewhere sensible** — *done*, on a self-contained "you can close this tab" page with no JWT. The callback redirects to the SPA root with the Triton JWT **in a query
   string**, which then routes to `/chat`. Somebody who clicked a button in ERPNext ends up logged into a
   different product with no route back. A minimal "Google connected — you can close this tab" page, or
   an allow-listed `return_to`, would close the loop.
4. **A real `GET /integrations/google/status`** — *done*, and ERPNext's probe now prefers it. Would remove the probe's one side effect — reading Drive
   can make Triton refresh and re-save the user's OAuth token — at the cost of answering a weaker
   question (a row can exist with a revoked refresh token). Worth it only alongside (2).
5. **`docs/api-reference.md` documented `/auth/google/login` as `POST`** — *fixed*. The route is `@router.get`.

Two findings came out of adversarially reviewing that Triton change before it shipped, both fixed
there: a refused connect was leaving a live full-scope grant on the wrong Google account (it is now
revoked before the 403), and a connect could report success over a credential write that failed.
