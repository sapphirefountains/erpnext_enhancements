"""Bench-free unit tests for certificate issuance, revocation, verification and
recertification.

Stubs a minimal ``frappe`` (no site/bench) so ``training.certificates`` runs
under plain unittest. Installed in ``setUpModule`` (execution time), not at
import, so it never fools the bench-only suites' ``import frappe`` skip-guards —
same shape as ``test_training_gcs_media.py``.

The weighting of this suite follows where the damage is. None of these failures
announce themselves:

  * **a step that rolls back its cause.** Issuance does three things — mint the
    certificate, award badges, send the pass email — and only the first is the
    audit artefact. A badge module that raises, or a mail server that is down,
    must not take a completion somebody earned down with it. Tested by breaking
    each step in turn and asserting the certificate survives;
  * **a snapshot that is not one.** ``certificate_html`` is rendered once. If a
    later submit re-rendered it, a print-format edit would silently rewrite a
    document already in somebody's hands, and nothing would look wrong;
  * **a revocation that does not revoke.** A withdrawn completion whose
    certificate still reads Active, or whose assignment never reopens, leaves the
    system asserting somebody is trained when the company has decided otherwise;
  * **PII on a public route.** ``verify_certificate`` is shared with third
    parties. The keys it returns are pinned exactly, so adding a convenient
    ``full name`` later fails here rather than in somebody's inbox;
  * **a duplicate open assignment.** ``Training Assignment.validate`` throws on a
    second open row for the same pair, so a recertification sweep that inserts
    rather than re-dates would abort the nightly job for everybody after it.

Run: python -m unittest erpnext_enhancements.tests.test_training_certificates
"""

import calendar
import datetime
import inspect
import re
import sys
import types
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

certificates = None

STATE = {}

TODAY = "2026-08-01"
COURSE = "TRN-CRS-00001"
LEARNER = "tech@example.com"
LEARNER_NAME = "Nikolas Bradshaw"
LEARNER_EMAIL = "tech@example.com"
COMPLETION = "TRN-CMP-000001"

# What the real Training Certificate controller mints: upper case, no separators.
CODE_RE = re.compile(r"^[A-Z0-9]+$")


class _Dict(dict):
	def __getattr__(self, key):
		try:
			return self[key]
		except KeyError as exc:
			raise AttributeError(key) from exc

	def __setattr__(self, key, value):
		self[key] = value


# ------------------------------------------------------------------ date maths


def _to_date(value):
	if isinstance(value, datetime.datetime):
		return value.date()
	if isinstance(value, datetime.date):
		return value
	return datetime.date.fromisoformat(str(value)[:10])


def _today():
	return STATE["today"]


def _add_days(value, days):
	return (_to_date(value) + datetime.timedelta(days=int(days))).isoformat()


def _add_months(value, months):
	date = _to_date(value)
	index = date.month - 1 + int(months)
	year = date.year + index // 12
	month = index % 12 + 1
	day = min(date.day, calendar.monthrange(year, month)[1])
	return datetime.date(year, month, day).isoformat()


def _cint(value=0, *args, **kwargs):
	try:
		return int(float(value or 0))
	except (TypeError, ValueError):
		return 0


# ------------------------------------------------------------------ fake store


def _rows(doctype):
	return STATE["rows"].setdefault(doctype, {})


def _compare(value, target):
	try:
		return value < target
	except TypeError:
		return str(value) < str(target)


def _matches(row, filters):
	for key, condition in (filters or {}).items():
		value = row.get(key)
		if isinstance(condition, list | tuple):
			operator, target = condition[0], condition[1]
			if operator == "<":
				if value is None or not _compare(value, target):
					return False
			elif operator == ">":
				if value is None or not _compare(target, value):
					return False
			elif operator == "in":
				if value not in target:
					return False
			elif operator == "!=":
				if value == target:
					return False
			elif operator == "is":
				if target == "not set" and value:
					return False
				if target == "set" and not value:
					return False
			else:
				raise AssertionError(f"stub does not implement operator {operator!r}")
		elif value != condition:
			return False
	return True


def _find(doctype, filters):
	if isinstance(filters, str):
		row = _rows(doctype).get(filters)
		return [row] if row else []
	return [row for row in _rows(doctype).values() if _matches(row, filters)]


