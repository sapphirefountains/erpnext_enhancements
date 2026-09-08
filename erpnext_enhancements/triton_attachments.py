"""File attachments for the embedded Triton chat widget.

Separate module from ``triton_chat.py`` on purpose, and the reason is a security property
rather than tidiness. Every method in ``triton_chat`` reaches Triton, so every one of them
passes through ``mint_user_token()`` -- which is where the *only* authorization gate in that
file lives (the ``Guest`` throw and ``user_has_widget_access()``). Nothing there is gated by
being ``@frappe.whitelist()``; ``@frappe.whitelist()`` requires a session and nothing more.

The methods here write Frappe rows; all but one of them reach Triton **not at all**. So they
would have inherited no gate whatsoever from sitting next to their neighbours, and a reader
skimming ``triton_chat.py`` would have had every reason to assume otherwise. Putting them in
their own file with one loud :func:`require_widget_access` at the top of every method makes
the gate a thing you can see instead of a thing you inherit.

The exception is :func:`google_link_status`, which does mint a token and does call Triton --
and it is exactly the case the rule was written for. It opens with the same
:func:`require_widget_access` as its neighbours *before* it mints anything, so the gate is
still the visible one at the top of the method rather than the incidental one inside
``mint_user_token``.

HOW THE FILE ACTUALLY REACHES THE MODEL
---------------------------------------
It is a **reference**, not a payload. Nothing here extracts text, and nothing here uploads
bytes to Triton.

The reason is that the endpoint the widget already talks to cannot carry a file and cannot be
made to carry one from this side. ``POST /api/v1/assistant/sessions/{id}/query/stream`` binds
Triton's ``ChatQuery``, and while that schema *declares* ``parts`` and
``google_drive_file_ids``, the handler reads neither -- pydantic accepts them, FastAPI
discards them, and there is no 400 to tell you. Triton's real file surface is a different
route (``.../query/stream_multimodal``, multipart), which ``triton_chat._request``
structurally cannot produce: it hardcodes ``json=payload`` and
``Content-Type: application/json``.

So the file is named to the model and the model goes and gets it, with two tools that are
already in Triton's CORE tool pack on every session for every persona:

* ``fac_extract_file_content`` -- Frappe Assistant Core, takes a Frappe ``file_url``
  verbatim, 50 MB ceiling, ``max_pages`` 50 by default, and runs under the ERPNext identity
  the user linked to Triton. Its own ``_check_file_access`` calls
  ``frappe.has_permission(file.attached_to_doctype, "read", file.attached_to_name)``, which
  lands on this app's ``has_permission`` hook for ``Triton Chat Attachment``. That is why
  the file is anchored to a row rather than left orphaned: the same function refuses a
  private *unattached* file to anyone below System Manager.
* ``gws_get_file_content`` -- reads a Google Drive file by id with the user's own stored
  OAuth credential.

What that buys, beyond not needing a Triton deploy: the extracted text enters the
conversation **once, on demand, as a tool result**, instead of being pasted into the prompt.
Triton's ``build_history`` applies no truncation and no window to user messages, so text
injected into one prompt is re-billed verbatim on every later turn of that session, forever.
A 40-page contract injected on turn 2 of a ten-turn session is roughly 270k additional prompt
tokens over the session. The reference costs about twenty.

The ceiling and the truncation therefore live in two places, and neither of them is here:
the **upload** ceiling is enforced by :func:`register_upload` against the bytes on disk, and
the **extraction** ceiling is FAC's (50 MB / 50 pages, with its own truncation note). This
module's job is to make sure the thing being pointed at is one the caller is allowed to
point at.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
* **It never fetches a Google Drive file.** The app's only Drive client
  (``google_drive/drive_utils.py``) is a service account with ``SCOPES = [".../auth/drive"]``
  and no ``with_subject`` anywhere in the repo -- so it can read the two Shared Drives it was
  added to and nothing else. On a file out of somebody's personal Drive it returns
  ``404 File not found``, which reads as a bug rather than as a permission boundary. Drive
  bytes stay in Drive; Triton reads them as the user.
* **It never accepts, stores or logs a Google access token.** :func:`attach_drive_file` takes
  no token parameter, because it has nothing to spend one on. A token parameter would create
  a place for a credential to end up in an Error Log's frame locals, which this app has
  already been bitten by once.
* **It never hands the browser a ``/private/files/...`` URL.** Attachments are addressed by
  their ``Triton Chat Attachment`` name and served by :func:`download`. Two reasons: the
  proxy's ``scrub_urls`` blanks any URL containing a space and Frappe v16 stores file names
  with their spaces intact (``File.before_insert`` runs ``unquote(self.file_url)``), and --
  the older reason, borrowed from the chat module -- there is no point publishing the key to
  a door and then relying on the lock.

INDENTATION: four spaces, matching ``triton_chat.py``, which is the file this one is read
next to. ``ruff format`` is configured ``indent-style = "tab"`` repo-wide; running it here
would rewrite the file and bury the change.
"""
from __future__ import annotations

import mimetypes
import os
from contextlib import contextmanager
from urllib.parse import quote, urlparse

import frappe
import requests
from frappe import _
from frappe.utils import add_days, cint, now_datetime, nowdate

from erpnext_enhancements.triton_chat import get_settings, mint_user_token, user_has_widget_access

DOCTYPE = "Triton Chat Attachment"

