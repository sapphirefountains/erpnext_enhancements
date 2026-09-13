# Google Chat teardown — the half the code removal cannot do

**Status:** to be run by a human after v1.426.0 is deployed. Nothing here is automated, and
nothing here happens as a side effect of the deploy.

[ADR 0011](../decisions/adr/0011-retire-google-chat-and-coworker-chat.md) removed the chat
module from this app and [`patches/delete_chat_module.py`](../erpnext_enhancements/patches/delete_chat_module.py)
removes its database residue. Neither touches Google. This file is what is left.

---

## Read this before you start

**None of it is under Terraform.** `grep -ril chat infra/ modules/` returns nothing;
`infra/project.tf` enables six APIs and not one of them is `chat`, `pubsub`,
`workspaceevents` or `iamcredentials`; `infra/iam.tf` grants no
`roles/iam.serviceAccountTokenCreator` anywhere. **Every resource below was created by hand
in the console or with `gcloud`, and lives in no state file.** So `terraform plan` will show
no drift before or after this teardown, and `terraform destroy` would remove the VM, load
balancer and SQL instance while leaving every item on this list standing.

**There are no credentials to revoke for *this* integration.** The chat mirror was keyless by
construction: the VM signed its own delegation assertions through IAM Credentials `signJwt`,
and `Chat Settings` held identifiers only — zero Password fields, asserted by a test that no
longer exists because the doctype no longer exists. There is no service-account JSON key, no
Secret Manager entry and no `__Auth` row for any of it. The revocations below are IAM bindings
and OAuth scope grants, not secrets.

**Do not generalise that to the other Google integrations.** Drive and Calendar share a real
service-account **JSON key**, stored in a `Password` field on
`Project Folder Google Drive Settings`. Nothing on this list touches it, and nothing on this
list should.

**Get the resource names from the deploy log.** `delete_chat_module` prints every Google
identifier `Chat Settings` held immediately before deleting the rows, under
`delete_chat_module: Google resources this integration named`. That print exists because
those rows were the only place the topic and subscription names were written down — the
provisioning runbook (`RUNBOOK_google_cloud_setup.md`, cited by the old
`gchat/smoke_test.py`) is **not in this repository**. If the deploy log has rolled, work
from the console listings instead; the shapes are given below.

**Two projects, and they are easy to confuse.** The Chat estate is in
**`erpnext-465317`** — the project that owns the ERPNext VM (`infra/prod.tfvars`).
Triton's Drive picker lives in **`triton-497321`** and is **not part of this teardown**.
Deleting the wrong project's OAuth client breaks the Drive attach button in the Triton
widget, which is the surface this whole change exists to keep.

---

## Order matters

Stop the inbound flow first, then remove what produces it, then the identity. Doing it in
this order means nothing is ever publishing into a topic nobody is draining, and the Chat
app never sees an endpoint that has already gone.

### 1. Workspace Events subscriptions — do these first

One `spaces/-` subscription per coworker, created under domain-wide delegation.

```bash
gcloud alpha workspace-events subscriptions list --project=erpnext-465317
```

Delete each one that targets Google Chat. These are the resources that were already failing in
production (`CHANGELOG` 1.340.1/1.340.2: **no renewal this app ever issued had succeeded** —
sixteen consecutive failures apiece — and four later found in state `DELETED` with no code path
able to recreate them), so expect the live set to be smaller than the roster.

**They also expire on their own.** A Workspace Events subscription that is not renewed is
deleted by Google and cannot be recovered — and nothing renews them now. So this step is
about being deliberate rather than about preventing anything: left alone, they lapse within
days. Delete them anyway so the estate matches the intent.

### 2. The Chat app registration

Google Cloud console → **Google Chat API → Configuration**, project `erpnext-465317`.

Set the app to **not live**, or delete the configuration outright. Until you do, Chat still
holds an HTTP endpoint URL pointing at this site's webhook — a route that now 404s. An app
left Live against a dead endpoint is the one item here a user can still see: it appears in
their Chat app list and fails when they message it.

While you are here, note the **HTTP endpoint URL** the configuration holds. It is the
byte-exact JWT audience the webhook verified against, and it is the value
`interaction_endpoint_url` mirrored.

### 3. Pub/Sub

```bash
gcloud pubsub subscriptions list --project=erpnext-465317
gcloud pubsub topics list --project=erpnext-465317
```

There are up to three resources: one topic, and two pull subscriptions against it (the
events subscription and the interaction subscription — `Chat Settings` named both). Delete
the **subscriptions first, then the topic**: deleting a topic leaves its subscriptions
orphaned rather than removing them, and an orphaned subscription keeps accruing a backlog.

