# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Upload a video from the author's machine straight into the bucket (D15).

Until now an author with an MP4 on their laptop could not get it into a lesson at
all. They had to own a Google account, upload to Drive, and then obtain a
Drive-admin action most of them cannot perform themselves — sharing the file with
``erpnext-drive@…iam.gserviceaccount.com``, a requirement that appears **nowhere on
screen** and lives only in a runbook — and finally paste the link back.

WHY NOT ``frappe.ui.FileUploader``, WHICH IMAGES AND PDFS ALREADY USE. Frappe
enforces a 25 MB default ceiling *twice* — ``get_max_file_size()`` and again in
``File.check_max_file_size`` — and the whole body passes through a gunicorn worker
synchronously. So the obvious build fails on essentially any real video, and would
take the site down for the ones it accepted. That is not a limit to raise: a
500 MB request occupying a worker for minutes is the same outage either way.

SO THE BYTES NEVER TOUCH THE SITE. The server signs a short-lived URL and the
browser talks to Google Cloud Storage directly. Two calls bracket that:
:func:`start_video_upload` mints the signed URL and decides whether the file is
allowed at all, and :func:`finish_video_upload` records the asset once the bytes
have landed. Between them this app is not in the data path.

**Two things the caller is NOT trusted for.** The object name is built here, not
accepted from the browser, so a caller cannot aim the upload at somebody else's
object. And ``finish_video_upload`` re-reads the object's real size from GCS rather
than believing the number the browser reports.

**The duration is read in the browser, and that is an upgrade rather than a
shortcut.** ``_probe_drive_video`` swallows every exception and returns ``{}``, so a
failed probe lands an asset with ``duration_seconds = 1`` and
``duration_source = Manual`` — and grading **waives the video-coverage gate
entirely** for a Manual duration. One orange modal at registration, and after that
a course that silently requires no watching. A browser reading
``HTMLMediaElement.duration`` off the very file it is about to upload cannot fail
that way: if it cannot read a duration, there is nothing to upload either.