SOURCE_UPLOAD = "ERPNext Upload"
SOURCE_DRIVE_LINK = "Drive Link"

#: How many un-sent chips one person may hold at once. A composer, not a filing cabinet.
MAX_PENDING_PER_USER = 8

#: How many attachments may ride on a single turn. Each one is a line of preamble and an
#: invitation for the model to spend a tool call, so the cap is about the model's attention
#: rather than about storage.
MAX_ATTACHMENTS_PER_TURN = 4

#: Fallback ceiling when nothing is configured. Matches Frappe v16's own fallback
#: (``frappe.core.api.file.get_max_file_size`` -> ``25 * 1024 * 1024``) so the two agree by
#: default. Note that ``chat/sync/attachments.py`` still documents Frappe's default as 10 MB;
#: that comment predates v16 and is wrong.
DEFAULT_MAX_UPLOAD_MB = 25

#: Days a *used* attachment is kept. Time-based, because Triton owns the conversation and
#: ERPNext can never observe a chat being deleted -- there is no other GC signal available.
DEFAULT_RETENTION_DAYS = 30

#: Hours an un-sent chip survives. A chip attached and never sent is abandoned, not archived.
PENDING_TTL_HOURS = 24

#: Longest filename echoed into the prompt or a chip label.
_MAX_NAME_CHARS = 200

#: Extensions we will anchor. **Derived from the stored filename server-side** -- a
#: client-declared ``content_type`` is an assertion by the uploader, not evidence, so it is
#: never trusted and never stored.
#:
#: The list is scoped to what ``fac_extract_file_content`` can actually do something with
#: (pypdf, PIL, pandas, python-docx). Something outside it is not a security incident, it is
#: an attachment the model could only see the name of -- so the refusal is a product decision
#: and says so. Those readers are guarded by ``ImportError`` checks on the bench, so a
#: missing library is a tool error rather than a crash either way.
ALLOWED_EXTENSIONS = frozenset({
    ".pdf",
    ".txt", ".md", ".csv", ".tsv", ".json", ".log", ".xml", ".yaml", ".yml",
    ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff",
})

#: The three answers :func:`google_link_status` may give. ``UNKNOWN`` is not a failure mode,
#: it is the honest answer to "we could not find out", and the widget must never block on it.
LINK_CONNECTED = "connected"
LINK_DISCONNECTED = "disconnected"
LINK_UNKNOWN = "unknown"

#: The probe. Chosen over ``/google/drive/folders`` (three Google calls, ~500x the payload for
#: the same answer) and over ``/google/calendar/list`` (``list_calendars`` swallows its own
#: exception and returns ``[]``, so a revoked credential would probe as *connected*). One
#: ``files.list`` with ``pageSize=1``; the discovery document is bundled, so there is no
#: second round trip.
_GOOGLE_PROBE_PATH = "/api/v1/integrations/google/drive?limit=1"

#: Where the user goes to hand Triton a Google credential. It is Triton's ordinary Google
#: login (``access_type=offline``, ``prompt=consent``), and its callback resolves the user by
#: *email* and updates the existing row -- which is why a bridge-provisioned user comes back
#: with a credential on the same row rather than a second one.
_GOOGLE_CONNECT_PATH = "/api/v1/auth/google/login"

#: (connect, read) seconds. Deliberately NOT ``settings["timeout"]``, which is 120: this runs
#: on panel open, and a widget that hangs for two minutes deciding whether to show a hint is
#: worse than one that shrugs and lets the picker open.
_LINK_PROBE_TIMEOUT = (5, 8)

#: How long each answer is trusted. **Asymmetric on purpose.** There is no callback from
#: Triton into ERPNext when somebody completes OAuth, so a long negative TTL would lock a user
#: out of the feature they just fixed for half an hour; a short one costs one cheap request a
#: minute for the handful of people who genuinely are not connected. ``unknown`` is absent
#: from this map and is therefore never cached at all -- caching "we could not tell" would
#: turn a thirty-second Triton blip into thirty minutes of a wrong hint.
_LINK_CACHE_TTL = {LINK_CONNECTED: 1800, LINK_DISCONNECTED: 60}

#: Hosts a picker ``webViewLink`` may point at. The value is rendered as an ``href`` in the
#: widget, and "it came from Google's picker" is a claim made by the browser.
_DRIVE_HOSTS = frozenset({
    "drive.google.com",
    "docs.google.com",
    "sheets.google.com",
    "slides.google.com",
})


