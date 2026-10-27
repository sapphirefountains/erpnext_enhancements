"""Bench-free tests for the Time Kiosk's Photos tab (``api/kiosk_photos.py``, ``photos.js``).

Stubs a minimal ``frappe`` in ``setUpModule`` (execution time, not import time, so it never
fools a bench suite's ``import frappe`` guard) and drives the module's pure helpers and both
endpoints against it. Every File name, URL, Job Interval and Employee here is made up: the
repo is public. What each class guards, in the terms it would fail in:

- **Grouping** (``build_groups``): the shapes on prod on 2026-10-27. An "Add files" picture
  has a File and a photo row stuck at Pending that never points at it; counting that row as
  "not uploaded" would tell a technician a photo is missing while it is on screen.
- **Whose photos** (``get_my_photos``): the employee comes from the session and nowhere
  else. The stub's ``frappe.db.sql`` applies each condition of the real WHERE clause only
  when its text is in the query, so deleting the ownership condition, or either EXISTS,
  lets rows through (or keeps them out) and a test here fails.
- **The thumbnail** (``photo_thumbnail``, ``make_thumbnail``): only an image on the caller's
  own Job Interval, refused before the file is opened, and a missing File and somebody
  else's look the same. Decoding is bounded: a format allowlist (EPS bytes named .jpg) and a
  pixel cap on everything but JPEG. Upright (EXIF applied) and small. Needs Pillow; CI
  installs it.
- **The front end**: every name ``photos.js`` dials is a GET-only whitelisted def here, and
  the module stays outside the prefix ``kiosk-sw.js`` caches per URL rather than per person.

Run: python -m unittest erpnext_enhancements.tests.test_kiosk_photos
"""

import ast
import datetime
import importlib.util
import inspect
import io
import os
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
MODULE = APP / "api" / "kiosk_photos.py"
PHOTOS_JS = APP / "public" / "js" / "kiosk" / "photos.js"
WORKER = APP / "www" / "kiosk-sw.js"

TODAY = datetime.date(2026, 10, 27)
HAVE_PIL = importlib.util.find_spec("PIL") is not None

# What the stubbed database answers. Reset by each test through _reset().
STATE = {}
kp = None


class StubPermissionError(Exception):
    pass


class StubDoesNotExistError(Exception):
    pass


class StubValidationError(Exception):
    pass


class _Dict(dict):
    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError as exc:
            raise AttributeError(key) from exc


def _getdate(value=None):
    if value is None:
        return TODAY
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    return datetime.date.fromisoformat(str(value)[:10])


def _cint(value):
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


# The conditions of get_my_photos's WHERE clause, as the module writes them, connectives
# included (so "OR 0 AND EXISTS (…)" is not mistaken for the real thing). The stub applies
# each one only when its exact text is in the query, whitespace collapsed.
WHERE_EMPLOYEE = "WHERE ji.employee = %(employee)s"
WHERE_FROM = "AND ji.start_time >= %(from_dt)s"
WHERE_TO = "AND ji.start_time <= %(to_dt)s"
EXISTS_IMAGE = (
    "AND ( EXISTS ( SELECT 1 FROM `tabFile` f WHERE f.attached_to_doctype = 'Job Interval' "
    "AND f.attached_to_name = ji.name AND f.is_folder = 0 AND LOWER(f.file_url) REGEXP %(image_re)s )"
)
EXISTS_WAITING = (
    "OR EXISTS ( SELECT 1 FROM `tabJob Interval Photo` p WHERE p.parent = ji.name "
    "AND p.parenttype = 'Job Interval' AND IFNULL(p.file_doc, '') = '' "
    "AND p.upload_status IN ('Pending', 'Failed') ) )"
)


