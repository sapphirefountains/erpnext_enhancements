"""Bench-free tests for the Time Kiosk's Photos tab (``api/kiosk_photos.py``, ``photos.js``).

Stubs a minimal ``frappe`` in ``setUpModule`` (execution time, not import time, so it never
fools a bench suite's ``import frappe`` guard) and drives the module's pure helpers and both
endpoints against it. What each class guards, in the terms it would fail in:

- **Grouping** (``build_groups``): the shapes actually on prod on 2026-10-27. An "Add files"
  picture has a File and a photo row stuck at Pending that never points at it; counting that
  row as "not uploaded" would tell a technician a photo is missing while it is on screen.
- **Whose photos** (``get_my_photos`` / ``photo_thumbnail``): the employee comes from the
  session and nowhere else, and a thumbnail of someone else's job, or of a non-job File, is
  refused before the file is opened.
- **The thumbnail itself**: upright (EXIF applied) and small. Needs Pillow; CI installs it.
- **The front end**: every name ``photos.js`` dials is a GET-only whitelisted def here, and
  the module stays outside the prefix ``kiosk-sw.js`` caches per URL rather than per person.

Run: python -m unittest erpnext_enhancements.tests.test_kiosk_photos
"""

import ast
import datetime
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

    def sql(query, values=None, as_dict=False):
        STATE["calls"].append(("sql", query, values))
        return [_Dict(r) for r in STATE["intervals"]][: values["limit"]]

    def get_all(doctype, filters=None, fields=None, as_list=False, **kwargs):
        STATE["calls"].append(("get_all", doctype, filters))
        if doctype == "File":
            names = set(filters["attached_to_name"][1])
            return [_Dict(f) for f in STATE["files"].values() if f.get("attached_to_name") in names]
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
    frappe.db = types.SimpleNamespace(get_value=get_value, sql=sql)
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


def _reset(**kw):
    STATE.clear()
    STATE.update(
        employees={"tech@example.com": "EMP-1"},
        intervals=[],
        files={},
        rows=[],
        interval_owner={},
        paths={},
        calls=[],
        opened=[],
    )
    STATE.update(kw)
    frappe = sys.modules["frappe"]
    frappe.session.user = "tech@example.com"
    frappe.local.response = _Dict()
    frappe.local.response_headers = {}


def _interval(name, start="2026-10-27 13:24:06", project="PRJ-1", task=None):
    return {"name": name, "project": project, "task": task, "start_time": start,
            "end_time": "2026-10-27 15:00:00", "status": "Completed"}


def _file(name, interval, url=None, private=1, creation="2026-10-27 13:25:00"):
    return {"name": name, "file_name": (url or f"/private/files/{name}.jpg").rsplit("/", 1)[-1],
            "file_url": url or f"/private/files/{name}.jpg", "is_private": private, "is_folder": 0,
            "attached_to_name": interval, "attached_to_doctype": "Job Interval",
            "content_hash": "abcdef0123456789", "creation": creation, "modified": creation}


def _row(interval, status, file_doc=None, captured="2026-10-27 13:24:39", caption=None):
    return {"parent": interval, "upload_status": status, "file_doc": file_doc,
            "captured_on": captured, "caption": caption}


class TestHelpers(unittest.TestCase):
    def test_is_image(self):
        for url in ("/private/files/a.jpg", "/files/IMG_0588.JPEG", "/files/x.png", "/files/y.heic",
                    "/private/files/z.webp "):
            self.assertTrue(kp.is_image(url), url)
        for url in ("/files/quote.pdf", "/files/a.jpg.pdf", "", None, "/files/jpg"):
            self.assertFalse(kp.is_image(url), url)

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
        url = kp.full_url({"name": "172befe8e6", "file_url": "/private/files/image.jpg", "is_private": 1})
        self.assertEqual(url, "/private/files/image.jpg?fid=172befe8e6")

    def test_a_public_url_is_encoded_and_carries_no_fid(self):
        url = kp.full_url({"name": "x", "file_url": "/files/Screenshot 2026-09-18 125141.png", "is_private": 0})
        self.assertEqual(url, "/files/Screenshot%202026-09-18%20125141.png")

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
        """JOB-INT-00022 on prod: one camera photo, one "Add files" picture whose row is Pending."""
        groups = kp.build_groups(
            [_interval("JI-22")],
            [_file("F-cam", "JI-22"), _file("F-add", "JI-22", url="/files/IMG_0588.jpeg", private=0)],
            [_row("JI-22", "Uploaded", "F-cam"), _row("JI-22", "Pending", None)],
        )
        self.assertEqual([p["name"] for p in groups[0]["photos"]], ["F-cam", "F-add"])
        self.assertEqual(groups[0]["waiting"], 0)

    def test_photos_that_never_arrived_keep_the_job_on_screen(self):
        """JOB-INT-00019 on prod: two camera captures, no bytes."""
        groups = kp.build_groups(
            [_interval("JI-19")], [], [_row("JI-19", "Pending"), _row("JI-19", "Failed")]
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
            [_interval("JI-1", project="PRJ-9", task="TASK-1")], [_file("F1", "JI-1")], [],
            project_titles={"PRJ-9": "Main Fountain"},
        )
        self.assertEqual((groups[0]["project_title"], groups[0]["task_title"]), ("Main Fountain", "TASK-1"))