# ---------------------------------------------------------------------------
# The gate
# ---------------------------------------------------------------------------
def require_widget_access() -> dict:
    """Open every whitelisted method in this module. Returns the resolved settings.

    The two throws are copied **verbatim** from ``triton_chat.mint_user_token`` -- the same
    strings, the same ``frappe.PermissionError`` -- rather than paraphrased, because they are
    the same refusal and a user turned away from one surface should not get two different
    explanations. The third throw is copied from ``triton_chat._request``, which is the only
    place the master switch is checked; a method that never calls ``_request`` would
    otherwise keep working with the assistant switched off, and the master switch is the top
    layer of the rollback story.

    This does not mint a token, deliberately. Most methods here make no Triton call at all, so
    minting one would burn a bridge round trip and a cache entry for a gate we can evaluate
    locally -- and :func:`google_link_status`, which does call Triton, mints its own *after*
    passing this, so a refused caller never costs the bridge a request.
    """
    user = frappe.session.user
    if user in ("Guest", None):
        frappe.throw(_("You must be logged in to use Triton."), frappe.PermissionError)

    settings = get_settings()
    if not settings["enabled"]:
        frappe.throw(_("Triton Assistant is disabled."))
    if not user_has_widget_access(settings):
        frappe.throw(_("You do not have access to the Triton assistant."), frappe.PermissionError)
    if not settings.get("attachments_enabled"):
        frappe.throw(_("Attachments are turned off for the Triton assistant."))
    return settings


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------
def effective_max_upload_bytes(settings: dict | None = None) -> int:
    """The real ceiling: the lower of our setting and the site's own.

    ``configured > 0`` rather than ``configured or DEFAULT`` because on a Single a new
    ``Int`` field reads ``None`` on every existing site -- ``load_from_db`` calls
    ``get_singles_dict``, which reads ``tabSingles`` and applies no defaults -- and
    ``cint(None)`` is ``0``. Read the obvious way, a declared 25 MB cap becomes a 0 MB cap
    that silently rejects every upload, on production only. Same shape as
    ``crm_enhancements/fountain_move/intake.py``'s photo cap, and the same reason.

    Capped against ``get_max_file_size()`` because core enforces that one first: a larger
    number here would be a promise the site refuses to keep, and the user would see core's
    ``MaxFileSizeReachedError`` instead of our message.
    """
    if settings is None:
        settings = get_settings()
    configured = cint(settings.get("max_upload_mb"))
    ours = (configured if configured > 0 else DEFAULT_MAX_UPLOAD_MB) * 1024 * 1024

    site_cap = 0
    try:
        from frappe.core.api.file import get_max_file_size

        site_cap = cint(get_max_file_size())
    except Exception:
        # Older/newer core, or no site context. Our own ceiling still applies.
        site_cap = 0
    return min(ours, site_cap) if site_cap > 0 else ours


def _retention_days(settings: dict | None = None) -> int:
    if settings is None:
        settings = get_settings()
    configured = cint(settings.get("attachment_retention_days"))
    return configured if configured > 0 else DEFAULT_RETENTION_DAYS


def _extension_of(file_name) -> str:
    return os.path.splitext(str(file_name or ""))[1].lower()


