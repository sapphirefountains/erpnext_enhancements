# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every whitelisted endpoint declares the HTTP methods it accepts. Bench-free, AST only.

``@frappe.whitelist()`` with no ``methods`` argument answers GET, POST, PUT and DELETE.
An endpoint that changes something should say ``methods=["POST"]``, the request the Desk
sends for a change, and a read that is meant to be fetched with GET should say
``methods=["GET"]`` (or ``["GET", "POST"]``) so the choice is visible in review.

This suite is a ratchet, in three parts.

**The baseline.** ``data/whitelist_methods_baseline.txt`` lists, one per line and sorted,
every endpoint that does NOT declare ``methods=`` today. The suite fails when:

* an endpoint without ``methods=`` is missing from the baseline: declare
  ``methods=[...]`` explicitly on new endpoints rather than extending the baseline; or
* the baseline names an endpoint that now declares ``methods=`` or no longer exists:
  remove that line from the baseline.

**The ceiling.** :data:`BASELINE_CEILING` is the baseline's length, kept in this file. The
suite fails unless the two agree, and ``--write`` refuses to write a longer list, so the
number only goes down and every change to it is a line in a diff.

**The names.** An endpoint that answers GET, because it declares GET or declares nothing,
and whose name reads as a change (``create_``, ``send_``, ``delete_`` and so on, see
:data:`CHANGE_WORDS`) must be in :data:`GET_REVIEWED` with the reason it is fine. So
declaring ``methods=["GET"]`` on a change to satisfy the baseline does not pass either.

Names are ``dotted.module.function`` (or ``dotted.module.Class.method`` for a controller
method).

Regenerate the baseline after declaring methods on some endpoints (or after a rename), then
lower :data:`BASELINE_CEILING` to the count it prints::

    python -m erpnext_enhancements.tests.test_whitelist_methods_ratchet --write

