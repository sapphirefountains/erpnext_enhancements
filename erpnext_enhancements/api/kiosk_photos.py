"""The technician's own job photos, for the Time Kiosk's Photos tab.

Answers one question for the person signed in to ``/kiosk``: "which photos have
I taken on my jobs?" Nik asked for it on 2026-10-27 ("an easy way for
technicians to see the photos they've uploaded through the Time Kiosk") and
picked the shape: a Photos tab in the kiosk, the last 7 days by default, grouped
by day and job, own photos only, view only.

## Where a kiosk photo actually lives

Two buttons on the Clock tab put a picture on a job, and they leave different
traces (``public/js/kiosk/app.js``):

1. **Take photo** registers a ``Job Interval Photo`` row the instant the shutter
   fires (``upload_status = Pending``), uploads the bytes as a **private** File
   attached to the Job Interval, then flips the row to ``Uploaded`` with
   ``file_doc`` pointing at that File (``time_kiosk.record_job_photo``).
2. **Add files** uploads a File attached to the Job Interval and copies it onto
   the Project and Task (``time_kiosk.link_attachment``). For an image it also
   registers a photo row, but it passes the File's *file name* where
   ``record_job_photo`` wants its *docname*, so the row never finds the File and
   stays ``Pending`` for good. On prod (2026-10-27) every such row is in that
   state. Fixing that is its own change; this module only has to not be fooled
   by it.

So the thing a technician can look at is **an image File attached to one of
their Job Intervals**, whichever button made it, and that is what is listed. The
photo row adds what the File does not know (when the shutter fired, a caption)
when there is one.

## "Didn't upload"

A photo row that is still ``Pending``/``Failed`` with no File behind it is a
picture the phone took but the office never received. The bytes cannot be
replayed after the kiosk reloads (a queued ``File`` object does not survive in
``localStorage``), so the group says so rather than pretending. The "Add files"
rows above would be counted here as well, so an image File on the same job
that no photo row points at is taken to be the photo one of those rows meant:
``waiting = unsettled rows - unlinked image Files``, never below zero. That is
exact for every row on prod and stays right once the button is fixed (it then
leaves no unlinked Files behind).

## Security

- **The employee comes from the session**, the same rule as every
  ``time_kiosk`` endpoint (``Employee.user_id`` of ``frappe.session.user``).
  There is no ``employee`` parameter to tamper with, and a user with no
  Employee gets a permission error, not someone else's photos.
- **Each person signs in to the kiosk with their own account** (``www/kiosk.py``
  redirects guests to the login page), so a phone handed to the next person
  shows whoever is signed in on it.
- **Full-size photos go through Frappe's own file route.** A private File's URL
  carries ``?fid=<File name>``, which ``download_private_file`` checks with
  ``File.has_permission`` (owner, or read on the Job Interval it is attached
  to). Nothing here hands out bytes it has not checked.
- **Thumbnails are this module's own bytes**, so ``photo_thumbnail`` checks the
  File is an image attached to a Job Interval of the session employee before
  it opens it, and answers a File that is missing and one that is somebody
  else's with the same 404, so a guessed name learns nothing. Frappe's
  ``File.make_thumbnail`` is deliberately not used: for a private file it writes
  the thumbnail under ``public/``, where anyone can read it without signing in.
- **Decoding is bounded before it starts** (``make_thumbnail``,
  ``decode_problem``): an allowlist of formats, so bytes that are really EPS
  never reach Ghostscript whatever the file is called, and size limits read from
  the header, so a small file cannot decode to an image that takes a worker's
  memory. The limits and what each one cost before it existed are with the
  ``MAX_*`` constants below.
- **Not under ``api.time_kiosk.``** on purpose. ``www/kiosk-sw.js`` answers every
  GET under that prefix network-first and keeps the last response for offline,
  keyed by URL rather than by person. A shared phone that goes offline would
  then show the previous person's photo list to the next, and every thumbnail's
  bytes would pile up in the worker's cache. Outside the prefix the worker
  leaves both alone. Thumbnails are cached by the browser instead, ``private``
  and ``Vary: Cookie``, under a URL that changes when the file's content does.
"""

import io
import os
import re
from datetime import timedelta
from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import cint, getdate, nowdate

#: The range chips on the Photos tab, as days back from today (0 = today only).
#: ``all`` has no lower bound. Nik's default (2026-10-27) is the last 7 days.
RANGES = {"today": 0, "7d": 6, "30d": 29, "all": None}
DEFAULT_RANGE = "7d"