def _stub_sql(query, values=None, as_dict=False):
    """The intervals the real query would return, from the conditions actually in it."""
    STATE["calls"].append(("sql", query, values))
    q = " ".join(query.split())
    rows = list(STATE["intervals"])
    if WHERE_EMPLOYEE in q:
        rows = [r for r in rows if r["employee"] == values["employee"]]
    if WHERE_FROM in q:
        rows = [r for r in rows if r["start_time"] >= values["from_dt"]]
    if WHERE_TO in q:
        rows = [r for r in rows if r["start_time"] <= values["to_dt"]]

    def has_image(name):
        return any(
            f["attached_to_doctype"] == "Job Interval"
            and f["attached_to_name"] == name
            and not f["is_folder"]
            and re.search(values["image_re"], f["file_url"].lower())
            for f in STATE["files"].values()
        )

    def has_waiting(name):
        return any(
            r["parent"] == name and not r.get("file_doc") and r["upload_status"] in ("Pending", "Failed")
            for r in STATE["rows"]
        )

    image, waiting = EXISTS_IMAGE in q, EXISTS_WAITING in q
    rows = [r for r in rows if (image and has_image(r["name"])) or (waiting and has_waiting(r["name"]))]
    if "ORDER BY ji.start_time DESC, ji.name DESC" in q:
        rows.sort(key=lambda r: (r["start_time"], r["name"]), reverse=True)
    rows = rows[values["offset"] :][: values["limit"]]
    return [_Dict({k: v for k, v in r.items() if k != "employee"}) for r in rows]


def _install_frappe_stub():
    frappe = types.ModuleType("frappe")
    utils = types.ModuleType("frappe.utils")
    utils.cint = _cint
    utils.getdate = _getdate
    utils.nowdate = lambda: str(TODAY)

    def throw(msg, exc=None, title=None):
        raise (exc or StubValidationError)(msg)

    def get_value(doctype, filters, fieldname=None, as_dict=False, **kwargs):
        STATE["calls"].append(("get_value", doctype, filters))
        if doctype == "Employee":
            return STATE["employees"].get(filters.get("user_id"))
        if doctype == "File":
            row = STATE["files"].get(filters)
            return _Dict(row) if row else None
        if doctype == "Job Interval":
            return STATE["interval_owner"].get(filters)
        raise AssertionError(f"unexpected get_value on {doctype}")

    def get_all(doctype, filters=None, fields=None, as_list=False, **kwargs):
        STATE["calls"].append(("get_all", doctype, filters))
        if doctype == "File":
            names = set(filters["attached_to_name"][1])
            return [
                _Dict(f)
                for f in STATE["files"].values()
                if f["attached_to_doctype"] == filters["attached_to_doctype"] and f["attached_to_name"] in names
            ]
        if doctype == "Job Interval Photo":
            names = set(filters["parent"][1])
            return [_Dict(r) for r in STATE["rows"] if r["parent"] in names]
        if doctype in ("Project", "Task"):
            return [(n, f"{doctype} title {n}") for n in filters["name"][1]]
        raise AssertionError(f"unexpected get_all on {doctype}")

    def get_doc(doctype, name):
        assert doctype == "File"
        STATE["opened"].append(name)
        return types.SimpleNamespace(get_full_path=lambda: STATE["paths"].get(name))

    def whitelist(**kwargs):
        def wrap(fn):
            return fn

        return wrap

    frappe._ = lambda s: s
    frappe.throw = throw
    frappe.whitelist = whitelist
    frappe.PermissionError = StubPermissionError
    frappe.DoesNotExistError = StubDoesNotExistError
    frappe.ValidationError = StubValidationError
    frappe.session = types.SimpleNamespace(user="tech@example.com")
    frappe.form_dict = _Dict()
    frappe.db = types.SimpleNamespace(get_value=get_value, sql=_stub_sql)
    frappe.get_all = get_all
    frappe.get_doc = get_doc
    frappe.local = types.SimpleNamespace(response=_Dict(), response_headers={})
    frappe.utils = utils
    sys.modules["frappe"] = frappe
    sys.modules["frappe.utils"] = utils


def setUpModule():
    global kp
    _install_frappe_stub()
    sys.modules.pop("erpnext_enhancements.api.kiosk_photos", None)
    from erpnext_enhancements.api import kiosk_photos

    kp = kiosk_photos


def _reset(intervals=(), files=(), rows=(), **kw):
    STATE.clear()
    STATE.update(
        employees={"tech@example.com": "EMP-1", "other@example.com": "EMP-2"},
        intervals=list(intervals),
        files={f["name"]: f for f in files},
        rows=list(rows),
        interval_owner={i["name"]: i["employee"] for i in intervals},
        paths={},
        calls=[],
        opened=[],
    )
    STATE.update(kw)
    frappe = sys.modules["frappe"]
    frappe.session.user = "tech@example.com"
    frappe.form_dict = _Dict()
    frappe.local.response = _Dict()
    frappe.local.response_headers = {}


