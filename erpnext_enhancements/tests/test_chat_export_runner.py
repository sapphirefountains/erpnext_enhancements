# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The export runner's wiring. Bench-free, AST — the rows themselves need a database.

`export.py` decides what leaves the building and is tested against real bytes. This file
tests the things a database would hide rather than reveal:

* the `frappe.enqueue` kwarg name, which this package has been bitten by **twice** — once by
  a kwarg called `event` that the enqueue machinery ate before the job saw it, once by a
  rename to `job_name` that traded "unexpected keyword argument" for "missing required
  positional argument";
* that the download endpoint writes its audit row **before** the bytes are attached, and
  **fails closed** when the write does not happen;
* that the job cannot strand a row in `In Progress`.

None of those is visible in a passing integration test. All three are visible here.
"""

import ast
import pathlib
import sys
import types
import unittest


def setUpModule():
	"""A `frappe` stub, because the controller imports `frappe.model.document.Document`.

	The runner itself is only ever parsed here, never imported — it pulls in the whole chat
	package. The controller is imported for its transition table, which is plain data.
	"""
	if "frappe" in sys.modules and getattr(sys.modules["frappe"], "_ee_test_stub", False):
		return
	frappe = types.ModuleType("frappe")
	frappe._ee_test_stub = True
	frappe.utils = types.ModuleType("frappe.utils")
	frappe.utils.cint = lambda v: int(v or 0)
	frappe.utils.now = lambda: "2026-08-14 00:00:00"
	frappe.cint = frappe.utils.cint
	frappe.db = types.SimpleNamespace(sql=lambda *a, **k: [], get_value=lambda *a, **k: None)
	frappe.throw = lambda *a, **k: (_ for _ in ()).throw(Exception("throw"))
	frappe.log_error = lambda *a, **k: None
	frappe.ValidationError = Exception
	frappe.PermissionError = Exception
	frappe._ = lambda s: s
	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = type("Document", (), {})
	model.document = document
	frappe.model = model
	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = frappe.utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document

CHAT = pathlib.Path(__file__).resolve().parents[1] / "chat"
RUNNER = CHAT / "governance" / "export_runner.py"
DOCTYPE_DIR = CHAT / "doctype" / "chat_export_request"

#: Names `frappe.enqueue` consumes from **kwargs before the job is called.
RESERVED_ENQUEUE_KWARGS = frozenset(
	{"method", "queue", "timeout", "event", "is_async", "job_name", "now", "enqueue_after_commit",
	 "job_id", "deduplicate", "at_front"}
)


def _tree(path=RUNNER):
	return ast.parse(path.read_text(encoding="utf-8"))


def _func(name, path=RUNNER):
	for node in ast.walk(_tree(path)):
		if isinstance(node, ast.FunctionDef) and node.name == name:
			return node
	raise AssertionError(f"{name}() not found in {path.name}")


def _src(name, path=RUNNER):
	return ast.get_source_segment(path.read_text(encoding="utf-8"), _func(name, path))


def _calls(node):
	out = []
	for inner in ast.walk(node):
		if isinstance(inner, ast.Call):
			f = inner.func
			out.append(f.id if isinstance(f, ast.Name) else getattr(f, "attr", ""))
	return out


class EnqueueContractTest(unittest.TestCase):
	"""The trap this package has fallen into twice."""

	def _enqueue_call(self):
		for node in ast.walk(_func("_enqueue")):
			if isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "enqueue":
				return node
		raise AssertionError("_enqueue does not call frappe.enqueue")

	def test_the_job_kwarg_is_not_a_name_frappe_eats(self):
		"""`export_request`, not `event` or `job_name`.

		A kwarg by a reserved name is consumed by the enqueue machinery and never reaches the
		worker — which surfaces as a TypeError inside a background process nobody is watching.
		"""
		call = self._enqueue_call()
		passed = {kw.arg for kw in call.keywords if kw.arg}
		job_kwargs = passed - RESERVED_ENQUEUE_KWARGS
		self.assertEqual(job_kwargs, {"export_request"})

	def test_the_worker_accepts_exactly_that_parameter(self):
		"""Both halves, because renaming one and not the other is how this last broke."""
		params = [a.arg for a in _func("run_export_job").args.args]
		self.assertEqual(params, ["export_request"])

	def test_deduplicate_is_paired_with_an_explicit_job_id(self):
		"""`deduplicate=True` throws on v16 without one."""
		call = self._enqueue_call()
		passed = {kw.arg for kw in call.keywords if kw.arg}
		if "deduplicate" in passed:
			self.assertIn("job_id", passed)

	def test_the_target_is_a_dotted_string_not_a_function_object(self):
		"""A function object pickles its module and ties the queueing process to the running
		one across a deploy."""
		call = self._enqueue_call()
		self.assertTrue(call.args, "enqueue called with no target")
		self.assertIsInstance(call.args[0], ast.Name)  # WORKER_PATH, a module constant
		src = RUNNER.read_text(encoding="utf-8")
		self.assertIn('WORKER_PATH = "erpnext_enhancements.chat.governance.export_runner.run_export_job"', src)

	def test_enqueue_failure_cannot_fail_the_request(self):
		"""A queue that will not accept work must not roll back the row that records the ask."""
		src = _src("_enqueue")
		self.assertIn("try:", src)
		self.assertIn("except Exception:", src)
		self.assertNotIn("raise", src)


class JobLifecycleTest(unittest.TestCase):
	"""A row stranded in In Progress reads as 'still working', not as 'broken'."""

	def test_every_exit_from_the_job_reaches_a_terminal_status(self):
		src = _src("run_export_job")
		self.assertIn("STATUS_COMPLETE", src)
		self.assertIn("_fail(", src)

	def test_the_build_is_wrapped_so_a_raise_becomes_a_failed_row(self):
		src = _src("run_export_job")
		self.assertIn("except Exception as exc:", src)
		self.assertIn("rollback", src)

	def test_the_failure_writer_never_re_raises(self):
		"""It runs in a worker; raising here replaces a legible Failed row with a traceback.

		AST, not text: the docstring has to say "never re-raise" to explain itself, and a
		substring scan reads that as the thing it forbids. That is the fourth time in this
		change a text scan flagged prose — a guard that cannot tell an explanation from a
		statement is satisfied by deleting the explanation.
		"""
		raises = [n for n in ast.walk(_func("_fail")) if isinstance(n, ast.Raise)]
		self.assertEqual(raises, [])
		self.assertEqual(_src("_fail").count("except Exception:"), 2)

	def test_a_second_run_on_a_finished_row_is_a_no_op(self):
		"""`deduplicate` drops an overlapping enqueue, but a sweeper can still arrive later."""
		src = _src("run_export_job")
		self.assertIn("!= STATUS_PENDING", src)

	def test_the_claim_happens_before_the_build(self):
		calls = _calls(_func("run_export_job"))
		self.assertLess(calls.index("_set_status"), calls.index("_build"))


class DownloadAuditTest(unittest.TestCase):
	"""The row that answers 'who actually took a copy'."""

	def test_the_audit_row_is_written_before_the_bytes_are_attached(self):
		"""Attaching first and recording after is a download that happened unrecorded if
		anything in between raises."""
		src = _src("download_export")
		self.assertLess(
			src.index("record_governance_event"),
			src.index("frappe.local.response.filecontent"),
			"the bytes are attached to the response before the audit row is written",
		)

	def test_a_failed_audit_write_refuses_the_download(self):
		"""`record_governance_event` SWALLOWS its failures and returns None.

		A caller that ignores the return cannot tell a recorded download from an unrecorded
		one — and assumes the first. This is the check that makes the two-event split mean
		something.
		"""
		src = _src("download_export")
		self.assertIn("if not recorded:", src)
		self.assertIn("raise", src.split("if not recorded:")[1][:400])

	def test_it_uses_its_own_event_type(self):
		src = _src("download_export")
		self.assertIn('event_type="export_downloaded"', src)
		self.assertNotIn('event_type="export_requested"', src)

	def test_the_request_endpoint_uses_the_other_one(self):
		src = _src("request_export")
		self.assertIn('event_type="export_requested"', src)

	def test_the_download_is_not_post_and_says_why(self):
		"""Uniquely on this surface. `response.type = "download"` needs a navigation; an XHR
		receives the bytes and drops them."""
		fn = _func("download_export")
		for dec in fn.decorator_list:
			if isinstance(dec, ast.Call) and getattr(dec.func, "attr", "") == "whitelist":
				self.assertEqual([k.arg for k in dec.keywords], [], "download_export declares methods=")
		self.assertIn("navigate", ast.get_docstring(fn) or "")

	def test_a_missing_or_unfinished_export_gets_one_uniform_refusal(self):
		"""Distinguishing them answers 'does an export of this room exist' to somebody who may
		not read it."""
		src = _src("download_export")
		self.assertIn("Not permitted", src)
		self.assertNotIn("does not exist", src)


class RequestGateTest(unittest.TestCase):
	def test_the_role_and_reason_are_checked_before_anything_is_written(self):
		calls = _calls(_func("request_export"))
		self.assertLess(calls.index("_require_auditor"), calls.index("insert"))
		self.assertLess(calls.index("_require_reason"), calls.index("insert"))

	def test_the_audit_row_precedes_the_enqueue(self):
		"""If the queue is down the request is still recorded as having been made. The
		reverse — a bundle built from a request nobody recorded — is what this phase exists
		to prevent."""
		calls = _calls(_func("request_export"))
		self.assertLess(calls.index("record_governance_event"), calls.index("_enqueue"))

	def test_the_reason_uses_the_shared_grader(self):
		"""One threshold across the door, the viewer and the compliance report."""
		src = _src("_require_reason")
		self.assertIn("access_report.reason_quality", src)
		self.assertIn("REASON_OK", src)

	def test_the_audit_detail_carries_no_message_text(self):
		"""`record_governance_event`'s docstring forbids it, and an export's detail is the
		most tempting place to put a sample of what was exported."""
		src = _src("_request_detail")
		for forbidden in ("text", "body", "message_record", "snippet"):
			self.assertNotIn(f'"{forbidden}"', src)


class SqlBindingTest(unittest.TestCase):
	"""Room names arrive in a request body."""

	def test_room_names_are_bound_not_interpolated(self):
		src = _src("_room_sql")
		self.assertIn("%(", src)
		# The only thing built by string join is the placeholder list itself.
		self.assertIn('f"%({k})s"', src)

	def test_each_reader_uses_the_bound_helper(self):
		for name in ("_messages", "_revisions", "_attachments"):
			self.assertIn("_room_sql(rooms)", _src(name), f"{name} builds its own IN list")

	def test_an_empty_room_list_returns_nothing_rather_than_everything(self):
		for name in ("_messages", "_revisions", "_attachments"):
			src = _src(name)
			self.assertIn("if not rooms:", src, f"{name} has no empty-room guard")


class DocTypeTest(unittest.TestCase):
	def setUp(self):
		import json

		self.meta = json.loads(
			(DOCTYPE_DIR / "chat_export_request.json").read_text(encoding="utf-8")
		)

	def test_it_ships_zero_docperm(self):
		"""Which is what closes /private/files/ on the bundle: Frappe's private-file check
		delegates to the attached document's has_permission, and there is none to grant."""
		self.assertEqual(self.meta["permissions"], [])

	def test_house_style(self):
		self.assertEqual(self.meta["module"], "Chat")
		self.assertEqual(self.meta["autoname"], "hash")
		self.assertEqual(self.meta["naming_rule"], "Random")
		for key in ("track_changes", "track_seen", "track_views"):
			self.assertEqual(self.meta[key], 0, key)
		self.assertNotIn("is_submittable", self.meta)

	def test_field_order_matches_the_fields(self):
		self.assertEqual(
			[f["fieldname"] for f in self.meta["fields"]], self.meta["field_order"]
		)

	def test_the_reason_category_select_is_populated_from_the_one_vocabulary(self):
		"""An empty Select offers nothing on the desk form, and the writer would then refuse
		every value the person could not choose."""
		from erpnext_enhancements.chat import audit

		field = next(f for f in self.meta["fields"] if f["fieldname"] == "reason_category")
		options = [o for o in field["options"].split("\n") if o.strip()]
		self.assertEqual(options, list(audit.REASON_CATEGORIES))

	def test_include_deleted_content_defaults_to_off(self):
		"""Decision D-9."""
		field = next(
			f for f in self.meta["fields"] if f["fieldname"] == "include_deleted_content"
		)
		self.assertEqual(field.get("default"), "0")

	def test_the_scope_fields_are_read_only(self):
		"""They are restated in manifest.json and in the audit row; three copies that can
		drift apart is worse than one that cannot be corrected."""
		frozen = {"rooms", "reason", "reason_category", "include_deleted_content", "requested_by"}
		for field in self.meta["fields"]:
			if field["fieldname"] in frozen:
				self.assertEqual(field.get("read_only"), 1, field["fieldname"])


