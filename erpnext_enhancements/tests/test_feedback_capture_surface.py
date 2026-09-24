"""Bench-free: where the capture widget is allowed to load, and what it is wired to (WI-079 slice 2).

The capture recorder watches console errors and network requests. On a guest, token or customer
page that would mean recording someone else's session, so ADR 0016 §4 mounts it on an
**allowlist**: the Desk, ``/kiosk``, ``/feedback``, ``/itinerary`` and ``/travel_guidelines``.
It is **never** in ``web_include_js``, which Frappe emits on every website page, ``/pay`` and
``/contract-sign`` included. Acceptance: "loading ``/pay``, ``/contract-sign`` and ``/wall``
fetches no capture code". Every one of those pages is a template in this repo, so the
guarantee can be checked here, as text, without a browser.

It also pins the wiring an easy edit can quietly break:
- the Help-menu item's action;
- the two scheduled jobs;
- the four provenance fields staying out of client input and inside ``_FROZEN_FIELDS``;
- the transport naming the endpoint.

No stub. It parses files, and it runs in the stub-free feedback CI step.

Run: python -m unittest erpnext_enhancements.tests.test_feedback_capture_surface -v
"""

import ast
import json
import re
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
WWW = APP / "www"
HOOKS = APP / "hooks.py"
JS = APP / "public" / "js"

ALLOWLIST = {
	"kiosk.html": "kiosk",
	"feedback.html": "web",
	"itinerary.html": "web",
	"travel_guidelines.html": "web",
}

#: The WI's never-list, restricted to the pages that are templates in this app.
NEVER = (
	"pay.html",
	"pay-card.html",
	"stripe-return.html",
	"contract-sign.html",
	"fountain-move.html",
	"training_certificate.html",
	"wall.html",
	"stock-scan.html",
)

CAPTURE_MARKERS = ("capture.bundle", "capture_panel.bundle", "EE_CAPTURE", "ee_capture")