def _interval(name, start="2026-10-27 13:24:06", project="PRJ-90001", task=None, employee="EMP-1"):
    return {"name": name, "employee": employee, "project": project, "task": task, "start_time": start,
            "end_time": "2026-10-27 15:00:00", "status": "Completed"}


def _file(name, interval, url=None, private=1, creation="2026-10-27 13:25:00", doctype="Job Interval"):
    url = url or f"/private/files/{name}.jpg"
    return {"name": name, "file_name": url.rsplit("/", 1)[-1], "file_url": url, "is_private": private,
            "is_folder": 0, "attached_to_name": interval, "attached_to_doctype": doctype,
            "content_hash": "abcdef0123456789", "creation": creation, "modified": creation}


def _row(interval, status, file_doc=None, captured="2026-10-27 13:24:39", caption=None):
    return {"parent": interval, "upload_status": status, "file_doc": file_doc,
            "captured_on": captured, "caption": caption}


def _sql_call():
    """The one ``frappe.db.sql`` call ``get_my_photos`` made: ``("sql", query, values)``."""
    return next(c for c in STATE["calls"] if c[0] == "sql")


class TestHelpers(unittest.TestCase):
    def test_is_image(self):
        for url in ("/private/files/a.jpg", "/files/IMG_0001.JPEG", "/files/x.png", "/files/y.heic",
                    "/private/files/z.webp"):
            self.assertTrue(kp.is_image(url), url)
        for url in ("/files/quote.pdf", "/files/a.jpg.pdf", "", None, "/files/jpg", "/files/a.jpg "):
            self.assertFalse(kp.is_image(url), url)

    def test_the_python_rule_and_the_sql_rule_agree(self):
        """The list is chosen by the SQL and the thumbnail by Python: one file, one answer."""
        for url in ("/files/a.jpg", "/files/A.JPG", "/files/a.jpg ", "/files/a.jpg\t", "/files/a.jpg\n",
                    "/files/a.pdf", "/files/a.jpg.pdf", "/files/jpg", "/private/files/b.HEIC", " /files/c.png"):
            self.assertEqual(kp.is_image(url), bool(re.search(kp._IMAGE_SQL_RE, url.lower())), repr(url))

    def test_the_default_is_the_last_seven_days(self):
        self.assertEqual(kp.range_bounds(today=TODAY),
                         ("7d", datetime.date(2026, 10, 21), TODAY))

    def test_every_period(self):
        self.assertEqual(kp.range_bounds("today", today=TODAY), ("today", TODAY, TODAY))
        self.assertEqual(kp.range_bounds("30d", today=TODAY)[1], datetime.date(2026, 9, 28))
        self.assertEqual(kp.range_bounds("all", today=TODAY), ("all", None, None))

    def test_an_unknown_period_falls_back_rather_than_failing(self):
        self.assertEqual(kp.range_bounds("fortnight", today=TODAY)[0], "7d")

    def test_a_date_wins_and_is_one_day(self):
        day = datetime.date(2026, 9, 21)
        self.assertEqual(kp.range_bounds("all", date="2026-09-21", today=TODAY), ("date", day, day))

    def test_paging_is_clamped(self):
        self.assertEqual(kp.page_args(-5, None), (0, 10))
        self.assertEqual(kp.page_args("20", "0"), (20, 10))
        self.assertEqual(kp.page_args(0, 500), (0, 30))

    def test_a_private_photo_is_opened_through_its_own_file_row(self):
        url = kp.full_url({"name": "abc123def0", "file_url": "/private/files/IMG_0001.jpg", "is_private": 1})
        self.assertEqual(url, "/private/files/IMG_0001.jpg?fid=abc123def0")

    def test_a_public_url_is_encoded_and_carries_no_fid(self):
        url = kp.full_url({"name": "x", "file_url": "/files/Site Photo 0001.png", "is_private": 0})
        self.assertEqual(url, "/files/Site%20Photo%200001.png")

    def test_the_thumbnail_url_changes_with_the_content(self):
        a = kp.thumb_url({"name": "F1", "content_hash": "aaaaaaaaaaaaaaaa"})
        b = kp.thumb_url({"name": "F1", "content_hash": "bbbbbbbbbbbbbbbb"})
        self.assertNotEqual(a, b)
        self.assertTrue(a.startswith("/api/method/erpnext_enhancements.api.kiosk_photos.photo_thumbnail?photo=F1&v="))


