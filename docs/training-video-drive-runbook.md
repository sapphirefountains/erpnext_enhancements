# Getting a training video from Google Drive into a lesson

Written after the first attempt failed. The short version of what went wrong: the video
lives somewhere the service account cannot see, and the Training Video Asset was created
by hand rather than registered, so its duration is a typed number wearing a "Probed"
label.

---

## The one fact everything depends on

The training module does **not** stream from Drive. Drive is only where an author puts
the file. On publish, the video is copied into a private GCS bucket and served to the
player as a short-lived signed URL.

That copy is done by a **service account**, not by you. So the question is never "can I
open this file?" — it is always "can the service account open this file?"

| | |
|---|---|
| **Service account** | `erpnext-drive@triton-497321.iam.gserviceaccount.com` |
| **Shared drives it can currently see** | `Customer Accounts`, `Operations` |
| **File that failed** | `11PjhqS2yM6K1cTb7qMclECVbNYe0dx12` — `404 File not found` |

Google returns **404, not 403**, for a file a service account has no access to. So "file
not found" almost always means "not shared with the service account", not "deleted".

Your video is in a shared drive that is not one of those two.

---

## Step 1 — Give the service account access

Either of these works. Pick one.

**A. Add it to the shared drive** (best if you will add more videos there)

1. Open the shared drive in Drive.
2. **Manage members**.
3. Add `erpnext-drive@triton-497321.iam.gserviceaccount.com`.
4. Role: **Viewer** is enough — it only ever reads.

**B. Share the single file**

1. Right-click the video → **Share**.
2. Add `erpnext-drive@triton-497321.iam.gserviceaccount.com` as **Viewer**.
3. Turn off "Notify people" (it is a robot).

> **Check the download setting.** If the shared drive has *"Viewers and commenters can
> download, print and copy"* switched **off**, the copy still fails after you grant
> access — the service account can list the file and not read its bytes. That produces a
> `403`, not a `404`, so the two failures are distinguishable in `last_error`.

The file does **not** have to live in the `Customer Accounts` drive. The copy is not
scoped to a particular drive; any file the service account can read will do.

---

## Step 2 — Get the right file id

From the file's own URL:

```
https://drive.google.com/file/d/11PjhqS2yM6K1cTb7qMclECVbNYe0dx12/view?usp=sharing
                               └──────────── this is the file id ────────────┘
```

Three ways to get the wrong one:

- **A folder URL** — `/drive/folders/<id>`. That id is a folder, and it 404s the same way.
- **A shared drive URL** — ids beginning `0A` are drives, not files.
- **A shortcut.** If somebody "added a shortcut to Drive", the shortcut has its own id and
  no content. Right-click → **Show file location** to reach the real file first.

---

## Step 3 — Register the video (do not create it by hand)

**This is the step that was skipped, and it is not cosmetic.**

Watch coverage is a *fraction of `duration_seconds`*. If that number is wrong, the gate is
arithmetic on a guess: a hand-typed 600 against a real 900-second video lets an 80% gate
pass on 53% of an actual watch.

The module defends against this with `duration_source`:

- **`Probed`** — read from Drive's own metadata. The coverage gate is applied.
- **`Manual`** — typed by a person. `evaluate_gates` **waives the coverage gate entirely**
  and stamps the progress row with `duration_unverified`, rather than gating on a number
  nobody checked.

Creating the asset in the Desk form lets you type a duration *and* leave the label saying
`Probed`, which is the one combination that defeats the safeguard. `TRN-VID-00001` is
currently in exactly that state: `duration_seconds = 90`, `duration_source = Probed`, and
a file the service account has never been able to read.

**Register it through the endpoint instead.** With `/training` open, in the browser
console:

```js
await fetch('/api/method/erpnext_enhancements.api.training_author.register_video_asset', {
  method: 'POST', credentials: 'same-origin',
  headers: {'Content-Type':'application/json','X-Frappe-CSRF-Token': window.TRAINING_CSRF},
  body: JSON.stringify({ drive_file_id: 'PASTE_THE_FILE_ID', title: 'Using the Training Module' })
}).then(r => r.json())
```