def _hook(name):
	for node in ast.parse(HOOKS.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and target.id == name:
					return ast.literal_eval(node.value)
	return None


def _module_constant(path, name):
	for node in ast.parse(path.read_text(encoding="utf-8")).body:
		if isinstance(node, ast.Assign):
			for target in node.targets:
				if isinstance(target, ast.Name) and target.id == name:
					return node.value
	return None


class TestWhereTheWidgetLoads(unittest.TestCase):
	def test_never_in_web_include_js(self):
		value = _hook("web_include_js")
		items = [value] if isinstance(value, str) else list(value or [])
		for item in items:
			self.assertNotIn("capture", item.lower(), item)

	def test_every_allowlisted_page_loads_the_recorder_first_with_its_surface(self):
		for page, surface in ALLOWLIST.items():
			with self.subTest(page=page):
				text = (WWW / page).read_text(encoding="utf-8")
				block = text[text.index("{% block script %}") :]
				self.assertIn("bundled_asset('capture.bundle.js')", block)
				self.assertIn("bundled_asset('capture_panel.bundle.js')", block)
				self.assertIn(f'surface: "{surface}"', block)
				self.assertIn("launcher: false" if surface == "kiosk" else "launcher: true", block)
				# First script in the block, so it sees errors from the page's own scripts.
				first_src = re.search(r"<script src=\"([^\"]+)\"", block).group(1)
				self.assertIn("capture.bundle.js", first_src)

	def test_no_other_template_loads_any_capture_code(self):
		offenders = []
		for path in list(WWW.rglob("*.html")) + list((APP / "templates").rglob("*.html")):
			if path.parent == WWW and path.name in ALLOWLIST:
				continue
			text = path.read_text(encoding="utf-8", errors="replace")
			if any(marker in text for marker in CAPTURE_MARKERS):
				offenders.append(str(path.relative_to(APP)))
		self.assertEqual(offenders, [])

	def test_the_named_never_pages_exist_and_are_clean(self):
		for page in NEVER:
			with self.subTest(page=page):
				text = (WWW / page).read_text(encoding="utf-8")
				for marker in CAPTURE_MARKERS:
					self.assertNotIn(marker, text)

	def test_the_desk_bundle_starts_with_the_recorder(self):
		lines = [
			line.strip()
			for line in (JS / "erpnext_enhancements.bundle.js").read_text(encoding="utf-8").splitlines()
			if line.strip().startswith("import ")
		]
		self.assertTrue(lines, "no imports found")
		self.assertIn("capture/recorder.js", lines[0])

	def test_both_bundles_exist(self):
		for name in ("capture.bundle.js", "capture_panel.bundle.js"):
			self.assertTrue((JS / name).exists(), name)

	def test_the_recorder_never_uses_the_guest_error_logger(self):
		# api/logger.log_client_error is guest-callable and writes unscrubbed text to Error Log.
		for path in (JS / "capture").rglob("*.js"):
			self.assertNotIn("log_client_error", path.read_text(encoding="utf-8"), path.name)


class TestWiring(unittest.TestCase):
	def test_the_help_menu_item(self):
		items = _hook("standard_help_items") or []
		(item,) = [i for i in items if i.get("item_label") == "Report a Problem"]
		self.assertEqual(item["item_type"], "Action")
		self.assertEqual(item["is_standard"], 1)
		self.assertIn("ee_capture.open()", item["action"])
		self.assertFalse(item["action"].startswith("erpnext_enhancements."))

	def test_the_scheduled_jobs(self):
		events = _hook("scheduler_events")
		self.assertIn(
			"erpnext_enhancements.product_feedback.capture_jobs.purge_expired_capture_files", events["daily"]
		)
		self.assertIn(
			"erpnext_enhancements.product_feedback.capture_jobs.match_capture_error_logs", events["hourly"]
		)

	def test_the_transport_names_the_endpoint(self):
		text = (JS / "feedback" / "transport.js").read_text(encoding="utf-8")
		self.assertIn('CAPTURE: "erpnext_enhancements.api.feedback.submit_capture"', text)


class TestProvenanceFields(unittest.TestCase):
	PROVENANCE = ("source", "source_doctype", "source_ref", "context_release")

	def setUp(self):
		path = APP / "product_feedback/doctype/enhancement_request/enhancement_request.json"
		self.meta = json.loads(path.read_text(encoding="utf-8"))
		self.fields = {f["fieldname"]: f for f in self.meta["fields"]}

	def test_the_fields_and_their_shapes(self):
		self.assertEqual(self.fields["source"]["fieldtype"], "Select")
		self.assertEqual(self.fields["source"]["options"].split("\n"), ["Feedback form", "Capture", "Design Review"])
		# ADR 0016 §1: a normal doctype's column default reaches existing rows via the ALTER,
		# so the 16 existing requests read "Feedback form" with no patch.
		self.assertEqual(self.fields["source"]["default"], "Feedback form")
		self.assertEqual(self.fields["source_doctype"]["options"], "DocType")
		self.assertEqual(self.fields["source_ref"]["fieldtype"], "Dynamic Link")
		self.assertEqual(self.fields["source_ref"]["options"], "source_doctype")
		self.assertEqual(self.fields["terminal_at"]["fieldtype"], "Datetime")
		# No default: on a normal doctype the ALTER would stamp every existing row with it.
		self.assertNotIn("default", self.fields["terminal_at"])
		for name in (*self.PROVENANCE, "terminal_at"):
			self.assertEqual(self.fields[name].get("read_only"), 1, name)
			self.assertIn(name, self.meta["field_order"])

	def test_provenance_is_frozen_after_insert(self):
		frozen = ast.literal_eval(
			_module_constant(APP / "product_feedback/doctype/enhancement_request/enhancement_request.py", "_FROZEN_FIELDS")
		)
		for name in self.PROVENANCE:
			self.assertIn(name, frozen)
		self.assertNotIn("terminal_at", frozen, "the controller stamps it; freezing it would refuse its own write")

	def test_provenance_is_never_client_input(self):
		node = _module_constant(APP / "api/feedback.py", "SUBMIT_ALLOWED_FIELDS")
		allowed = {elt.value for elt in node.args[0].elts}
		for name in (*self.PROVENANCE, "terminal_at", "requested_by", "status"):
			self.assertNotIn(name, allowed)


if __name__ == "__main__":
	unittest.main()