class TestGrouping(unittest.TestCase):
    def test_a_camera_photo_takes_its_time_and_caption_from_its_row(self):
        groups = kp.build_groups(
            [_interval("JI-1")],
            [_file("F1", "JI-1")],
            [_row("JI-1", "Uploaded", "F1", captured="2026-10-27 13:24:39", caption="Pump vault")],
        )
        self.assertEqual(len(groups), 1)
        photo = groups[0]["photos"][0]
        self.assertEqual((photo["captured_on"], photo["caption"]), ("2026-10-27 13:24:39", "Pump vault"))
        self.assertEqual(groups[0]["waiting"], 0)
        self.assertEqual(groups[0]["day"], "2026-10-27")

    def test_an_add_files_picture_is_shown_and_its_stuck_row_is_not_missing(self):
        """A job as prod has them: one camera photo, one "Add files" picture whose row is Pending."""
        groups = kp.build_groups(
            [_interval("JI-2")],
            [_file("F-cam", "JI-2"), _file("F-add", "JI-2", url="/files/IMG_0001.jpeg", private=0)],
            [_row("JI-2", "Uploaded", "F-cam"), _row("JI-2", "Pending", None)],
        )
        self.assertEqual([p["name"] for p in groups[0]["photos"]], ["F-cam", "F-add"])
        self.assertEqual(groups[0]["waiting"], 0)

    def test_photos_that_never_arrived_keep_the_job_on_screen(self):
        """Also on prod: two camera captures whose bytes never reached the server."""
        groups = kp.build_groups(
            [_interval("JI-3")], [], [_row("JI-3", "Pending"), _row("JI-3", "Failed")]
        )
        self.assertEqual((groups[0]["photos"], groups[0]["waiting"]), ([], 2))

    def test_only_the_unexplained_rows_count(self):
        groups = kp.build_groups(
            [_interval("JI-1")],
            [_file("F1", "JI-1"), _file("F2", "JI-1")],
            [_row("JI-1", "Uploaded", "F1"), _row("JI-1", "Pending"), _row("JI-1", "Pending")],
        )
        self.assertEqual(groups[0]["waiting"], 1)

    def test_a_job_with_only_a_pdf_is_dropped(self):
        groups = kp.build_groups([_interval("JI-1")], [_file("F1", "JI-1", url="/files/quote.pdf")], [])
        self.assertEqual(groups, [])

    def test_an_uploaded_row_is_never_waiting(self):
        groups = kp.build_groups([_interval("JI-1")], [], [_row("JI-1", "Uploaded", "F-gone")])
        self.assertEqual(groups, [])

    def test_jobs_keep_their_order_and_photos_go_in_shutter_order(self):
        groups = kp.build_groups(
            [_interval("JI-2", start="2026-10-27 13:00:00"), _interval("JI-1", start="2026-10-26 08:00:00")],
            [_file("F-late", "JI-2", creation="2026-10-27 14:00:00"),
             _file("F-early", "JI-2", creation="2026-10-27 13:05:00"),
             _file("F-old", "JI-1")],
            [],
        )
        self.assertEqual([g["job_interval"] for g in groups], ["JI-2", "JI-1"])
        self.assertEqual([p["name"] for p in groups[0]["photos"]], ["F-early", "F-late"])

    def test_titles_fall_back_to_the_name(self):
        groups = kp.build_groups(
            [_interval("JI-1", project="PRJ-90009", task="TASK-90001")], [_file("F1", "JI-1")], [],
            project_titles={"PRJ-90009": "Main Fountain"},
        )
        self.assertEqual((groups[0]["project_title"], groups[0]["task_title"]), ("Main Fountain", "TASK-90001"))


