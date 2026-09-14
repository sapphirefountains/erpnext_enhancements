"""Uploading a video without putting it through the site.

An author with an MP4 on their laptop could not get it into a lesson at all: they
needed a Google account, a Drive upload, and a Drive-admin action most of them
cannot perform — sharing the file with the *service account*, a requirement that
appeared nowhere on screen and lived only in a runbook.

**The obvious build does not work**, which is what this module exists to keep true.
Frappe enforces a 25 MB ceiling twice (``get_max_file_size`` and again in
``File.check_max_file_size``) and streams the whole body through a gunicorn worker
synchronously, so pointing ``frappe.ui.FileUploader`` at a video — the way Image and
PDF already upload — fails on any real one and would take the site down for the
ones it accepted. So the bytes go browser → bucket and this app is never in the
data path. Every assertion below is about keeping it out of it, or about the two
things a browser is not trusted for.

Run: python -m unittest erpnext_enhancements.tests.test_training_video_upload
"""

import ast
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
UPLOAD_PY = APP / "training" / "video_upload.py"
GCS_PY = APP / "training" / "gcs_media.py"
CANVAS_JS = APP / "training" / "page" / "training_canvas" / "training_canvas.js"
CI = APP.parent / ".github" / "workflows" / "ci.yml"


def source(path):
    return path.read_text(encoding="utf-8")


def js_code(path):
    src = re.sub(r"/\*.*?\*/", "", source(path), flags=re.S)
    return chr(10).join(line for line in src.splitlines() if not line.strip().startswith("//"))


def py_code(path):
    """Python with docstrings and comments removed, for assertions about absence.

    The eighth instance of one shape in this repo, and the most inevitable version
    of it: this module's docstring explains at length why `get_max_file_size` is
    NOT used, so a test asserting that token is absent read the explanation and
    reported the thing still present. Docstrings are stripped through the AST
    rather than by regex, because a triple-quoted string is not reliably
    distinguishable from one by pattern alone.
    """
    tree = ast.parse(source(path))
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                body.pop(0)
    # ast.unparse drops comments entirely, which is the other half of what is wanted.
    return ast.unparse(tree)


def function_body(path, name):
    text = source(path)
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(f"{name} is not defined in {path.name}")


def signature(path, name):
    text = source(path)
    for node in ast.walk(ast.parse(text)):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return [a.arg for a in node.args.args]
    raise AssertionError(f"{name} is not defined in {path.name}")


class TestTheBytesNeverTouchTheSite(unittest.TestCase):
    def test_it_uses_no_frappe_file_machinery(self):
        """`upload_file`, the File doctype and `save_file` all put the body through
        a worker, which is the single thing this design exists to avoid."""
        text = py_code(UPLOAD_PY)
        for token in ("upload_file", "save_file", "new_doc('File'", "get_max_file_size"):
            with self.subTest(token):
                self.assertNotIn(token, text)

    def test_it_signs_a_url_instead(self):
        self.assertIn("gcs_media.generate_signed_url", source(UPLOAD_PY))

    def test_the_canvas_talks_to_storage_directly(self):
        code = js_code(CANVAS_JS)
        self.assertIn("gcs_session", code)
        self.assertIn("gcs_put", code)
        self.assertIn("XMLHttpRequest", code)


class TestWhatTheBrowserIsNotTrustedFor(unittest.TestCase):
    def test_the_object_name_is_never_accepted_from_the_caller(self):
        """An object name supplied by the browser is an arbitrary write path into
        the bucket: it would let one author aim an upload at another's object, or
        at a Drive copy, by typing the name."""
        self.assertNotIn("object_name", signature(UPLOAD_PY, "start_video_upload"))
        self.assertIn("_object_name(", function_body(UPLOAD_PY, "start_video_upload"))

    def test_finishing_refuses_an_object_it_did_not_mint(self):
        body = function_body(UPLOAD_PY, "finish_video_upload")
        self.assertIn("OBJECT_PREFIX", body)

    def test_the_size_is_re_read_rather_than_believed(self):
        """The client already reported a size once, at the start, and that number
        decided whether the upload was allowed. Believing it again would make the
        ceiling advisory."""
        body = function_body(UPLOAD_PY, "finish_video_upload")
        self.assertIn("gcs_media.object_size(object_name)", body)
        self.assertNotIn("size_bytes", signature(UPLOAD_PY, "finish_video_upload"))