class _Doc(_Dict):
	"""Enough Document for this module: insert / submit / cancel / db_set."""

	def _store(self):
		_rows(self["doctype"])[self["name"]] = dict(self)

	def insert(self, ignore_permissions=False):
		doctype = self["doctype"]
		if STATE["insert_error"] == doctype:
			raise RuntimeError(f"insert refused for {doctype}")
		# What the *caller* wrote, before the stand-in controller fills the rest in.
		STATE["written_fields"].append(set(self) - {"doctype"})
		if doctype == "Training Certificate":
			_apply_certificate_controller(self)
		STATE["counter"] += 1
		self.setdefault("name", f"{doctype}-{STATE['counter']:05d}")
		self.setdefault("docstatus", 0)
		self._store()
		STATE["inserts"].append(dict(self))
		return self

	def submit(self):
		self["docstatus"] = 1
		self._store()
		return self

	def cancel(self):
		if STATE["cancel_error"]:
			raise RuntimeError("cancel refused")
		self["docstatus"] = 2
		self._store()
		return self

	def db_set(self, field, value=None, update_modified=True, **kwargs):
		updates = field if isinstance(field, dict) else {field: value}
		for key in updates:
			# Frappe raises on a column that is not there. Modelled, because the
			# column guards this module carries are only worth anything if a missing
			# column actually hurts in the test.
			if (self["doctype"], key) in STATE["absent_columns"]:
				raise RuntimeError(f"unknown column {self['doctype']}.{key}")
		self.update(updates)
		self._store()


def _apply_certificate_controller(doc):
	"""Stand-in for ``Training Certificate.validate``, which another module owns.

	Only the parts ``certificates.py`` actually leans on: the verification code and
	the snapshot taken from the completion. Mirrored from the real controller
	deliberately — if that seam moves, the assertions here about codes and holder
	names stop meaning what they say, and a stub that invented its own values would
	hide exactly that.
	"""
	completion = _rows("Training Completion").get(doc.get("completion")) or {}
	for field, value in (
		("status", "Valid"),
		("course", completion.get("course")),
		("course_title", completion.get("course_title_snapshot")),
		("holder_user", completion.get("user")),
		("employee", completion.get("employee")),
		("score_percent", completion.get("score_percent")),
	):
		if not doc.get(field):
			doc[field] = value
	if not doc.get("holder_name"):
		employee = _rows("Employee").get(doc.get("employee") or "") or {}
		learner = _rows("User").get(completion.get("user")) or {}
		doc["holder_name"] = employee.get("employee_name") or learner.get("full_name") or ""
	if not doc.get("verification_code"):
		STATE["code_counter"] += 1
		doc["verification_code"] = f"CODE{STATE['code_counter']:08d}"