#: Jobs per page. A job is never split across pages, so a page carries every
#: photo of the jobs on it; the thumbnails load lazily, so a long job costs
#: nothing until it is scrolled to.
PAGE_LENGTH = 10
MAX_PAGE_LENGTH = 30

#: Longest side of a thumbnail, in pixels. Three tiles across a 600 px column on
#: a 2x screen.
THUMB_PX = 360
THUMB_QUALITY = 72

#: What counts as a photo. The same list as ``countAttachedImageAsJobPhoto`` in
#: ``public/js/kiosk/app.js``: a PDF quote attached to a job is not a photo of it.
IMAGE_EXTENSIONS = ("jpg", "jpeg", "png", "heic", "heif", "webp", "gif", "bmp")
_IMAGE_RE = re.compile(r"\.(?:" + "|".join(IMAGE_EXTENSIONS) + r")$", re.IGNORECASE)
#: The same rule for MariaDB, applied to ``LOWER(file_url)``. Neither side trims, so
#: the list and the thumbnail endpoint can never disagree about a file.
_IMAGE_SQL_RE = r"\.(" + "|".join(IMAGE_EXTENSIONS) + r")$"

#: The only formats Pillow may open for a thumbnail. Pillow identifies a file by
#: its bytes, never its name, so without this an EPS saved as ``.jpg`` opens as
#: EPS and runs Ghostscript when it is loaded. HEIC is absent on purpose: Pillow
#: cannot read it without a plugin, so it gets the "No preview" tile either way.
#: So is WebP: Pillow builds libwebp's animation decoder, whole RGBA canvases and
#: all, inside ``Image.open`` itself, still image or not, so a 41 KB 16 MP WebP
#: took 119 MB before any size check could run. No camera writes WebP; one added
#: through "Add files" gets the plain tile and still opens full size.
#: There is no "MPO" opener to list: the JPEG plugin opens a multi-picture JPEG
#: and reports its format as MPO.
THUMB_FORMATS = ("JPEG", "PNG", "GIF", "BMP")
#: Formats ``Image.draft`` can shrink while decoding (MPO is a JPEG container).
DRAFT_FORMATS = ("JPEG", "MPO")

# Decoding limits. All of them are read from the header (and, for a JPEG, from the
# size ``draft`` will decode at) before a single pixel is decoded, because a small
# file can decode to a very large image. The figures are the extra peak memory of
# one thumbnail, measured with Pillow 12.3.0 on 2026-10-27; a phone camera's JPEG
# costs about 10 MB. A few requests run at once, one per worker, and ``v`` is not
# read, so a cache-busting URL makes every request decode again.

#: A JPEG's header size. Covers any phone camera's normal output (a 50 MP sensor
#: is 8160x6120), which ``draft`` then decodes at 1/8 a side.
MAX_JPEG_PIXELS = 60_000_000
#: A progressive JPEG keeps a coefficient buffer for the WHOLE image, about 3.4
#: bytes a pixel, whatever ``draft`` asks for: a 1.2 MB 12000x12000 one took
#: 425 MB. Phone cameras write baseline JPEGs; 16 MP is about 55 MB.
MAX_PROGRESSIVE_PIXELS = 16_000_000
#: What may actually be decoded: the size after ``draft`` for a JPEG, the header
#: size for everything else, which decodes at full size. ``draft`` shrinks by the
#: SHORTER side, so a 65500x1439 strip (1.4 MB) gets no reduction and took 723 MB.
#: 16 MP covers a 5K screenshot (14.7 MP, 60 MB as an RGB PNG) and a phone
#: panorama after ``draft``. The dearest image it lets through is a 16 MP RGBA
#: PNG, 130 MB, because resizing RGBA makes a premultiplied full-size copy.
MAX_DECODE_PIXELS = 16_000_000

#: Photo rows whose bytes the office has not got.
UNSETTLED_STATES = ("Pending", "Failed")

THUMBNAIL_METHOD = "/api/method/erpnext_enhancements.api.kiosk_photos.photo_thumbnail"


# ---------------------------------------------------------------------------
# Pure helpers (no database; tests/test_kiosk_photos.py runs them bench-free)
# ---------------------------------------------------------------------------


def is_image(file_url):
    """True when the File's URL ends in an image extension. Exactly the rule the
    SQL applies (``LOWER(file_url) REGEXP``), untrimmed like it."""
    return bool(file_url) and bool(_IMAGE_RE.search(str(file_url)))