class TestGetMyPhotos(unittest.TestCase):
    def test_no_employee_is_a_permission_error(self):
        _reset(employees={})
        with self.assertRaises(StubPermissionError):
            kp.get_my_photos()

    def test_the_employee_is_the_session_users(self):
        _reset(intervals=[_interval("JI-1")], files={"F1": _file("F1", "JI-1")})
        out = kp.get_my_photos(period="all")
        sql = [c for c in STATE["calls"] if c[0] == "sql"][0]
        self.assertEqual(sql[2]["employee"], "EMP-1")
        self.assertNotIn("from_dt", sql[2])
        self.assertEqual([g["job_interval"] for g in out["groups"]], ["JI-1"])
        self.assertEqual((out["period"], out["has_more"], out["next_start"]), ("all", False, 1))

    def test_there_is_no_employee_parameter_to_tamper_with(self):
        import inspect

        for fn in (kp.get_my_photos, kp.photo_thumbnail):
            self.assertNotIn("employee", inspect.signature(fn).parameters)

    def test_the_seven_day_window_is_bound_not_formatted_in(self):
        _reset()
        kp.get_my_photos()
        _, query, values = [c for c in STATE["calls"] if c[0] == "sql"][0]
        self.assertEqual((values["from_dt"], values["to_dt"]),
                         ("2026-10-21 00:00:00", "2026-10-27 23:59:59.999999"))
        self.assertNotIn("2026-10", query)
        self.assertIn("ji.employee = %(employee)s", query)

    def test_one_extra_job_is_asked_for_to_know_there_is_another_page(self):
        intervals = [_interval(f"JI-{i}") for i in range(4)]
        files = {f"F{i}": _file(f"F{i}", f"JI-{i}") for i in range(4)}
        _reset(intervals=intervals, files=files)
        out = kp.get_my_photos(page_length=3)
        values = [c for c in STATE["calls"] if c[0] == "sql"][0][2]
        self.assertEqual(values["limit"], 4)
        self.assertEqual((len(out["groups"]), out["has_more"], out["next_start"]), (3, True, 3))

    def test_the_sql_filters_on_images_with_a_bound_pattern(self):
        _reset()
        kp.get_my_photos()
        _, query, values = [c for c in STATE["calls"] if c[0] == "sql"][0]
        self.assertIn("REGEXP %(image_re)s", query)
        pattern = re.compile(values["image_re"])
        self.assertTrue(pattern.search("/private/files/image.jpg"))
        self.assertFalse(pattern.search("/files/quote.pdf"))


@unittest.skipUnless(__import__("importlib").util.find_spec("PIL"), "Pillow is not installed")
class TestThumbnail(unittest.TestCase):
    def setUp(self):
        from PIL import Image

        # A landscape sensor image tagged "rotate 90 CW", as a phone stores a portrait shot.
        image = Image.new("RGB", (800, 400), (200, 30, 30))
        exif = image.getexif()
        exif[0x0112] = 6
        buf = io.BytesIO()
        image.save(buf, format="JPEG", exif=exif)
        self.raw = buf.getvalue()
        handle = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        handle.write(self.raw)
        handle.close()
        self.path = handle.name

    def tearDown(self):
        os.unlink(self.path)

    def _own(self, **files):
        _reset(
            files={"F1": _file("F1", "JI-1"), **files},
            interval_owner={"JI-1": "EMP-1", "JI-9": "EMP-2"},
            paths={"F1": self.path},
        )

    def test_upright_and_small(self):
        from PIL import Image

        out = Image.open(io.BytesIO(kp.make_thumbnail(self.raw)))
        self.assertEqual(out.size, (180, 360))
        self.assertEqual(out.format, "JPEG")

    def test_own_photo_is_served_inline_and_cached_privately(self):
        self._own()
        kp.photo_thumbnail(photo="F1", v="abc")
        frappe = sys.modules["frappe"]
        self.assertEqual(frappe.local.response.type, "download")
        self.assertEqual(frappe.local.response.display_content_as, "inline")
        self.assertEqual(frappe.local.response.content_type, "image/jpeg")
        self.assertTrue(frappe.local.response.filecontent.startswith(b"\xff\xd8"))
        self.assertIn("private", frappe.local.response_headers["Cache-Control"])
        self.assertEqual(frappe.local.response_headers["Vary"], "Cookie")

    def test_someone_elses_photo_is_refused_before_it_is_opened(self):
        self._own(F9=_file("F9", "JI-9"))
        with self.assertRaises(StubPermissionError):
            kp.photo_thumbnail(photo="F9")
        self.assertEqual(STATE["opened"], [])

    def test_a_file_not_on_a_job_is_refused(self):
        other = _file("F7", "CUST-1")
        other["attached_to_doctype"] = "Customer"
        self._own(F7=other)
        with self.assertRaises(StubPermissionError):
            kp.photo_thumbnail(photo="F7")
        self.assertEqual(STATE["opened"], [])

    def test_a_pdf_on_your_own_job_is_not_a_photo(self):
        self._own(F2=_file("F2", "JI-1", url="/private/files/quote.pdf"))
        with self.assertRaises(StubDoesNotExistError):
            kp.photo_thumbnail(photo="F2")
        self.assertEqual(STATE["opened"], [])

    def test_unreadable_bytes_are_a_404_not_a_500(self):
        with open(self.path, "wb") as handle:
            handle.write(b"not an image")
        self._own()
        with self.assertRaises(StubDoesNotExistError):
            kp.photo_thumbnail(photo="F1")

    def test_no_employee_is_refused(self):
        self._own()
        STATE["employees"] = {}
        with self.assertRaises(StubPermissionError):
            kp.photo_thumbnail(photo="F1")


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


if __name__ == "__main__":
    unittest.main()