def _clip(value, limit: int = _MAX_NAME_CHARS) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _human_size(num_bytes) -> str:
    size = cint(num_bytes)
    if size <= 0:
        return "size unknown"
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{size / 1024:.0f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def _bytes_on_disk(file_url) -> int:
    """Size of the actual file, not what the uploader said it was.

    ``File.file_size`` is set from ``frappe.form_dict.file_size`` -- a form field the client
    supplies -- so it is a record, not an enforcement point. Returns 0 when the path cannot
    be resolved; the caller treats that as "unknown" and falls back to the stored value
    rather than waving the upload through.
    """
    url = str(file_url or "")
    try:
        if url.startswith("/private/"):
            path = frappe.get_site_path(url.lstrip("/"))
        elif url.startswith("/files/"):
            path = frappe.get_site_path("public", url.lstrip("/"))
        else:
            return 0
        return os.path.getsize(path) if os.path.exists(path) else 0
    except Exception:
        return 0


def _discard_and_throw(file_docname: str, message: str) -> None:
    """Delete the just-uploaded File, then refuse. Never returns.

    **The commit is the whole reason this is a helper.** The ``File`` was written by a
    *previous* request -- core's ``/api/method/upload_file`` -- so it is already committed to
    disk and to the table. ``frappe.throw`` rolls back *this* request, which would roll the
    delete back with it and leave the rejected bytes on disk forever with nothing pointing at
    them: the exact orphan this module exists to avoid, created by the code path that
    refused it. Committing the delete first is what makes the refusal actually remove the
    file. Safe to commit here because every caller finishes its reads before reaching this
    point and has written nothing.
    """
    try:
        frappe.delete_doc("File", file_docname, ignore_permissions=True, delete_permanently=True)
        frappe.db.commit()
    except Exception:
        frappe.db.rollback()
    frappe.throw(message, title=_("Attachment refused"))


# ---------------------------------------------------------------------------
# Whitelisted API (called by the widget)
# ---------------------------------------------------------------------------
@frappe.whitelist()
def register_upload(file: str | None = None, file_url: str | None = None,
                    session_id: int | str | None = None) -> dict:
    """Adopt a File the browser has already uploaded, and return its chip.

    **The upload itself does not come through here.** The widget POSTs multipart straight to
    core's ``/api/method/upload_file`` with ``is_private=1`` and **no** ``doctype`` /
    ``docname`` -- permitted for any signed-in user, because ``check_write_permission``
    returns immediately when ``doctype`` is empty (v16 ``handler.py``). That is the pattern
    ``public/js/feedback/transport.js`` already uses, and it is why this app does not need an
    upload endpoint of its own: a bespoke one would have to re-implement core's extension
    checks, image optimisation and folder handling, and would arrive with no authorization
    gate at all unless somebody remembered to add one.

    What this method adds is everything core does not know to do: the ownership check, the
    ceilings, and the anchor row that makes the file both readable and collectable.

    Five refusals, in this order, and the ordering matters -- ownership is settled before
    anything that would reveal a property of the file:

    1. **The File exists.** A missing row and an unreadable one answer identically.
    2. **You uploaded it.** A File the caller does not own is somebody else's.
    3. **It is not already attached to something.** Re-pointing an attached File would move
       an attachment off whatever document currently holds it.
    4. **The extension is one we can do something with.**
    5. **It is inside the ceiling**, measured on disk.

    A refusal after step 2 deletes the file -- see :func:`_discard_and_throw` for why that
    needs its own commit.

    ``session_id`` is accepted and stored when known, but it is genuinely optional: the
    widget's ``ensureSession()`` runs at send time, so a user who attaches before their first
    message has no session yet. :func:`describe_attachments_for_prompt` stamps it later.
    """
    settings = require_widget_access()
    user = frappe.session.user

    name = (file or "").strip()
    if not name:
        name = _resolve_by_url(file_url, user)
    if not name:
        frappe.throw(_("That upload is no longer available."))

    row = frappe.db.get_value(
        "File",
        name,
        ["name", "owner", "attached_to_doctype", "attached_to_name", "file_url",
         "file_name", "is_private", "file_size"],
        as_dict=True,
    )
    if not row:
        frappe.throw(_("That upload is no longer available."))
    if (row.get("owner") or "") != user:
        # Not discarded: it is not ours to delete.
        frappe.throw(_("You can only attach files you uploaded."), frappe.PermissionError)
    if row.get("attached_to_doctype"):
        frappe.throw(_("That file is already attached to something else."))

    file_name = _clip(row.get("file_name") or os.path.basename(row.get("file_url") or ""))
    extension = _extension_of(file_name)
    if extension not in ALLOWED_EXTENSIONS:
        _discard_and_throw(
            row["name"],
            _("Triton can't read {0} files. Attach a document, spreadsheet, PDF or image.")
            .format(extension or _("those")),
        )

    ceiling = effective_max_upload_bytes(settings)
    measured = _bytes_on_disk(row.get("file_url") or "") or cint(row.get("file_size"))
    if measured > ceiling:
        _discard_and_throw(
            row["name"],
            _("{0} is {1}, over the {2} MB limit for Triton attachments.").format(
                file_name, _human_size(measured), int(ceiling / (1024 * 1024))
            ),
        )

    pending = frappe.db.count(DOCTYPE, {"owner": user, "used_at": ["is", "not set"]})
    if cint(pending) >= MAX_PENDING_PER_USER:
        _discard_and_throw(
            row["name"],
            _("You already have {0} attachments waiting. Send them or remove one first.")
            .format(MAX_PENDING_PER_USER),
        )

    if not cint(row.get("is_private")):
        # Repair, not the control. The bytes were briefly on a path the web server serves
        # with no permission check at all, so the widget must send is_private=1 -- but a
        # public file left as-is would be permanently world-readable, and the alternative to
        # repairing it is deleting somebody's upload over a client bug.
        #
        # Saving through the Document (not `db.set_value`) is what actually MOVES the bytes:
        # `File.validate` calls `handle_is_private_changed` when the flag changes. A
        # `db.set_value` here would flip the column and leave the file on the public path,
        # which is worse than not trying.
        try:
            doc = frappe.get_doc("File", row["name"])
            doc.is_private = 1
            doc.save(ignore_permissions=True)
            row["file_url"] = doc.file_url
        except Exception:
            _discard_and_throw(
                row["name"],
                _("That upload could not be made private, so it was removed. Please try again."),
            )

    attachment = frappe.get_doc({
        "doctype": DOCTYPE,
        "source": SOURCE_UPLOAD,
        "file": row["name"],
        "file_name": file_name,
        # Guessed here rather than taken from the request: what the browser calls a file is
        # an assertion by the uploader.
        "content_type": (mimetypes.guess_type(file_name)[0] or "application/octet-stream"),
        "file_size": measured,
        "triton_session": cint(session_id) or 0,
        "expires_on": add_days(nowdate(), _retention_days(settings)),
    })
    attachment.insert(ignore_permissions=True)

    # Re-point the File at its anchor. `update_modified=False` because this is bookkeeping,
    # not an edit anybody made.
    frappe.db.set_value(
        "File",
        row["name"],
        {"attached_to_doctype": DOCTYPE, "attached_to_name": attachment.name},
        update_modified=False,
    )

    return _chip(
        attachment.name, SOURCE_UPLOAD, file_name, attachment.content_type, measured, None
    )


@frappe.whitelist()
def attach_drive_file(drive_file_id: str, file_name: str | None = None,
                      mime_type: str | None = None, drive_url: str | None = None,
                      session_id: int | str | None = None) -> dict:
    """Record a Google Drive file the picker returned. **No bytes are fetched.**

    Note the signature: there is no ``access_token`` parameter, and that is a decision rather
    than an omission. ERPNext has nothing to spend a Drive token on, because it never reads
    the file -- Triton does, with the user's own stored Google credential. Accepting one
    would create a place for a live OAuth token to reach an Error Log's frame locals, and
    this app has already shipped that bug once.

    The server-side fetch this replaces would not have worked anyway. The app's only Drive
    client is a service account with no domain-wide delegation, so it can see the two Shared
    Drives it was added to and nothing else; on a file out of somebody's personal Drive it
    returns ``404 File not found`` -- i.e. it fails as "missing" rather than as "not
    permitted", on precisely the case this feature exists to serve.

    Two consequences the caller must handle rather than discover:

    * A user who has never signed in to the Triton web app has no Google credential stored
      there (the ERPNext identity bridge provisions email and full name and nothing else),
      so the model's Drive read will come back empty. That needs a "connect Google in
      Triton" pre-flight in the UI, not a silent no-op -- :func:`google_link_status` is it.
    * ``drive_url`` is validated, not trusted. It is rendered as an ``href``, and "the picker
      gave me this" is a claim made by the browser.
    """
    settings = require_widget_access()

    file_id = (drive_file_id or "").strip()
    if not file_id or len(file_id) > 140 or "/" in file_id or "\\" in file_id:
        frappe.throw(_("That Google Drive file could not be identified."))

    label = _clip(file_name) or _("Google Drive file")
    url = _safe_drive_url(drive_url)

    attachment = frappe.get_doc({
        "doctype": DOCTYPE,
        "source": SOURCE_DRIVE_LINK,
        "file_name": label,
        # The picker's mimeType is a Google-authored string about a Google-hosted object, so
        # it is recorded -- but it is only ever displayed and passed to the model as prose,
        # never used to decide anything here.
        "content_type": _clip(mime_type, 140),
        "drive_file_id": file_id,
        "drive_url": url,
        "triton_session": cint(session_id) or 0,
        "expires_on": add_days(nowdate(), _retention_days(settings)),
    })
    attachment.insert(ignore_permissions=True)

    return _chip(attachment.name, SOURCE_DRIVE_LINK, label, attachment.content_type, 0, url)


@frappe.whitelist()
def google_link_status(refresh: int | str = 0) -> dict:
    """Can Triton read Drive as this user? The pre-flight :func:`attach_drive_file` needs.

    This closes the silent no-op that method's docstring describes. A user who has only ever
    used this widget exists in Triton with an email and a full name and nothing else -- the
    identity bridge provisions no Google credential -- so ``gws_get_file_content`` comes back
    with "Google Workspace is not connected.", the model says it could not read the file, and
    the person concludes attachments are broken. Nobody is told to go and connect anything.

    **THREE states, never two, and the third one is the whole point.** ``unknown`` is returned
    for a timeout, a 5xx, a 403, an unreachable Triton -- anything that is not one of the two
    answers we can actually prove. The widget must fail *open* on it and let the picker run.
    A two-state helper that read "not 200" as "not connected" would render "Connect Google" to
    the entire company during a Triton outage and send already-connected people through a
    pointless OAuth round trip, at exactly the moment the widget is already degraded.

    Why the status code decides and the body does not:

    * **400 is authoritative.** Triton's ``_gws`` guard raises it on a falsy ``client.creds``,
      and that is the *identical* predicate the model's own tool gates on -- ``intelligence``
      constructs the same ``GoogleWorkspaceClient`` and checks the same attribute. So this is
      not an approximation of what the user will experience, it is the same boolean. It is
      broader than "no row" (a rotated encryption key or a revoked refresh token also land
      here) and that breadth is correct: the question is "will the Drive read work", not
      "does a row exist".
    * **The body is not consulted.** Triton renders errors as ``{"message": ..., "request_id":
      ...}`` -- the key is ``message``, not ``detail`` -- and matching on prose would mean an
      upstream copy edit silently downgrading a provable 400 to ``unknown``.
    * **A revoked-but-unexpired credential answers 500**, because Drive's 401 is not in
      Triton's retry set and falls through to its generic handler. That is ``unknown`` here,
      which is the right cost: a rare user gets no hint rather than everybody getting a wrong
      one.

    Not folded into ``get_config``: that method is called on every page load by every user and
    must never make a network call, and this one is only interesting to somebody who is about
    to touch the Drive button.

    The one thing this cannot answer is *which* Google account the person will consent as --
    nothing binds the consent to their ERPNext session, so the credential can land on a
    different Triton row entirely. That defence is a sentence in the empty state naming the
    expected address, not a check here.
    """
    settings = require_widget_access()
    key = _link_cache_key(frappe.session.user)
    # Recomputed from settings on every call rather than stored with the cached state: the
    # cached thing is the *finding*, and a base_url edited in the Desk must not be shadowed by
    # a half-hour-old copy of itself.
    connect_url = _google_connect_url(settings)

    if not cint(refresh):
        cached = _cached_link_state(key)
        if cached:
            return {
                "state": cached["state"],
                "connect_url": connect_url,
                # The probe's timestamp, not this request's -- the field says when we last
                # actually found out, which is what a "checked just now?" affordance needs.
                "checked_at": cached.get("checked_at") or "",
            }

    state = _probe_google_link(settings)
    checked_at = _now_iso()

    ttl = _LINK_CACHE_TTL.get(state)
    if ttl:
        try:
            frappe.cache().set_value(
                key, {"state": state, "checked_at": checked_at}, expires_in_sec=ttl
            )
        except Exception:
            # A cache that will not write costs a request per panel open, not a feature.
            pass

    return {"state": state, "connect_url": connect_url, "checked_at": checked_at}


@frappe.whitelist()
def list_pending() -> list:
    """The caller's un-sent chips, so a page reload repaints the composer.

    ``["is", "not set"]`` rather than a comparison. A ``<`` or ``!=`` filter on a nullable
    datetime is wrapped in a ``coalesce`` sentinel by Frappe's query builder and silently
    matches NULLs -- which here would mean every attachment ever sent reappearing as a
    pending chip.
    """
    require_widget_access()
    rows = frappe.get_all(
        DOCTYPE,
        filters={"owner": frappe.session.user, "used_at": ["is", "not set"]},
        fields=["name", "source", "file_name", "content_type", "file_size", "drive_url"],
        order_by="creation asc",
        limit_page_length=MAX_PENDING_PER_USER,
    )
    return [
        _chip(
            r["name"], r["source"], r["file_name"], r["content_type"],
            r["file_size"], r.get("drive_url"),
        )
        for r in rows
    ]


@frappe.whitelist()
def remove(attachment: str) -> dict:
    """Delete one of the caller's own attachments, and its bytes with it.

    ``delete_doc`` on this row calls core's ``remove_all(doctype, name, from_delete=True)``,
    which deletes the attached ``File`` and its bytes. That is the entire garbage-collection
    story for the upload path, and it only works because the file is *attached* to this row.

    The ownership check is written out rather than left to ``ignore_permissions=False``:
    ``delete_doc`` would consult the ``if_owner`` DocPerm and the ``has_permission`` hook and
    reach the same answer, but it would reach it with a stack trace, and a user removing a
    chip should get a sentence.
    """
    require_widget_access()
    name = (attachment or "").strip()
    owner = frappe.db.get_value(DOCTYPE, name, "owner") if name else None
    if not owner or owner != frappe.session.user:
        # Same refusal whether it is missing or somebody else's: distinguishing them answers
        # "does an attachment called X exist" for a person with no business knowing.
        frappe.throw(_("That attachment is no longer available."), frappe.PermissionError)

    frappe.delete_doc(DOCTYPE, name, ignore_permissions=True, delete_permanently=True)
    return {"removed": name}


@frappe.whitelist()
def download(attachment: str) -> None:
    """Serve one attachment's bytes back to its owner. **The** byte path.

    Addressed by ``Triton Chat Attachment`` name, never by a ``File`` name and never by a
    ``/private/files/`` URL, for the reason the chat module states plainly: there is no point
    publishing the key to a door and then relying on the lock. There is a second, duller
    reason here -- ``triton_chat``'s ``scrub_urls`` blanks any URL containing a space, and
    Frappe v16 stores file names with their spaces intact (``File.before_insert`` runs
    ``unquote(self.file_url)``), so ``/private/files/Purchase Order 123.pdf`` renders as an
    empty ``href`` and the chip looks broken. A query-string URL on this endpoint has no such
    problem.

    Guest is refused explicitly and first, before any lookup. An unauthenticated request is
    not a permission question.

    Every refusal is the same ``PermissionError`` -- missing row, somebody else's row, a
    Drive Link with no local bytes, a File whose bytes are gone. Distinguishing them would
    answer "does this person have an attachment called X" for somebody who is not that
    person.
    """
    from erpnext_enhancements.ai_governance import permissions

    require_widget_access()

    name = (attachment or "").strip()
    user = frappe.session.user
    if not name or user in ("", "Guest", None):
        raise frappe.PermissionError(_("Not permitted"))

    row = frappe.db.get_value(
        DOCTYPE, name, ["name", "owner", "source", "file", "file_name"], as_dict=True
    )
    # The decision is the hook itself, called directly rather than through
    # `frappe.has_permission` -- one rule, one expression of it.
    if not row or not permissions.triton_chat_attachment_has_permission(row, "read", user):
        raise frappe.PermissionError(_("Not permitted"))

    if (row.get("source") or "") == SOURCE_DRIVE_LINK or not row.get("file"):
        raise frappe.PermissionError(_("Not permitted"))

    data = _file_bytes(str(row.get("file")))
    if not data:
        raise frappe.PermissionError(_("Not permitted"))

    frappe.local.response.filename = _safe_download_name(row.get("file_name") or "")
    frappe.local.response.filecontent = data
    frappe.local.response.type = "download"


# ---------------------------------------------------------------------------
# The prompt side -- called by triton_chat._build_prompt, not whitelisted
# ---------------------------------------------------------------------------
def describe_attachments_for_prompt(refs, session_id=None) -> str:
    """Turn ``{"type": "file", "name": ...}`` refs into the preamble block, and mark them used.

    **The browser sends a name and nothing else, and everything else is re-resolved here.**
    That is the security property of this function. A ref carrying its own ``file_url`` would
    let anyone with a session name any path on the site and have the model fetch it under
    their identity -- so a client-supplied ``file_url`` is ignored, and the ``owner`` filter
    is written out explicitly rather than left to the permission hook (a hook covers
    ``get_all`` but says nothing about a query somebody writes later).

    A ref the caller does not own is **skipped in silence**: no line, no error. Raising would
    answer "is there an attachment called X" for the person guessing.

    Returns "" when there is nothing to say, so on every turn without an attachment the
    caller's preamble is byte-identical to what it was before this feature existed.
    """
    names, seen = [], set()
    for ref in refs or []:
        if not isinstance(ref, dict) or ref.get("type") != "file":
            continue
        name = str(ref.get("name") or "").strip()
        if name and name not in seen:
            seen.add(name)
            names.append(name)
        if len(names) >= MAX_ATTACHMENTS_PER_TURN:
            break
    if not names:
        return ""

    rows = frappe.get_all(
        DOCTYPE,
        filters={"name": ["in", names], "owner": frappe.session.user},
        fields=["name", "source", "file", "file_name", "content_type", "file_size",
                "drive_file_id"],
    )
    if not rows:
        return ""

    lines, used = [], []
    for row in rows:
        label = _clip(row.get("file_name")) or "attachment"
        if (row.get("source") or "") == SOURCE_DRIVE_LINK:
            drive_id = row.get("drive_file_id") or ""
            if not drive_id:
                continue
            lines.append(f"- {label} — Google Drive file id: {drive_id}")
        else:
            url = (
                frappe.db.get_value("File", row.get("file"), "file_url")
                if row.get("file")
                else None
            )
            if not url:
                continue
            lines.append(
                f"- {label} — {row.get('content_type') or 'unknown type'}, "
                f"{_human_size(row.get('file_size'))} — Frappe file_url: {url}"
            )
        used.append(row["name"])

    if not lines:
        return ""

    _mark_used(used, session_id)

    # Its own block, deliberately not folded into the `[ERPNEXT PAGE CONTEXT]` preamble.
    # That preamble is about what the user is LOOKING at and tells the model to fetch live
    # details; this is about what they HANDED OVER. Keeping them separate also means every
    # turn without an attachment produces the byte-identical preamble it always has.
    return (
        "[ERPNEXT ATTACHMENTS] The user attached the following to this message. The contents "
        "are NOT in your context -- read one before answering about it, with "
        "`fac_extract_file_content` for a Frappe file_url or `gws_get_file_content` for a "
        "Google Drive file id. Do not answer from the filename alone, and do not guess at a "
        "file you were unable to read -- say so:\n" + "\n".join(lines) + "\n\n"
    )


def _mark_used(names, session_id=None) -> None:
    """Stamp ``used_at`` (and the session, once it exists) on the attachments just sent.

    Called from the request half of ``stream_query``, before the streaming ``Response`` is
    handed back -- never from inside the generator, which runs after Frappe's request
    teardown and has no site context to write with.

    Failure here is swallowed on purpose: the stamp is bookkeeping for the sweeper and the
    composer, and a chat turn must not fail because a retention date could not be written.
    """
    if not names:
        return
    values = {"used_at": now_datetime()}
    if cint(session_id):
        values["triton_session"] = cint(session_id)
    for name in names:
        try:
            frappe.db.set_value(DOCTYPE, name, values, update_modified=False)
        except Exception:
            continue


# ---------------------------------------------------------------------------
# Retention
# ---------------------------------------------------------------------------
def purge_expired() -> int:
    """Daily: delete attachments past retention, and chips nobody ever sent.

    Two clocks, because they measure different things. ``expires_on`` retires a *used*
    attachment on a fixed schedule -- the only kind of expiry available, since Triton owns
    the conversation and ERPNext never learns that a chat was deleted. ``PENDING_TTL_HOURS``
    retires an *un-sent* chip, which is abandoned rather than archived.

    Deleting the row deletes its ``File`` through core's ``remove_all``. Each row is deleted
    on its own and failures are stepped over: one undeletable file must not stop the sweep,
    and the next pass tries it again.
    """
    cutoff = add_days(now_datetime(), -1 * (PENDING_TTL_HOURS / 24.0))
    stale_pending = frappe.get_all(
        DOCTYPE,
        filters={"used_at": ["is", "not set"], "creation": ["<", cutoff]},
        pluck="name",
        limit_page_length=0,
    )
    expired = frappe.get_all(
        DOCTYPE,
        filters={"expires_on": ["<", nowdate()]},
        pluck="name",
        limit_page_length=0,
    )

    deleted = 0
    for name in set(stale_pending) | set(expired):
        try:
            frappe.delete_doc(DOCTYPE, name, ignore_permissions=True, delete_permanently=True)
            deleted += 1
        except Exception:
            frappe.db.rollback()
            continue
    if deleted:
        frappe.db.commit()
    return deleted


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _chip(name: str, source, file_name, content_type, file_size, drive_url) -> dict:
    """The browser's view of one attachment.

    ``url`` is this app's own download endpoint with the attachment name in the query string
    -- never the ``File``'s ``file_url``. A query-string URL survives the widget's
    ``isSafeUrl`` (root-relative, same origin) and survives ``scrub_urls`` (no space in the
    path), which a real file path with a space in the filename does not.
    """
    if (source or "") == SOURCE_DRIVE_LINK:
        url = drive_url or ""
    else:
        url = (
            "/api/method/erpnext_enhancements.triton_attachments.download"
            f"?attachment={quote(str(name), safe='')}"
        )
    return {
        "type": "file",
        "name": name,
        "source": source,
        "title": file_name,
        "content_type": content_type or "",
        "file_size": cint(file_size),
        "url": url,
    }


def _now_iso() -> str:
    """``checked_at`` as ISO 8601."""
    return now_datetime().isoformat()


def _link_cache_key(user: str) -> str:
    """Per-USER, like ``triton_personas::`` and unlike ``triton_models_list``.

    The model list is identical for everyone; whether *you* have connected Google is not. A
    site-wide key here would serve one person's finding to the whole company -- and would do
    it in the direction that hurts, hiding the hint from everybody the moment one connected
    user warms the cache.
    """
    return f"triton_google_link::{user}"


def _cached_link_state(key: str) -> dict | None:
    """The stored finding, or None. A shape we no longer recognise reads as no cache."""
    try:
        cached = frappe.cache().get_value(key)
    except Exception:
        return None
    if isinstance(cached, dict) and cached.get("state") in _LINK_CACHE_TTL:
        return cached
    return None


def _google_connect_url(settings: dict) -> str:
    """The consent link handed to the browser. Never carries a credential.

    Built from ``base_url`` alone -- the Gateway Secret lives on the same settings dict and
    has no business in anything this module returns. Empty when the gateway is unconfigured,
    because a link to ``/api/v1/auth/google/login`` on no host is worse than no button.
    """
    base = (settings.get("base_url") or "").strip().rstrip("/")
    return f"{base}{_GOOGLE_CONNECT_PATH}" if base else ""


@contextmanager
def _muted():
    """Swallow the *message* as well as the exception.

    ``frappe.throw`` calls ``msgprint`` first, and ``msgprint`` appends to
    ``frappe.message_log`` **before** it raises. So catching the exception leaves the message
    behind, and a whitelisted method that then returns 200 hands the browser a populated
    ``_server_messages`` with no ``exc_type`` to attach it to -- which Frappe's own
    ``request.js`` renders as a red modal, because no handler claimed it.

    That is precisely the promise this probe makes and would otherwise break. During a Triton
    outage every user loading a Desk page would get a modal quoting a connection traceback,
    from a hint they never asked for, repeating on every panel open because ``unknown`` is
    deliberately never cached.

    ``frappe.flags.mute_messages`` is msgprint's own opt-out: it raises without appending, so
    the ``except`` still sees the exception and nothing reaches the browser. Read defensively
    because this module is imported by bench-free tests whose frappe stub has no ``flags``.
    """
    flags = getattr(frappe, "flags", None)
    previous = getattr(flags, "mute_messages", None) if flags is not None else None
    if flags is not None:
        flags.mute_messages = True
    try:
        yield
    finally:
        if flags is not None:
            flags.mute_messages = previous


def _probe_google_link(settings: dict) -> str:
    """One cheap Drive read as this user. Returns one of the three ``LINK_*`` states.

    Its own request helper rather than ``triton_chat._request``, and that is the reason this
    function exists at all: ``_request`` collapses every status >= 400 into a generic
    ``frappe.throw(_("Triton error ({0})"))`` and discards the body, so it structurally cannot
    tell 400 ("no Google credential") from 500 ("Triton is unwell"). Those are the two answers
    this whole feature turns on.

    It also re-mints on **401 or 403**, not 401 alone. Triton's ``get_current_user`` answers
    403 for a stale or invalid JWT -- only a wholly absent ``Authorization`` header produces
    401, from FastAPI's own bearer scheme -- so the retry-on-401 pattern copied from
    ``_request`` would never refresh a token that had simply aged out of our cache.

    Every failure is swallowed into ``unknown``. A probe that raises would take the panel down
    with it, which is a spectacular price for a hint.
    """
    base = (settings.get("base_url") or "").strip().rstrip("/")
    if not base:
        return LINK_UNKNOWN
    url = f"{base}{_GOOGLE_PROBE_PATH}"

    for attempt in range(2):
        try:
            # Muted: mint_user_token reports its failures with frappe.throw, and a throw
            # caught here would still have queued its message for the browser. See _muted().
            with _muted():
                token = mint_user_token(force_refresh=attempt > 0)
        except Exception:
            # The bridge is unreachable or unconfigured. Not a statement about Google.
            return LINK_UNKNOWN
        try:
            resp = requests.request(
                "GET",
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=_LINK_PROBE_TIMEOUT,
            )
        except Exception:
            return LINK_UNKNOWN

        status = cint(getattr(resp, "status_code", None) or 0)
        if status in (401, 403) and attempt == 0:
            continue
        if status == 200:
            return LINK_CONNECTED
        if status == 400:
            return LINK_DISCONNECTED
        return LINK_UNKNOWN
    return LINK_UNKNOWN


def _resolve_by_url(file_url, user: str) -> str:
    """Fallback lookup when the caller has a URL but not a File docname.

    Scoped to the caller's own *unattached* files, and **ambiguity is a refusal**: Frappe
    deduplicates identical content onto one ``file_url``, so two rows can legitimately share
    one, and picking either would be a coin flip on which file gets adopted.
    """
    url = str(file_url or "").strip()
    if not url:
        return ""
    matches = frappe.get_all(
        "File",
        filters={"file_url": url, "owner": user, "attached_to_doctype": ["is", "not set"]},
        pluck="name",
        limit_page_length=2,
    )
    return matches[0] if len(matches) == 1 else ""


def _safe_drive_url(value) -> str:
    """Accept a picker link only if it is https on a Google host. Otherwise store nothing.

    Returning "" rather than throwing: a missing link costs the chip its hyperlink, which is
    a cosmetic loss, whereas refusing the whole attachment over it would lose the file id the
    model actually needs.
    """
    url = str(value or "").strip()
    if not url:
        return ""
    try:
        parsed = urlparse(url)
    except Exception:
        return ""
    if parsed.scheme != "https" or parsed.username or parsed.password:
        return ""
    host = (parsed.hostname or "").lower()
    return url[:500] if host in _DRIVE_HOSTS else ""


def _safe_download_name(file_name) -> str:
    """A filename for the Content-Disposition header, with path separators taken out."""
    cleaned = (
        str(file_name or "")
        .replace("\\", "_")
        .replace("/", "_")
        .replace("\r", "")
        .replace("\n", "")
    )
    return _clip(cleaned.strip(), 200) or "attachment"


def _file_bytes(file_docname: str) -> bytes:
    """Read a File's bytes. Returns b"" rather than raising -- the caller answers 403."""
    try:
        return frappe.get_doc("File", file_docname).get_content() or b""
    except Exception:
        return b""
