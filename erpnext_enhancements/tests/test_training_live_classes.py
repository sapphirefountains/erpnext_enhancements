"""Bench-free tests for Training Live Classes (WI-071 Phase B).

Three things are guarded without a bench:

  * **the join URL is http(s) or it does not save.** It is served to a learner as an
    `<a href>` and clicked, so a `javascript:` link would run under their session; the
    controller refuses any other scheme (the player refuses it a second time on
    render). This is the executable half — the controller imports only frappe + a
    Document base, so it runs under a minimal stub;
  * **the doctype shape** — the status options a learner is filtered on, the join URL
    field, the batch link, the Training module;
  * **the learner surface is wired and scoped** — `get_learner_bootstrap` sends a
    `live_classes` key, `_learner_live_classes` filters strictly on the learner's own
    batch membership and on `Scheduled`/`Live` only, and the player renders a Join
    anchor only for an http(s) scheme. These are source assertions: importing
    `api/training.py` pulls in half the module, and the property that matters (the
    scoping, the scheme guard) is a claim the source can hold.

Run: python -m unittest erpnext_enhancements.tests.test_training_live_classes
"""

import json
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
live_class = None


def _install_stubs():
	frappe = types.ModuleType("frappe")

	class _ValidationError(Exception):
		pass

	frappe.ValidationError = _ValidationError

	def _throw(msg, exc=None):
		raise (exc or _ValidationError)(msg)

	frappe.throw = _throw
	frappe.__dict__["_"] = lambda s: s
	sys.modules["frappe"] = frappe

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")

	class Document:
		pass

	document.Document = Document
	model.document = document
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document


def setUpModule():
	global live_class
	_install_stubs()
	from erpnext_enhancements.training.doctype.training_live_class import training_live_class as module

	live_class = module


def _doc(join_url):
	doc = live_class.TrainingLiveClass()
	doc.join_url = join_url
	return doc


class TestJoinUrlGuard(unittest.TestCase):
	def test_an_https_link_is_accepted_and_trimmed(self):
		doc = _doc("  https://meet.example.com/abc  ")
		doc._check_join_url()
		self.assertEqual(doc.join_url, "https://meet.example.com/abc")

	def test_an_http_link_is_accepted(self):
		doc = _doc("http://zoom.example.com/j/123")
		doc._check_join_url()  # does not raise
		self.assertEqual(doc.join_url, "http://zoom.example.com/j/123")

	def test_a_blank_url_is_fine(self):
		doc = _doc("   ")
		doc._check_join_url()
		self.assertEqual(doc.join_url, "")

	def test_a_javascript_url_is_refused(self):
		with self.assertRaises(Exception):
			_doc("javascript:alert(1)")._check_join_url()

	def test_a_bare_or_other_scheme_is_refused(self):
		for bad in ("meet.example.com/abc", "ftp://x/y", "data:text/html,x"):
			with self.assertRaises(Exception):
				_doc(bad)._check_join_url()


class TestDoctypeShape(unittest.TestCase):
	def setUp(self):
		self.spec = json.loads(
			(APP / "training/doctype/training_live_class/training_live_class.json").read_text(encoding="utf-8")
		)
		self.fields = {f["fieldname"]: f for f in self.spec["fields"]}

	def test_module_is_training(self):
		self.assertEqual(self.spec["module"], "Training")

	def test_status_options_are_the_ones_the_learner_is_filtered_on(self):
		self.assertEqual(self.fields["status"]["options"], "Scheduled\nLive\nCompleted\nCanceled")

	def test_batch_is_a_required_link(self):
		self.assertEqual(self.fields["batch"]["options"], "Training Batch")
		self.assertEqual(self.fields["batch"].get("reqd"), 1)

	def test_join_url_is_present(self):
		self.assertIn("join_url", self.fields)
		self.assertEqual(self.fields["join_url"]["fieldtype"], "Data")

	def test_starts_on_is_required(self):
		self.assertEqual(self.fields["starts_on"].get("reqd"), 1)


class TestLearnerSurfaceWiring(unittest.TestCase):
	def setUp(self):
		self.api = (APP / "api/training.py").read_text(encoding="utf-8")
		self.player = (APP / "public/js/training/player.js").read_text(encoding="utf-8")

	def test_the_bootstrap_sends_live_classes(self):
		self.assertIn('"live_classes": _learner_live_classes(user)', self.api)

	def test_the_query_is_scoped_to_the_learners_own_membership(self):
		# _learner_live_classes must filter by the learner's own batch membership and
		# by Scheduled/Live only — never the whole class list.
		start = self.api.index("def _learner_live_classes")
		body = self.api[start : self.api.index("\ndef ", start + 1)]
		self.assertIn('"Training Batch Member", filters={"learner": user}', body)
		self.assertIn('"status": ["in", ["Scheduled", "Live"]]', body)

	def test_the_player_reads_live_classes_and_guards_the_scheme(self):
		start = self.player.index("function liveClassBlock")
		body = self.player[start : self.player.index("\n\t\tfunction ", start + 1)]
		self.assertIn("b.live_classes", body)
		# A Join anchor is rendered only for an http(s) URL.
		self.assertIn("/^https?:\\/\\//i.test(url)", body)


if __name__ == "__main__":
	unittest.main()