class TestGetMyPhotos(unittest.TestCase):
    def test_the_stub_sees_the_whole_where_clause(self):
        """Anti-vacuity: if the module's query stops containing a condition the stub knows,
        the stub stops applying it, so it must still find every one of them today."""
        _reset()
        kp.get_my_photos()
        query = " ".join(_sql_call()[1].split())
        for clause in (WHERE_EMPLOYEE, WHERE_FROM, WHERE_TO, EXISTS_IMAGE, EXISTS_WAITING):
            self.assertIn(clause, query)

    def test_no_employee_is_a_permission_error(self):
        _reset(employees={})
        with self.assertRaises(StubPermissionError):
            kp.get_my_photos()

    def test_only_the_callers_jobs_are_listed(self):
        mine, theirs = _interval("JI-1"), _interval("JI-9", employee="EMP-2")
        _reset(intervals=[mine, theirs], files=[_file("F1", "JI-1"), _file("F9", "JI-9")])
        out = kp.get_my_photos(period="all")
        self.assertEqual([g["job_interval"] for g in out["groups"]], ["JI-1"])
        self.assertEqual(_sql_call()[2]["employee"], "EMP-1")

    def test_the_employee_never_comes_from_the_request(self):
        mine, theirs = _interval("JI-1"), _interval("JI-9", employee="EMP-2")
        _reset(intervals=[mine, theirs], files=[_file("F1", "JI-1"), _file("F9", "JI-9")])
        sys.modules["frappe"].form_dict = _Dict(employee="EMP-2")
        out = kp.get_my_photos(period="all")
        self.assertEqual([g["job_interval"] for g in out["groups"]], ["JI-1"])
        # And a user with no Employee of their own gets nobody's, whatever the request says.
        STATE["employees"] = {}
        with self.assertRaises(StubPermissionError):
            kp.get_my_photos(period="all")

    def test_the_module_reads_nothing_from_the_request(self):
        """No ``form_dict``, no ``request``, and no ``**kwargs`` that could carry an employee:
        the whitelisted signatures are the whole of what a caller can send."""
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        touched = sorted({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)
                          and n.attr in ("form_dict", "request", "form")})
        self.assertEqual(touched, [])
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                self.assertIsNone(node.args.kwarg, f"{node.name} takes **kwargs")
        for fn in (kp.get_my_photos, kp.photo_thumbnail):
            self.assertNotIn("employee", inspect.signature(fn).parameters)

    def test_a_job_whose_photos_never_arrived_is_listed(self):
        _reset(intervals=[_interval("JI-3")], rows=[_row("JI-3", "Pending")])
        out = kp.get_my_photos(period="all")
        self.assertEqual([(g["job_interval"], g["waiting"]) for g in out["groups"]], [("JI-3", 1)])

    def test_a_job_without_a_photo_is_not_listed(self):
        _reset(
            intervals=[_interval("JI-1"), _interval("JI-2"), _interval("JI-3")],
            files=[_file("F1", "JI-1"), _file("F2", "JI-2", url="/files/quote.pdf")],
            rows=[_row("JI-3", "Uploaded", "F-gone")],
        )
        out = kp.get_my_photos(period="all")
        self.assertEqual(([g["job_interval"] for g in out["groups"]], out["next_start"]), (["JI-1"], 1))

    def test_the_seven_day_window_is_bound_not_formatted_in(self):
        old = _interval("JI-OLD", start="2026-10-01 09:00:00")
        _reset(intervals=[old, _interval("JI-1")], files=[_file("F0", "JI-OLD"), _file("F1", "JI-1")])
        out = kp.get_my_photos()
        _, query, values = _sql_call()
        self.assertEqual((values["from_dt"], values["to_dt"]),
                         ("2026-10-21 00:00:00", "2026-10-27 23:59:59.999999"))
        self.assertNotIn("2026-10", query)
        self.assertEqual([g["job_interval"] for g in out["groups"]], ["JI-1"])

    def test_one_extra_job_is_asked_for_to_know_there_is_another_page(self):
        intervals = [_interval(f"JI-{i}", start=f"2026-10-27 0{i}:00:00") for i in range(4)]
        _reset(intervals=intervals, files=[_file(f"F{i}", f"JI-{i}") for i in range(4)])
        out = kp.get_my_photos(page_length=3)
        self.assertEqual(_sql_call()[2]["limit"], 4)
        self.assertEqual([g["job_interval"] for g in out["groups"]], ["JI-3", "JI-2", "JI-1"])
        self.assertEqual((out["has_more"], out["next_start"]), (True, 3))
        STATE["calls"].clear()
        out = kp.get_my_photos(start=3, page_length=3)
        self.assertEqual(([g["job_interval"] for g in out["groups"]], out["has_more"]), (["JI-0"], False))

    def test_the_sql_filters_on_images_with_a_bound_pattern(self):
        _reset()
        kp.get_my_photos()
        _, query, values = _sql_call()
        self.assertIn("REGEXP %(image_re)s", query)
        pattern = re.compile(values["image_re"])
        self.assertTrue(pattern.search("/private/files/img_0001.jpg"))
        self.assertFalse(pattern.search("/files/quote.pdf"))


