# Training media bucket — CORS

Browser-direct video upload (v1.437.0) talks to Google Cloud Storage from the
author's browser rather than through this site. That is the whole point: frappe
enforces a 25 MB ceiling twice and streams a body through a gunicorn worker
synchronously, so an upload that went through the app would fail on any real video
and hold a worker hostage for the ones it accepted.

**The browser will not talk to the bucket until the bucket says it may.** That is a
bucket setting, not app code, so it does not arrive with a deploy — which is why
this file exists rather than the requirement living in somebody's memory, the way
the Drive service-account share did for a year.

- **Bucket:** `sf-erpnext-training-media`
- **Project:** `erpnext-465317`
- **Config:** [`gcs-training-cors.json`](gcs-training-cors.json)

## Apply it

**The file is the WHOLE configuration, not an addition.** `--cors-file` replaces
everything, and this bucket already had a rule: `GET`/`HEAD` with the range headers,
which is what lets the player seek in a video. A single-entry upload config would
have silently deleted it. So the file carries **two entries** — the original,
preserved byte-for-byte, and the upload one beside it. GCS matches an entry on
origin **and** method, so a `GET` gets the first and a `POST` the second, each with
its own header list.

Read the current one before you apply anything, every time:

```bash
gcloud storage buckets describe gs://sf-erpnext-training-media --format="json(cors_config)"
```

Then:

```bash
gcloud storage buckets update gs://sf-erpnext-training-media --cors-file=docs/gcs-training-cors.json
```

## Verify it, rather than assuming it

A CORS mistake does not raise anything the server can see: the browser refuses the
request before it is sent and reports status 0 with no detail. So check the
preflight directly — this is the exact request the upload makes:

```bash
curl -s -o /dev/null -D - -X OPTIONS "https://storage.googleapis.com/sf-erpnext-training-media/training/uploads/probe.mp4" -H "Origin: https://erp.sapphirefountains.com" -H "Access-Control-Request-Method: POST" -H "Access-Control-Request-Headers: x-goog-resumable,content-type"
```

**Read the headers, not the status code.** Measured against the live bucket on
2026-09-14, before the upload entry was added, that request answered:

```
HTTP/1.1 200 OK
Vary: Origin
Content-Length: 0
```

A clean **200 with no CORS headers at all** — which is a refusal. The browser rejects
the request because `Access-Control-Allow-Origin` is *absent*, not because anything
returned an error, so "the preflight returns OK" is not evidence of anything.

Note what that 200 did **not** tell you: the bucket had CORS all along, just not for
`POST`. An entry that does not match the requested *method* is no entry at all, and
the response looks identical to a bucket with no configuration whatsoever.

Three headers have to come back, and **the third is the one people miss**:

| Header | Why |
|---|---|
| `Access-Control-Allow-Origin: https://erp.sapphirefountains.com` | the origin is admitted at all |
| `Access-Control-Allow-Methods` containing `POST` and `PUT` | POST starts the resumable session, PUT sends the bytes |
| `Access-Control-Expose-Headers` containing `Location` | **the session URI comes back in `Location`, and a browser cannot read a response header that is not exposed.** Without it the upload starts, succeeds at the network level, and the client has nowhere to send the file |

## Why each entry is in the file

- **`POST`** — starts the resumable session. **`PUT`** — uploads the bytes to the
  session URI. **`GET`/`HEAD`** — not needed for `<video src>`, which is not a CORS
  request, but a `<track>` caption file *is* subject to CORS, so captions would
  fail later without it.
- **`x-goog-resumable`** in `responseHeader` — GCS uses that list for
  `Access-Control-Allow-Headers` as well as `Access-Control-Expose-Headers`, so the
  header must be listed before the browser is allowed to *send* it. It is not
  optional decoration: `x-goog-resumable: start` is part of the **signed canonical
  request**, so the browser must send it back byte-identical or the signature does
  not verify — and Google answers a mismatch with a 403 that says nothing about
  which header was wrong.
- **`Location`** — see the table above.
- **`Content-Range`/`Range`** — resuming an interrupted upload, and byte-range
  seeking in a video.
- **One origin only.** Narrow on purpose.

## Verified state, 2026-09-14

Applied and checked end to end rather than assumed:

| Check | Result |
|---|---|
| `POST` preflight with `x-goog-resumable` | `Allow-Methods: POST,PUT`, `Allow-Headers` includes `x-goog-resumable` |
| `PUT` preflight | same entry, allowed |
| **Actual `POST` response** | `Expose-Headers` includes **`Location`**, `ETag`, `x-goog-resumable` |
| `GET` preflight with `Range` (playback) | still answered by the original entry, with its own header list |
| A different origin | no CORS headers at all — refused |

The third row is the one worth re-running after any change: the preflight can pass
while `Location` goes unexposed, and the upload would then start, succeed at the
network level, and have nowhere to send the file.

An unsigned probe answers **403** with those CORS headers attached, which is the
expected shape — CORS and authorization are decided separately, and the 403 is the
signature check, not the origin check.

## What this does NOT do

**CORS is not authorization.** It tells a browser which origins may make a request;
it grants nothing. Objects in this bucket stay private and are reached only through
short-lived signed URLs minted by `training/gcs_media.py`. Adding CORS does not make
the bucket public, and removing it would not make a public bucket private.

## If uploads still fail afterwards

`training.video_upload.upload_preflight` answers whether the *app* side is
configured — a bucket name and a signing key on Training Settings. It cannot see the
bucket's CORS rules, so it will happily report `enabled: true` on a site where every
upload fails at preflight. A CORS failure surfaces in the canvas as *"The storage
bucket refused the browser"*; that message means this file has not been applied, or
has been applied to a different bucket.