Run: python -m unittest erpnext_enhancements.tests.test_whitelist_methods_ratchet
"""

import ast
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
BASELINE = APP / "tests" / "data" / "whitelist_methods_baseline.txt"

#: The number of endpoints on the baseline. Lower it when the baseline shrinks; never raise it.
BASELINE_CEILING = 355

#: The tests tree defines stubs and fixtures, not endpoints.
SKIP_PARTS = frozenset({"tests", "node_modules", "__pycache__", ".git"})

HEADER = (
	"# Whitelisted endpoints that do not declare `methods=`, one per line, sorted.\n"
	"# This list can only shrink: declare `methods=[...]` on a new endpoint instead of adding it here.\n"
	"# Regenerate with: python -m erpnext_enhancements.tests.test_whitelist_methods_ratchet --write\n"
	"# See tests/test_whitelist_methods_ratchet.py.\n"
)

#: A function whose name starts with one of these reads as a change, not a lookup.
CHANGE_WORDS = frozenset(
	"""accept activate add append apply approve archive assign attach author book broadcast cache cancel
	change charge clear close complete confirm connect convert copy create deactivate delete detach disable
	disconnect dispatch edit enable enqueue execute finish generate grant import insert invite link lock log
	make mark merge move notify pay post process publish push receive record refund register reject release
	remove rename reopen request resend reserve reset restore retry revoke run save schedule send set sign
	start stop store submit sync toggle transfer trigger unlink unlock update upload write""".split()
)

#: A leading word that says where an endpoint is used, skipped before the name is judged.
NAME_PREFIXES = frozenset({"portal", "bulk"})

_APP = "erpnext_enhancements."
_CONTRACT = "sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract."

#: Endpoints that answer GET although their name reads as a change. Each was read, and the
#: reason says why it is fine. A new one fails the suite until it declares POST, or is added
#: here with its reason by somebody who has read it.
_REVIEWED = (
	(
		"Called by the phone gateway with the webhook secret, not from a browser session.",
		(
			"api.call_intelligence.process_call_intelligence",
			"api.telephony.append_call_transcript",
			"api.telephony.notify_incoming_call",
			"api.telephony.process_unified_recording",
			"api.telephony.process_unified_sms",
			"api.telephony.update_caller_info",
		),
	),
	(
		"Called by Twilio and checked against the request signature.",
		("api.telephony.receive_mms",),
	),
	(
		"Builds an unsaved document for the form to open (frappe.model.mapper); stores nothing.",
		(
			"api.supplier_returns.make_from_receipt",
			"opportunity_enhancements.make_project",
			_CONTRACT + "make_contract_from_project",
			_CONTRACT + "make_contract_from_project_contract",
			_CONTRACT + "make_contract_from_sales_order",
		),
	),
	(
		"A lookup: the first word of the name is a noun here.",
		(
			"api.supplier_returns.refund_source",
			"form_kit.api.record_info",
			"form_kit.api.record_notes",
		),
	),
	(
		"Returns a preview, a draft or a copy for the page to show; stores and sends nothing.",
		(
			"api.scope_tasks.delete_preview",
			"api.supplier_returns.request_draft",
			"training.video_upload.upload_preflight",
			"travel_management.planner.copy_plan",
		),
	),
	(
		"Runs a check or a calculation and returns the answer; stores nothing.",
		(
			"api.integrations_health.run_drive_test",
			"water_engineering.api.designer.run_calc",
		),
	),
	(
		"Intuit's Disconnect URL: the browser arrives by redirect, which is a GET. QuickBooks operators only.",
		("quickbooks_online.core.api.disconnect_callback",),
	),
)
GET_REVIEWED = {_APP + name: reason for reason, names in _REVIEWED for name in names}


def _sources():
	for path in sorted(APP.rglob("*.py")):
		if SKIP_PARTS.intersection(path.relative_to(APP).parts):
			continue
		yield path


def _module_name(path):
	parts = list(path.relative_to(APP.parent).with_suffix("").parts)
	if parts[-1] == "__init__":
		parts.pop()
	return ".".join(parts)


def _whitelist_call(decorator):
	"""``(is_whitelist, methods)`` for one decorator node.

	``methods`` is None when nothing is declared, a tuple of the declared names, or
	``("?",)`` when the argument is not a literal and so cannot be read here.
	"""
	target = decorator.func if isinstance(decorator, ast.Call) else decorator
	if ast.unparse(target) not in ("frappe.whitelist", "whitelist"):
		return False, None
	if isinstance(decorator, ast.Call):
		for keyword in decorator.keywords:
			if keyword.arg == "methods":
				try:
					return True, tuple(str(m).upper() for m in ast.literal_eval(keyword.value))
				except (ValueError, TypeError, SyntaxError):
					return True, ("?",)
	return True, None


def _walk(node, prefix):
	for child in ast.iter_child_nodes(node):
		if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
			for decorator in child.decorator_list:
				is_whitelist, methods = _whitelist_call(decorator)
				if is_whitelist:
					yield f"{prefix}.{child.name}", methods
			# A function nested in a function is not an endpoint.
		elif isinstance(child, ast.ClassDef):
			yield from _walk(child, f"{prefix}.{child.name}")
		elif isinstance(child, (ast.If, ast.Try)):
			yield from _walk(child, prefix)


def declared_methods():
	"""``{dotted name: [methods, ...]}``, one entry per definition of that name."""
	found = {}
	for path in _sources():
		try:
			tree = ast.parse(path.read_text(encoding="utf-8"))
		except (SyntaxError, UnicodeDecodeError):
			continue
		for name, methods in _walk(tree, _module_name(path)):
			found.setdefault(name, []).append(methods)
	return found


def endpoints():
	"""``{dotted name: declares_methods}`` for every whitelisted function in the app.

	A name defined twice counts as declared only if every definition declares.
	"""
	return {name: all(m is not None for m in defs) for name, defs in declared_methods().items()}


def undeclared():
	return sorted(name for name, declares in endpoints().items() if not declares)


def answers_get():
	"""Every endpoint a GET reaches: nothing declared, GET declared, or not readable here."""
	return sorted(
		name
		for name, defs in declared_methods().items()
		if any(m is None or "GET" in m or "?" in m for m in defs)
	)


def reads_as_change(name):
	"""True when the function's own name starts with one of :data:`CHANGE_WORDS`."""
	words = name.rsplit(".", 1)[-1].strip("_").split("_")
	if words and words[0] in NAME_PREFIXES:
		words = words[1:]
	return bool(words) and words[0] in CHANGE_WORDS


def read_baseline():
	text = BASELINE.read_text(encoding="utf-8")
	return [line.strip() for line in text.splitlines() if line.strip() and not line.startswith("#")]