def range_bounds(period=None, date=None, today=None):
    """``(key, from_date, to_date)`` for the Photos tab's filter.

    A specific ``date`` wins and gives that one day (key ``"date"``). Otherwise
    ``period`` is one of ``RANGES``; anything else falls back to the default
    rather than failing, because the chip a stale cached page sends should still
    show something. ``all`` returns ``None`` bounds. Dates are site dates.
    """
    today = getdate(today or nowdate())
    if date:
        day = getdate(date)
        return "date", day, day
    key = period if period in RANGES else DEFAULT_RANGE
    days = RANGES[key]
    if days is None:
        return key, None, None
    return key, today - timedelta(days=days), today


def page_args(start=None, page_length=None):
    """``(start, page_length)`` clamped to something safe to put in a LIMIT."""
    start = max(cint(start), 0)
    length = cint(page_length) or PAGE_LENGTH
    return start, min(max(length, 1), MAX_PAGE_LENGTH)


def full_url(file_row):
    """The URL the viewer opens. A private File carries ``?fid=`` so Frappe checks
    permission on exactly this File row rather than the first one sharing its URL."""
    url = quote(str(file_row.get("file_url") or ""), safe="/")
    if cint(file_row.get("is_private")):
        return f"{url}?fid={quote(str(file_row.get('name')), safe='')}"
    return url


def thumb_url(file_row):
    """The thumbnail URL. ``v`` changes with the bytes, so the browser may keep it."""
    version = str(file_row.get("content_hash") or file_row.get("modified") or "")[:12]
    name = quote(str(file_row.get("name")), safe="")
    return f"{THUMBNAIL_METHOD}?photo={name}&v={quote(version, safe='')}"


def build_groups(intervals, files, photo_rows, project_titles=None, task_titles=None):
    """One group per Job Interval, in the order given (newest job first).

    ``intervals`` are dicts with ``name, project, task, start_time, end_time,
    status``; ``files`` are Files attached to those intervals (any type; only
    images are kept); ``photo_rows`` are their ``Job Interval Photo`` rows.
    A group with neither a photo nor a waiting one is dropped.
    """
    project_titles = project_titles or {}
    task_titles = task_titles or {}

    files_by_interval = {}
    for f in files:
        if cint(f.get("is_folder")) or not is_image(f.get("file_url")):
            continue
        files_by_interval.setdefault(f.get("attached_to_name"), []).append(f)

    rows_by_interval = {}
    for r in photo_rows:
        rows_by_interval.setdefault(r.get("parent"), []).append(r)

    groups = []
    for interval in intervals:
        name = interval.get("name")
        images = files_by_interval.get(name, [])
        rows = rows_by_interval.get(name, [])
        row_by_file = {r.get("file_doc"): r for r in rows if r.get("file_doc")}

        photos = []
        for f in images:
            row = row_by_file.get(f.get("name")) or {}
            photos.append({
                "name": f.get("name"),
                "file_name": f.get("file_name") or "",
                "url": full_url(f),
                "thumb": thumb_url(f),
                "captured_on": str(row.get("captured_on") or f.get("creation") or ""),
                "caption": row.get("caption") or "",
            })
        photos.sort(key=lambda p: (p["captured_on"], p["name"]))

        unsettled = sum(
            1 for r in rows if not r.get("file_doc") and r.get("upload_status") in UNSETTLED_STATES
        )
        unlinked = sum(1 for f in images if f.get("name") not in row_by_file)
        waiting = max(unsettled - unlinked, 0)

        if not photos and not waiting:
            continue

        start = interval.get("start_time")
        project = interval.get("project")
        task = interval.get("task")
        groups.append({
            "job_interval": name,
            "day": str(getdate(start)) if start else "",
            "start_time": str(start or ""),
            "end_time": str(interval.get("end_time") or ""),
            "status": interval.get("status") or "",
            "project": project or "",
            "project_title": project_titles.get(project) or project or "",
            "task": task or "",
            "task_title": task_titles.get(task) or task or "",
            "photos": photos,
            "waiting": waiting,
        })
    return groups


