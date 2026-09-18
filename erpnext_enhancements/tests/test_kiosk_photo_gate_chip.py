"""The photo-gate chip must reflect the situation, not just the gate (v1.484.0).

Reported from the field: the chip read **"Needs 1 more photo"** and never cleared,
however many photos were added — it looked like a stuck counter.

It was not stuck. There are two upload paths and only one of them fed it:

* `capturePhoto()` (the camera) writes a `Job Interval Photo` row through
  `record_job_photo`, increments `app.photoCount` and re-renders. That path always
  worked, and cleared the chip immediately.
* `linkFile()` (the attachment card) pushed to `app.attachments` and touched
  neither. And the server agreed with it: `workforce/photo_gate.py` counts
  `Job Interval Photo` rows, which an attachment never created.

So the chip was telling the truth about the **gate** and lying about the
**situation** — the worse of the two failures, because a warning that never
responds to the thing it is asking for reads as broken and gets ignored. That is
the same erosion the gate's own docstring warns about when it explains why the
gate blocks on capture rather than on upload.

An attached **image** now registers as a job photo, so it counts and the chip
moves. Deliberately only images, and only while clocked in: a PDF quote is an
attachment, not evidence the job was photographed.

Run: python -m unittest erpnext_enhancements.tests.test_kiosk_photo_gate_chip
"""

import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
APP_JS = APP / "public" / "js" / "kiosk" / "app.js"


def _function(body, name):
    """The source of `function <name>(` up to its closing brace at column 2.

    The kiosk files are 2-space indented, so a top-level function inside the IIFE
    closes on a line that is exactly two spaces then '}'.
    """
    start = body.index("function " + name + "(")
    end = body.index("\n  }", start)
    return body[start:end]


class TestTheAttachmentPathFeedsTheChip(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_the_helper_exists(self):
        self.assertIn("function countAttachedImageAsJobPhoto(", self.js)

    def test_link_file_calls_it(self):
        """This is the actual regression: linkFile() used to end at renderAttachments()."""
        self.assertIn("countAttachedImageAsJobPhoto", _function(self.js, "linkFile"))

    def test_only_images_count(self):
        fn = _function(self.js, "countAttachedImageAsJobPhoto")
        for ext in ("jpe?g", "png", "heic"):
            with self.subTest(ext=ext):
                self.assertIn(ext, fn)

    def test_it_does_nothing_without_an_active_interval(self):
        fn = _function(self.js, "countAttachedImageAsJobPhoto")
        self.assertIn("if (!ci.name) return;", fn)

    def test_it_registers_a_real_job_photo_row(self):
        """Incrementing the local counter alone would clear the chip while leaving
        the server-side gate unsatisfied — a clock-out would then be refused by a
        rule the UI had just said was met."""
        fn = _function(self.js, "countAttachedImageAsJobPhoto")
        self.assertIn("record_job_photo", fn)

    def test_the_chip_moves_only_after_the_write_lands(self):
        """Incrementing before the call would move the counter on a write that
        failed — the same class of lie, pointing the other way."""
        fn = _function(self.js, "countAttachedImageAsJobPhoto")
        self.assertLess(fn.index("record_job_photo"), fn.index("app.photoCount"))
        self.assertIn("renderPhotoChip()", fn)


class TestTheCameraPathStillWorks(unittest.TestCase):
    """The path that was already correct, pinned so this change cannot break it."""

    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_capture_counts_locally_before_the_network(self):
        """An offline capture is real even though the server has not heard about
        it — the count must not wait on a request that may never succeed."""
        fn = _function(self.js, "capturePhoto")
        self.assertLess(fn.index("app.photoCount"), fn.index("registerPhoto"))
        self.assertIn("renderPhotoChip()", fn)

    def test_a_server_count_never_lowers_a_local_one(self):
        """applyStatus takes the max: lowering it would re-block a technician who
        has already taken the photo but is still offline."""
        self.assertRegex(
            self.js,
            r"app\.photoCount\s*=\s*Math\.max\(\s*message\.photo_count",
        )


class TestTheChipWording(unittest.TestCase):
    def setUp(self):
        self.js = APP_JS.read_text(encoding="utf-8")

    def test_it_says_the_requirement_is_met_rather_than_going_blank(self):
        fn = _function(self.js, "renderPhotoChip")
        self.assertIn("Photo requirement met", fn)
        self.assertIn("more photo", fn)

    def test_the_chip_is_hidden_entirely_when_photos_are_not_required(self):
        fn = _function(self.js, "renderPhotoChip")
        self.assertIn("require_job_photos", fn)