class TestTheCeilingIsRealAtLast(unittest.TestCase):
    def test_it_reads_the_setting_that_nothing_read(self):
        """`Training Settings.max_video_mb` has existed with a default of 300 and
        was read by ZERO lines of Python or JavaScript, so there was no size limit
        anywhere in the upload path."""
        self.assertIn("max_video_mb", function_body(UPLOAD_PY, "_max_bytes"))

    def test_the_limit_is_enforced_before_anything_is_sent(self):
        body = function_body(UPLOAD_PY, "start_video_upload")
        self.assertIn("_max_bytes()", body)
        self.assertIn("frappe.throw", body)

    def test_only_browser_playable_formats_are_accepted(self):
        """A .mov carrying HEVC, an .avi or an .mkv all upload perfectly and then
        render as a black rectangle, which reads as the player being broken."""
        text = source(UPLOAD_PY)
        allowed = re.search(r"ALLOWED_MIME = \{(.*?)\}", text, re.S).group(1)
        self.assertEqual(set(re.findall(r'"(video/[^"]+)"', allowed)), {"video/mp4", "video/webm"})


class TestTheDurationCannotSilentlyWaiveTheGate(unittest.TestCase):
    def test_a_zero_duration_is_refused_rather_than_stored(self):
        """`duration_source = Manual` makes grading waive the video-coverage gate
        ENTIRELY, and a failed Drive probe lands exactly that with one orange modal
        and no further sign. A browser reading the length off the file it is about
        to send cannot fail that way."""
        body = function_body(UPLOAD_PY, "finish_video_upload")
        self.assertIn("duration <= 0", body)
        self.assertIn("frappe.throw", body)

    def test_an_uploaded_asset_is_probed_not_manual(self):
        body = function_body(UPLOAD_PY, "finish_video_upload")
        self.assertIn('doc.duration_source = "Probed"', body)
        self.assertNotIn('"Manual"', body)

    def test_the_canvas_reads_it_before_uploading_and_refuses_zero(self):
        code = js_code(CANVAS_JS)
        self.assertIn("read_duration(file)", code)
        self.assertIn("loadedmetadata", code)
        start = code.index("\tupload_video(lesson, block, file, ready) {")
        body = code[start : code.index("\tgcs_session(", start)]
        self.assertIn("if (!seconds)", body)


class TestTheSigner(unittest.TestCase):
    def test_extra_headers_are_sorted_and_declared(self):
        """Header names in a V4 canonical request must be lowercase and SORTED, and
        the signed-headers string must agree exactly — a mismatch is a 403 from
        Google at the moment of use with nothing that says which header was wrong."""
        body = function_body(GCS_PY, "generate_signed_url")
        self.assertIn("sorted(to_sign)", body)
        self.assertIn('signed_headers = ";".join(sorted(to_sign))', body)

    def test_a_plain_get_still_signs_host_only(self):
        """The existing read path passes no headers and must be unchanged."""
        body = function_body(GCS_PY, "generate_signed_url")
        self.assertIn('to_sign = {"host": GCS_HOST}', body)
        self.assertIn("(headers or {})", body)

    def test_the_resumable_start_header_is_signed(self):
        body = function_body(UPLOAD_PY, "start_video_upload")
        self.assertIn('"x-goog-resumable": "start"', body)

    def test_the_browser_is_sent_exactly_what_was_signed(self):
        """Rather than choosing its own headers, which is how the signature stops
        matching for reasons nobody can see."""
        body = function_body(UPLOAD_PY, "start_video_upload")
        self.assertIn('"headers":', body)


class TestTheFailureThatNobodyGuesses(unittest.TestCase):
    def test_a_cors_refusal_says_so_in_words(self):
        """The bucket must allow this origin and expose `Location`. That is a
        deployment dependency, and a browser reports it as status 0 with no detail —
        so the one message an author can act on has to be written here."""
        code = js_code(CANVAS_JS)
        self.assertIn("CORS", code)
        start = code.index("\tgcs_session(start, file) {")
        self.assertIn("xhr.status === 0", code[start : code.index("\tgcs_put(", start)])

    def test_uploads_being_unconfigured_is_said_before_a_file_is_chosen(self):
        code = js_code(CANVAS_JS)
        self.assertIn("upload_preflight", code)
        start = code.index("\tpick_video(lesson, block) {")
        body = code[start : code.index("\tread_duration(file) {", start)]
        self.assertIn("ready.enabled", body)
        self.assertIn("input.click()", body)

    def test_progress_is_shown(self):
        """A 300MB upload with no progress is indistinguishable from a hang, which
        is also why this is XHR and not fetch — fetch has no upload progress event."""
        code = js_code(CANVAS_JS)
        self.assertIn("xhr.upload.onprogress", code)
        self.assertIn("paint_status(kind, text)", code)


class TestItIsWiredIntoCI(unittest.TestCase):
    def test_ci_runs_this_module(self):
        self.assertIn("erpnext_enhancements.tests.test_training_video_upload", source(CI))


if __name__ == "__main__":
    unittest.main()