def decode_problem(fmt, size, drafted, progressive=False):
    """Why an image may not be decoded for a thumbnail, or None.

    ``size`` is the header's, ``drafted`` the size it will actually decode at (the
    same, unless ``draft`` shrank a JPEG). Pure, so every limit is tested without
    building the huge files the limits exist for.
    """
    width, height = size
    pixels = width * height
    if fmt in DRAFT_FORMATS:
        if pixels > MAX_JPEG_PIXELS:
            return f"{width}x{height} JPEG is over the {MAX_JPEG_PIXELS:,}-pixel cap"
        if progressive and pixels > MAX_PROGRESSIVE_PIXELS:
            return f"{width}x{height} progressive JPEG is over the {MAX_PROGRESSIVE_PIXELS:,}-pixel cap"
    if drafted[0] * drafted[1] > MAX_DECODE_PIXELS:
        return f"{width}x{height} {fmt} would decode at {drafted[0]}x{drafted[1]}, over the {MAX_DECODE_PIXELS:,}-pixel cap"
    return None


def make_thumbnail(raw, size=THUMB_PX, quality=THUMB_QUALITY):
    """JPEG bytes of ``raw`` shrunk to ``size`` on its longest side, upright.

    ``Image.open`` reads only the header, so every limit is checked before a
    single pixel is decoded: the format must be in ``THUMB_FORMATS``, and
    ``decode_problem`` must find nothing wrong with its size.

    ``draft`` lets the JPEG decoder skip most of a 12-megapixel image instead of
    decoding all of it to throw it away; for any other format it does nothing.
    Phones store a portrait photo as a landscape image plus an EXIF orientation
    tag; ``exif_transpose`` applies the tag, or every thumbnail lies on its side.
    Raises on anything refused or unreadable (HEIC without a plugin, a truncated
    upload); the caller turns that into a 404.
    """
    from PIL import Image, ImageOps

    image = Image.open(io.BytesIO(raw), formats=THUMB_FORMATS)
    header = image.size
    image.draft("RGB", (size * 2, size * 2))
    problem = decode_problem(image.format, header, image.size, bool(image.info.get("progressive")))
    if problem:
        raise ValueError(problem)
    # Shrink first, then turn upright. exif_transpose returns a copy, and of the
    # full-size image that copy was the largest allocation of all; thumbnail()
    # keeps the EXIF block, so the orientation is still there to apply. Only the
    # first frame of an animated GIF is ever decoded: nothing here seeks.
    image.thumbnail((size, size))
    image = ImageOps.exif_transpose(image)
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=quality, optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


def _session_employee():
    """Employee linked to the session user, or None. The same rule as
    ``time_kiosk._session_employee``; repeated rather than imported so this
    module does not pull the whole clock in with it."""
    return frappe.db.get_value("Employee", {"user_id": frappe.session.user}, "name")


def _require_session_employee():
    employee = _session_employee()
    if not employee:
        frappe.throw(_("No Employee record found for this user."), frappe.PermissionError)
    return employee


def _intervals_with_photos(employee, from_date, to_date, start, length):
    """A page of the employee's Job Intervals that have an image File or a photo
    still waiting, newest first. One more than ``length`` is asked for, so the
    caller can tell whether there is another page without counting them all."""
    conditions = ["ji.employee = %(employee)s"]
    values = {
        "employee": employee,
        "image_re": _IMAGE_SQL_RE,
        "limit": length + 1,
        "offset": start,
    }
    if from_date:
        conditions.append("ji.start_time >= %(from_dt)s")
        values["from_dt"] = f"{from_date} 00:00:00"
    if to_date:
        conditions.append("ji.start_time <= %(to_dt)s")
        values["to_dt"] = f"{to_date} 23:59:59.999999"
    return frappe.db.sql(
        f"""
        SELECT ji.name, ji.project, ji.task, ji.start_time, ji.end_time, ji.status
        FROM `tabJob Interval` ji
        WHERE {" AND ".join(conditions)}
          AND (
            EXISTS (
              SELECT 1 FROM `tabFile` f
              WHERE f.attached_to_doctype = 'Job Interval'
                AND f.attached_to_name = ji.name
                AND f.is_folder = 0
                AND LOWER(f.file_url) REGEXP %(image_re)s
            )
            OR EXISTS (
              SELECT 1 FROM `tabJob Interval Photo` p
              WHERE p.parent = ji.name
                AND p.parenttype = 'Job Interval'
                AND IFNULL(p.file_doc, '') = ''
                AND p.upload_status IN ('Pending', 'Failed')
            )
          )
        ORDER BY ji.start_time DESC, ji.name DESC
        LIMIT %(limit)s OFFSET %(offset)s
        """,
        values,
        as_dict=True,
    )