It returns `{video_asset, created, duration_probed}`. **`duration_probed: true` is the
thing to check** — if it is `false`, the service account still cannot read the file, and
you are back at step 1. Do not simply type the duration in.

Or from a bench shell:

```bash
bench --site erp.sapphirefountains.com execute erpnext_enhancements.api.training_author.register_video_asset --kwargs "{'drive_file_id': 'PASTE_THE_FILE_ID'}"
```

### Fixing the existing asset

`register_video_asset` returns the existing record rather than re-probing when the
`drive_file_id` already exists. So for `TRN-VID-00001`, once access is granted, either:

- delete it and register again (nothing points at it yet except the block, which you would
  re-link), or
- open it in the Desk and set `duration_source` to **Manual** until it has been re-probed —
  which honestly waives the coverage gate rather than pretending to enforce it.

---

## Step 4 — Put it in a lesson

1. Open the course → **Open Builder**.
2. Select the lesson, add a **Video** block.
3. In the inspector, set **Video asset** to the registered asset.
4. Set **min coverage** if this block should gate the lesson. Leaving it `0` falls back to
   the course's `min_video_coverage` (80% on both current courses).
5. Turn on **checkpoints** if you want in-video questions.

---

## Step 5 — Publish. Use Publish

Use the **Publish** action, not the Submit button on the Training Course Version form.

They are not the same act. `publish_version` writes each lesson's answer-free payload, the
separate answer key that grades the quiz, the table of contents, the totals and the content
hash — and *then* submits. Submitting the form directly used to freeze a version that
looked published and was completely empty: no outline, lessons that render nothing, and a
quiz that could not be graded at all.

That is what happened to `TRN-CRS-00002-V2`, and it is why the course showed a title and a
summary and nothing else. **As of v1.226.0 the form refuses that submit** and tells you to
use Publish.

Publishing is also what queues the Drive → GCS copy.

---

## Step 6 — Verify

The copy runs in the background. Give it a moment for a large file, then check the
Training Video Asset:

| Field | What you want |
|---|---|
| `status` | **Available** |
| `gcs_object` | set (e.g. `training/TRN-VID-00001.mp4`) |
| `gcs_synced_on` | a timestamp |
| `last_error` | empty |

`status = Error` with a traceback in `last_error` is the failure path, and the traceback
names the cause. From the browser console on `/training`, this is the end-to-end check:

```js
const post = (m,b) => fetch('/api/method/erpnext_enhancements.api.training.'+m, {
  method:'POST', credentials:'same-origin',
  headers:{'Content-Type':'application/json','X-Frappe-CSRF-Token':window.TRAINING_CSRF},
  body:JSON.stringify(b)}).then(r=>r.json()).then(j=>j.message);

const c = await post('get_course', {course:'TRN-CRS-00002'});
const att = c.attempt.attempt;
const l  = await post('get_lesson', {attempt:att, lesson_key:c.toc[0].lesson_key});
const vb = l.lesson.blocks.find(b => b.type === 'Video');
await post('get_media_url', {attempt:att, block_key:vb.block_key});
```

A working video returns `{url: "https://storage.googleapis.com/...", expires_in_minutes: 15}`.
A `reason` instead of a `url` is the module telling you why:

| `reason` | Meaning |
|---|---|
| `not_available` | no `gcs_object` — the copy has not run or failed |
| `not_configured` | GCS settings incomplete in Training Settings |

---

## Why this is more steps than it should be

Two of them exist only because the UI does not cover them yet:

- **There is no "add a video from Drive" button in the builder.** It offers a Link field to
  an already-registered asset, so registration has to happen through the API or the Desk —
  and the Desk path is the one that lets a duration be typed and mislabelled.
- **Nothing surfaces a failed copy to the author.** The asset carries `status = Error` and
  the traceback, but the builder does not show it, so a broken video looks the same as one
  that simply has not finished copying.

Both are worth closing. Until then, step 3 and step 6 are the two that actually need care.
