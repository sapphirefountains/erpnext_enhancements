# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Bench-free tests: the client's sign-off by email (v1.614.0).

Nik, 2026-10-13: "will this signature requirement allow the tech to send it via email to the
client to sign off? the clients aren't always available to sign in person". The technician (at
Finish) or a reviewer (from the visit form) emails the client a link; the client reads the
service report on ``/visit-sign-off``, types their name, signs and confirms; the signature lands
on the visit, approved or not. ``sapphire_maintenance/sign_off.py`` holds all of it. Pinned here:

* the token: 256 bits, base64url accepted by its own shape guard, only its SHA-256 stored, never
  in a stored row; a resend mints a new one and supersedes the live link; links expire after
  fourteen days and sign once;
* signing: the token is the only authority; unknown, malformed, expired, superseded and canceled
  links get the same words; the evidence is assigned field by field and the signature reaches
  the visit through ``db_set`` with the method "By email"; a second Sign answers "already";
* sending: who may (the technician, the crew, the reviewer roles, write permission), the address
  checked as exactly one, and Finish's send never raising or undoing the Finish;
* the shape of the thing: no guest code calls ``.update(``, the guest endpoint is POST-only and
  rate limited with no parameter named ``sid``, the www controller's underscore filename, the
  page's CSRF token, the email in the design system's shell, the print format and the doctype.

The technician-signature exemption for visits done before signatures existed is tested with the
record's own controller in ``test_visit_finished_for_review`` (``TestVisitsFromBeforeSignatures``).

Stubs a minimal ``frappe`` with an in-memory table in ``setUpModule``.

Run: python -m unittest erpnext_enhancements.tests.test_visit_sign_off
"""

import ast
import base64
import datetime
import hashlib
import json
import re
import struct
import subprocess
import sys
import types
import unittest
import zlib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

APP = REPO_ROOT / "erpnext_enhancements"
MODULE = "erpnext_enhancements.sapphire_maintenance.sign_off"
SIGN_OFF_PY = APP / "sapphire_maintenance" / "sign_off.py"
PAGE_PY = APP / "www" / "visit_sign_off.py"
PAGE_HTML = APP / "www" / "visit-sign-off.html"
PAGE_JS = APP / "public" / "js" / "visit_sign_off" / "visit_sign_off.js"
EMAIL = APP / "templates" / "emails" / "sapphire_maintenance" / "visit_sign_off_request.html"
DOCTYPE_JSON = APP / "sapphire_maintenance" / "doctype" / "visit_sign_off_request" / "visit_sign_off_request.json"
RECORD_JSON = (
	APP / "sapphire_maintenance" / "doctype" / "sapphire_maintenance_record" / "sapphire_maintenance_record.json"
)
RECORD_PY = APP / "sapphire_maintenance" / "doctype" / "sapphire_maintenance_record" / "sapphire_maintenance_record.py"
STUBBED = ("frappe", "frappe.utils", "frappe.rate_limiter", "erpnext_enhancements.email_style", MODULE)

DT = datetime.datetime
NOW = DT(2026, 10, 13, 15, 0)
STATE = {}
_saved = {}
so = None


class _Dict(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


class _Thrown(Exception):
	pass


class _PermissionError(Exception):
	pass


class _Session:
	@property
	def user(self):
		return STATE["user"]


class _Local:
	#: For utils.deploy.get_deploy_version, which falls back to the app version without it.
	sites_path = "no-such-sites-dir"

	@property
	def request_ip(self):
		return STATE["ip"]


class _Doc:
	"""A row of the in-memory table, with the Document calls sign_off.py makes."""

	def __init__(self, doctype, row, table):
		self.__dict__["_doctype"] = doctype
		self.__dict__["_row"] = row
		self.__dict__["_table"] = table
		self.__dict__["flags"] = _Dict()

	def __getattr__(self, key):
		if key == "doctype":
			return self._doctype
		return self._row.get(key)

	def __setattr__(self, key, value):
		self._row[key] = value

	def get(self, key, default=None):
		return self._row.get(key, default)

	def insert(self, ignore_permissions=False):
		assert ignore_permissions, "a guest or a technician without create permission inserts it"
		STATE["seq"] += 1
		self._row["name"] = f"VSR-{STATE['seq']:05d}"
		self._row["creation"] = STATE["seq"]
		self._table[self._row["name"]] = self._row
		return self

	def save(self, ignore_permissions=False):
		assert ignore_permissions
		self._table[self._row["name"]] = self._row
		STATE["saves"].append(dict(self._row))
		return self

	def db_set(self, values, value=None, update_modified=True):
		assert isinstance(values, dict)
		STATE["db_set"].append((self._row["name"], dict(values)))
		self._row.update(values)

	def add_comment(self, kind, text):
		STATE["comments"].append((self._row.get("name"), text))

	def check_permission(self, ptype):
		if self._row.get("name") in STATE["unreadable"]:
			raise _PermissionError(ptype)


def _match(row, filters):
	for key, want in (filters or {}).items():
		have = row.get(key)
		if isinstance(want, list):
			op, value = want
			if op == "in" and have not in value:
				return False
			if op == "<" and not (have is not None and have < value):
				return False
		elif have != want:
			return False
	return True


def _table(doctype):
	return STATE["requests"] if doctype == "Visit Sign-Off Request" else STATE["visits"]


def _get_value(doctype, filters, fieldname=None, as_dict=False, for_update=False, **kwargs):
	if doctype == "Company":
		return STATE["company_email"]
	if doctype in ("Project", "Customer", "User", "Sapphire Maintenance Contract", "Project Contract", "Contact"):
		return STATE["lookups"].get((doctype, filters, fieldname))
	table = _table(doctype)
	if isinstance(filters, dict):
		rows = [r for r in table.values() if _match(r, filters)]
		row = rows[0] if rows else None
	else:
		row = table.get(filters)
	if for_update:
		STATE["locked"].append((doctype, row.get("name") if row else filters))
	if row is None:
		return None
	if isinstance(fieldname, (list, tuple)):
		values = _Dict({f: row.get(f) for f in fieldname})
		return values if as_dict else tuple(values.values())
	return row.get(fieldname)


def _set_value(doctype, name, field, value=None, update_modified=True):
	values = field if isinstance(field, dict) else {field: value}
	_table(doctype)[name].update(values)
	STATE["set_values"].append((doctype, name, dict(values)))


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, limit=None, **kwargs):
	rows = [r for r in _table(doctype).values() if _match(r, filters)]
	if order_by == "creation desc":
		rows.sort(key=lambda r: r.get("creation") or 0, reverse=True)
	if limit:
		rows = rows[:limit]
	if pluck:
		return [r.get(pluck) for r in rows]
	return [_Dict({f: r.get(f) for f in fields}) for r in rows]


def _get_doc(doctype, name):
	return _Doc(doctype, _table(doctype)[name], _table(doctype))


def _sendmail(**kwargs):
	if STATE["sendmail_raises"]:
		raise RuntimeError("smtp down")
	STATE["mail"].append(kwargs)


def _has_permission(doctype, ptype="read", doc=None, user=None):
	return (user, ptype) in STATE["perms"] or (ptype == "read" and (user, "write") in STATE["perms"])


def _reset():
	STATE.clear()
	STATE.update(
		{
			"now": NOW,
			"seq": 0,
			"requests": {},
			"visits": {
				"PRJ-1-2026-V001": {
					"name": "PRJ-1-2026-V001",
					"docstatus": 0,
					# Finished: a link goes only once the visit has left Draft (v1.614.0 review).
					"workflow_state": "Pending Review",
					"project": "PRJ-1",
					"customer": "CUST-1",
					"maintenance_contract": "SMC-1",
					"technician": "austin@example.com",
					"crew": [_Dict(user="korben@example.com")],
					"visit_date": "2026-10-13",
					"client_sign_off": None,
				}
			},
			"lookups": {
				("Project", "PRJ-1", "project_name"): "Myers Mortuary",
				("Sapphire Maintenance Contract", "SMC-1", "project_contract"): "PC-1",
				("Project Contract", "PC-1", "contact_email"): "Owner@Myers.example.com",
				("User", "austin@example.com", "full_name"): "Austin Tech",
			},
			"company_email": "office@sapphire.example.com",
			"mail": [],
			"sendmail_raises": False,
			"comments": [],
			"db_set": [],
			"set_values": [],
			"saves": [],
			"locked": [],
			"errors": [],
			"savepoints": [],
			"rollbacks": [],
			"user": "austin@example.com",
			"roles": {"austin@example.com": ["Maintenance User"], "lisa@example.com": ["Maintenance Reviewer"]},
			"perms": {("austin@example.com", "write"), ("lisa@example.com", "read"), ("bob@example.com", "read")},
			"unreadable": set(),
			"ip": "203.0.113.9",
			"ua": "Mozilla/5.0 (iPhone)",
		}
	)


def _install_stub():
	frappe = types.ModuleType("frappe")
	frappe.__dict__["_"] = lambda text, *a, **k: text
	frappe._dict = _Dict
	frappe.PermissionError = _PermissionError

	def whitelist(allow_guest=False, methods=None, **kwargs):
		def wrap(fn):
			fn._whitelist = {"allow_guest": allow_guest, "methods": methods}
			return fn

		return wrap

	frappe.whitelist = whitelist

	def _throw(message, exc=None, title=None, **kwargs):
		raise (exc or _Thrown)(message)

	frappe.throw = _throw
	frappe.log_error = lambda *a, **k: STATE["errors"].append(k.get("title"))
	frappe.get_traceback = lambda: "traceback"
	frappe.DoesNotExistError = _Thrown
	frappe.db = types.SimpleNamespace(
		get_value=_get_value,
		set_value=_set_value,
		exists=lambda doctype, name: name in _table(doctype),
		has_column=lambda doctype, column: True,
		savepoint=lambda name: STATE["savepoints"].append(name),
		rollback=lambda save_point=None: STATE["rollbacks"].append(save_point),
	)
	frappe.get_all = _get_all
	frappe.get_doc = _get_doc
	frappe.new_doc = lambda doctype: _Doc(doctype, {"doctype": doctype}, _table(doctype))
	frappe.sendmail = _sendmail
	frappe.has_permission = _has_permission
	frappe.get_roles = lambda user=None: STATE["roles"].get(user, [])
	frappe.session = _Session()
	frappe.local = _Local()
	frappe.get_request_header = lambda name: STATE["ua"] if name == "User-Agent" else None
	frappe.defaults = types.SimpleNamespace(get_global_default=lambda key: "Sapphire Fountains")

	utils = types.ModuleType("frappe.utils")
	utils.add_to_date = lambda value, days=0, **k: value + datetime.timedelta(days=days)
	utils.cint = lambda v: int(v or 0)
	utils.escape_html = lambda s: str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
	utils.get_datetime = lambda v: v if isinstance(v, DT) else DT.fromisoformat(str(v))
	utils.get_url = lambda path="": "https://erp.example.com" + path
	utils.getdate = lambda v=None: (
		STATE["now"].date() if v is None else v if isinstance(v, datetime.date) and not isinstance(v, DT) else DT.fromisoformat(str(v)[:10]).date()
	)
	utils.now_datetime = lambda: STATE["now"]
	utils.nowdate = lambda: str(STATE["now"].date())
	utils.format_datetime = lambda v, *a: f"<{v}>"
	utils.formatdate = lambda v, *a: f"[{v}]"
	utils.validate_email_address = lambda e, throw=False: e if re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]+", e) else ""
	frappe.utils = utils

	rate_limiter = types.ModuleType("frappe.rate_limiter")

	def rate_limit(**kwargs):
		def wrap(fn):
			fn._rate_limit = kwargs
			return fn

		return wrap

	rate_limiter.rate_limit = rate_limit
	frappe.rate_limiter = rate_limiter

	email_style = types.ModuleType("erpnext_enhancements.email_style")
	email_style.render = lambda template, context=None, **kw: json.dumps(
		{"template": template, "context": context, "chrome": kw}, default=str
	)

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.rate_limiter": rate_limiter,
			"erpnext_enhancements.email_style": email_style,
		}
	)


def setUpModule():
	global so
	for name in STUBBED:
		_saved[name] = sys.modules.pop(name, None)
	import erpnext_enhancements as app
	import erpnext_enhancements.sapphire_maintenance as package

	_saved["_email_style_attr"] = vars(app).get("email_style")
	if "sign_off" in vars(package):
		delattr(package, "sign_off")
	_install_stub()
	app.email_style = sys.modules["erpnext_enhancements.email_style"]
	from erpnext_enhancements.sapphire_maintenance import sign_off

	so = sign_off


def tearDownModule():
	import erpnext_enhancements as app
	import erpnext_enhancements.sapphire_maintenance as package

	if "sign_off" in vars(package):
		delattr(package, "sign_off")
	if _saved.get("_email_style_attr") is not None:
		app.email_style = _saved["_email_style_attr"]
	elif "email_style" in vars(app):
		delattr(app, "email_style")
	for name in STUBBED:
		sys.modules.pop(name, None)
		if _saved.get(name) is not None:
			sys.modules[name] = _saved[name]


def _png(width=40, height=20):
	"""A real PNG, built by hand so the test needs no Pillow."""

	def chunk(kind, data):
		return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

	raw = b"".join(b"\x00" + b"\x00\x00\x00\x00" * width for _ in range(height))
	body = (
		chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
		+ chunk(b"IDAT", zlib.compress(raw))
		+ chunk(b"IEND", b"")
	)
	return "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + body).decode("ascii")


def _visit():
	return _get_doc("Sapphire Maintenance Record", "PRJ-1-2026-V001")


def _send(email="owner@myers.example.com"):
	"""Send a link as the technician; returns (status, the token in the email)."""
	status = so.send_request(_visit(), email)
	context = json.loads(STATE["mail"][-1]["message"])["context"]
	return status, context["sign_off_url"].split("ref=", 1)[1]


def _sign(token, **overrides):
	args = {"ref": token, "signed_name": "Jane Owner", "signature_image": _png(), "confirmed": 1}
	args.update(overrides)
	return so.sign_visit(**args)


# ---------------------------------------------------------------------------


class TestTheToken(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_guard_accepts_what_the_generator_makes(self):
		"""base64url has ``-`` and ``_``: a guard without them refuses most real tokens, sometimes."""
		for _ in range(500):
			token, digest = so.mint_token()
			self.assertEqual(so.hash_token(token), digest)
		self.assertGreaterEqual(so.TOKEN_BYTES * 8, 256)
		self.assertEqual(len({so.mint_token()[0] for _ in range(300)}), 300)

	def test_a_malformed_token_never_reaches_the_database(self):
		for bad in (None, "", "short", "x" * 200, "has space", "semi;colon", "../../etc", "%2e%2e"):
			with self.subTest(bad=bad):
				self.assertIsNone(so.hash_token(bad))
				self.assertIsNone(so.resolve(bad))

	def test_only_the_hash_is_stored(self):
		_status, token = _send()
		row = next(iter(STATE["requests"].values()))
		self.assertEqual(row["token_hash"], hashlib.sha256(token.encode()).hexdigest())
		self.assertNotIn(token, json.dumps(STATE["requests"], default=str))
		self.assertNotIn(token, json.dumps(STATE["comments"]))
		self.assertNotIn("token", json.dumps(_status), "the status handed to the browser carries neither")

	def test_the_link_names_the_page_and_a_ref_never_sid(self):
		"""``Session.__init__`` pops a parameter named ``sid`` during auth."""
		self.assertEqual(so.sign_off_url("T" * 43), "https://erp.example.com/visit-sign-off?ref=" + "T" * 43)
		tree = ast.parse(SIGN_OFF_PY.read_text(encoding="utf-8"))
		params = {a.arg for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) for a in node.args.args}
		self.assertNotIn("sid", params)


class TestSending(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_a_link_is_sent_for_fourteen_days(self):
		status, _token = _send("Owner@Myers.Example.com")
		row = STATE["requests"][status["request"]]
		self.assertEqual(
			(row["status"], row["email"], row["visit"], row["customer"], row["sent_by"]),
			("Sent", "owner@myers.example.com", "PRJ-1-2026-V001", "CUST-1", "austin@example.com"),
		)
		self.assertEqual(row["expires_on"] - row["sent_on"], datetime.timedelta(days=14))
		self.assertEqual(so.LINK_DAYS, 14)

	def test_the_email_is_the_shell_on_the_service_pillar_and_names_the_visit(self):
		_send()
		mail = STATE["mail"][-1]
		self.assertEqual(mail["recipients"], ["owner@myers.example.com"])
		self.assertEqual(mail["subject"], "Please sign off today's service visit — Myers Mortuary")
		self.assertEqual(
			(mail["reference_doctype"], mail["reference_name"]), ("Sapphire Maintenance Record", "PRJ-1-2026-V001")
		)
		self.assertEqual(mail["reply_to"], "office@sapphire.example.com")
		body = json.loads(mail["message"])
		self.assertEqual(body["template"], so.TEMPLATE)
		self.assertEqual(body["chrome"]["pillar"], "service")
		self.assertTrue(body["chrome"]["tagline"])
		self.assertEqual(body["context"]["technician"], "Austin Tech")

	def test_an_earlier_visit_is_not_called_todays(self):
		STATE["visits"]["PRJ-1-2026-V001"]["visit_date"] = "2026-10-10"
		_send()
		self.assertEqual(STATE["mail"][-1]["subject"], "Please sign off your service visit on [2026-10-10] — Myers Mortuary")

	def test_a_resend_supersedes_the_live_link_and_mints_a_new_token(self):
		first, old_token = _send()
		second, new_token = _send()
		self.assertNotEqual(old_token, new_token)
		self.assertEqual(STATE["requests"][first["request"]]["status"], "Superseded")
		self.assertEqual(STATE["requests"][second["request"]]["status"], "Sent")
		self.assertEqual(_sign(old_token), {"ok": False, "error": so.UNAVAILABLE})
		self.assertEqual(_sign(new_token), {"ok": True})

	def test_one_address_exactly(self):
		for bad in ("", "nobody", "a@b.com, evil@x.com", "a@b.com;c@d.com", "Jane <a@b.com>", "a @b.com"):
			with self.subTest(bad=bad), self.assertRaises(_Thrown):
				so.send_request(_visit(), bad)
		self.assertEqual(STATE["mail"], [])

	def test_the_prefilled_address_is_the_agreements_contact(self):
		self.assertEqual(so.default_email(_visit()), "owner@myers.example.com")

	def test_then_the_last_address_this_client_was_sent_a_link_at(self):
		_send("manager@myers.example.com")
		self.assertEqual(so.default_email(_visit()), "manager@myers.example.com")


class TestWhoMaySend(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_the_technician_the_crew_and_the_reviewers(self):
		visit = _visit()
		for user in ("austin@example.com", "lisa@example.com", "korben@example.com"):
			if user == "korben@example.com":
				STATE["perms"].add((user, "read"))
			with self.subTest(user=user):
				self.assertTrue(so.can_send(visit, user))

	def test_not_someone_who_can_only_read_it(self):
		self.assertFalse(so.can_send(_visit(), "bob@example.com"))
		self.assertFalse(so.can_send(_visit(), "Guest"))

	def test_not_once_signed_or_canceled(self):
		STATE["visits"]["PRJ-1-2026-V001"]["client_sign_off"] = _png()
		self.assertFalse(so.can_send(_visit(), "lisa@example.com"))
		STATE["visits"]["PRJ-1-2026-V001"].update(client_sign_off=None, docstatus=2)
		self.assertFalse(so.can_send(_visit(), "lisa@example.com"))

	def test_the_form_endpoint_refuses_without_it(self):
		STATE["user"] = "bob@example.com"
		with self.assertRaises(_PermissionError):
			so.send_sign_off_request("PRJ-1-2026-V001", "owner@myers.example.com")
		self.assertEqual(STATE["mail"], [])

	def test_write_permission_alone_is_not_enough(self):
		"""v1.614.0 review: every Maintenance User has write on every visit, so a write fallback let
		any technician email any visit's report and photos to any address."""
		STATE["roles"]["dave@example.com"] = ["Maintenance User"]
		STATE["perms"].add(("dave@example.com", "write"))
		self.assertFalse(so.can_send(_visit(), "dave@example.com"))
		STATE["user"] = "dave@example.com"
		with self.assertRaises(_PermissionError) as caught:
			so.send_sign_off_request("PRJ-1-2026-V001", "anyone@example.com")
		self.assertIn("technician or crew", str(caught.exception))
		self.assertEqual(STATE["mail"], [])
		STATE["visits"]["PRJ-1-2026-V001"]["technician"] = "dave@example.com"
		self.assertTrue(so.can_send(_visit(), "dave@example.com"), "on their own visit, yes")


class TestOnlyAFinishedVisit(unittest.TestCase):
	"""v1.614.0 review: a client confirms "the service described above was performed", so a link
	goes only once the visit has left Draft."""

	def setUp(self):
		_reset()

	def _set(self, docstatus, workflow_state):
		STATE["visits"]["PRJ-1-2026-V001"].update(docstatus=docstatus, workflow_state=workflow_state)

	def test_a_draft_is_refused(self):
		for docstatus, state in ((0, "Draft"), (0, None), (0, "")):
			with self.subTest(state=state):
				self._set(docstatus, state)
				self.assertFalse(so.finished(_visit()))
				self.assertFalse(so.can_send(_visit(), "lisa@example.com"))
		STATE["user"] = "lisa@example.com"
		with self.assertRaises(_Thrown) as caught:
			so.send_sign_off_request("PRJ-1-2026-V001", "owner@myers.example.com")
		self.assertIn("send it for review", str(caught.exception))
		out = so.send_after_finish(_visit(), "owner@myers.example.com")
		self.assertIn("sign_off_error", out)
		self.assertEqual((STATE["mail"], STATE["requests"]), ([], {}))

	def test_waiting_for_review_or_approved_it_goes(self):
		for docstatus, state in ((0, "Pending Review"), (1, "Approved"), (1, None)):
			with self.subTest(docstatus=docstatus, state=state):
				self._set(docstatus, state)
				self.assertTrue(so.can_send(_visit(), "lisa@example.com"))

	def test_the_form_hides_its_button_on_a_draft(self):
		self._set(0, "Draft")
		STATE["user"] = "lisa@example.com"
		self.assertFalse(so.get_sign_off_status("PRJ-1-2026-V001")["can_send"])
		self._set(0, "Pending Review")
		self.assertTrue(so.get_sign_off_status("PRJ-1-2026-V001")["can_send"])


class TestSendingAtFinish(unittest.TestCase):
	"""The client's sign-off never blocks review: Finish's send never raises. It runs after the
	workflow has moved the visit to Pending Review (or submitted it), which :func:`finished` takes."""

	def setUp(self):
		_reset()

	def test_sent(self):
		out = so.send_after_finish(_visit(), "owner@myers.example.com")
		self.assertEqual(out["sign_off"]["status"], "Sent")
		self.assertEqual(STATE["savepoints"], ["visit_sign_off"])

	def test_a_bad_address_is_said_not_raised(self):
		out = so.send_after_finish(_visit(), "not an email")
		self.assertIn("sign_off_error", out)
		self.assertEqual(STATE["requests"], {})

	def test_a_failed_send_rolls_back_only_itself(self):
		STATE["sendmail_raises"] = True
		out = so.send_after_finish(_visit(), "owner@myers.example.com")
		self.assertIn("sign_off_error", out)
		self.assertEqual(STATE["rollbacks"], ["visit_sign_off"])
		self.assertIn("Visit sign-off: email after Finish failed", STATE["errors"])

	def test_signed_on_site_sends_nothing(self):
		STATE["visits"]["PRJ-1-2026-V001"]["client_sign_off"] = _png()
		self.assertEqual(so.send_after_finish(_visit(), "owner@myers.example.com"), {})
		self.assertEqual(STATE["mail"], [])


class TestSigning(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_it_lands_on_the_visit_by_email(self):
		status, token = _send()
		STATE["user"] = "Guest"
		STATE["now"] = DT(2026, 10, 14, 9, 30)
		self.assertEqual(_sign(token), {"ok": True})
		row = STATE["requests"][status["request"]]
		self.assertEqual(
			(row["status"], row["signed_name"], row["signed_on"], row["confirmed"], row["signer_ip"], row["user_agent"]),
			("Signed", "Jane Owner", DT(2026, 10, 14, 9, 30), 1, "203.0.113.9", "Mozilla/5.0 (iPhone)"),
		)
		visit = STATE["visits"]["PRJ-1-2026-V001"]
		self.assertEqual(
			(visit["client_sign_off_method"], visit["client_signed_by"], visit["client_signed_on"]),
			("By email", "Jane Owner", DT(2026, 10, 14, 9, 30)),
		)
		self.assertEqual(visit["client_sign_off"], row["signature_image"])
		self.assertEqual(
			STATE["db_set"][-1][1].keys(),
			{"client_sign_off", "client_signed_by", "client_signed_on", "client_sign_off_method"},
			"db_set, so an approved visit takes it without its submit pipeline running again",
		)
		self.assertIn(("Visit Sign-Off Request", status["request"]), STATE["locked"])
		self.assertIn(("Sapphire Maintenance Record", "PRJ-1-2026-V001"), STATE["locked"])

	def test_an_approved_visit_takes_it_too(self):
		"""Through db_set, which writes with frappe.db.set_value and never asks whether a field is
		allow_on_submit (frappe version-16 Document.db_set), so the four fields need not be: they
		are not, and a save of the submitted visit cannot change them (v1.614.0 review)."""
		_status, token = _send()
		STATE["visits"]["PRJ-1-2026-V001"]["docstatus"] = 1
		self.assertEqual(_sign(token), {"ok": True})
		self.assertEqual(STATE["visits"]["PRJ-1-2026-V001"]["client_sign_off_method"], "By email")
		written = set(STATE["db_set"][-1][1])
		fields = {f["fieldname"]: f for f in json.loads(RECORD_JSON.read_text(encoding="utf-8"))["fields"]}
		self.assertFalse([name for name in written if fields[name].get("allow_on_submit")])
		self.assertEqual(STATE["saves"][-1]["status"], "Signed", "only the request is saved")

	def test_signing_twice_is_already_not_an_error(self):
		_status, token = _send()
		_sign(token)
		self.assertEqual(_sign(token), {"ok": True, "already": True})
		self.assertEqual(len(STATE["db_set"]), 1)

	def test_every_dead_link_gets_the_same_words(self):
		_status, token = _send()
		expired_status, expired = _send()
		STATE["requests"][expired_status["request"]]["expires_on"] = NOW - datetime.timedelta(seconds=1)
		cases = {
			"unknown": "A" * 43,
			"malformed": "not a token",
			"superseded": token,
			"expired": expired,
		}
		for label, ref in cases.items():
			with self.subTest(label):
				self.assertEqual(_sign(ref), {"ok": False, "error": so.UNAVAILABLE})
		self.assertEqual(STATE["db_set"], [])

	def test_a_canceled_visit_cannot_be_signed(self):
		_status, token = _send()
		STATE["visits"]["PRJ-1-2026-V001"]["docstatus"] = 2
		self.assertEqual(_sign(token), {"ok": False, "error": so.UNAVAILABLE})

	def test_signed_on_site_meanwhile_that_signature_stands(self):
		status, token = _send()
		STATE["visits"]["PRJ-1-2026-V001"]["client_sign_off"] = "data:image/png;base64,ONSITE"
		self.assertEqual(_sign(token), {"ok": True, "already": True})
		self.assertEqual(STATE["visits"]["PRJ-1-2026-V001"]["client_sign_off"], "data:image/png;base64,ONSITE")
		self.assertEqual(STATE["requests"][status["request"]]["status"], "Superseded")

	def test_name_signature_and_the_box_are_all_needed(self):
		_status, token = _send()
		for label, overrides in {
			"no tick": {"confirmed": 0},
			"no name": {"signed_name": "   "},
			"no drawing": {"signature_image": ""},
			"an svg": {"signature_image": "data:image/svg+xml;base64," + "A" * 40},
			"not a png": {"signature_image": "data:image/png;base64," + base64.b64encode(b"GIF89a" + b"x" * 40).decode()},
		}.items():
			with self.subTest(label):
				out = _sign(token, **overrides)
				self.assertFalse(out["ok"])
				self.assertNotEqual(out["error"], so.UNAVAILABLE)
		self.assertEqual(STATE["db_set"], [])

	def test_the_name_loses_control_characters(self):
		_status, token = _send()
		_sign(token, signed_name="Jane" + chr(0x202E) + " Owner" + chr(7))
		self.assertEqual(STATE["visits"]["PRJ-1-2026-V001"]["client_signed_by"], "Jane Owner")

	def test_the_name_loses_angle_brackets_on_the_request_and_the_visit(self):
		"""v1.614.0 review: db_set skips frappe's _sanitize_content and Report View renders a Data
		cell unescaped, so markup in the typed name would be stored XSS on the Desk."""
		status, token = _send()
		self.assertEqual(_sign(token, signed_name='Jane <img src=x onerror="alert(1)"> Owner'), {"ok": True})
		on_visit = STATE["visits"]["PRJ-1-2026-V001"]["client_signed_by"]
		on_request = STATE["requests"][status["request"]]["signed_name"]
		self.assertEqual(on_visit, on_request)
		self.assertEqual(on_visit, 'Jane img src=x onerror="alert(1)" Owner')
		self.assertNotRegex(on_visit, "[<>]")
		self.assertEqual(so.clean_text("<<>>"), "", "nothing left is no name")

	def test_the_signature_is_one_the_visit_accepts(self):
		"""Re-encoded from its bytes, and matched by the record's own CLIENT_SIGNATURE."""
		tree = ast.parse(RECORD_PY.read_text(encoding="utf-8"))
		pattern = next(
			node.value.args[0].value
			for node in tree.body
			if isinstance(node, ast.Assign) and getattr(node.targets[0], "id", "") == "CLIENT_SIGNATURE"
		)
		image = so.validated_signature_image(_png().replace("base64,", "base64,\n"))
		self.assertTrue(re.fullmatch(pattern, image))


class TestStatus(unittest.TestCase):
	def setUp(self):
		_reset()

	def test_viewed_counts_and_moves_sent_on(self):
		status, _token = _send()
		so.mark_viewed(status["request"])
		so.mark_viewed(status["request"])
		row = STATE["requests"][status["request"]]
		self.assertEqual((row["status"], row["view_count"], row["first_viewed_on"]), ("Viewed", 2, NOW))

	def test_expiry_reads_from_the_date_and_the_sweep_writes_it(self):
		status, _token = _send()
		row = STATE["requests"][status["request"]]
		STATE["now"] = NOW + datetime.timedelta(days=14, seconds=1)
		self.assertEqual(so.effective_status(row), "Expired")
		self.assertFalse(so.is_signable(row))
		so.expire_stale()
		self.assertEqual(row["status"], "Expired")

	def test_the_words_for_the_digest_and_the_form(self):
		visit = _Dict(client_sign_off="x", client_sign_off_method="By email", client_signed_by="Jane", client_signed_on=NOW)
		self.assertEqual(so.describe(visit), f"Signed by email by Jane on <{NOW}>")
		visit = _Dict(client_sign_off="x", client_sign_off_method="On site", client_signed_on=NOW)
		self.assertEqual(so.describe(visit), f"Signed on site <{NOW}>")
		self.assertEqual(so.describe(_Dict(client_sign_off="x")), "Signed", "a signature from before v1.614.0")
		request = _Dict(status="Viewed", email="a@b.com", sent_on=NOW, expires_on=NOW + datetime.timedelta(days=14))
		self.assertEqual(so.describe(_Dict(), request), f"Emailed to a@b.com on <{NOW}>; opened, not signed yet")
		self.assertEqual(so.describe(_Dict()), "Not signed")


class TestThePage(unittest.TestCase):
	"""The controller's states and report, and the template rendered with them."""

	page = None

	@classmethod
	def setUpClass(cls):
		import importlib

		frappe = sys.modules["frappe"]
		frappe.form_dict = _Dict()
		frappe.enqueue = lambda method, **kw: STATE["enqueued"].append((method, kw))
		frappe.utils.flt = lambda v, *a: float(v or 0)
		sys.modules.pop("erpnext_enhancements.www.visit_sign_off", None)
		cls.page = importlib.import_module("erpnext_enhancements.www.visit_sign_off")

	@classmethod
	def tearDownClass(cls):
		sys.modules.pop("erpnext_enhancements.www.visit_sign_off", None)

	def setUp(self):
		_reset()
		STATE["enqueued"] = []
		STATE["visits"]["PRJ-1-2026-V001"].update(
			{
				"visit_notes": "Basin <script>alert(1)</script> refilled.",
				"chemistry_readings": [
					_Dict(reading="pH", reading_value=8.4, uom="", min_value=7.2, max_value=7.8, out_of_range=1, notes="Acid added"),
				],
				"cleaning_tasks": [_Dict(task="Vacuum basin", is_done=1), _Dict(task="Backwash", not_applicable=1)],
				"maintenance_results": [_Dict(question="Pump", selection="Pass", answer="Quiet")],
				"consumables": [_Dict(item="CL3", item_name="Chlorine", qty=2, uom="tabs"), _Dict(item="ALG", qty=0)],
				"follow_ups": [],
				"creation": "2026-10-13 08:00:00",
			}
		)

	def _context(self, ref):
		sys.modules["frappe"].form_dict = _Dict(ref=ref)
		return self.page.get_context(_Dict())

	def test_each_state(self):
		_status, token = _send()
		self.assertEqual(self._context("nonsense").state, "invalid")
		self.assertEqual(self._context("A" * 43).state, "invalid", "unknown renders as malformed does")
		context = self._context(token)
		self.assertEqual(context.state, "signable")
		self.assertEqual(STATE["enqueued"][-1][0], "erpnext_enhancements.sapphire_maintenance.sign_off.mark_viewed")
		self.assertEqual(context.boot["ref"], token)
		_second, newer = _send()
		self.assertEqual(self._context(token).state, "replaced")
		STATE["visits"]["PRJ-1-2026-V001"]["docstatus"] = 2
		self.assertEqual(self._context(newer).state, "invalid")
		STATE["visits"]["PRJ-1-2026-V001"]["docstatus"] = 1
		STATE["now"] = NOW + datetime.timedelta(days=15)
		self.assertEqual(self._context(newer).state, "expired")
		_sign_at = STATE["now"] = NOW
		_sign(newer)
		self.assertEqual(self._context(newer).state, "signed")

	def test_the_report(self):
		_status, token = _send()
		report = self._context(token).report
		self.assertEqual((report["site"], report["technician"], report["number"]), ("Myers Mortuary", "Austin Tech", "PRJ-1-2026-V001"))
		self.assertEqual(report["readings"][0]["range"], "7.2–7.8")
		self.assertTrue(report["out_of_range"])
		self.assertEqual([t["status"] for t in report["tasks"]], ["Done", "N/A"])
		self.assertEqual(report["chemicals"], [{"item": "Chlorine", "qty": "2", "uom": "tabs"}])

	def test_the_template_renders_it_escaped(self):
		try:
			from jinja2 import DictLoader, Environment
		except ImportError:  # pragma: no cover
			self.skipTest("jinja2 not installed")
		_status, token = _send()
		context = self._context(token)
		base = "{% block head_include %}{% endblock %}{% block page_content %}{% endblock %}"
		env = Environment(loader=DictLoader({"templates/web.html": base, "page": PAGE_HTML.read_text(encoding="utf-8")}))
		env.globals["_"] = lambda text: text
		env.filters["tojson"] = lambda value: json.dumps(value)
		out = env.get_template("page").render(**context)
		self.assertIn("Basin &lt;script&gt;alert(1)&lt;/script&gt; refilled.", out)
		self.assertNotIn("<script>alert(1)", out)
		for marker in ('id="vs-pad"', 'id="vs-confirm"', 'id="vs-name"', "I confirm the service described above was performed.", "Out of range"):
			self.assertIn(marker, out)
		sys.modules["frappe"].form_dict = _Dict(ref="nonsense")
		out = env.get_template("page").render(**self.page.get_context(_Dict()))
		self.assertIn("This link isn't available", out)
		self.assertNotIn('id="vs-pad"', out)


# ---------------------------------------------------------------------------
# The shape of the thing, read from the source.


def _calls_named(path, attr):
	tree = ast.parse(path.read_text(encoding="utf-8"))
	return [
		node.lineno
		for node in ast.walk(tree)
		if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == attr
	]


def _decorators(path, name):
	tree = ast.parse(path.read_text(encoding="utf-8"))
	fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
	return fn, [ast.unparse(d) for d in fn.decorator_list]


class TestTheShape(unittest.TestCase):
	def test_no_guest_code_splats_a_payload_onto_a_doc(self):
		"""Under ignore_permissions read_only is a UI hint, so ``doc.update(payload)`` could set the
		status. Neither the module nor the page's controller calls ``.update(`` at all."""
		for path in (SIGN_OFF_PY, PAGE_PY):
			with self.subTest(path=path.name):
				self.assertEqual(_calls_named(path, "update"), [])

	def test_the_guest_endpoint_is_post_only_rate_limited_and_whitelisted_outermost(self):
		fn, decorators = _decorators(SIGN_OFF_PY, "sign_visit")
		self.assertEqual(decorators[0], "frappe.whitelist(allow_guest=True, methods=['POST'])")
		self.assertTrue(decorators[1].startswith("rate_limit("), decorators)
		self.assertIn("methods=['POST']", decorators[1])
		self.assertEqual([a.arg for a in fn.args.args], ["ref", "signed_name", "signature_image", "confirmed"])

	def test_only_sign_visit_is_open_to_guests(self):
		source = SIGN_OFF_PY.read_text(encoding="utf-8")
		self.assertEqual(source.count("allow_guest=True"), 1)

	def test_the_www_controller_is_importable(self):
		self.assertTrue(PAGE_PY.is_file())
		self.assertTrue(PAGE_HTML.is_file())
		self.assertFalse((APP / "www" / "visit-sign-off.py").exists())
		result = subprocess.run(
			[sys.executable, str(REPO_ROOT / "scripts" / "check_www_controllers.py")],
			capture_output=True,
			text=True,
		)
		self.assertEqual(result.returncode, 0, result.stderr)

	def test_the_page_hands_over_the_sessions_csrf_token_and_the_script_sends_it(self):
		source = PAGE_PY.read_text(encoding="utf-8")
		self.assertIn('"csrf_token": (getattr(frappe.session, "data", None) or {}).get("csrf_token")', source)
		# Never minted: get_csrf_token() makes one a Guest session never persists.
		self.assertEqual(_calls_named(PAGE_PY, "get_csrf_token"), [])
		html = PAGE_HTML.read_text(encoding="utf-8")
		self.assertIn("window.VS_BOOT = {{ boot | tojson }};", html)
		self.assertIn("X-Frappe-CSRF-Token", PAGE_JS.read_text(encoding="utf-8"))

	def test_the_page_is_private_cache_busted_and_adds_no_history(self):
		html = PAGE_HTML.read_text(encoding="utf-8")
		self.assertIn('<meta name="robots" content="noindex, nofollow">', html)
		self.assertIn('<meta name="referrer" content="no-referrer">', html)
		self.assertIn("visit_sign_off.css?v={{ deploy_version }}", html)
		self.assertIn("visit_sign_off.js?v={{ deploy_version }}", html)
		self.assertIn("no_cache = 1", PAGE_PY.read_text(encoding="utf-8"))
		code = re.sub(r"/\*[\s\S]*?\*/|//.*", "", PAGE_JS.read_text(encoding="utf-8"))
		self.assertIsNone(re.search(r"pushState|replaceState|popstate|location\.reload", code))

	def test_every_value_on_the_page_is_escaped(self):
		html = re.sub(r"\{#.*?#\}", "", PAGE_HTML.read_text(encoding="utf-8"), flags=re.S)
		for expr in re.findall(r"\{\{(.*?)\}\}", html):
			expr = expr.strip()
			if expr.startswith(("_(", "deploy_version", "boot | tojson", "row.tone")):
				continue
			with self.subTest(expr=expr):
				self.assertTrue(expr.endswith(("| e", '| join(", ")')), expr)

	def test_the_email_is_built_in_the_shell(self):
		source = SIGN_OFF_PY.read_text(encoding="utf-8")
		self.assertIn("email_style.render(", source)
		self.assertIn("reference_doctype=VISIT", source)
		template = EMAIL.read_text(encoding="utf-8")
		self.assertNotIn("max-width", re.sub(r"\{#.*?#\}", "", template, flags=re.S))
		self.assertIn('{% import "erpnext_enhancements/templates/emails/_components.html" as ee %}', template)

	def test_the_print_format_says_how_the_client_signed(self):
		records = json.loads((APP / "fixtures" / "print_format.json").read_text(encoding="utf-8"))
		html = next(r for r in records if r["name"] == "Maintenance Record Print")["html"]
		for use in (
			'doc.client_sign_off_method == "By email"',
			"doc.client_signed_by | e",
			"frappe.utils.format_datetime(doc.client_signed_on)",
			"mr.sign_off_sent",
			"Not signed on site",
		):
			with self.subTest(use=use):
				self.assertIn(use, html)

	def test_the_request_doctype(self):
		meta = json.loads(DOCTYPE_JSON.read_text(encoding="utf-8"))
		self.assertEqual((meta["name"], meta["module"]), ("Visit Sign-Off Request", "Sapphire Maintenance"))
		fields = {f["fieldname"]: f for f in meta["fields"]}
		for name in (
			"visit", "status", "email", "sent_by", "sent_on", "expires_on", "first_viewed_on",
			"signed_on", "signed_name", "signature_image", "signer_ip", "user_agent", "token_hash",
		):
			with self.subTest(field=name):
				self.assertEqual(fields[name].get("read_only"), 1, "evidence is read-only")
		self.assertEqual((fields["token_hash"].get("hidden"), fields["token_hash"].get("unique")), (1, 1))
		self.assertEqual(fields["status"]["options"].split("\n"), ["Sent", "Viewed", "Signed", "Expired", "Superseded"])
		self.assertFalse(any(p.get("create") or p.get("write") for p in meta["permissions"]), "made by code only")

	def test_no_client_sign_off_field_can_be_edited_after_submit(self):
		"""v1.614.0 review. Saving a submitted visit runs before_update_after_submit, not validate,
		so the CLIENT_SIGNATURE check never sees it: an allow_on_submit client_sign_off could be set
		through frappe.client.set_value to a value that leaves the <img src>, and the other three
		could forge "Signed by email by" anyone. The emailed path writes them with db_set, which
		does not need allow_on_submit. no_copy: a duplicated visit is not signed."""
		meta = json.loads(RECORD_JSON.read_text(encoding="utf-8"))
		fields = {f["fieldname"]: f for f in meta["fields"]}
		for name in ("client_sign_off", "client_sign_off_method", "client_signed_by", "client_signed_on"):
			with self.subTest(field=name):
				self.assertFalse(fields[name].get("allow_on_submit"), f"{name} must not be allow_on_submit")
				self.assertEqual(fields[name].get("no_copy"), 1)
				self.assertIn(name, meta["field_order"])
		for name in ("client_sign_off_method", "client_signed_by", "client_signed_on"):
			self.assertEqual(fields[name].get("read_only"), 1)
		self.assertEqual(fields["client_sign_off_method"]["options"], "\nOn site\nBy email")

	def test_every_signature_src_is_escaped(self):
		"""The print format, the portal page: no signature reaches HTML unescaped."""
		records = json.loads((APP / "fixtures" / "print_format.json").read_text(encoding="utf-8"))
		html = next(r for r in records if r["name"] == "Maintenance Record Print")["html"]
		portal = (RECORD_JSON.parent / "sapphire_maintenance_record.html").read_text(encoding="utf-8")
		for label, source in (("print format", html), ("portal page", portal)):
			for field in ("client_sign_off", "technician_signature"):
				with self.subTest(where=label, field=field):
					self.assertIn('src="{{ doc.' + field + ' | e }}"', source)
					self.assertNotIn('src="{{ doc.' + field + ' }}"', source)

	def test_finish_sends_after_the_visit_has_moved_on(self):
		source = (APP / "api" / "maintenance_visit.py").read_text(encoding="utf-8")
		finish = source[source.index("def finish_visit(") : source.index("def _retire_sign_off_links")]
		self.assertIn("email_sign_off=None", finish.splitlines()[0])
		self.assertLess(finish.index("apply_workflow(doc"), finish.index("send_after_finish(doc, email_sign_off)"))

	def test_the_page_in_a_browser_keeps_back_and_forward(self):
		"""``scripts/test_web_flow_history.js visit-sign-off`` runs the page's real script."""
		import shutil

		node = shutil.which("node")
		if not node:
			self.skipTest("node is not on PATH")
		result = subprocess.run(
			[node, str(REPO_ROOT / "scripts" / "test_web_flow_history.js"), "visit-sign-off"],
			capture_output=True,
			text=True,
			timeout=120,
			check=False,
		)
		self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

	def test_the_daily_sweep_is_scheduled(self):
		self.assertIn(
			'"erpnext_enhancements.sapphire_maintenance.sign_off.expire_stale"',
			(APP / "hooks.py").read_text(encoding="utf-8"),
		)


if __name__ == "__main__":
	unittest.main()