def _reset_state():
	STATE.clear()
	STATE.update(
		{
			"today": TODAY,
			"counter": 0,
			"rows": {},
			"inserts": [],
			"written_fields": [],
			"errors": [],
			"commits": 0,
			"columns": {
				("Training Assignment", "completion"),
				("Training Completion", "certificate"),
			},
			"absent_columns": set(),
			"doctypes": {
				"Training Certificate",
				"Training Completion",
				"Training Assignment",
				"Training Course",
				"Print Format",
			},
			"settings": {
				"training_enabled": 1,
				"notifications_enabled": 0,
				"gamification_enabled": 0,
				"default_due_days": 14,
			},
			"print_html": "<div>rendered at issue</div>",
			"print_calls": 0,
			"print_error": False,
			"insert_error": None,
			"cancel_error": False,
			"code_counter": 0,
			"badges": [],
			"badge_error": False,
			"emails": [],
			"email_error": False,
		}
	)

	_rows("Training Course")[COURSE] = {
		"name": COURSE,
		"course_title": "Backwash Basics",
		"issues_certificate": 1,
		"certificate_valid_months": 12,
		"recertify_months": 0,
		"certificate_print_format": None,
		"status": "Published",
		"due_days": 14,
		"weight": "Required",
	}
	_rows("User")[LEARNER] = {
		"name": LEARNER,
		"full_name": LEARNER_NAME,
		"email": LEARNER_EMAIL,
		"enabled": 1,
	}
	_rows("Print Format")["Training Certificate - Sapphire"] = {
		"name": "Training Certificate - Sapphire"
	}


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._dict = _Dict
	frappe.flags = _Dict(in_migrate=False, in_install=False, in_patch=False, in_import=False)
	frappe.session = _Dict(user="manager@example.com")
	frappe.log_error = lambda *a, **k: STATE["errors"].append(
		k.get("title") or (a[1] if len(a) > 1 else (a[0] if a else ""))
	)
	frappe.get_traceback = lambda: "traceback"
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.logger = lambda *a, **k: types.SimpleNamespace(info=lambda *x, **y: None)
	frappe.clear_messages = lambda: None
	frappe.get_roles = lambda *a, **k: []
	frappe.__dict__["_"] = lambda s, *a, **k: _Translated(s)

	def _generate_hash(length=10, *args, **kwargs):
		STATE["code_counter"] += 1
		return (f"{STATE['code_counter']:x}" * length)[:length]

	frappe.generate_hash = _generate_hash

	def _get_doc(*args, **kwargs):
		if args and isinstance(args[0], dict):
			return _Doc(args[0])
		doctype, name = args[0], args[1]
		row = _rows(doctype).get(name)
		if row is None:
			raise KeyError(f"{doctype} {name} does not exist")
		return _Doc(dict(row))

	frappe.get_doc = _get_doc
	frappe.get_cached_doc = lambda *a, **k: _Dict(STATE["settings"])

	def _get_all(doctype, filters=None, fields=None, pluck=None, **kwargs):
		found = _find(doctype, filters)
		if pluck:
			return [row.get(pluck) for row in found]
		if fields:
			return [_Dict({f: row.get(f) for f in fields}) for row in found]
		return [_Dict({"name": row.get("name")}) for row in found]

	frappe.get_all = _get_all
	frappe.get_list = _get_all

	def _get_print(doctype=None, name=None, print_format=None, **kwargs):
		STATE["print_calls"] += 1
		if STATE["print_error"]:
			raise RuntimeError("print format is broken")
		return STATE["print_html"]

	frappe.get_print = _get_print

	def _db_exists(doctype, filters=None, **kwargs):
		if doctype == "DocType":
			return filters if filters in STATE["doctypes"] else None
		if filters is None:
			return None
		found = _find(doctype, filters)
		return found[0]["name"] if found else None

	def _db_get_value(doctype, filters=None, fieldname="name", as_dict=False, **kwargs):
		found = _find(doctype, filters)
		if not found:
			return None
		row = found[0]
		if isinstance(fieldname, list | tuple):
			values = {f: row.get(f) for f in fieldname}
			return _Dict(values) if as_dict else [values[f] for f in fieldname]
		return row.get(fieldname)

	def _db_set_value(doctype, name, field, value=None, update_modified=True, **kwargs):
		targets = _find(doctype, name)
		updates = field if isinstance(field, dict) else {field: value}
		for row in targets:
			row.update(updates)

	frappe.db = types.SimpleNamespace(
		exists=_db_exists,
		get_value=_db_get_value,
		set_value=_db_set_value,
		get_single_value=lambda doctype, field: STATE["settings"].get(field),
		has_column=lambda doctype, column: (doctype, column) in STATE["columns"],
		commit=lambda: STATE.__setitem__("commits", STATE["commits"] + 1),
	)

	utils = types.ModuleType("frappe.utils")
	utils.add_days = _add_days
	utils.add_months = _add_months
	utils.cint = _cint
	utils.flt = lambda v=0, *a, **k: float(v or 0)
	utils.cstr = lambda v="": "" if v is None else str(v)
	utils.getdate = _to_date
	utils.nowdate = _today
	utils.today = _today
	utils.now_datetime = lambda: datetime.datetime.fromisoformat(f"{STATE['today']}T12:00:00")
	utils.formatdate = lambda v=None, *a, **k: str(v or "")
	utils.get_url = lambda path="", *a, **k: f"https://example.com{path}"
	utils.escape_html = lambda v: (
		str(v or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	)
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Doc
	model.document = document
	frappe.model = model

	rate_limiter = types.ModuleType("frappe.rate_limiter")
	rate_limiter.rate_limit = lambda *a, **k: (lambda fn: fn)
	frappe.rate_limiter = rate_limiter

	sys.modules["frappe"] = frappe
	sys.modules["frappe.utils"] = utils
	sys.modules["frappe.model"] = model
	sys.modules["frappe.model.document"] = document
	sys.modules["frappe.rate_limiter"] = rate_limiter


class _Translated(str):
	"""``frappe._`` returns something ``.format()``-able; str is enough."""


def _install_sibling_stubs():
	"""Fakes for the two modules ``certificates`` imports lazily.

	Both are deliberate seams. ``gamification`` is owned by another part of Phase
	4 and may not exist at all on a given checkout; ``notifications`` is real but
	sends mail, which a unit test must not.
	"""
	gamification = types.ModuleType("erpnext_enhancements.training.gamification")

	def _award(completion):
		if STATE["badge_error"]:
			raise RuntimeError("leaderboard is down")
		STATE["badges"].append(completion.name)

	gamification.award_for_completion = _award
	sys.modules["erpnext_enhancements.training.gamification"] = gamification

	notifications = types.ModuleType("erpnext_enhancements.training.notifications")

	def _recipient(user):
		if STATE["email_error"]:
			raise RuntimeError("mail server unreachable")
		row = _rows("User").get(user)
		if not row:
			return None
		return _Dict(user=user, full_name=row["full_name"], email=row["email"])

	def _send(recipient, subject, body, reference_name=None):
		STATE["emails"].append((recipient.email, str(subject), body))
		return True

	notifications._recipient = _recipient
	notifications._send = _send
	sys.modules["erpnext_enhancements.training.notifications"] = notifications


def setUpModule():
	global certificates
	_install_frappe_stub()
	_reset_state()
	_install_sibling_stubs()
	from erpnext_enhancements.training import certificates as module

	certificates = module


# ------------------------------------------------------------------ fixtures


def _completion(**overrides):
	doc = _Doc(
		{
			"doctype": "Training Completion",
			"name": COMPLETION,
			"course": COURSE,
			"course_title_snapshot": "Backwash Basics",
			"course_version": f"{COURSE}-V1",
			"user": LEARNER,
			"employee": None,
			"customer": None,
			"status": "Valid",
			"docstatus": 1,
			"expires_on": None,
			"completed_on": f"{TODAY} 09:00:00",
			"score_percent": 92,
		}
	)
	doc.update(overrides)
	_rows("Training Completion")[doc["name"]] = dict(doc)
	return doc


def _certificates():
	return list(_rows("Training Certificate").values())


def _assignments():
	return list(_rows("Training Assignment").values())


def _open_assignment(**overrides):
	row = {
		"name": "TRN-ASG-000001",
		"doctype": "Training Assignment",
		"course": COURSE,
		"user": LEARNER,
		"status": "Not Started",
		"due_date": "2026-07-01",
		"assignment_source": "Manual",
	}
	row.update(overrides)
	_rows("Training Assignment")[row["name"]] = row
	return row


class _CertificateCase(unittest.TestCase):
	def setUp(self):
		_reset_state()
		_install_sibling_stubs()


# ------------------------------------------------------------------- issuance


class TestIssuance(_CertificateCase):
	def test_a_pass_produces_a_submitted_certificate(self):
		certificates.after_completion(_completion())
		issued = _certificates()
		self.assertEqual(len(issued), 1)
		self.assertEqual(issued[0]["docstatus"], 1)
		self.assertEqual(issued[0]["status"], "Valid")
		self.assertEqual(issued[0]["completion"], COMPLETION)

	def test_the_rendered_html_is_stored_on_the_certificate(self):
		"""The snapshot. Serving a live render would let a print-format edit next
		year rewrite a document somebody already holds."""
		certificates.after_completion(_completion())
		self.assertEqual(_certificates()[0]["certificate_html"], "<div>rendered at issue</div>")

	def test_it_is_rendered_exactly_once(self):
		certificates.after_completion(_completion())
		self.assertEqual(STATE["print_calls"], 1)

	def test_resubmitting_neither_re_renders_nor_mints_a_second_code(self):
		"""An amended completion re-submitting must not put two documents in
		circulation that both claim to be *the* certificate."""
		completion = _completion()
		certificates.after_completion(completion)
		first = dict(_certificates()[0])
		certificates.after_completion(completion)
		self.assertEqual(len(_certificates()), 1)
		self.assertEqual(STATE["print_calls"], 1)
		self.assertEqual(_certificates()[0]["verification_code"], first["verification_code"])

	def test_expiry_comes_from_certificate_valid_months(self):
		certificates.after_completion(_completion())
		self.assertEqual(_certificates()[0]["expires_on"], "2027-08-01")

	def test_expiry_falls_back_to_the_recertification_cycle(self):
		"""A certificate that outlives the training it attests to is worse than
		none, so the retraining interval is used when no explicit life is set."""
		_rows("Training Course")[COURSE].update(
			{"certificate_valid_months": 0, "recertify_months": 6}
		)
		certificates.after_completion(_completion())
		self.assertEqual(_certificates()[0]["expires_on"], "2027-02-01")

	def test_no_months_at_all_means_no_expiry(self):
		_rows("Training Course")[COURSE].update(
			{"certificate_valid_months": 0, "recertify_months": 0}
		)
		certificates.after_completion(_completion())
		self.assertIsNone(_certificates()[0]["expires_on"])

	def test_the_completion_gets_its_back_reference(self):
		"""Nothing else writes Training Completion.certificate — the issuer is the
		only party that knows which certificate closed which pass."""
		completion = _completion()
		certificates.after_completion(completion)
		self.assertEqual(completion["certificate"], _certificates()[0]["name"])

	def test_a_bench_without_the_back_reference_column_issues_cleanly(self):
		"""doc_events fire on benches that have not migrated the Phase 4 schema yet.

		The certificate survives either way — it is submitted before the
		back-reference is written — so what the guard actually buys is the rest of
		issuance: without it the write raises, issuance reports failure, and the
		learner's email arrives with no link to the certificate that does exist.
		"""
		STATE["settings"]["notifications_enabled"] = 1
		STATE["columns"].discard(("Training Completion", "certificate"))
		STATE["absent_columns"].add(("Training Completion", "certificate"))
		certificates.after_completion(_completion())
		self.assertEqual(len(_certificates()), 1)
		self.assertEqual(STATE["errors"], [])
		self.assertIn("/training_certificate?certificate=", STATE["emails"][0][2])

	def test_the_expiry_is_mirrored_onto_the_completion(self):
		"""The recertification sweep reads completions, so the date has to be there
		whether or not the course issues a certificate."""
		completion = _completion()
		certificates.after_completion(completion)
		self.assertEqual(completion["expires_on"], "2027-08-01")

	def test_a_course_that_issues_none_issues_none(self):
		_rows("Training Course")[COURSE]["issues_certificate"] = 0
		certificates.after_completion(_completion())
		self.assertEqual(_certificates(), [])

	def test_it_writes_only_what_the_issuer_knows(self):
		"""The issuer says which completion and how long the company stands behind
		it; the certificate's own controller snapshots the rest. Two modules writing
		one field is how the two end up disagreeing."""
		certificates.after_completion(_completion())
		self.assertEqual(
			STATE["written_fields"][0],
			{"completion", "course", "issued_on", "expires_on", "print_format"},
		)


class TestVerificationCodes(_CertificateCase):
	def test_the_code_is_minted_by_the_doctype_not_here(self):
		"""One mechanism per field. A second code generator in this module would put
		two formats in one column and quietly stop half of them verifying."""
		self.assertNotIn("generate_hash", inspect.getsource(certificates))
		certificates.after_completion(_completion())
		self.assertRegex(_certificates()[0]["verification_code"], CODE_RE)

	def test_typed_variations_still_find_the_certificate(self):
		"""Somebody reading a code off paper adds spaces and dashes and types it in
		lower case. None of that may read as not-found."""
		certificates.after_completion(_completion())
		stored = _certificates()[0]["verification_code"]
		for typed in (stored, stored.lower(), f" {stored[:4]}-{stored[4:]} ", f"{stored[:6]} {stored[6:]}"):
			self.assertTrue(certificates.verify_certificate(typed)["valid"], typed)

	def test_an_empty_code_is_not_a_lookup(self):
		self.assertEqual(certificates._normalize_code(""), "")
		self.assertEqual(certificates._normalize_code(None), "")
		self.assertEqual(certificates._normalize_code("  -- "), "")


# -------------------------------------------- a consequence never undoes its cause


class TestNothingRollsBackACompletion(_CertificateCase):
	"""Every step of issuance is independently guarded, and this is why.

	The completion is the audit record. The certificate, the badge and the email
	are consequences of it. A consequence that can take its cause down means a
	learner who did the work has no record of having done it, because a mail
	server was down.
	"""

	def test_a_failing_badge_award_still_leaves_the_certificate(self):
		STATE["settings"]["gamification_enabled"] = 1
		STATE["badge_error"] = True
		certificates.after_completion(_completion())
		self.assertEqual(len(_certificates()), 1)
		self.assertTrue(STATE["errors"])

	def test_gamification_not_being_built_yet_is_not_fatal(self):
		STATE["settings"]["gamification_enabled"] = 1
		sys.modules.pop("erpnext_enhancements.training.gamification", None)
		try:
			certificates.after_completion(_completion())
		finally:
			_install_sibling_stubs()
		self.assertEqual(len(_certificates()), 1)

	def test_a_failing_email_still_leaves_the_certificate(self):
		STATE["settings"]["notifications_enabled"] = 1
		STATE["email_error"] = True
		certificates.after_completion(_completion())
		self.assertEqual(len(_certificates()), 1)

	def test_a_broken_print_format_still_issues_a_usable_certificate(self):
		"""An ugly layout is recoverable. A completion with no artefact is not."""
		STATE["print_error"] = True
		certificates.after_completion(_completion())
		html = _certificates()[0]["certificate_html"]
		self.assertIn(LEARNER_NAME, html)
		self.assertIn(_certificates()[0]["verification_code"], html)

	def test_a_missing_print_format_falls_back_rather_than_failing(self):
		_rows("Print Format").clear()
		certificates.after_completion(_completion())
		self.assertIn("Certificate of Completion", _certificates()[0]["certificate_html"])

	def test_a_failing_certificate_does_not_stop_badges_or_email(self):
		"""The reverse direction. The three steps are siblings, not a chain."""
		STATE["settings"].update({"gamification_enabled": 1, "notifications_enabled": 1})
		STATE["insert_error"] = "Training Certificate"
		certificates.after_completion(_completion())
		self.assertEqual(_certificates(), [])
		self.assertEqual(STATE["badges"], [COMPLETION])
		self.assertEqual(len(STATE["emails"]), 1)

	def test_the_pass_email_is_gated_on_the_notifications_switch(self):
		certificates.after_completion(_completion())
		self.assertEqual(STATE["emails"], [])

	def test_badges_are_gated_on_the_gamification_switch(self):
		certificates.after_completion(_completion())
		self.assertEqual(STATE["badges"], [])


class TestMaintenanceContext(_CertificateCase):
	"""Nothing acts during migrate/install/patch/import — house rule, and here it
	also stops a fixture import from mailing everybody a certificate."""

	def tearDown(self):
		for flag in ("in_migrate", "in_install", "in_patch", "in_import"):
			certificates.frappe.flags[flag] = False

	def test_issuance_is_skipped(self):
		certificates.frappe.flags.in_migrate = True
		certificates.after_completion(_completion())
		self.assertEqual(_certificates(), [])

	def test_revocation_is_skipped(self):
		certificates.frappe.flags.in_patch = True
		certificates.on_revoke(_completion())
		self.assertEqual(_assignments(), [])

	def test_the_daily_sweep_is_skipped(self):
		certificates.frappe.flags.in_install = True
		_completion(expires_on="2026-01-01")
		certificates.expire_and_recertify()
		self.assertEqual(_rows("Training Completion")[COMPLETION]["status"], "Valid")


# ----------------------------------------------------------------- revocation


class TestRevocation(_CertificateCase):
	def setUp(self):
		super().setUp()
		self.completion = _completion()
		certificates.after_completion(self.completion)
		self.certificate = _certificates()[0]["name"]

	def test_the_certificate_is_cancelled_and_marked_revoked(self):
		certificates.on_revoke(self.completion)
		row = _rows("Training Certificate")[self.certificate]
		self.assertEqual(row["docstatus"], 2)
		self.assertEqual(row["status"], "Revoked")
		self.assertEqual(row["expires_on"], TODAY)

	def test_a_refused_cancel_still_marks_it_revoked(self):
		"""The status write is unconditional on purpose: a cancel can fail for
		reasons unrelated to the decision, and a document left reading Active is
		the one outcome that must not happen."""
		STATE["cancel_error"] = True
		certificates.on_revoke(self.completion)
		self.assertEqual(_rows("Training Certificate")[self.certificate]["status"], "Revoked")

	def test_the_assignment_is_reopened(self):
		"""A withdrawal that leaves no assignment behind removes the obligation
		instead of restoring it."""
		_open_assignment(status="Completed", completion=COMPLETION)
		certificates.on_revoke(self.completion)
		row = _assignments()[0]
		self.assertEqual(row["status"], "Not Started")
		self.assertEqual(row["due_date"], "2026-08-15")

	def test_an_already_open_assignment_is_left_alone(self):
		"""Two open rows for one pair is exactly what Training Assignment.validate
		refuses; creating one here would make the row unsaveable ever after."""
		_open_assignment(status="In Progress", due_date="2026-09-30")
		certificates.on_revoke(self.completion)
		self.assertEqual(len(_assignments()), 1)
		self.assertEqual(_assignments()[0]["status"], "In Progress")
		self.assertEqual(_assignments()[0]["due_date"], "2026-09-30")

	def test_a_missing_assignment_is_raised_fresh(self):
		certificates.on_revoke(self.completion)
		self.assertEqual(len(_assignments()), 1)
		self.assertEqual(_assignments()[0]["status"], "Not Started")
		self.assertEqual(_assignments()[0]["assignment_source"], "Recertification")


# --------------------------------------------------------------------- verify


EXPECTED_KEYS = {"valid", "status", "course_title", "holder_initials", "issued_on", "expires_on"}


class TestPublicVerification(_CertificateCase):
	def setUp(self):
		super().setUp()
		certificates.after_completion(_completion())
		self.certificate = _certificates()[0]
		self.code = self.certificate["verification_code"]

	def test_a_valid_certificate_verifies(self):
		answer = certificates.verify_certificate(self.code)
		self.assertTrue(answer["valid"])
		self.assertEqual(answer["status"], "Valid")
		self.assertEqual(answer["course_title"], "Backwash Basics")

	def test_it_returns_exactly_the_agreed_keys(self):
		"""Pinned so that adding a convenient extra field fails here rather than in
		a third party's inbox."""
		self.assertEqual(set(certificates.verify_certificate(self.code)), EXPECTED_KEYS)

	def test_it_discloses_initials_and_nothing_more(self):
		answer = certificates.verify_certificate(self.code)
		self.assertEqual(answer["holder_initials"], "N.B.")
		blob = " ".join(str(v) for v in answer.values())
		self.assertNotIn(LEARNER_NAME, blob)
		self.assertNotIn(LEARNER_EMAIL, blob)
		self.assertNotIn(COMPLETION, blob)

	def test_initials_handle_a_single_name(self):
		self.assertEqual(certificates._initials("Cher"), "C.")
		self.assertEqual(certificates._initials(""), "")
		self.assertEqual(certificates._initials(None), "")

	def test_an_unknown_code_is_simply_invalid(self):
		answer = certificates.verify_certificate("ABCD-EFGH-JKMN-PQRS")
		self.assertFalse(answer["valid"])
		self.assertIsNone(answer["status"])
		self.assertEqual(set(answer), EXPECTED_KEYS)

	def test_garbage_does_not_reach_the_database(self):
		self.assertFalse(certificates.verify_certificate("<script>")["valid"])
		self.assertFalse(certificates.verify_certificate(None)["valid"])

	def test_a_lapsed_certificate_reads_expired_before_the_sweep_runs(self):
		"""The nightly job writes the status down, but a verifier asking at 09:00
		about a certificate that lapsed at midnight must not be told it is valid."""
		self.certificate["expires_on"] = "2026-07-31"
		answer = certificates.verify_certificate(self.code)
		self.assertEqual(answer["status"], "Expired")
		self.assertFalse(answer["valid"])

	def test_a_revoked_certificate_says_revoked_not_not_found(self):
		"""Hiding a withdrawal behind a not-found lets the holder argue the
		verifier is broken — the opposite of what revocation is for."""
		certificates.on_revoke(_rows("Training Completion")[COMPLETION] and _completion())
		answer = certificates.verify_certificate(self.code)
		self.assertEqual(answer["status"], "Revoked")
		self.assertFalse(answer["valid"])

	def test_a_draft_certificate_is_not_valid(self):
		self.certificate["docstatus"] = 0
		self.assertFalse(certificates.verify_certificate(self.code)["valid"])


# ------------------------------------------------------- expiry / recertification


class TestRecertification(_CertificateCase):
	def test_a_lapsed_completion_is_expired_and_reassigned(self):
		_completion(expires_on="2026-07-01")
		certificates.expire_and_recertify()
		self.assertEqual(_rows("Training Completion")[COMPLETION]["status"], "Expired")
		self.assertEqual(len(_assignments()), 1)
		self.assertEqual(_assignments()[0]["assignment_source"], "Recertification")
		self.assertEqual(_assignments()[0]["due_date"], "2026-08-15")

	def test_an_open_assignment_is_re_dated_never_duplicated(self):
		"""The collision api.training_author._raise_retake handles the same way:
		Training Assignment.validate throws on a second open row, which would abort
		the whole nightly sweep for everybody after this learner."""
		_open_assignment(status="Overdue", due_date="2026-07-01")
		_completion(expires_on="2026-07-01")
		certificates.expire_and_recertify()
		self.assertEqual(len(_assignments()), 1)
		self.assertEqual(_assignments()[0]["due_date"], "2026-08-15")
		self.assertEqual(_assignments()[0]["assignment_source"], "Recertification")
		self.assertEqual(STATE["inserts"], [])

	def test_a_completion_still_in_date_is_left_alone(self):
		_completion(expires_on="2026-12-31")
		certificates.expire_and_recertify()
		self.assertEqual(_rows("Training Completion")[COMPLETION]["status"], "Valid")
		self.assertEqual(_assignments(), [])

	def test_a_completion_with_no_expiry_never_lapses(self):
		_completion(expires_on=None)
		certificates.expire_and_recertify()
		self.assertEqual(_rows("Training Completion")[COMPLETION]["status"], "Valid")

	def test_a_retired_course_expires_but_chases_nobody(self):
		_rows("Training Course")[COURSE]["status"] = "Retired"
		_completion(expires_on="2026-07-01")
		certificates.expire_and_recertify()
		self.assertEqual(_rows("Training Completion")[COMPLETION]["status"], "Expired")
		self.assertEqual(_assignments(), [])

	def test_a_departed_learner_expires_but_is_not_reassigned(self):
		"""The record stays honest; reassigning training to a disabled account only
		produces an overdue row nobody can ever close."""
		_rows("User")[LEARNER]["enabled"] = 0
		_completion(expires_on="2026-07-01")
		certificates.expire_and_recertify()
		self.assertEqual(_rows("Training Completion")[COMPLETION]["status"], "Expired")
		self.assertEqual(_assignments(), [])

	def test_lapsed_certificates_are_marked_expired(self):
		certificates.after_completion(_completion())
		name = _certificates()[0]["name"]
		_rows("Training Certificate")[name]["expires_on"] = "2026-07-01"
		certificates.expire_and_recertify()
		self.assertEqual(_rows("Training Certificate")[name]["status"], "Expired")

	def test_a_dormant_module_does_nothing(self):
		"""Ships dormant, and the daily job has to honour that or a fresh install
		starts emailing before anybody has configured it."""
		STATE["settings"]["training_enabled"] = 0
		_completion(expires_on="2026-07-01")
		certificates.expire_and_recertify()
		self.assertEqual(_rows("Training Completion")[COMPLETION]["status"], "Valid")


if __name__ == "__main__":
	unittest.main()