class TestWhitelistMethodsRatchet(unittest.TestCase):
	def test_no_new_endpoint_without_declared_methods(self):
		baseline = set(read_baseline())
		new = [name for name in undeclared() if name not in baseline]
		self.assertEqual(
			new,
			[],
			"These whitelisted endpoints do not declare `methods=`. Declare "
			'`@frappe.whitelist(methods=["POST"])` (or `["GET"]` for a read) on new '
			"endpoints instead of adding them to the baseline:\n  " + "\n  ".join(new),
		)

	def test_baseline_has_no_stale_entries(self):
		current = set(undeclared())
		stale = [name for name in read_baseline() if name not in current]
		self.assertEqual(
			stale,
			[],
			"The baseline names endpoints that now declare `methods=` or no longer exist. "
			"Remove them from tests/data/whitelist_methods_baseline.txt (or regenerate it "
			"with `python -m erpnext_enhancements.tests.test_whitelist_methods_ratchet "
			"--write`):\n  " + "\n  ".join(stale),
		)

	def test_baseline_is_sorted_and_unique(self):
		lines = read_baseline()
		self.assertEqual(lines, sorted(set(lines)), "the baseline must be sorted with no duplicates")

	def test_baseline_is_exactly_its_ceiling(self):
		count = len(read_baseline())
		self.assertLessEqual(
			count,
			BASELINE_CEILING,
			f"The baseline has {count} endpoints and its ceiling is {BASELINE_CEILING}. The baseline "
			'does not grow: declare `methods=["POST"]` (or `["GET"]` for a read) on the new endpoint.',
		)
		self.assertEqual(
			count,
			BASELINE_CEILING,
			f"The baseline shrank to {count}. Lower BASELINE_CEILING in this file to {count}.",
		)

	def test_the_scan_reaches_the_endpoint_modules(self):
		"""An absence rule over an empty corpus passes forever; pin that the walk is real."""
		found = endpoints()
		self.assertGreater(len(found), 500, "endpoint walk collapsed -- the ratchet proves nothing")
		self.assertTrue(
			found.get("erpnext_enhancements.stripe_payments.core.api.refund_payment"),
			"refund_payment is expected to declare methods",
		)
		self.assertTrue(
			found.get("erpnext_enhancements.api.time_kiosk.log_time"),
			"log_time is expected to declare methods",
		)


class TestNamesThatReadAsChanges(unittest.TestCase):
	def test_an_endpoint_named_like_a_change_does_not_answer_get(self):
		flagged = [name for name in answers_get() if reads_as_change(name) and name not in GET_REVIEWED]
		self.assertEqual(
			flagged,
			[],
			"These endpoints answer GET and their names read as a change. Declare "
			'`methods=["POST"]` on each one. If one really is a read, add it to GET_REVIEWED in '
			"this file with the reason:\n  " + "\n  ".join(flagged),
		)

	def test_the_reviewed_list_has_no_stale_entries(self):
		current = set(answers_get())
		stale = sorted(name for name in GET_REVIEWED if name not in current or not reads_as_change(name))
		self.assertEqual(
			stale,
			[],
			"GET_REVIEWED names endpoints that no longer answer GET, no longer exist, or no longer "
			"need a reason. Remove them:\n  " + "\n  ".join(stale),
		)

	def test_every_reviewed_entry_has_a_reason(self):
		for name, reason in GET_REVIEWED.items():
			with self.subTest(name=name):
				self.assertGreater(len(reason.strip()), 20)

	def test_the_name_rule_itself(self):
		for name in (
			"create_invoice",
			"portal_confirm_card_payment",
			"bulk_update_rows",
			"send_sms",
			"_delete_row",
		):
			with self.subTest(name=name):
				self.assertTrue(reads_as_change("erpnext_enhancements.x." + name))
		for name in (
			"get_invoice",
			"list_rows",
			"preview_import",
			"portal_invoice_pdf",
			"who_is_out",
			"boot",
		):
			with self.subTest(name=name):
				self.assertFalse(reads_as_change("erpnext_enhancements.x." + name))

	def test_a_declared_get_is_seen(self):
		"""The name check covers declared GETs, not only the baseline."""
		get = set(answers_get())
		self.assertIn("erpnext_enhancements.form_kit.api.record_info", get)
		self.assertNotIn("erpnext_enhancements.stripe_payments.core.api.refund_payment", get)


def write_baseline():
	"""Rewrite the baseline from the code. Refuses to write a list longer than the ceiling."""
	names = undeclared()
	if len(names) > BASELINE_CEILING:
		added = sorted(set(names) - set(read_baseline()))
		print(
			f"Not written: {len(names)} endpoints declare no methods and the ceiling is "
			f"{BASELINE_CEILING}. The baseline does not grow. Declare `methods=[...]` on:\n  "
			+ "\n  ".join(added)
		)
		return 1
	BASELINE.parent.mkdir(parents=True, exist_ok=True)
	with open(BASELINE, "w", encoding="utf-8", newline="\n") as handle:
		handle.write(HEADER + "\n".join(names) + "\n")
	print(f"wrote {len(names)} endpoints to {BASELINE}")
	if len(names) < BASELINE_CEILING:
		print(f"Now lower BASELINE_CEILING in {Path(__file__).name} from {BASELINE_CEILING} to {len(names)}.")
	return 0


if __name__ == "__main__":
	if "--write" in sys.argv:
		sys.exit(write_baseline())
	unittest.main()