class ControllerTest(unittest.TestCase):
	CONTROLLER = DOCTYPE_DIR / "chat_export_request.py"

	def test_a_completed_request_cannot_be_deleted(self):
		"""Deleting it does not recall the ZIP; it removes the evidence that one was taken."""
		src = _src("on_trash", self.CONTROLLER)
		self.assertIn("STATUS_COMPLETE", src)
		self.assertIn("frappe.throw", src)

	def test_terminal_statuses_have_no_outgoing_transitions(self):
		from erpnext_enhancements.chat.doctype.chat_export_request import chat_export_request as mod

		for status in (mod.STATUS_COMPLETE, mod.STATUS_FAILED):
			self.assertEqual(mod.LEGAL_TRANSITIONS[status], frozenset(), status)

	def test_a_failed_export_is_re_requested_rather_than_retried_in_place(self):
		"""A retry that overwrote the row would lose the record that the first attempt
		happened — the only thing that makes 'why is there no export?' answerable."""
		from erpnext_enhancements.chat.doctype.chat_export_request import chat_export_request as mod

		self.assertNotIn(mod.STATUS_IN_PROGRESS, mod.LEGAL_TRANSITIONS[mod.STATUS_FAILED])


class ReviewFindingsTest(unittest.TestCase):
	"""Five defects an adversarial review found in the first version of this runner.

	Each is pinned by name, because every one of them fails *silently* — a stranded row that
	reads as "still working", a bundle with no audit row, a download that leaves no trace, an
	attachments directory the README promises and the code never writes, and a truncated
	export that counts itself as complete.
	"""

	def test_the_success_path_is_inside_the_try(self):
		"""It was outside it. A lock-wait timeout on the UPDATE — the shape a 30-minute
		export actually has — escaped the job and stranded the row at In Progress with the
		ZIP already on disk and nothing pointing at it. `status != STATUS_PENDING` then
		blocked every re-run, so it could neither complete nor be retried.
		"""
		fn = _func("run_export_job")
		tries = [n for n in ast.walk(fn) if isinstance(n, ast.Try)]
		self.assertTrue(tries)
		source = RUNNER.read_text(encoding="utf-8")
		body_src = "\n".join(
			ast.get_source_segment(source, stmt) or "" for t in tries for stmt in t.body
		)
		self.assertIn("STATUS_COMPLETE", body_src)
		self.assertIn("db.commit", body_src)

	def test_request_export_fails_closed_when_the_audit_row_is_not_written(self):
		"""`record_governance_event` swallows failures and returns None. The first version
		discarded the return and queued the build anyway — a downloadable bundle with no
		`export_requested` row, which is what `download_export` already refused. Two call
		sites in one file must not make opposite trades for the same failure.
		"""
		fn = _func("request_export")
		# AST, not text: the branch carries a long comment and a `raise` counted by substring
		# offset drifted out of range the moment that comment grew. Same lesson as every
		# other guard in this change.
		guards = [
			n
			for n in ast.walk(fn)
			if isinstance(n, ast.If)
			and isinstance(n.test, ast.UnaryOp)
			and isinstance(n.test.op, ast.Not)
			and getattr(n.test.operand, "id", "") == "recorded"
		]
		self.assertEqual(len(guards), 1, "no `if not recorded:` guard in request_export")
		self.assertTrue(
			[n for n in ast.walk(guards[0]) if isinstance(n, ast.Raise)],
			"the guard exists but does not refuse",
		)
		self.assertIn("recorded = audit.record_governance_event", _src("request_export"))
		calls = _calls(fn)
		self.assertIn("record_governance_event", calls)
		self.assertLess(calls.index("record_governance_event"), calls.index("_enqueue"))

	def test_the_bundle_file_is_not_owned_by_the_requester(self):
		"""Frappe grants a File's owner access BEFORE delegating to the attached document.

		The worker runs as the requesting auditor, so the bundle was owned by them and
		reachable at /private/files/chat-export-<name>.zip with no export_downloaded row and
		no download_count — the audited endpoint would have been one door of two.
		"""
		src = _src("_store")
		self.assertIn('frappe.db.set_value("File", doc.name, "owner", "Administrator"', src)
		self.assertLess(src.index("doc.insert"), src.index('"owner"'),
			"owner must be reassigned AFTER insert; set_user_and_timestamp overwrites it")

	def test_attachments_are_resolved_by_docname_not_by_url(self):
		"""`Chat Attachment.file` is a Link to File and holds the DOCNAME.

		Looking it up by {"file_url": ...} matched nothing, raised, and was swallowed into
		b"" — so every attachment was dropped from every bundle while README.txt told the
		reader the directory was there.
		"""
		# AST: the docstring has to quote the broken `{"file_url": ...}` lookup to explain
		# what went wrong, and a substring scan reads that as the bug still being present.
		fn = _func("_file_bytes")
		lookups = [
			n
			for n in ast.walk(fn)
			if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "get_doc"
		]
		self.assertEqual(len(lookups), 1)
		args = lookups[0].args
		self.assertEqual(len(args), 2)
		self.assertNotIsInstance(
			args[1], ast.Dict, "resolved by filter dict again, not by primary key"
		)
		self.assertEqual([a.arg for a in fn.args.args], ["file_name"])

	def test_an_oversized_range_is_refused_rather_than_truncated(self):
		"""The caps were LIMITs. The bundle returned the first 50,000 rows and counted them
		as the whole range — a false statement in a legal disclosure. This module's own
		docstring already says a partial export is worse than none because it looks complete.
		"""
		src = _src("_build")
		self.assertIn("> MAX_MESSAGES", src)
		self.assertIn("raise", src)

	def test_the_readers_fetch_one_over_the_cap_so_overflow_is_visible(self):
		"""At exactly the limit, a full page and a truncated page are indistinguishable."""
		for name in ("_messages", "_revisions"):
			self.assertIn("MAX_MESSAGES + 1", _src(name), name)

	def test_an_omitted_attachment_is_named_in_the_manifest(self):
		"""'This export is missing something' has to be a fact a reader can find, not a
		difference they would have to notice."""
		self.assertIn("attachments_omitted", _src("_build"))
		self.assertIn("omitted.append", _src("_attachments"))


if __name__ == "__main__":
	unittest.main()
