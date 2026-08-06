# Error Log runbook — the failures that are not code

Companion to the v1.254.0 debugging pass. That release fixed the Error Log
signatures that were **defects in this app**; everything below is a signature
that no code change can fix, because the cause is an expired credential, a
missing grant, an unshared calendar, or a binary that is not installed on the
host.

Each section says what the row means, how to confirm it is still true, and the
fix — in **Bash** (macOS/Linux, or an SSH session on the server) and
**PowerShell** (Windows workstation). The commands are equivalent, not
identical: PowerShell examples use `gcloud compute ssh` to reach the bench,
because that is how a Windows workstation gets there.

> **Read before running anything.** `bench` commands must run as the `frappe`
> user from `/home/frappe/frappe-bench`. The site is
> `erp.sapphirefountains.com`. Nothing here writes to business data; the only
> destructive command in this document is the Error Log prune in
> [§9](#9-error-log-retention) — which is optional, and called out where it appears.

---

## Contents

| # | Signature | Cause | Still firing? |
|---|---|---|---|
| [1](#1-mdm-retry-failed-for-miradore--mdm-action1-token-refresh-failed) | `MDM retry failed for Miradore`, `MDM Action1 token refresh failed` | Both MDM credentials dead | Paused, not fixed |
| [2](#2-gsc-api-error) | `GSC API Error` | Service account not a user on the Search Console property | Yes |
| [3](#3-finance-calendar-fetch-failed) | `Finance Calendar fetch failed` | Calendar id wrong or not shared | Yes |
| [4](#4-training-media) | `Training media` | Missing GCS IAM binding | Yes |
| [5](#5-smtp-relay--email-account) | SMTP `550 5.7.1`, "no default Email Account" | Sending IP not registered in Workspace | Intermittent |
| [6](#6-attempted-unauthorized-file-access-in-pdf-generator) | `Attempted Unauthorized File Access in PDF Generator` | Letterhead points at a malformed file path | Yes |
| [7](#7-frappemodeldocumentexecute_action) | `frappe.model.document.execute_action` | Upstream Frappe v16 bug | Yes |
| [8](#8-report-execution-failures) | `Report execution failed for: …` | Auto Email Report with no filters | Yes |
| [9](#9-error-log-retention) | 78,648 rows in the table | Ninety days of history, one huge incident in it | **Nothing to fix** |
| [10](#10-filelocks) | `Filelock: Failed to aquire …` | Overlapping bench runs | Intermittent |
| [11](#11-get_lesson-missing-1-required-positional-argument-attempt) | `get_lesson() missing …` | Stale browser tab | Self-healing |
| [12](#12-bridge-token-failed-403) | `Bridge token failed: 403` | Non-`sapphirefountains.com` account | By design |

---

## Triage first

Before fixing anything, see what is actually still failing. Everything in the
Error Log is historical by default — the two biggest signatures in the table
stopped months ago.

**Bash**

```bash
bench --site erp.sapphirefountains.com mariadb <<'SQL'
SELECT method, COUNT(*) AS rows_logged, MAX(creation) AS last_seen
FROM `tabError Log`
WHERE creation >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY method
ORDER BY rows_logged DESC
LIMIT 25;
SQL
```

**PowerShell**

```powershell
$sql = @"
SELECT method, COUNT(*) AS rows_logged, MAX(creation) AS last_seen
FROM ``tabError Log``
WHERE creation >= DATE_SUB(NOW(), INTERVAL 7 DAY)
GROUP BY method ORDER BY rows_logged DESC LIMIT 25;
"@
gcloud compute ssh frappe-erp --zone us-central1-a --command `
  "cd /home/frappe/frappe-bench && bench --site erp.sapphirefountains.com mariadb -e '$sql'"
```

A signature whose `last_seen` is weeks old needs no fix at all. Retention will
remove it on its own — see [§9](#9-error-log-retention), which explains why the
table's size is not the problem it looks like.

---

## 1. `MDM retry failed for Miradore` / `MDM Action1 token refresh failed`

**What it means.** Both MDM providers' credentials are dead. Miradore returns
`401 Unauthorized` on `GET /devices`; Action1's OAuth token refresh fails.

**Why it matters more than the row count suggests.** This is the signature that
wrote **44,069 rows in about thirty hours** on 2026-06-15/16 — 88% of the Error
Log at the time — because the retry loop hammered a known-bad credential every
cycle. The app now pauses a provider after a non-retryable failure
(`*_auth_blocked`), which is why the storm stopped. **The credentials were never
fixed.** Both providers currently read `status = Failed`, `auth_blocked = 1`,
and Miradore has never completed a sync (`miradore_last_sync` is null).

So: device inventory has been stale since June. The log is quiet because the app
gave up, not because the problem went away.

**Confirm.**

```bash
bench --site erp.sapphirefountains.com console <<'PY'
s = frappe.get_single("MDM Settings")
for f in ("miradore_status", "miradore_auth_blocked", "miradore_last_sync",
          "action1_status", "action1_auth_blocked", "action1_last_sync"):
    print(f, "=", s.get(f))
PY
```

**Fix.** This is a credential rotation, done in the vendor consoles:

1. **Miradore** — Miradore console → *System* → *API access* → issue a new API
   key. Paste it into **MDM Settings** in ERPNext.
2. **Action1** — Action1 console → *Settings* → *API access* → issue a new
   client id/secret pair. Paste both into **MDM Settings**.
3. Save MDM Settings. Saving clears `auth_blocked` on both providers.
4. Press **Test Connection** for each provider. A pass also clears the pause.
5. Run a sync and confirm the cursor advances:

```bash
bench --site erp.sapphirefountains.com execute \
  erpnext_enhancements.mdm_integration.sync.run_device_sync --args "['Miradore']"
bench --site erp.sapphirefountains.com execute \
  erpnext_enhancements.mdm_integration.sync.run_device_sync --args "['Action1']"
```

**PowerShell**

```powershell
gcloud compute ssh frappe-erp --zone us-central1-a --command `
  "cd /home/frappe/frappe-bench && bench --site erp.sapphirefountains.com execute erpnext_enhancements.mdm_integration.sync.run_device_sync --args ""['Miradore']"""
```

Do **not** re-enable the retry loop before the keys are replaced. The pause is
the only thing standing between a dead credential and another 44,000 rows.

---

## 2. `GSC API Error`

**What it means.** Search Console returns `403 User does not have sufficient
permission for site 'http://sapphirefountains.com'`. The GA4 service account can
authenticate — it just is not a user on that property.

Note the scheme in Google's message: `http://`, not `https://`. A Search Console
property is scheme- and host-exact, so `https://sapphirefountains.com`,
`http://sapphirefountains.com` and `sc-domain:sapphirefountains.com` are three
different properties. Granting access on the wrong one produces exactly the same
403. Check which one **GA4 Settings → GSC Property URL** actually holds before
granting anything.

**Confirm** (prints the service account address to grant, from the uploaded
credentials file):

```bash
bench --site erp.sapphirefountains.com console <<'PY'
import json, os, frappe
s = frappe.get_single("GA4 Settings")
print("configured property:", s.gsc_property_url)
path = frappe.get_site_path("private", "files", s.credentials_json.split("/")[-1])
print("service account:", json.load(open(path)).get("client_email"))
PY
```

**Fix.**

1. Open [Search Console](https://search.google.com/search-console) as an owner of
   the property.
2. Confirm the property you are in matches **GA4 Settings → GSC Property URL**
   exactly, scheme included. If it does not, either add the missing property or
   correct the setting — whichever is right is a business decision, not a
   technical one.
3. *Settings* → *Users and permissions* → *Add user*.
4. Paste the `client_email` printed above. **Full** permission is not required;
   *Restricted* is enough for `searchanalytics.query`.
5. Re-run the fetch:

```bash
bench --site erp.sapphirefountains.com execute \
  erpnext_enhancements.api.analytics.get_gsc_data
```

Since v1.254.0 a 403 here returns a message naming this fix rather than a
traceback, and the log row is throttled — so if it is still wrong you will see
one clear row per hour, not forty lines per scheduled run.

---

## 3. `Finance Calendar fetch failed`

**What it means.** `404 Not Found` for calendar
`c_1nsch8ttqlambckueg534ksq0g@group.calendar.google.com`. To the Google Calendar
API, "does not exist" and "exists but you cannot see it" are the same 404 — so
this is either a wrong id or a calendar that was never shared with the Drive
service account.

**Confirm.** Ask the service account what it *can* see. If the configured id is
absent from this list, that is your answer:

```bash
bench --site erp.sapphirefountains.com execute \
  erpnext_enhancements.api.finance_calendar.list_calendars
```

**Fix — if the calendar exists and is simply unshared:**

1. Google Calendar → the Finance calendar → *Settings and sharing*.
2. *Share with specific people* → *Add people* → the Drive service account
   address (the same `client_email` as §2, unless the Drive service account is
   separate — `list_calendars` above authenticates as whichever one is
   configured).
3. Permission: *See all event details*. Nothing more; the widget only reads.

**Fix — if the id is simply wrong:** copy the correct one from *Settings and
sharing* → *Integrate calendar* → **Calendar ID**, and paste it into **Finance
Settings → Finance Calendar ID**.

Either way the widget recovers within ~2 minutes: v1.254.0 caches the refusal for
120s (deliberately far below the 30-minute success cache) so a corrected id shows
up almost immediately instead of after half an hour. To skip even that wait:

```bash
bench --site erp.sapphirefountains.com clear-cache
```

---

## 4. `Training media`

**What it means.**

```
sa-training-media@erpnext-465317.iam.gserviceaccount.com does not have
storage.buckets.get access to the Google Cloud Storage bucket
sf-erpnext-training-media
```

A missing IAM binding. The service account exists and authenticates; it has no
role on the bucket.

**Confirm.**

**Bash**

```bash
gcloud storage buckets describe gs://sf-erpnext-training-media --format="value(name)"
gcloud storage buckets get-iam-policy gs://sf-erpnext-training-media \
  --format="table(bindings.role, bindings.members)"
```

**PowerShell**

```powershell
gcloud storage buckets describe gs://sf-erpnext-training-media --format="value(name)"
gcloud storage buckets get-iam-policy gs://sf-erpnext-training-media `
  --format="table(bindings.role, bindings.members)"
```

If the first command 404s, the bucket does not exist and must be created before
the grant will mean anything.

**Fix.** Grant `roles/storage.objectAdmin` (read + write objects; training media
is uploaded as well as served) plus `roles/storage.legacyBucketReader` (which is
what actually carries `storage.buckets.get`, the permission named in the error):

**Bash**

```bash
gcloud storage buckets add-iam-policy-binding gs://sf-erpnext-training-media \
  --member="serviceAccount:sa-training-media@erpnext-465317.iam.gserviceaccount.com" \
  --role="roles/storage.objectAdmin"

gcloud storage buckets add-iam-policy-binding gs://sf-erpnext-training-media \
  --member="serviceAccount:sa-training-media@erpnext-465317.iam.gserviceaccount.com" \
  --role="roles/storage.legacyBucketReader"
```

**PowerShell**

```powershell
$sa = "serviceAccount:sa-training-media@erpnext-465317.iam.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding gs://sf-erpnext-training-media `
  --member="$sa" --role="roles/storage.objectAdmin"

gcloud storage buckets add-iam-policy-binding gs://sf-erpnext-training-media `
  --member="$sa" --role="roles/storage.legacyBucketReader"
```

IAM propagation is not instant — allow a minute or two, then verify with the
app's own check:

```bash
bench --site erp.sapphirefountains.com execute \
  erpnext_enhancements.training.gcs_media.test_connection
```

---

## 5. SMTP relay / Email account

Three related signatures:

- `(550, b"5.7.1 Invalid credentials for relay [35.194.95.244]. The IP address you've registered in your Workspace SMTP Relay service does not match…")`
- `Unable to send mail because of a missing email account…`
- `Password reset email could not be sent`

**What it means.** Google Workspace SMTP Relay authorises by **sending IP**, and
`35.194.95.244` is not on the allow-list. Most likely the server's egress IP
changed — which it will do on its own if the VM's external address is ephemeral
rather than a reserved static one.

**Confirm the current egress IP** (this is the value Google will see, which is
not necessarily what `ifconfig` reports):

**Bash**

```bash
curl -s https://ifconfig.me; echo
gcloud compute addresses list --format="table(name, address, status, region)"
```

**PowerShell**

```powershell
(Invoke-RestMethod https://ifconfig.me)
gcloud compute addresses list --format="table(name, address, status, region)"
```

**Fix.**

1. Google Workspace Admin → *Apps* → *Google Workspace* → *Gmail* → **Routing** →
   *SMTP relay service*.
2. Edit the relay entry → *Allowed senders* → add the IP from above.
3. **Reserve the address so this cannot recur.** If `gcloud compute addresses
   list` shows the IP as ephemeral, promote it to static — otherwise the next VM
   restart silently breaks mail again:

```bash
gcloud compute addresses create frappe-erp-egress \
  --addresses 35.194.95.244 --region us-central1
```

4. Then confirm ERPNext has a default outgoing account at all — the "missing
   email account" rows are a *separate* misconfiguration, not a symptom of the
   relay problem:

```bash
bench --site erp.sapphirefountains.com console <<'PY'
import frappe
rows = frappe.get_all("Email Account",
    filters={"enable_outgoing": 1},
    fields=["name", "email_id", "default_outgoing", "smtp_server"])
print(rows or "NO OUTGOING EMAIL ACCOUNT CONFIGURED")
PY
```

If nothing has `default_outgoing = 1`, set it in *Email Account*. Frappe will not
fall back to any other account.

---

## 6. `Attempted Unauthorized File Access in PDF Generator`

**What it means.** Not an attack, despite the name. The PDF generator refused a
path that resolved outside the allowed roots:

```
Blocked access to:  private/files/Logo-cropped.png
Resolved Path to:   /data/frappe-sites/erp.sapphirefountains.com/public/private/files/Logo-cropped.png
```

Look at the resolved path: `public/private/files/`. A **relative** URL
(`private/files/…`, no leading slash) got joined onto the *public* root, so it
resolved to a directory that does not and should not exist. The guard did its
job; the input was malformed.

**Fix.** Find the letterhead or print format holding the bad URL and give it a
leading slash:

```bash
bench --site erp.sapphirefountains.com console <<'PY'
import frappe
for dt, field in (("Letter Head", "image"), ("Letter Head", "content"),
                  ("Print Format", "html")):
    for row in frappe.get_all(dt, fields=["name", field]):
        val = row.get(field) or ""
        if "private/files/Logo-cropped.png" in val:
            print(dt, row.name, "->", field)
PY
```

Then either:

- **Preferred** — re-upload the logo with *Is Private* unticked, and point the
  letterhead at the resulting `/files/…` URL. A logo on every outbound PDF is not
  private data, and a public file needs no path juggling.
- **Or** — correct the reference to `/private/files/Logo-cropped.png` (leading
  slash). The file must genuinely be private for this to resolve.

For PDF failures that are about the *host* rather than a path — missing
`wkhtmltopdf`, `Chromium took too long to start`, `Failed to disconnect CDP
session` — see [`pdf-generation.md`](pdf-generation.md), which already covers
both backends and the host fix.

---

## 7. `frappe.model.document.execute_action`

**What it means.** An upstream Frappe v16 bug, not ours:

```
cannot import name '_follow_document' from 'frappe.desk.form.document_follow'
```

Triggered by **Role Profile → Technician → Update All Users**, which resaves every
user with that profile; the resave path imports a symbol that no longer exists in
`document_follow.py`. 20 rows on 2026-07-31, all from one operator action.

**There is no fix on our side** — the missing symbol is in `apps/frappe`. Options,
in order of preference:

1. **Avoid the trigger.** Role changes made by editing individual users, or by
   this app's own `training.assignment.on_user_roles_changed` path, do not go
   through `update_all_users` and are unaffected.
2. **Take the upstream fix** when the Frappe patch lands, on the next bench
   update.

Confirm whether the current bench still has the bug before assuming it does:

```bash
bench --site erp.sapphirefountains.com console <<'PY'
import frappe.desk.form.document_follow as df
print("_follow_document present:", hasattr(df, "_follow_document"))
PY
```

If that prints `True`, the bench has moved on and the rows are historical.

---

## 8. Report execution failures

Three signatures, one cause:

- `Report execution failed for: Brian Commissions Report` (filters were `{}`)
- `Script report execution error for Trial Balance: Fiscal Year None is required`
- `Script report execution error for Profit and Loss Statement: From Date and To Date are mandatory`

**What it means.** These reports have **mandatory filters** and are being run with
none. In the Brian Commissions case the traceback shows `filters = '{}'` and
`are_default_filters = True` — i.e. an **Auto Email Report** fired on schedule
carrying no filter payload at all.

A financial report with no fiscal year is not a bug to fix in code; it is a
scheduled job that was never given its parameters.

**Confirm which schedules are under-configured:**

```bash
bench --site erp.sapphirefountains.com console <<'PY'
import frappe
for r in frappe.get_all("Auto Email Report",
        filters={"enabled": 1},
        fields=["name", "report", "filters", "frequency"]):
    if not r.filters or r.filters.strip() in ("{}", "[]", "null"):
        print("NO FILTERS:", r.name, "|", r.report, "|", r.frequency)
PY
```

**Fix.** Open each **Auto Email Report** listed and populate its *Filters*
section — fiscal year, from/to dates, company — then save. Frappe stores the
filter payload on the schedule, so the next run carries it.

For a report that genuinely has no sensible standing filters, disable the
schedule rather than leaving it to fail weekly.

---

## 9. Error Log retention

**Retention is already configured and already working. There is nothing to fix
here — this section exists so nobody "fixes" it anyway.**

`Log Settings` retains **Error Log for 90 days** (alongside Activity Log,
Scheduled Job Log and twelve others), and Frappe's daily log-clearing job is
enforcing it. The proof is the boundary: the oldest row in the table is
`2026-05-08 06:12`, which is *exactly* 90 days before the sample date, and only
**77** rows sit past the cutoff — those are aging out today.

So the table is **self-bounding**, and 78,648 rows is not unbounded growth. It is
roughly ninety days of history that happens to contain one enormous incident: the
MDM storm of 2026-06-15/16 ([§1](#1-mdm-retry-failed-for-miradore--mdm-action1-token-refresh-failed))
is 56% of the table on its own, and it will age out by itself around **2026-09-14**.

The lever that actually matters is therefore not retention but the [v1.254.0
circuit breaker](../erpnext_enhancements/utils/error_throttle.py). Retention only
bounds *how long* a storm stays in the table; the breaker bounds whether it is
written at all. Ninety days of a 44,069-row incident is 44,069 rows either way.

**Confirm** — note the child-table fieldname is `logs_to_clear`, not `logs`.
Reading the wrong one returns an empty list and looks exactly like "no retention
configured", which is a genuinely easy mistake to make:

```bash
bench --site erp.sapphirefountains.com console <<'PY'
import frappe
ls = frappe.get_single("Log Settings")
print([(r.ref_doctype, r.days) for r in ls.logs_to_clear])

r = frappe.db.sql("""SELECT MIN(creation) oldest, COUNT(*) total,
    SUM(creation < DATE_SUB(NOW(), INTERVAL 90 DAY)) past_cutoff
    FROM `tabError Log`""", as_dict=True)[0]
print(r)
PY
```

If `oldest` is at the 90-day boundary and `past_cutoff` is small, retention is
running. Nothing to do.

**Optional — prune early.** Only worth doing if you want the table small *now*
rather than in September (for example, to make the desk's Error Log list usable
again). This is not a fix; it just brings the aging-out forward.

> ⚠️ **This deletes data.** It is the only destructive command in this runbook,
> and it is the only genuinely optional one. Take a database snapshot first, and
> run the counting query before the delete so you know what you are removing.

Count first:

```bash
bench --site erp.sapphirefountains.com mariadb <<'SQL'
SELECT COUNT(*) AS total,
       SUM(creation < DATE_SUB(NOW(), INTERVAL 90 DAY)) AS older_than_90d
FROM `tabError Log`;
SQL
```

Snapshot, then delete in batches (a single unbounded `DELETE` on ~78k rows will
hold a long transaction and can lock out live writes):

**Bash**

```bash
gcloud sql backups create --instance=<INSTANCE_NAME>

bench --site erp.sapphirefountains.com mariadb <<'SQL'
DELETE FROM `tabError Log`
WHERE creation < DATE_SUB(NOW(), INTERVAL 90 DAY)
LIMIT 5000;
SQL
```

Repeat the `DELETE` until it reports 0 rows affected, then reclaim the space:

```bash
bench --site erp.sapphirefountains.com mariadb -e 'OPTIMIZE TABLE `tabError Log`;'
```

**PowerShell**

```powershell
gcloud sql backups create --instance=<INSTANCE_NAME>

$del = "DELETE FROM ``tabError Log`` WHERE creation < DATE_SUB(NOW(), INTERVAL 90 DAY) LIMIT 5000;"
do {
  $out = gcloud compute ssh frappe-erp --zone us-central1-a --command `
    "cd /home/frappe/frappe-bench && bench --site erp.sapphirefountains.com mariadb -e '$del' && bench --site erp.sapphirefountains.com mariadb -e 'SELECT ROW_COUNT();'"
  Write-Host $out
} while ($out -notmatch '\b0\b')
```

---

## 10. Filelocks

```
Filelock: Failed to aquire /data/frappe-sites/erp.sapphirefountains.com/locks/bench_migrate.lock
Filelock: Failed to aquire /home/frappe/frappe-bench/config/helpdesk_corpus_download.lock
```

**What it means.** Two processes wanted the same lock. The `bench_migrate` one
(6 rows) is overlapping deploys — a migrate started while another was running.
The `helpdesk_corpus_download` one wrote **3,781 rows** in May–June from a job
retrying against a permanently held lock.

**Usually self-resolving.** A lock held by a *live* process is correct behaviour,
and the second process backing off is the system working. Only intervene if the
lock is **stale** — held by a process that no longer exists.

**Confirm before deleting anything:**

```bash
ls -la /data/frappe-sites/erp.sapphirefountains.com/locks/
ls -la /home/frappe/frappe-bench/config/*.lock
ps aux | grep -E 'bench|migrate' | grep -v grep
```

**Fix (stale locks only — no matching process in `ps`):**

```bash
sudo -u frappe rm -f /data/frappe-sites/erp.sapphirefountains.com/locks/bench_migrate.lock
sudo -u frappe rm -f /home/frappe/frappe-bench/config/helpdesk_corpus_download.lock
```

**PowerShell**

```powershell
gcloud compute ssh frappe-erp --zone us-central1-a --command `
  "ps aux | grep -E 'bench|migrate' | grep -v grep; ls -la /data/frappe-sites/erp.sapphirefountains.com/locks/"
```

Deleting a lock that a running process still holds will corrupt that process's
work. Check `ps` first, every time.

To stop the deploy overlap recurring, serialise migrates in the Cloud Build
pipeline rather than deleting locks after the fact.

---

## 11. `get_lesson() missing 1 required positional argument: 'attempt'`

**What it means.** A browser is running **old training player JavaScript**. The
old player called `getLesson({course, lesson_key})` against an endpoint whose
signature is `get_lesson(attempt, lesson_key)`; Frappe dropped the unknown
`course`, found no `attempt`, and raised.

That call shape was corrected in the app some releases ago, and
`tests/test_training_boot_wire.py` now fails the build if any transport call
omits a required argument. Production runs 1.253.0, which has the fix — so the
7 remaining rows are **stale tabs**, not a live defect. `/training` cache-busts
its assets per deploy (`?v={{ deploy_version }}`), but a tab that was *already
open* across the deploy keeps the JavaScript it loaded.

**Fix.** Hard-refresh the tab: <kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd>
(Windows/Linux) or <kbd>Cmd</kbd>+<kbd>Shift</kbd>+<kbd>R</kbd> (macOS).

If it survives a hard refresh, the deploy token is not moving and that *is* a
real bug — check it:

```bash
bench --site erp.sapphirefountains.com console <<'PY'
from erpnext_enhancements.utils.deploy import get_deploy_version
print("deploy token:", get_deploy_version())
PY
```

The token is the mtime of `sites/assets/assets.json`, so it must change after
every `bench build`. If it does not, `bench build` is not running on deploy.

---

## 12. `Bridge token failed: 403`

```
403 {"message":"Access restricted to sapphirefountains.com accounts only."}
```

**Working as designed.** Someone tried to mint a bridge token with a non-company
Google account. 4 rows total.

No fix needed. If a legitimate user is blocked, they are signed into the wrong
Google account — sign out of the personal one and retry with their
`@sapphirefountains.com` identity.

---

## What this runbook does *not* cover

Fixed in code in v1.254.0 — see the [CHANGELOG](../CHANGELOG.md#12540---2026-08-06):

- `Google Calendar Sync Failed` — `.isoformat()` on a `str`
- `Drive Shadow Sync` — transient Google 500s now retried
- `Training setup` — missing DocType guard on the categories seeder
- `GSC API Error` / `Finance Calendar fetch failed` — the *logging* half; the
  grants in §2 and §3 above are still yours to make

Out of this repo entirely:

- `Document Update Error` — `frappe_assistant_core`'s `update_document` tool.
  Most rows are genuine data validation (a Task's expected end date after its
  Project's, a dependent task not yet complete, a missing Project Manager) and
  should be fixed in the records, not in code.
- `Session Stopped` — Frappe's websocket/Redis churn. Benign at this volume.