def _titles(doctype, names, field):
    names = sorted({n for n in names if n})
    if not names:
        return {}
    return dict(frappe.get_all(doctype, filters={"name": ["in", names]}, fields=["name", field], as_list=True))


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@frappe.whitelist(methods=["GET"])
def get_my_photos(period=None, date=None, start=0, page_length=None):
    """A page of the session employee's job photos, grouped by job, newest first.

    ``period`` is ``today`` / ``7d`` (default) / ``30d`` / ``all``; ``date``
    (YYYY-MM-DD) picks one day instead. ``start`` and ``page_length`` page by
    **job**, 10 at a time; ``next_start`` is where the next page begins and
    ``has_more`` says whether there is one.
    """
    employee = _require_session_employee()
    key, from_date, to_date = range_bounds(period, date)
    start, length = page_args(start, page_length)

    intervals = _intervals_with_photos(employee, from_date, to_date, start, length)
    has_more = len(intervals) > length
    intervals = intervals[:length]
    names = [i.name for i in intervals]

    files, rows = [], []
    if names:
        files = frappe.get_all(
            "File",
            filters={"attached_to_doctype": "Job Interval", "attached_to_name": ["in", names], "is_folder": 0},
            fields=["name", "file_name", "file_url", "is_private", "is_folder", "attached_to_name",
                    "content_hash", "creation", "modified"],
            order_by="creation asc",
        )
        rows = frappe.get_all(
            "Job Interval Photo",
            filters={"parent": ["in", names], "parenttype": "Job Interval"},
            fields=["parent", "file_doc", "captured_on", "caption", "upload_status"],
        )

    groups = build_groups(
        intervals,
        files,
        rows,
        project_titles=_titles("Project", [i.project for i in intervals], "project_name"),
        task_titles=_titles("Task", [i.task for i in intervals], "subject"),
    )
    return {
        "period": key,
        "from_date": str(from_date) if from_date else None,
        "to_date": str(to_date) if to_date else None,
        "groups": groups,
        "start": start,
        "next_start": start + len(intervals),
        "has_more": has_more,
    }


@frappe.whitelist(methods=["GET"])
def photo_thumbnail(photo=None, v=None):
    """A small JPEG of one of the session employee's job photos.

    ``v`` is only a cache key (the start of the File's content hash) and is not
    read. Anything that is not an image on the caller's own Job Interval is
    refused before the file is opened, and always with the same 404: a File that
    does not exist and one on somebody else's job must look alike, or the error
    itself confirms that a guessed name is real.
    """
    employee = _require_session_employee()

    row = None
    if photo:
        row = frappe.db.get_value(
            "File",
            photo,
            ["name", "file_url", "is_folder", "attached_to_doctype", "attached_to_name"],
            as_dict=True,
        )
    owner = None
    if row and row.attached_to_doctype == "Job Interval" and row.attached_to_name:
        owner = frappe.db.get_value("Job Interval", row.attached_to_name, "employee")
    if not row or owner != employee or cint(row.is_folder) or not is_image(row.file_url):
        frappe.throw(_("That photo does not exist."), frappe.DoesNotExistError)

    # Read the bytes directly. File.get_content() tries to decode them as text
    # first, and a JPEG can happen to be valid windows-1252.
    path = frappe.get_doc("File", row.name).get_full_path()
    if not path or not os.path.isfile(path):
        frappe.throw(_("That photo's file is missing."), frappe.DoesNotExistError)
    with open(path, "rb") as handle:
        raw = handle.read()
    try:
        thumb = make_thumbnail(raw)
    except Exception:
        # HEIC without a plugin, a format or size make_thumbnail refuses, or a
        # truncated upload: no preview. The kiosk draws a plain tile, and the
        # photo still opens full size through Frappe's own permission check.
        frappe.throw(_("No preview for this photo."), frappe.DoesNotExistError)

    frappe.local.response.filename = f"{row.name}.jpg"
    frappe.local.response.filecontent = thumb
    frappe.local.response.content_type = "image/jpeg"
    frappe.local.response.display_content_as = "inline"
    frappe.local.response.type = "download"
    # The URL changes with the content (``v``), so the phone may keep this. Private:
    # never a shared proxy cache. Vary on the cookie: a phone handed to someone else
    # does not answer their requests from the first person's cache.
    frappe.local.response_headers["Cache-Control"] = "private, max-age=2592000"
    frappe.local.response_headers["Vary"] = "Cookie"