def _jpeg(size=(800, 400), orientation=None):
    from PIL import Image

    image = Image.new("RGB", size, (200, 30, 30))
    buf = io.BytesIO()
    if orientation:
        exif = image.getexif()
        exif[0x0112] = orientation
        image.save(buf, format="JPEG", exif=exif)
    else:
        image.save(buf, format="JPEG")
    return buf.getvalue()


def _png(size, mode="RGB"):
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, size).save(buf, format="PNG")
    return buf.getvalue()


@unittest.skipUnless(HAVE_PIL, "Pillow is not installed")
class TestMakeThumbnail(unittest.TestCase):
    def test_upright_and_small(self):
        """A landscape sensor image tagged "rotate 90 CW", as a phone stores a portrait shot."""
        from PIL import Image

        out = Image.open(io.BytesIO(kp.make_thumbnail(_jpeg(orientation=6))))
        self.assertEqual(out.size, (180, 360))
        self.assertEqual(out.format, "JPEG")

    def test_eps_bytes_named_like_a_photo_never_reach_ghostscript(self):
        """Pillow picks the decoder from the bytes. Without the allowlist these open as EPS
        (and load() runs Ghostscript); with it they are not recognised at all."""
        from PIL import UnidentifiedImageError

        eps = b"%!PS-Adobe-3.0 EPSF-3.0\n%%BoundingBox: 0 0 10 10\n%%EndComments\nshowpage\n%%EOF\n"
        with self.assertRaises(UnidentifiedImageError):
            kp.make_thumbnail(eps)

    def test_a_png_over_the_pixel_cap_is_refused_before_it_is_decoded(self):
        # 42 MP, a few hundred KB on disk; mode "1" keeps building it cheap here.
        raw = _png((7000, 6000), mode="1")
        with self.assertRaisesRegex(ValueError, "pixel cap"):
            kp.make_thumbnail(raw)

    def test_the_cap_spares_jpeg_which_draft_shrinks_while_decoding(self):
        saved = kp.MAX_PIXELS
        kp.MAX_PIXELS = 100
        try:
            self.assertTrue(kp.make_thumbnail(_jpeg((20, 20))).startswith(b"\xff\xd8"))
            with self.assertRaisesRegex(ValueError, "pixel cap"):
                kp.make_thumbnail(_png((20, 20)))
        finally:
            kp.MAX_PIXELS = saved


@unittest.skipUnless(HAVE_PIL, "Pillow is not installed")
class TestPhotoThumbnail(unittest.TestCase):
    def setUp(self):
        handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        handle.write(_jpeg(orientation=6))
        handle.close()
        self.path = handle.name

    def tearDown(self):
        os.unlink(self.path)

    def _setup(self, *extra_files):
        _reset(
            intervals=[_interval("JI-1"), _interval("JI-9", employee="EMP-2")],
            files=[_file("F1", "JI-1"), *extra_files],
            paths={"F1": self.path},
        )

    def _refused(self, photo):
        with self.assertRaises(StubDoesNotExistError) as ctx:
            kp.photo_thumbnail(photo=photo)
        self.assertEqual(STATE["opened"], [], "refused, but only after opening the file")
        return str(ctx.exception)

    def test_own_photo_is_served_inline_and_cached_privately(self):
        self._setup()
        kp.photo_thumbnail(photo="F1", v="abc")
        frappe = sys.modules["frappe"]
        self.assertEqual(frappe.local.response.type, "download")
        self.assertEqual(frappe.local.response.display_content_as, "inline")
        self.assertEqual(frappe.local.response.content_type, "image/jpeg")
        self.assertTrue(frappe.local.response.filecontent.startswith(b"\xff\xd8"))
        self.assertIn("private", frappe.local.response_headers["Cache-Control"])
        self.assertEqual(frappe.local.response_headers["Vary"], "Cookie")

    def test_missing_and_someone_elses_look_the_same(self):
        self._setup(_file("F9", "JI-9"))
        theirs = self._refused("F9")
        missing = self._refused("no-such-file")
        nothing = self._refused(None)
        self.assertEqual(theirs, missing)
        self.assertEqual(theirs, nothing)

    def test_a_file_on_another_doctype_is_refused_even_under_an_own_job_name(self):
        """Attached to a Project that happens to share a name with one of the caller's Job
        Intervals: only the doctype check tells them apart."""
        self._setup(_file("F7", "JI-1", doctype="Project"))
        self._refused("F7")

    def test_a_pdf_on_your_own_job_is_not_a_photo(self):
        self._setup(_file("F2", "JI-1", url="/private/files/quote.pdf"))
        self._refused("F2")

    def test_unreadable_bytes_are_a_404_not_a_500(self):
        with open(self.path, "wb") as handle:
            handle.write(b"not an image")
        self._setup()
        with self.assertRaises(StubDoesNotExistError):
            kp.photo_thumbnail(photo="F1")

    def test_no_employee_is_refused(self):
        self._setup()
        STATE["employees"] = {}
        with self.assertRaises(StubPermissionError):
            kp.photo_thumbnail(photo="F1")
        self.assertEqual(STATE["opened"], [])