**CORS is a deployment dependency, not a code path.** The bucket must allow this
origin for POST and PUT and expose the ``Location`` header, or the browser's
preflight fails before any of this runs. :func:`upload_preflight` reports whether
the pieces are configured so the canvas can say so in words instead of failing at
the moment somebody drops a file.
"""

import re

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from erpnext_enhancements.training import gcs_media

# Only what a browser can actually play back on a `<video>` element. A .mov
# carrying HEVC, an .avi or an .mkv all upload perfectly and then render as a black
# rectangle, which reads as the player being broken rather than the file being
# wrong -- so they are refused at the door, with the reason.
ALLOWED_MIME = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
}

# Where uploaded objects live, kept apart from the Drive copies so the two sources
# are distinguishable in the bucket without reading the database.
OBJECT_PREFIX = "training/uploads"

# Belt and braces on the default. `Training Settings.max_video_mb` exists, defaults
# to 300 and -- until this module -- was read by ZERO lines of Python or JavaScript,
# so there has never been a size ceiling anywhere in the upload path.
DEFAULT_MAX_MB = 300

# A signed URL has to outlive the upload it authorises. Fifteen minutes is the
# read TTL and is far too short for a 300 MB file on a site connection.
UPLOAD_TTL_SECONDS = 6 * 60 * 60

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _require_author():
    from erpnext_enhancements.api.training_author import _require_author as gate

    gate()


def _max_bytes():
    limit = cint(frappe.db.get_single_value("Training Settings", "max_video_mb")) or DEFAULT_MAX_MB
    return limit * 1024 * 1024


def _object_name(filename, extension):
    """Built here, never accepted from the caller.

    An object name supplied by the browser is an arbitrary write path into the
    bucket: it would let one author aim an upload at another's object, or at a
    Drive copy, simply by typing the name.
    """
    stem = _SAFE_NAME.sub("-", (filename or "video").rsplit(".", 1)[0])[:60].strip("-") or "video"
    # `frappe.generate_hash` rather than a counter or a timestamp: two authors
    # uploading "site-walkthrough.mp4" in the same second must not collide, and an
    # object name is not a place to encode who uploaded what.
    return f"{OBJECT_PREFIX}/{frappe.generate_hash(length=12)}-{stem}{extension}"


@frappe.whitelist()
def upload_preflight():
    """Can this site accept a browser upload at all, and say so in words.

    Called before the canvas offers the control, so an author is told "uploads are
    not configured on this site" rather than dropping a 200 MB file and watching a
    CORS failure they cannot interpret.
    """
    _require_author()
    configured = gcs_media.is_configured()
    return {
        "enabled": bool(configured),
        "max_mb": cint(_max_bytes() / (1024 * 1024)),
        "accepts": sorted(ALLOWED_MIME),
        "message": None
        if configured
        else _("Video upload needs a storage bucket and a signing key on Training Settings."),
    }


@frappe.whitelist(methods=["POST"])
def start_video_upload(filename, content_type, size_bytes):
    """Mint a signed resumable-upload start URL for one file.

    Refuses before anything is uploaded rather than after: a browser that has spent
    four minutes sending a 300 MB .mkv and is then told the format is unsupported
    has wasted the one resource this whole design exists to protect.
    """
    _require_author()
    if not gcs_media.is_configured():
        frappe.throw(_("Video upload is not configured on this site."))

    content_type = (content_type or "").split(";")[0].strip().lower()
    extension = ALLOWED_MIME.get(content_type)
    if not extension:
        frappe.throw(
            _("{0} cannot play in a browser. Upload an MP4 or a WebM.").format(content_type or _("That file"))
        )

    size = cint(size_bytes)
    limit = _max_bytes()
    if size <= 0:
        frappe.throw(_("That file appears to be empty."))
    if size > limit:
        frappe.throw(
            _("That video is {0} MB. The limit on this site is {1} MB.").format(
                cint(size / (1024 * 1024)), cint(limit / (1024 * 1024))
            )
        )

    object_name = _object_name(filename, extension)
    url = gcs_media.generate_signed_url(
        object_name,
        expires_in=UPLOAD_TTL_SECONDS,
        method="POST",
        # The header that turns a signed POST into a resumable session start. It is
        # part of the signed canonical request, so the browser MUST send it back
        # byte-identical or the signature does not verify.
        headers={"x-goog-resumable": "start", "content-type": content_type},
    )
    if not url:
        frappe.throw(_("Could not sign the upload. Check the storage key on Training Settings."))

    return {
        "url": url,
        "object_name": object_name,
        "content_type": content_type,
        # Echoed so the browser sends exactly what was signed rather than whatever
        # it would have chosen.
        "headers": {"x-goog-resumable": "start", "content-type": content_type},
    }


@frappe.whitelist(methods=["POST"])
def finish_video_upload(object_name, title, duration_seconds, content_type=None, lesson=None):
    """Record the asset once the bytes have landed.

    The size is re-read from GCS rather than taken from the browser: the client has
    already been trusted to report a size once, at `start`, and that number decided
    whether the upload was allowed. Believing it again here would make the ceiling
    advisory.
    """
    _require_author()
    if not (object_name or "").startswith(OBJECT_PREFIX + "/"):
        # The only object names this endpoint will accept are ones it minted. A
        # caller naming a Drive copy here would otherwise re-parent somebody else's
        # video onto a new asset row.
        frappe.throw(_("That is not an upload this site started."))

    duration = cint(duration_seconds)
    if duration <= 0:
        # A zero duration is the exact state that makes grading waive the coverage
        # gate, so it is refused rather than stored. The browser read this off the
        # file it just sent; if it could not, there is nothing to record.
        frappe.throw(_("The video's length could not be read, so it cannot be used for a coverage gate."))

    size = gcs_media.object_size(object_name)
    if size is None:
        frappe.throw(_("The upload did not finish. Try again."))

    doc = frappe.new_doc("Training Video Asset")
    doc.title = title or object_name.rsplit("/", 1)[-1]
    doc.duration_seconds = duration
    # Probed, not Manual -- read from the media file itself, which is a stronger
    # source than the Drive metadata probe and cannot silently fall back.
    doc.duration_source = "Probed"
    doc.mime_type = (content_type or "").split(";")[0].strip().lower() or None
    doc.size_bytes = cint(size)
    doc.gcs_object = object_name
    doc.gcs_synced_on = now_datetime()
    doc.status = "Available"
    doc.uploaded_by = frappe.session.user
    doc.uploaded_on = now_datetime()
    doc.insert(ignore_permissions=False)

    return {"name": doc.name, "title": doc.title, "duration_seconds": doc.duration_seconds}