**This is the item with a running cost.** Nothing drains these now — the one-minute cron
that pulled them was deleted with the module — so messages accumulate against the
subscription retention window and are billed as storage until it expires. Not a large
number at this volume, but it is the only line here that costs money while you postpone it.

Also remove the **publisher binding** that let `chat-api-push@system.gserviceaccount.com`
publish into the topic.

### 4. Domain-wide delegation — Workspace Admin, not GCP

admin.google.com → **Security → Access and data control → API controls → Domain-wide
delegation**.

Find the client ID for the **delegation service account** (the deploy log prints its email as
`delegation_service_account`) and remove the Chat scopes from it, or delete the entry outright
if Chat was its only purpose.

**Chat was almost certainly its only purpose.** Checked while writing this: neither
`google_drive/drive_utils.py` nor `google_calendar/calendar_utils.py` delegates at all — both
build a client directly from a service-account JSON key
(`google.oauth2.service_account.Credentials.from_service_account_info`), with no `subject=`
and no impersonation anywhere, and Calendar explicitly reuses Drive's credential rather than
holding its own. So this app has no *other* consumer of domain-wide delegation. Still confirm
against the entry's own scope list before deleting it — the Admin console is Workspace-wide
and something outside this repository could be using the same client ID.

Same page, worth doing while you are here: admin.google.com → **Apps → Google Workspace →
Google Chat** had the app marked **Trusted** at the root OU as part of the original setup
(`TASK-2026-01307`). That trust grant can go.

### 5. IAM

Remove the `roles/iam.serviceAccountTokenCreator` binding **between** the VM service account
and the delegation service account. This is the grant that made the keyless design work, and
it is hand-made and unmanaged.

**Do not narrow or delete the VM's own service account.** `modules/compute-vm/main.tf` gives
it `cloud-platform` scope and it is the identity for GCS training media and everything else
on the box. The chat-specific part is the binding, not the account.

If the delegation service account and the Chat app service account exist only for this, they
can be deleted once their bindings are gone.

### 6. APIs

Disable, in `erpnext-465317`, whichever of these no longer has a consumer:

| API | Safe to disable? |
|---|---|
| `chat.googleapis.com` | Yes — nothing else uses it. |
| `workspaceevents.googleapis.com` | Yes. |
| `pubsub.googleapis.com` | **Check first.** Disable only if no other workload publishes or subscribes in this project. |
| `iamcredentials.googleapis.com` | **Probably not.** Drive and Calendar delegation sign through it too. |
| `admin.googleapis.com` | **Check first** — org-structure mirroring used it, but other integrations may. |
| `aiplatform.googleapis.com` | **No.** The briefing and training AI paths use Vertex. |

None of these is free-of-charge-only, but none bills on enablement either; this step is
hygiene, not cost.

---

## On the ERPNext side, by hand

**`site_config.json`:** remove `chat_vapid_private_key` and `chat_vapid_public_key`. They are
the RFC 8292 VAPID keypair for the native Web Push that went with the coworker product —
nothing to do with Google, and nothing in the code will clean them up. Treat them as retired
secrets rather than leaving them in every backup.

**Nothing else.** The patch handles the tables, the Singles rows, both Roles, both
Notification Types, the `Logs To Clear` rows and the `Module Def`. There were no fixtures,
no Custom Fields, no Property Setters and no workspace.

---

## How to tell it is done

- `gcloud pubsub topics list --project=erpnext-465317` shows no chat topic.
- `gcloud alpha workspace-events subscriptions list --project=erpnext-465317` is empty.
- The Chat app no longer appears in a staff member's Google Chat app list.
- On the site: `bench --site <site> console` →
  `frappe.db.sql("show tables like 'tabChat%'")` **and**
  `frappe.db.sql("show tables like 'tabTriton Invocation Log'")` both return nothing, and
  `frappe.db.exists("Module Def", "Chat")` is falsy. Both queries, not just the first: 21 of
  the 22 tables are named `tabChat…`, and the twenty-second is `tabTriton Invocation Log` —
  a `tabChat%` glob reports a clean teardown while the largest per-turn log table is still
  sitting there.

The last one is the deploy's own responsibility rather than yours, but it is worth checking
once: a patch on this repo is allowed to fail quietly precisely so it cannot abort a deploy,
which means a silent failure is possible by design. `delete_chat_module` logs to the Error
Log under `delete_chat_module` if any step could not complete.