def _whitelisted():
    """name -> the whitelist decorator's keywords, for every whitelisted def in the module."""
    out = {}
    for node in ast.parse(MODULE.read_text(encoding="utf-8")).body:
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            target = dec.func if isinstance(dec, ast.Call) else dec
            if isinstance(target, ast.Attribute) and target.attr == "whitelist":
                kwargs = {k.arg: ast.literal_eval(k.value) for k in getattr(dec, "keywords", [])}
                out[node.name] = kwargs
    return out


def _strip_js_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return "\n".join(line for line in src.splitlines() if not line.strip().startswith("//"))


class TestTheFrontEndContract(unittest.TestCase):
    def test_every_name_photos_js_dials_is_a_get_only_whitelisted_def(self):
        code = _strip_js_comments(PHOTOS_JS.read_text(encoding="utf-8"))
        dialled = set(re.findall(r"PHOTOS_API \+ '(\w+)'", code))
        self.assertEqual(dialled, {"get_my_photos"}, "anti-vacuity: the view dials its list endpoint")
        allowed = _whitelisted()
        self.assertEqual(set(allowed), {"get_my_photos", "photo_thumbnail"})
        for name, kwargs in allowed.items():
            self.assertEqual(kwargs.get("methods"), ["GET"], f"{name} reads only, so it answers GET only")
        self.assertIn("var PHOTOS_API = 'erpnext_enhancements.api.kiosk_photos.';", code)

    def test_the_thumbnail_url_names_a_real_endpoint(self):
        self.assertTrue(kp.THUMBNAIL_METHOD.endswith("/erpnext_enhancements.api.kiosk_photos.photo_thumbnail"))

    def test_the_service_worker_does_not_answer_for_these_endpoints(self):
        """kiosk-sw.js keeps the last response of every GET under api.time_kiosk. for offline,
        keyed by URL. A photo list there would be shown to the next person on a shared phone."""
        worker = _strip_js_comments(WORKER.read_text(encoding="utf-8"))
        prefixes = re.findall(r"startsWith\('(/api/method/[^']+)'\)", worker)
        self.assertEqual(prefixes, ["/api/method/erpnext_enhancements.api.time_kiosk."])
        for prefix in prefixes:
            self.assertFalse(kp.THUMBNAIL_METHOD.startswith(prefix))
            self.assertFalse("/api/method/erpnext_enhancements.api.kiosk_photos.get_my_photos".startswith(prefix))
        self.assertNotIn("kiosk_photos", worker)

    def test_frappes_own_thumbnail_maker_is_never_used(self):
        """File.make_thumbnail writes a private file's thumbnail under public/."""
        code = MODULE.read_text(encoding="utf-8")
        self.assertNotRegex(code, r"\.make_thumbnail\(")

    def test_the_tab_is_mounted(self):
        app = (APP / "public" / "js" / "kiosk" / "app.js").read_text(encoding="utf-8")
        self.assertIn("{ id: 'photos', label: 'Photos' }", app)
        self.assertIn("id: 'tk-panel-photos'", app)
        self.assertIn("window.KioskViews.photos = { mount: mount, show: show, hide: hide };",
                      PHOTOS_JS.read_text(encoding="utf-8"))

    def test_a_waiting_photo_is_not_called_lost(self):
        """A capture still uploading on this phone is Pending too, so the card says "yet"."""
        code = _strip_js_comments(PHOTOS_JS.read_text(encoding="utf-8"))
        self.assertIn("' not uploaded yet'", code)
        self.assertNotIn("never reached", code)


if __name__ == "__main__":
    unittest.main()
