# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Knowledge Base's actions, end to end, against an in-memory Frappe (WI-080 PR 3).

Bench-free, with its own ``frappe`` stub, so it has its own CI step. The stub is small but it is a
real lifecycle: documents insert, save and submit through the hooks v16 runs, in v16's order
(``before_validate`` first, then ``validate``/``before_save``/``before_submit`` unless
``ignore_validate``, then ``on_update``/``on_submit``); the stored row is what
``get_doc_before_save`` returns; a stale ``modified`` is refused; a submitted document may change
only its ``allow_on_submit`` field; and a request is a transaction that commits on success and
rolls back on any exception. **The real controllers, the real ``doc_events`` handlers named in
``hooks.py``, and the real ``publish``, ``notify``, ``references``, ``files`` and API modules run
on top of it.** What it pins:

* **Every endpoint** is whitelisted with its method (POST, and GET for ``review_diff``) and checks,
  in order, a KB role, the document's permission, and the rules, before it writes.
* **The whole person test** of WI-080 (Parker drafts with a picture and submits; the approvers get
  ToDos; Parker, Administrator and a token are refused; James requests changes, edits, and is then
  refused as a contributor; Nik approves from a browser) through the endpoints.
* **The publish transaction's steps, in order**: the KB number under ``FOR UPDATE`` with the ``%``
  inside the bound parameter; the article written from the stored version under
  ``flags.kb_action``, with ``body_md`` and ``content_hash`` from the stored body; the used Files
  moved under ``flags.kb_action`` and nothing deleted; the version submitted with
  ``flags.kb_publish`` and the opened ``modified``; the previous version superseded under
  ``flags.kb_action``; ToDos closed. A failure at any step leaves nothing written.
* **Numbers and concurrency**: one more than the highest in the block; a deadlock retried from the
  start in a new transaction; a double-click on Approve publishes once.
* **Revisions**: one open version per article; copied from the live version; published as the next
  version number; the old one superseded.
* **ToDos**: raised inline for every KB Approver who may approve, never the author's side; closed
  when the version leaves In Review; the author's after Request Changes; **no draft text** (only
  the title) in any ToDo, ToDo-made Comment or bell, with sentinel strings in every other field.
* **Decisions (a) and (b)**: a version's Files cannot be deleted once it leaves Draft; a typed
  Comment or a hand-made ToDo on a version is refused, and every other Comment and ToDo on the site
  is untouched.
* **The forms' buttons** (``onload``) are the rules, for each person.

Run: python -m unittest erpnext_enhancements.tests.test_knowledge_base_actions -v
"""

import ast
import copy
import datetime
import importlib
import re
import sys
import types
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
REPO_ROOT = APP.parent
if str(REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(REPO_ROOT))

ARTICLE = "Knowledge Article"
VERSION = "Knowledge Article Version"
AUTHOR = "parker@example.com"
APPROVER = "james@example.com"
SECOND = "lisa@example.com"
NIK = "nik@example.com"
TECH = "tech@example.com"

TITLE = "Receiving a PO against a packing slip"
#: Draft text that must never leave the version: one per content field.
SENTINELS = {
	"summary": "SENTINEL-SUMMARY-7f3a",
	"keywords": "SENTINEL-KEYWORDS-7f3a",
	"body": "SENTINEL-BODY-7f3a",
	"change_note": "SENTINEL-CHANGE-7f3a",
	"review_note": "SENTINEL-NOTE-7f3a",
}
BODY = (
	'<div class="ql-editor read-mode"><p>Scan the slip. ' + SENTINELS["body"] + "</p>"
	'<p><img src="/private/files/slip.png?fid=file-used"></p></div>'
)
#: Built by concatenation, never a literal (push protection refused one once).
STRIPE_KEY = "sk" + "_live_" + "a1B2" * 6

API_MODULE = "erpnext_enhancements.api.knowledge_base"
MODULES = (
	"erpnext_enhancements.knowledge_base.files",
	"erpnext_enhancements.knowledge_base.references",
	"erpnext_enhancements.knowledge_base.notify",
	"erpnext_enhancements.knowledge_base.doctype.knowledge_article_version.knowledge_article_version",
	"erpnext_enhancements.knowledge_base.doctype.knowledge_article.knowledge_article",
	"erpnext_enhancements.knowledge_base.publish",
	API_MODULE,
)


# ------------------------------------------------------------------ exceptions the stub raises


class Refused(Exception):
	"""``frappe.throw`` / ``frappe.ValidationError``."""


class PermissionRefused(Refused):
	"""``frappe.PermissionError``."""


class DoesNotExist(Refused):
	pass


class Deadlock(Exception):
	"""``frappe.QueryDeadlockError``."""


class DuplicateEntry(Exception):
	"""``frappe.DuplicateEntryError``."""


class Stale(Refused):
	"""v16's TimestampMismatchError."""


class _Flags(dict):
	def __getattr__(self, key):
		return self.get(key)

	def __setattr__(self, key, value):
		self[key] = value


# ------------------------------------------------------------------ the store


STATE = {}
CLOCK = [datetime.datetime(2026, 9, 28, 9, 0, 0)]

#: What each doctype starts with, as v16's defaults would give it.
DEFAULTS = {
	VERSION: {"review_state": "Draft", "docstatus": 0, "review_every_months": 6, "ai_drafted": 0},
	ARTICLE: {"docstatus": 0, "review_every_months": 6, "ai_drafted": 0},
	"ToDo": {"status": "Open", "docstatus": 0},
	"Comment": {"docstatus": 0},
	"File": {"docstatus": 0, "is_private": 1},
}
#: A submitted document may change only these (plus what the framework stamps).
ALLOW_ON_SUBMIT = {VERSION: {"review_state"}}
STAMPS = {"modified", "modified_by"}


def _tick():
	CLOCK[0] += datetime.timedelta(seconds=1, microseconds=1)
	return CLOCK[0]


def _db():
	return STATE["db"]


def _row(doctype, name):
	return _db().get(doctype, {}).get(name)


def _reset():
	STATE.clear()
	STATE.update(
		{
			"db": {
				"User": {},
				"Has Role": {},
				VERSION: {},
				ARTICLE: {},
				"File": {},
				"ToDo": {},
				"Comment": {},
			},
			"committed": None,
			"savepoints": {},
			"writes": [],
			"locks": [],
			"sql": [],
			"errors": [],
			"bells": [],
			"markdown": [],
			"fail": {},
			"rollbacks": 0,
			"counters": {},
			"user_type_reads": [],
		}
	)
	for user, roles in (
		(AUTHOR, ("KB Author",)),
		(APPROVER, ("KB Approver",)),
		(SECOND, ("KB Approver",)),
		(NIK, ("KB Approver", "System Manager")),
		(TECH, ()),
		("disabled@example.com", ("KB Approver",)),
		("portal@example.com", ("KB Approver",)),
	):
		_db()["User"][user] = {
			"name": user,
			"enabled": 0 if user.startswith("disabled") else 1,
			"user_type": "Website User" if user.startswith("portal") else "System User",
			"roles": ("Desk User", *roles),
		}
		for role in roles:
			key = f"{user}:{role}"
			_db()["Has Role"][key] = {"name": key, "parent": user, "parenttype": "User", "role": role}
	_db()["User"]["Administrator"] = {"name": "Administrator", "enabled": 1, "user_type": "System User", "roles": ()}
	STATE["committed"] = copy.deepcopy(_db())


def _roles(user):
	if user == "Administrator":
		return ["Administrator", "System Manager", "KB Author", "KB Approver", "Desk User", "All"]
	row = _row("User", user)
	return list(row["roles"]) if row else ["Guest"]


def _match(row, filters):
	for key, want in (filters or {}).items():
		value = row.get(key)
		if isinstance(want, list | tuple) and want and want[0] == "in":
			if value not in want[1]:
				return False
		elif value != want:
			return False
	return True


def _next_name(doctype, doc):
	if doctype == VERSION:
		n = STATE["counters"].get(VERSION, 0) + 1
		STATE["counters"][VERSION] = n
		return f"KBV-{n:05d}"
	if doctype == ARTICLE:
		return doc.kb_number
	n = STATE["counters"].get(doctype, 0) + 1
	STATE["counters"][doctype] = n
	return f"{doctype.lower()}-{n}"


def _maybe_fail(point):
	queue = STATE["fail"].get(point)
	if queue:
		raise queue.pop(0)


# ------------------------------------------------------------------ Document


def _handlers():
	"""The knowledge_base doc_events handlers hooks.py registers, imported: the real ones run."""
	tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
	events = next(
		ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == "doc_events"
	)
	out = {}
	for doctype, methods in events.items():
		for method, targets in methods.items():
			for target in targets if isinstance(targets, list) else [targets]:
				if not target.startswith("erpnext_enhancements.knowledge_base."):
					continue
				module, _dot, fn = target.rpartition(".")
				out.setdefault(doctype, {}).setdefault(method, []).append(getattr(importlib.import_module(module), fn))
	return out


DOC_EVENTS = {}


class _Document:
	"""``frappe.model.document.Document``, as far as the KB touches it, with v16's hook order."""

	def __init__(self, values=None):
		d = self.__dict__
		d["flags"] = _Flags()
		d["_doc_before_save"] = None
		d["_onload"] = {}
		d["_new"] = True
		for key, value in (values or {}).items():
			d[key] = value

	def __getattr__(self, key):
		if key.startswith("_"):
			raise AttributeError(key)
		return None

	def get(self, key, default=None):
		if key.startswith("_") or key == "flags":
			return default
		return self.__dict__.get(key, default)

	def update(self, values):
		for key, value in values.items():
			setattr(self, key, value)

	def set_onload(self, key, value):
		self._onload[key] = value

	def is_new(self):
		return self._new

	def get_doc_before_save(self):
		return self._doc_before_save

	def row(self):
		return {k: v for k, v in self.__dict__.items() if not k.startswith("_") and k != "flags"}

	def check_permission(self, ptype="read"):
		_check_perm(self.doctype, ptype)

	def run_method(self, method):
		self._run(method)

	def _run(self, method):
		fn = getattr(type(self), method, None)
		if callable(fn):
			fn(self)
		for handler in DOC_EVENTS.get(self.doctype, {}).get(method, ()):
			handler(self, method)

	def _record(self, action):
		STATE["writes"].append(
			{"doctype": self.doctype, "name": self.name, "action": action, "flags": dict(self.flags), "row": copy.deepcopy(self.row())}
		)

	def insert(self, ignore_permissions=None, **kwargs):
		if ignore_permissions is not None:
			self.flags.ignore_permissions = ignore_permissions
		if not self.flags.ignore_permissions:
			_check_perm(self.doctype, "create")
		self._run("before_insert")
		if not self.name:
			self.name = _next_name(self.doctype, self)
		if _row(self.doctype, self.name) is not None:
			frappe_module().local.message_log.append(f"{self.doctype} {self.name} already exists")
			raise DuplicateEntry(self.name)
		self.owner = self.get("owner") or frappe_module().session.user
		self.creation = self.modified = _tick()
		self.docstatus = self.get("docstatus") or 0
		self._doc_before_save = None
		self._run("before_validate")
		if not self.flags.ignore_validate:
			self._run("validate")
			self._run("before_save")
		_db().setdefault(self.doctype, {})[self.name] = copy.deepcopy(self.row())
		self.__dict__["_new"] = False
		self._record("insert")
		self._run("after_insert")
		self._run("on_update")
		return self

	def save(self, ignore_permissions=None, **kwargs):
		if ignore_permissions is not None:
			self.flags.ignore_permissions = ignore_permissions
		if self._new:
			return self.insert()
		if not self.flags.ignore_permissions:
			_check_perm(self.doctype, "write")
		stored = _row(self.doctype, self.name)
		if stored is None:
			raise DoesNotExist(self.name)
		if stored.get("modified") != self.get("modified"):
			raise Stale(f"{self.name} has been modified after you opened it")
		before, after = stored.get("docstatus") or 0, self.get("docstatus") or 0
		action = {(0, 0): "save", (0, 1): "submit", (1, 1): "update_after_submit"}[(before, after)]
		self._doc_before_save = _load(self.doctype, stored)
		self.modified = _tick()
		if action in ("save", "submit"):
			self._run("before_validate")
		if not self.flags.ignore_validate:
			if action == "save":
				self._run("validate")
				self._run("before_save")
			elif action == "submit":
				self._run("validate")
				self._run("before_submit")
			else:
				self._run("before_update_after_submit")
		if action == "update_after_submit":
			allowed = ALLOW_ON_SUBMIT.get(self.doctype, set()) | STAMPS
			changed = sorted(k for k, v in self.row().items() if k not in allowed and stored.get(k) != v)
			if changed:
				raise Refused(f"Not allowed to change {changed} after submission")
		_db()[self.doctype][self.name] = copy.deepcopy(self.row())
		self._record(action)
		if action == "save":
			self._run("on_update")
		elif action == "submit":
			self._run("on_update")
			self._run("on_submit")
		else:
			self._run("on_update_after_submit")
		return self

	def submit(self):
		self.docstatus = 1
		return self.save()


class _ToDo(_Document):
	def after_insert(self):
		# v16 ToDo.validate + on_update: an "Assigned" Comment on the reference, carrying the
		# description (desk/doctype/todo/todo.py:40-52, :78-82).
		if self.get("reference_type") and self.get("reference_name"):
			frappe_module().get_doc(
				{
					"doctype": "Comment",
					"comment_type": "Assigned",
					"reference_doctype": self.reference_type,
					"reference_name": self.reference_name,
					"content": f"{self.assigned_by} assigned {self.allocated_to}: {self.description}",
				}
			).insert(ignore_permissions=True)


CONTROLLERS = {}


def _cls(doctype):
	return CONTROLLERS.get(doctype) or (_ToDo if doctype == "ToDo" else _Document)


def _load(doctype, row):
	doc = _cls(doctype)(copy.deepcopy(row))
	doc.__dict__["_new"] = False
	return doc


def _check_perm(doctype, ptype):
	frappe = frappe_module()
	user = frappe.session.user
	if user == "Administrator":
		return
	roles = set(_roles(user))
	if doctype == VERSION and roles & {"KB Author", "KB Approver"} and ptype in ("read", "write", "create", "print", "report"):
		return
	if doctype == ARTICLE and ptype in ("read", "print", "report") and "Desk User" in roles:
		return
	if doctype not in (VERSION, ARTICLE):
		# Comments, ToDos and Files: the guards under test decide, not DocPerm.
		return
	raise PermissionRefused(f"No permission for {doctype} ({ptype})")


# ------------------------------------------------------------------ the frappe stub


def frappe_module():
	return sys.modules["frappe"]


def _throw(message, exc=None, title=None, **kwargs):
	frappe = frappe_module()
	if not frappe.flags.mute_messages:
		frappe.local.message_log.append(message)
	raise (exc or Refused)(message)


def _get_doc(*args, for_update=None, **kwargs):
	if len(args) == 1 and isinstance(args[0], dict):
		values = dict(args[0])
		doctype = values["doctype"]
		return _cls(doctype)({**DEFAULTS.get(doctype, {}), **values})
	doctype, name = args[0], args[1]
	row = _row(doctype, name)
	if row is None:
		raise DoesNotExist(f"{doctype} {name} not found")
	if for_update:
		STATE["locks"].append((doctype, name))
	return _load(doctype, row)


def _new_doc(doctype):
	return _cls(doctype)({"doctype": doctype, **DEFAULTS.get(doctype, {})})


def _get_all(doctype, filters=None, fields=None, pluck=None, order_by=None, **kwargs):
	rows = [r for r in _db().get(doctype, {}).values() if _match(r, filters)]
	rows.sort(key=lambda r: (str(r.get("creation") or ""), str(r.get("name"))))
	if pluck:
		return [r.get(pluck) for r in rows]
	return [_Flags({f: r.get(f) for f in (fields or ["name"])}) for r in rows]


def _get_value(doctype, name, fieldname=None, as_dict=False, **kwargs):
	if doctype == "User" and fieldname == "user_type":
		STATE["user_type_reads"].append(name)
	row = _row(doctype, name) if isinstance(name, str) else None
	if not row:
		return None
	if isinstance(fieldname, list | tuple):
		values = {f: row.get(f) for f in fieldname}
		return _Flags(values) if as_dict else tuple(values.values())
	return row.get(fieldname)


def _sql(query, values=(), as_dict=False, pluck=False, **kwargs):
	flat = " ".join(query.split())
	STATE["sql"].append((flat, values))
	lowered = flat.lower()
	if lowered == "select name from `tabknowledge article` where name like %s for update":
		(pattern,) = values
		_maybe_fail("allocate")
		prefix = pattern[:-1].casefold()
		names = sorted(n for n in _db()[ARTICLE] if n.casefold().startswith(prefix))
		return names if pluck else [(n,) for n in names]
	if lowered.startswith(
		"select name, review_state from `tabknowledge article version` where article = %s and review_state in %s"
	):
		article, states = values
		rows = sorted(
			(r for r in _db()[VERSION].values() if r.get("article") == article and (r.get("review_state") or "Draft") in states),
			key=lambda r: r["creation"],
		)
		out = [_Flags(name=r["name"], review_state=r.get("review_state")) for r in rows]
		return out if as_dict else [(r.name, r.review_state) for r in out]
	raise AssertionError(f"unexpected SQL: {flat}")


def _savepoint(name):
	STATE["savepoints"][name] = copy.deepcopy(_db())


def _rollback(save_point=None, **kwargs):
	if save_point:
		STATE["db"] = copy.deepcopy(STATE["savepoints"][save_point])
		return
	STATE["rollbacks"] += 1
	STATE["db"] = copy.deepcopy(STATE["committed"])


def _to_markdown(html):
	STATE["markdown"].append(html)
	_maybe_fail("markdown")
	return re.sub(r"\n+", "\n", re.sub(r"<[^>]+>", "\n", html or "")).strip()


WHITELISTED = {}


def _whitelist(*args, **kwargs):
	def register(fn):
		WHITELISTED[fn.__name__] = kwargs
		return fn

	return register


def _install_frappe_stub():
	frappe = types.ModuleType("frappe")
	frappe._ = lambda message, *a, **k: message
	frappe.throw = _throw
	frappe.whitelist = _whitelist
	frappe.ValidationError = Refused
	frappe.PermissionError = PermissionRefused
	frappe.DoesNotExistError = DoesNotExist
	frappe.QueryDeadlockError = Deadlock
	frappe.DuplicateEntryError = DuplicateEntry
	frappe.get_roles = lambda user=None: _roles(user)
	frappe.get_doc = _get_doc
	frappe.new_doc = _new_doc
	frappe.get_all = _get_all
	frappe.has_permission = lambda doctype, ptype="read", *a, **k: _allowed(doctype, ptype)
	frappe.log_error = lambda title=None, message=None, **k: STATE["errors"].append((title, message))
	frappe.get_traceback = lambda *a, **k: "Traceback (most recent call last): stub"
	frappe.clear_messages = lambda: setattr(frappe.local, "message_log", [])
	frappe.session = _Flags(user=AUTHOR, sid="a1b2c3d4")
	frappe.flags = _Flags()
	frappe.local = types.SimpleNamespace(request=types.SimpleNamespace(headers={}), message_log=[])
	frappe.db = types.SimpleNamespace(
		get_value=_get_value,
		exists=lambda doctype, name=None: _row(doctype, name) is not None,
		sql=_sql,
		savepoint=_savepoint,
		release_savepoint=lambda name: STATE["savepoints"].pop(name, None),
		rollback=_rollback,
	)

	utils = types.ModuleType("frappe.utils")
	utils.cint = lambda v: int(float(v)) if v not in (None, "") and str(v).strip() else 0
	utils.cstr = lambda v: "" if v is None else str(v)
	utils.now_datetime = _tick
	utils.nowdate = lambda: "2026-09-28"
	utils.to_markdown = _to_markdown
	utils.get_url_to_form = lambda doctype, name: f"https://erp.example.com/desk/{doctype.lower().replace(' ', '-')}/{name}"
	utils.get_fullname = lambda user: f"Full {user}"
	frappe.utils = utils

	model = types.ModuleType("frappe.model")
	document = types.ModuleType("frappe.model.document")
	document.Document = _Document
	model.document = document
	frappe.model = model

	desk = types.ModuleType("frappe.desk")
	form = types.ModuleType("frappe.desk.form")
	assign_to = types.ModuleType("frappe.desk.form.assign_to")
	assign_to.notify_assignment = lambda *a, **k: STATE["bells"].append((a, k))

	sys.modules.update(
		{
			"frappe": frappe,
			"frappe.utils": utils,
			"frappe.model": model,
			"frappe.model.document": document,
			"frappe.desk": desk,
			"frappe.desk.form": form,
			"frappe.desk.form.assign_to": assign_to,
		}
	)


def _allowed(doctype, ptype):
	try:
		_check_perm(doctype, ptype)
	except PermissionRefused:
		return False
	return True


api = publish = notify = references = files = None


def setUpModule():
	global api, publish, notify, references, files
	_install_frappe_stub()
	for name in MODULES:
		sys.modules.pop(name, None)
	loaded = {name: importlib.import_module(name) for name in MODULES}
	api = loaded[API_MODULE]
	publish = loaded["erpnext_enhancements.knowledge_base.publish"]
	notify = loaded["erpnext_enhancements.knowledge_base.notify"]
	references = loaded["erpnext_enhancements.knowledge_base.references"]
	files = loaded["erpnext_enhancements.knowledge_base.files"]
	version_module = loaded[MODULES[3]]
	article_module = loaded[MODULES[4]]
	CONTROLLERS[VERSION] = version_module.KnowledgeArticleVersion
	CONTROLLERS[ARTICLE] = article_module.KnowledgeArticle
	DOC_EVENTS.clear()
	DOC_EVENTS.update(_handlers())


# ------------------------------------------------------------------ requests


def request(fn, *args, user=APPROVER, browser=True, token=None, flags=None, **kwargs):
	"""One HTTP request: a session, headers, flags, and a transaction that commits on success and
	rolls back on any exception, as Frappe's request handler does."""
	frappe = frappe_module()
	frappe.session = _Flags(user=user, sid=("f00dcafe" if browser else user))
	frappe.local.request = types.SimpleNamespace(headers={"Authorization": token} if token else {})
	frappe.local.message_log = []
	frappe.flags = _Flags(flags or {})
	STATE["committed"] = copy.deepcopy(_db())
	try:
		result = fn(*args, **kwargs)
	except BaseException:
		STATE["db"] = copy.deepcopy(STATE["committed"])
		raise
	STATE["committed"] = copy.deepcopy(_db())
	return result


def refused(test, fn, *args, exc=Refused, **kwargs):
	"""The request is refused; returns the message."""
	with test.assertRaises(exc) as caught:
		request(fn, *args, **kwargs)
	return str(caught.exception)


def draft(user=AUTHOR, body=BODY, **values):
	"""A draft as its author saves it in the Desk (the real controller's content rules run)."""

	def make():
		doc = frappe_module().get_doc(
			{
				"doctype": VERSION,
				"title": TITLE,
				"department_block": "06 Operations",
				"summary": SENTINELS["summary"],
				"keywords": SENTINELS["keywords"],
				"body": body,
				"change_note": SENTINELS["change_note"],
				**values,
			}
		)
		doc.insert()
		return doc.name

	name = request(make, user=user)
	_file(name, "file-used", "/private/files/slip.png")
	_file(name, "file-unused", "/private/files/spare.pdf")
	return name


def _file(version, name, url):
	_db()["File"][name] = {
		"doctype": "File",
		"name": name,
		"file_url": url,
		"file_name": url.rsplit("/", 1)[-1],
		"is_private": 1,
		"attached_to_doctype": VERSION,
		"attached_to_name": version,
		"owner": AUTHOR,
		"creation": _tick(),
		"modified": _tick(),
		"docstatus": 0,
	}
	STATE["committed"] = copy.deepcopy(_db())


def edit(name, user, **values):
	def change():
		doc = frappe_module().get_doc(VERSION, name)
		doc.update(values)
		doc.save()

	request(change, user=user)


def version(name):
	return _row(VERSION, name)


def opened(name):
	"""``modified`` as the approver's page loaded it."""
	return str(version(name)["modified"])


def open_todos(name=None):
	return sorted(
		r["allocated_to"]
		for r in _db()["ToDo"].values()
		if r["status"] == "Open" and (name is None or r["reference_name"] == name)
	)


def submitted(name, user=AUTHOR):
	request(api.submit_for_review, name, user=user)


def approve(name, user=NIK, **kwargs):
	return request(api.approve_and_publish, name, opened(name), user=user, **kwargs)


def writes(doctype=None):
	return [w for w in STATE["writes"] if doctype is None or w["doctype"] == doctype]


# ================================================================== the tests


class Base(unittest.TestCase):
	def setUp(self):
		_reset()


class TestEndpointsAreWhitelistedByMethod(Base):
	EXPECTED = {
		"start_revision": ["POST"],
		"submit_for_review": ["POST"],
		"withdraw": ["POST"],
		"request_changes": ["POST"],
		"approve_and_publish": ["POST"],
		"discard": ["POST"],
		"confirm_still_accurate": ["POST"],
		"retire": ["POST"],
		"review_diff": ["GET"],
	}

	def test_every_endpoint_names_its_method_and_none_is_open_to_guests(self):
		ours = {name: kw for name, kw in WHITELISTED.items() if getattr(api, name, None) is not None}
		self.assertEqual({name: kw.get("methods") for name, kw in ours.items()}, self.EXPECTED)
		for name, kw in ours.items():
			with self.subTest(name=name):
				self.assertFalse(kw.get("allow_guest"))

	def test_the_endpoint_names_are_the_work_items(self):
		wi = (REPO_ROOT / "work-items" / "WI-080-company-knowledge-base.md").read_text(encoding="utf-8")
		for name in self.EXPECTED:
			with self.subTest(name=name):
				self.assertIn(f"`{name}`", wi)


# ------------------------------------------------------------------ the WI-080 person test


class TestThePersonTest(Base):
	"""WI-080 acceptance, "Person test", steps 1-5, through the endpoints."""

	def test_parker_drafts_james_sends_back_nik_publishes(self):
		name = draft()
		self.assertEqual(version(name)["contributors"], AUTHOR)

		# 1-2. Parker submits; every KB Approver who may approve gets a ToDo, Parker none.
		out = request(api.submit_for_review, name, user=AUTHOR)
		self.assertEqual(out["review_state"], "In Review")
		self.assertEqual(sorted(p["user"] for p in out["notified"]), [APPROVER, SECOND, NIK])
		self.assertEqual(open_todos(name), [APPROVER, SECOND, NIK])
		self.assertEqual(version(name)["submitted_by"], AUTHOR)

		# 3. Parker's own approval, and Administrator's, are refused, naming the rule.
		message = refused(self, api.approve_and_publish, name, opened(name), user=AUTHOR)
		self.assertIn("only a KB Approver can approve a version", message)
		message = refused(self, api.approve_and_publish, name, opened(name), user="Administrator")
		self.assertIn("Administrator is a shared account, not a person", message)
		self.assertIn("a named KB Approver must approve it", message)

		# 4. James requests changes; review ToDos close; Parker gets one; the note stays on the version.
		request(api.request_changes, name, "Fix step 2. " + SENTINELS["review_note"], user=APPROVER)
		self.assertEqual(version(name)["review_state"], "Draft")
		self.assertEqual(version(name)["reviewer"], APPROVER)
		self.assertIn(SENTINELS["review_note"], version(name)["review_note"])
		self.assertEqual(open_todos(name), [AUTHOR])
		# James edits one word, and Parker resubmits: James is now a contributor, so not asked.
		edit(name, APPROVER, body=BODY.replace("Scan the slip.", "Scan the packing slip."))
		self.assertEqual(version(name)["contributors"], f"{AUTHOR}\n{APPROVER}")
		out = request(api.submit_for_review, name, user=AUTHOR)
		self.assertEqual(sorted(p["user"] for p in out["notified"]), [SECOND, NIK])
		self.assertEqual(open_todos(name), [SECOND, NIK])
		message = refused(self, api.approve_and_publish, name, opened(name), user=APPROVER)
		self.assertIn("you changed its content, so a different KB Approver must approve it", message)
		# Content edits while In Review are refused for everyone.
		for user in (AUTHOR, APPROVER, NIK):
			with self.subTest(user=user):
				message = refused(self, edit, name, user, body="<p>changed</p>")
				self.assertIn("In Review", message)

		# 5. Nik approves from a browser.
		out = approve(name)
		self.assertEqual(out["article"], "KB-0601")
		self.assertEqual(out["version_number"], 1)
		self.assertEqual(open_todos(), [])
		article = _row(ARTICLE, "KB-0601")
		self.assertEqual(article["approved_by"], NIK)
		self.assertEqual(article["author"], AUTHOR)
		self.assertNotEqual(article["approved_by"], article["author"])
		self.assertEqual(version(name)["review_state"], "Published")
		self.assertEqual(version(name)["docstatus"], 1)

	def test_no_draft_text_reaches_a_todo_a_comment_or_a_bell(self):
		name = draft()
		submitted(name)
		request(api.request_changes, name, "Please fix " + SENTINELS["review_note"], user=APPROVER)
		submitted(name)
		approve(name)
		texts = [r.get("description") or "" for r in _db()["ToDo"].values()]
		texts += [r.get("content") or "" for r in _db()["Comment"].values()]
		texts += [str(bell) for bell in STATE["bells"]]
		texts += [str(e) for e in STATE["errors"]]
		self.assertTrue(texts)
		for text in texts:
			for field, sentinel in SENTINELS.items():
				with self.subTest(field=field, text=text[:60]):
					self.assertNotIn(sentinel, text)
		# The title and a link are all a ToDo carries.
		for todo in _db()["ToDo"].values():
			self.assertIn(TITLE, todo["description"])
			self.assertIn(f"/desk/knowledge-article-version/{name}", todo["description"])


# ------------------------------------------------------------------ the publish transaction


class TestPublishTransaction(Base):
	def _published(self):
		name = draft()
		submitted(name)
		STATE["writes"].clear()
		STATE["sql"].clear()
		STATE["markdown"].clear()
		stored = copy.deepcopy(version(name))
		out = approve(name)
		return name, stored, out

	def test_the_steps_run_in_order_in_one_request(self):
		name, _stored, _out = self._published()
		sequence = [(w["doctype"], w["action"]) for w in STATE["writes"] if w["doctype"] in (ARTICLE, "File", VERSION)]
		self.assertEqual(sequence, [(ARTICLE, "insert"), ("File", "save"), (VERSION, "submit")])
		# ToDos close after the submit.
		todo_writes = [i for i, w in enumerate(STATE["writes"]) if w["doctype"] == "ToDo"]
		submit_at = next(i for i, w in enumerate(STATE["writes"]) if w["action"] == "submit")
		self.assertTrue(todo_writes and min(todo_writes) > submit_at)

	def test_the_number_is_allocated_under_for_update_with_the_percent_bound(self):
		self._published()
		allocation = [(q, v) for q, v in STATE["sql"] if "like" in q]
		self.assertEqual(allocation, [("select name from `tabKnowledge Article` where name like %s for update", ("KB-06%",))])

	def test_every_write_opts_in_by_name(self):
		self._published()
		by_doctype = {w["doctype"]: w for w in STATE["writes"] if w["action"] != "insert" or w["doctype"] == ARTICLE}
		self.assertTrue(by_doctype[ARTICLE]["flags"].get("kb_action"))
		self.assertTrue(by_doctype[ARTICLE]["flags"].get("ignore_permissions"))
		self.assertTrue(by_doctype["File"]["flags"].get("kb_action"))
		submit = next(w for w in STATE["writes"] if w["action"] == "submit")
		self.assertTrue(submit["flags"].get("kb_publish"))
		self.assertTrue(submit["flags"].get("ignore_permissions"))
		self.assertIsNotNone(submit["flags"].get("kb_opened_modified"))

	def test_the_article_is_the_stored_version_and_the_version_content_never_changes(self):
		from erpnext_enhancements.knowledge_base import content

		name, stored, _out = self._published()
		article = _row(ARTICLE, "KB-0601")
		for field in ("title", "department_block", "summary", "keywords", "body", "change_note"):
			with self.subTest(field=field):
				self.assertEqual(article[field], stored[field])
				self.assertEqual(version(name)[field], stored[field])
		self.assertEqual(STATE["markdown"], [stored["body"]])
		self.assertEqual(article["body_md"], _to_markdown(stored["body"]))
		self.assertEqual(article["content_hash"], content.content_hash(stored))
		self.assertEqual(article["live_version"], name)
		self.assertEqual(article["status"], "Published")
		self.assertEqual(article["version_number"], 1)
		self.assertEqual(article["review_every_months"], 6)
		self.assertEqual(article["last_reviewed_by"], NIK)
		self.assertEqual(article["review_by"], datetime.date(2027, 3, 28))
		self.assertIsNotNone(article["first_published_on"])

	def test_the_used_files_move_to_the_article_and_nothing_is_deleted(self):
		name, _stored, out = self._published()
		self.assertEqual(out["files_moved"], ["file-used"])
		used, unused = _row("File", "file-used"), _row("File", "file-unused")
		self.assertEqual((used["attached_to_doctype"], used["attached_to_name"]), (ARTICLE, "KB-0601"))
		self.assertEqual(used["file_url"], "/private/files/slip.png")
		self.assertEqual((unused["attached_to_doctype"], unused["attached_to_name"]), (VERSION, name))
		self.assertEqual(len(_db()["File"]), 2)

	def test_a_failure_at_any_step_leaves_nothing_written(self):
		name = draft()
		submitted(name)
		before = copy.deepcopy(_db())
		STATE["fail"]["markdown"] = [AttributeError("HTMLParseError")]
		message = refused(self, approve, name)
		self.assertIn("was not published", message)
		self.assertEqual(_db()[ARTICLE], {})
		self.assertEqual(_db()["File"], before["File"])
		self.assertEqual(version(name)["review_state"], "In Review")
		self.assertEqual(open_todos(name), [APPROVER, SECOND, NIK])
		# Only the exception's type is logged, never the text.
		self.assertTrue(any("AttributeError" in (m or "") for _t, m in STATE["errors"]))
		self.assertFalse(any(SENTINELS["body"] in (m or "") for _t, m in STATE["errors"]))

	def test_the_approval_rules_are_asked_before_anything_is_written(self):
		name = draft()
		submitted(name)
		STATE["writes"].clear()
		refused(self, api.approve_and_publish, name, "2020-01-01 00:00:00", user=NIK)
		self.assertEqual(STATE["writes"], [])
		self.assertEqual(_db()[ARTICLE], {})

	def test_user_type_is_read_from_the_user_row_at_approval(self):
		name = draft()
		submitted(name)
		_db()["User"][NIK]["user_type"] = "Website User"
		STATE["user_type_reads"].clear()
		message = refused(self, approve, name)
		self.assertIn("only a System User (a staff login) can approve", message)
		self.assertIn(NIK, STATE["user_type_reads"])


class TestNumbersAndConcurrency(Base):
	def test_two_articles_in_one_block_get_consecutive_numbers(self):
		first, second = draft(), draft()
		submitted(first)
		submitted(second)
		self.assertEqual(approve(first)["article"], "KB-0601")
		self.assertEqual(approve(second, user=SECOND)["article"], "KB-0602")

	def test_one_more_than_the_highest_in_the_block_never_a_gap(self):
		_db()[ARTICLE]["KB-0605"] = {"name": "KB-0605", "kb_number": "KB-0605", "status": "Published"}
		_db()[ARTICLE]["KB-0701"] = {"name": "KB-0701", "kb_number": "KB-0701", "status": "Published"}
		name = draft()
		submitted(name)
		self.assertEqual(approve(name)["article"], "KB-0606")

	def test_a_deadlock_is_retried_from_the_start_in_a_new_transaction(self):
		name = draft()
		submitted(name)
		STATE["fail"]["allocate"] = [Deadlock("1213")]
		out = approve(name)
		self.assertEqual(out["article"], "KB-0601")
		self.assertEqual(STATE["rollbacks"], 1)
		self.assertEqual(len(_db()[ARTICLE]), 1)

	def test_repeated_deadlocks_end_in_words_and_nothing_written(self):
		name = draft()
		submitted(name)
		STATE["fail"]["allocate"] = [Deadlock("1213") for _ in range(publish.ATTEMPTS)]
		message = refused(self, approve, name)
		self.assertIn("Nothing was saved", message)
		self.assertEqual(_db()[ARTICLE], {})
		self.assertEqual(version(name)["review_state"], "In Review")

	def test_a_double_click_publishes_once(self):
		name = draft()
		submitted(name)
		page = opened(name)
		request(api.approve_and_publish, name, page, user=NIK)
		message = refused(self, api.approve_and_publish, name, page, user=NIK)
		self.assertIn("it is Published, not In Review", message)
		self.assertIn("it changed after you opened it", message)
		self.assertEqual(len(_db()[ARTICLE]), 1)
		self.assertEqual(len([w for w in STATE["writes"] if w["action"] == "submit"]), 1)

	def test_each_action_locks_what_it_changes(self):
		name = draft()
		STATE["locks"].clear()
		submitted(name)
		self.assertIn((VERSION, name), STATE["locks"])


class TestRevisions(Base):
	def _live(self):
		name = draft()
		submitted(name)
		approve(name)
		return name

	def test_a_revision_copies_the_live_version_and_is_the_only_open_one(self):
		first = self._live()
		out = request(api.start_revision, "KB-0601", user=AUTHOR)
		self.assertTrue(out["created"])
		new = version(out["version"])
		for field in ("title", "department_block", "summary", "keywords", "body"):
			with self.subTest(field=field):
				self.assertEqual(new[field], version(first)[field])
		self.assertFalse(new.get("change_note"))
		self.assertEqual((new["article"], new["base_version"], new["version_number"]), ("KB-0601", first, 2))
		self.assertEqual(new["review_state"], "Draft")
		self.assertEqual(new["owner"], AUTHOR)
		self.assertEqual(new["contributors"], AUTHOR)
		again = request(api.start_revision, "KB-0601", user=APPROVER)
		self.assertEqual(again, {"version": out["version"], "created": False, "review_state": "Draft"})
		open_ones = [
			r for r in _db()[VERSION].values()
			if r.get("article") == "KB-0601" and r.get("review_state") in ("Draft", "In Review")
		]
		self.assertEqual(len(open_ones), 1)

	def test_publishing_a_revision_supersedes_the_old_version_and_closes_its_todos(self):
		first = self._live()
		# A ToDo left on the old version (say, by a later feature) closes when it is superseded.
		notify_todo = frappe_module().get_doc(
			{"doctype": "ToDo", "allocated_to": SECOND, "reference_type": VERSION, "reference_name": first, "description": "x"}
		)
		notify_todo.flags.kb_action = True
		request(notify_todo.insert, user=NIK)
		revision = request(api.start_revision, "KB-0601", user=AUTHOR)["version"]
		edit(revision, AUTHOR, body=BODY + "<p>One more step.</p>", change_note="Added a step")
		submitted(revision)
		STATE["writes"].clear()
		out = request(api.approve_and_publish, revision, opened(revision), user=APPROVER)
		self.assertEqual((out["article"], out["version_number"], out["superseded"]), ("KB-0601", 2, first))
		self.assertEqual(version(first)["review_state"], "Superseded")
		self.assertEqual(version(first)["docstatus"], 1)
		uas = next(w for w in STATE["writes"] if w["action"] == "update_after_submit")
		self.assertTrue(uas["flags"].get("kb_action"))
		article = _row(ARTICLE, "KB-0601")
		self.assertEqual((article["live_version"], article["version_number"], article["approved_by"]), (revision, 2, APPROVER))
		self.assertEqual(open_todos(first), [])
		self.assertEqual([w["action"] for w in writes(ARTICLE)], ["save"])

	def test_a_revision_keeps_its_department(self):
		self._live()
		revision = request(api.start_revision, "KB-0601", user=AUTHOR)["version"]
		edit(revision, AUTHOR, department_block="03 Finance")
		message = refused(self, api.submit_for_review, revision, user=AUTHOR)
		self.assertIn("an article keeps its department", message)

	def test_a_retired_article_cannot_be_revised(self):
		self._live()
		request(api.retire, "KB-0601", "Replaced by the scanner SOP.", user=APPROVER)
		message = refused(self, api.start_revision, "KB-0601", user=AUTHOR)
		self.assertIn("it is Retired", message)

	def test_a_reader_cannot_revise(self):
		self._live()
		refused(self, api.start_revision, "KB-0601", user=TECH, exc=PermissionRefused)


# ------------------------------------------------------------------ every transition, through the endpoints


class TestEveryTransitionThroughTheEndpoints(Base):
	def test_withdraw_by_the_author_closes_the_review_todos(self):
		name = draft()
		submitted(name)
		out = request(api.withdraw, name, user=AUTHOR)
		self.assertEqual(out["review_state"], "Draft")
		self.assertEqual(open_todos(name), [])

	def test_discard_a_draft_closes_its_todos_and_keeps_it(self):
		name = draft()
		submitted(name)
		request(api.request_changes, name, "Rethink it.", user=APPROVER)
		self.assertEqual(open_todos(name), [AUTHOR])
		request(api.discard, name, user=AUTHOR)
		self.assertEqual(version(name)["review_state"], "Discarded")
		self.assertEqual(open_todos(name), [])
		self.assertIn(name, _db()[VERSION])
		message = refused(self, edit, name, AUTHOR, title="Back again")
		self.assertIn("discarded", message)

	def test_every_endpoint_refuses_a_state_it_does_not_move(self):
		name = draft()
		cases = (
			(api.withdraw, (name,), AUTHOR, "it is Draft"),
			(api.request_changes, (name, "note"), APPROVER, "it is Draft"),
			(api.approve_and_publish, (name, None), NIK, "it is Draft, not In Review"),
		)
		for fn, args, user, phrase in cases:
			with self.subTest(fn=fn.__name__):
				self.assertIn(phrase, refused(self, fn, *args, user=user))
		submitted(name)
		self.assertIn("it is In Review", refused(self, api.submit_for_review, name, user=AUTHOR))
		self.assertIn("it is In Review", refused(self, api.discard, name, user=AUTHOR))
		approve(name)
		for fn, args in ((api.withdraw, (name,)), (api.discard, (name,)), (api.submit_for_review, (name,))):
			with self.subTest(fn=fn.__name__, state="Published"):
				self.assertIn("it is Published", refused(self, fn, *args, user=AUTHOR))

	def test_the_transition_writer_refuses_a_move_not_in_the_table(self):
		name = draft()

		def bad():
			doc = frappe_module().get_doc(VERSION, name, for_update=True)
			publish.transition(doc, "supersede")

		self.assertIn("only a version that is Published can be superseded", refused(self, bad))

	def test_a_submit_needs_a_title_and_text(self):
		name = draft(body='<div class="ql-editor read-mode"><p><br></p></div>')
		self.assertIn("it has no text", refused(self, api.submit_for_review, name, user=AUTHOR))

	def test_no_kb_role_is_refused_before_anything_is_read(self):
		name = draft()
		for fn, args in (
			(api.submit_for_review, (name,)),
			(api.withdraw, (name,)),
			(api.request_changes, (name, "x")),
			(api.approve_and_publish, (name, None)),
			(api.discard, (name,)),
			(api.start_revision, ("KB-0601",)),
		):
			with self.subTest(fn=fn.__name__):
				STATE["locks"].clear()
				refused(self, fn, *args, user=TECH, exc=PermissionRefused)
				self.assertEqual(STATE["locks"], [])

	def test_a_note_is_required_and_may_not_carry_a_secret(self):
		name = draft()
		submitted(name)
		self.assertIn("say what needs to change", refused(self, api.request_changes, name, "  ", user=APPROVER))
		message = refused(self, api.request_changes, name, "Use " + STRIPE_KEY, user=APPROVER)
		self.assertIn("looks like a Stripe secret key", message)
		self.assertNotIn(STRIPE_KEY, message)


# ------------------------------------------------------------------ tokens, AI cards, and who


class TestTheApprovalPathRefusesTokens(Base):
	def setUp(self):
		super().setUp()
		self.name = draft()
		submitted(self.name)
		self.name_article = None

	def _live_article(self):
		approve(self.name)
		return "KB-0601"

	def test_a_token_cannot_approve_send_back_or_read_a_draft(self):
		for token, browser in (("token abc:def", True), ("Bearer ya29.x", True), (None, False)):
			with self.subTest(token=token, browser=browser):
				for fn, args in (
					(api.approve_and_publish, (self.name, opened(self.name))),
					(api.request_changes, (self.name, "x")),
					(api.review_diff, (self.name,)),
				):
					message = refused(self, fn, *args, user=NIK, token=token, browser=browser)
					self.assertIn("browser", message)
		self.assertEqual(version(self.name)["review_state"], "In Review")

	def test_a_token_cannot_retire_or_confirm(self):
		article = self._live_article()
		for fn, args in ((api.retire, (article, "x")), (api.confirm_still_accurate, (article,))):
			with self.subTest(fn=fn.__name__):
				self.assertIn("browser", refused(self, fn, *args, user=APPROVER, token="token abc:def"))

	def test_an_ai_gate_card_cannot_approve_even_confirmed(self):
		for flags in ({"ai_gate_bypass": True}, {"ai_gate_pending": "AIPA-0001"}):
			with self.subTest(flags=flags):
				message = refused(self, api.approve_and_publish, self.name, opened(self.name), user=NIK, flags=flags)
				self.assertIn("AI assistant", message)

	def test_author_actions_do_not_need_a_browser(self):
		out = request(api.withdraw, self.name, user=AUTHOR, token="token abc:def")
		self.assertEqual(out["review_state"], "Draft")


# ------------------------------------------------------------------ articles


class TestArticleActions(Base):
	def setUp(self):
		super().setUp()
		name = draft(process_owner=SECOND)
		submitted(name)
		approve(name)

	def test_retire_with_a_reason(self):
		out = request(api.retire, "KB-0601", "  Replaced by KB-0602.  ", user=APPROVER)
		self.assertEqual(out["status"], "Retired")
		article = _row(ARTICLE, "KB-0601")
		self.assertEqual((article["retired_by"], article["retired_reason"]), (APPROVER, "Replaced by KB-0602."))
		self.assertTrue(writes(ARTICLE)[-1]["flags"].get("kb_action"))
		self.assertIn("already Retired", refused(self, api.retire, "KB-0601", "again", user=APPROVER))

	def test_retire_refusals(self):
		self.assertIn("give a reason", refused(self, api.retire, "KB-0601", "", user=APPROVER))
		self.assertIn("only a KB Approver can retire", refused(self, api.retire, "KB-0601", "x", user=AUTHOR))
		self.assertIn(
			"looks like a Stripe secret key", refused(self, api.retire, "KB-0601", "key " + STRIPE_KEY, user=APPROVER)
		)
		revision = request(api.start_revision, "KB-0601", user=AUTHOR)["version"]
		self.assertIn(f"{revision} is still open on it", refused(self, api.retire, "KB-0601", "x", user=APPROVER))

	def test_confirm_by_the_process_owner_restarts_the_review_clock(self):
		out = request(api.confirm_still_accurate, "KB-0601", user=SECOND)
		article = _row(ARTICLE, "KB-0601")
		self.assertEqual(article["last_reviewed_by"], SECOND)
		self.assertEqual(article["review_by"], datetime.date(2027, 3, 28))
		self.assertEqual(out["article"], "KB-0601")

	def test_confirm_by_someone_else_is_refused(self):
		self.assertIn(
			"only its process owner or a KB Approver", refused(self, api.confirm_still_accurate, "KB-0601", user=AUTHOR)
		)

	def test_nothing_else_can_write_an_article(self):
		def direct():
			doc = frappe_module().get_doc(ARTICLE, "KB-0601")
			doc.title = "Edited"
			doc.save(ignore_permissions=True)

		self.assertIn("cannot be edited directly", refused(self, direct, user=NIK))


# ------------------------------------------------------------------ review_diff


class TestReviewDiff(Base):
	def test_a_first_version_against_nothing(self):
		name = draft()
		out = request(api.review_diff, name, user=AUTHOR)
		self.assertIsNone(out["article"])
		self.assertEqual({f["field"] for f in out["fields"]} >= {"title", "department_block", "summary"}, True)
		self.assertTrue(all(line.startswith(("@@", "+")) for line in out["body"]))

	def test_a_revision_against_the_published_text(self):
		name = draft()
		submitted(name)
		approve(name)
		revision = request(api.start_revision, "KB-0601", user=AUTHOR)["version"]
		edit(revision, AUTHOR, title="Receiving a PO", body=BODY.replace("Scan the slip.", "Scan every slip."))
		out = request(api.review_diff, revision, user=APPROVER)
		self.assertEqual(out["article"], "KB-0601")
		self.assertEqual([f["field"] for f in out["fields"]], ["title"])
		self.assertEqual(out["fields"][0]["before"], TITLE)
		self.assertIn("-Scan the slip. " + SENTINELS["body"], out["body"])
		self.assertIn("+Scan every slip. " + SENTINELS["body"], out["body"])

	def test_readers_tokens_and_ai_cards_are_refused(self):
		name = draft()
		refused(self, api.review_diff, name, user=TECH)
		refused(self, api.review_diff, name, user=AUTHOR, token="token abc:def")
		refused(self, api.review_diff, name, user=AUTHOR, flags={"ai_gate_bypass": True})


# ------------------------------------------------------------------ ToDos


class TestReviewToDos(Base):
	def test_raised_for_every_eligible_approver_only(self):
		name = draft(ai_requested_by=SECOND)
		request(api.submit_for_review, name, user=APPROVER)
		# Parker created it, James submitted it, Lisa asked an AI; disabled and portal users and
		# Administrator are never asked.
		self.assertEqual(open_todos(name), [NIK])

	def test_nobody_free_to_approve_is_said_not_refused(self):
		name = draft(user=NIK)
		for user in (APPROVER, SECOND):
			edit(name, user, keywords=f"edited by {user}")
		out = request(api.submit_for_review, name, user=NIK)
		self.assertEqual(out["notified"], [])
		self.assertEqual(version(name)["review_state"], "In Review")

	def test_a_todo_that_cannot_be_written_does_not_stop_the_move_or_pop_a_modal(self):
		name = draft()
		original = notify._raise

		def failing(doc, user, description):
			if user == SECOND:
				frappe_module().throw("ToDo broke")
			return original(doc, user, description)

		notify._raise = failing
		try:
			out = request(api.submit_for_review, name, user=AUTHOR)
		finally:
			notify._raise = original
		self.assertEqual(sorted(p["user"] for p in out["notified"]), [APPROVER, NIK])
		self.assertEqual(open_todos(name), [APPROVER, NIK])
		self.assertEqual(frappe_module().local.message_log, [])
		self.assertTrue(any("raise a review to-do for lisa" in (m or "") for _t, m in STATE["errors"]))

	def test_a_deadlock_inside_a_todo_write_is_not_swallowed(self):
		name = draft()
		original = notify._raise

		def deadlocked(doc, user, description):
			raise Deadlock("1213")

		notify._raise = deadlocked
		try:
			with self.assertRaises(Deadlock):
				request(notify.raise_review_todos, frappe_module().get_doc(VERSION, name), user=AUTHOR)
		finally:
			notify._raise = original

	def test_inline_never_enqueued(self):
		source = (APP / "knowledge_base" / "notify.py").read_text(encoding="utf-8")
		code = "\n".join(line for line in source.splitlines() if not line.strip().startswith("#"))
		code = re.sub(r'"""[\s\S]*?"""', "", code)
		self.assertNotIn("enqueue", code)


# ------------------------------------------------------------------ decision (a): a version's Files


class TestVersionFilesAreProtectedOnceOutOfDraft(Base):
	def _file(self, doctype, name, **flags):
		return types.SimpleNamespace(attached_to_doctype=doctype, attached_to_name=name, flags=_Flags(flags))

	def test_a_drafts_files_may_be_deleted_and_nothing_else(self):
		name = draft()
		self.assertIs(files.file_has_permission(self._file(VERSION, name), "delete"), True)
		submitted(name)
		self.assertIs(files.file_has_permission(self._file(VERSION, name), "delete"), False)
		approve(name)
		self.assertIs(files.file_has_permission(self._file(VERSION, name), "delete"), False)
		self.assertIs(files.file_has_permission(self._file(ARTICLE, "KB-0601"), "delete"), False)

	def test_superseded_and_discarded_are_protected_too(self):
		for state, docstatus in (("Superseded", 1), ("Discarded", 0), ("In Review", 0), ("Published", 1)):
			with self.subTest(state=state):
				_db()[VERSION]["KBV-09999"] = {"name": "KBV-09999", "review_state": state, "docstatus": docstatus}
				self.assertIs(files.file_has_permission(self._file(VERSION, "KBV-09999"), "delete"), False)

	def test_kb_code_may_delete_by_saying_so_and_other_rights_are_untouched(self):
		name = draft()
		submitted(name)
		self.assertIs(files.file_has_permission(self._file(VERSION, name, kb_action=True), "delete"), True)
		for ptype in ("read", "write", "create", "share", None):
			with self.subTest(ptype=ptype):
				self.assertIs(files.file_has_permission(self._file(VERSION, name), ptype), True)

	def test_a_version_that_cannot_be_found_is_not_a_draft(self):
		self.assertIs(files.file_has_permission(self._file(VERSION, "KBV-00404"), "delete"), False)
		self.assertIs(files.file_has_permission(self._file(VERSION, ""), "delete"), False)

	def test_detaching_a_version_file_is_refused_already(self):
		name = draft()

		def detach():
			doc = frappe_module().get_doc("File", "file-used")
			doc.attached_to_doctype = "Project"
			doc.attached_to_name = "PRJ-00001"
			doc.save(ignore_permissions=True)

		self.assertIn("stays there", refused(self, detach, user=AUTHOR))
		self.assertEqual(_row("File", "file-used")["attached_to_name"], name)


# ------------------------------------------------------------------ decision (b): Comments and ToDos


class TestNoTypedTextAboutADraft(Base):
	def _comment(self, **values):
		return frappe_module().get_doc({"doctype": "Comment", "comment_type": "Comment", **values})

	def test_a_typed_comment_on_a_version_is_refused(self):
		name = draft()
		doc = self._comment(reference_doctype=VERSION, reference_name=name, content="change valve B")
		self.assertIn("Comments are not kept on a knowledge base draft", refused(self, doc.insert, user=APPROVER))
		self.assertEqual(_db()["Comment"], {})

	def test_an_existing_typed_comment_cannot_be_edited(self):
		name = draft()
		_db()["Comment"]["c-1"] = {
			"name": "c-1", "comment_type": "Comment", "reference_doctype": VERSION, "reference_name": name,
			"content": "old", "modified": _tick(), "docstatus": 0,
		}

		def change():
			doc = frappe_module().get_doc("Comment", "c-1")
			doc.content = "draft text"
			doc.save(ignore_permissions=True)

		refused(self, change, user=APPROVER)

	def test_framework_comments_other_doctypes_and_kb_code_pass(self):
		name = draft()
		for values in (
			{"comment_type": "Assigned", "reference_doctype": VERSION, "reference_name": name},
			{"comment_type": "Attachment", "reference_doctype": VERSION, "reference_name": name},
			{"comment_type": "Comment", "reference_doctype": ARTICLE, "reference_name": "KB-0601"},
			{"comment_type": "Comment", "reference_doctype": "Project", "reference_name": "PRJ-1"},
		):
			with self.subTest(values=values):
				request(self._comment(**values).insert, user=APPROVER)
		flagged = self._comment(reference_doctype=VERSION, reference_name=name)
		flagged.flags.kb_action = True
		request(flagged.insert, user=APPROVER)

	def test_a_hand_made_todo_on_a_version_is_refused(self):
		name = draft()
		todo = frappe_module().get_doc(
			{"doctype": "ToDo", "allocated_to": SECOND, "reference_type": VERSION, "reference_name": name, "description": "look at step 2: close valve B"}
		)
		self.assertIn("cannot be assigned by hand", refused(self, todo.insert, user=APPROVER))

	def test_a_kb_todo_can_be_closed_but_its_text_cannot_change(self):
		name = draft()
		submitted(name)
		todo_name = next(n for n, r in _db()["ToDo"].items() if r["allocated_to"] == SECOND)

		def edit_text():
			doc = frappe_module().get_doc("ToDo", todo_name)
			doc.description = "draft text"
			doc.save(ignore_permissions=True)

		def close():
			doc = frappe_module().get_doc("ToDo", todo_name)
			doc.status = "Closed"
			doc.save(ignore_permissions=True)

		self.assertIn("cannot be changed", refused(self, edit_text, user=SECOND))
		request(close, user=SECOND)
		self.assertEqual(_row("ToDo", todo_name)["status"], "Closed")

	def test_every_other_comment_and_todo_on_the_site_is_left_alone_without_a_query(self):
		class Untouchable:
			def __init__(self, **values):
				self.__dict__.update(values)
				self.flags = _Flags()

			def get(self, key, default=None):
				return self.__dict__.get(key, default)

			def get_doc_before_save(self):
				return None

		STATE["db"] = None  # any read of the store would now fail
		try:
			references.guard_comment(Untouchable(comment_type="Comment", reference_doctype="Project"), "before_validate")
			references.guard_todo(Untouchable(reference_type="Task", description="x"), "before_validate")
			references.guard_todo(Untouchable(), "before_validate")
			references.guard_comment(object(), "before_validate")
			references.guard_todo(object(), "before_validate")
		finally:
			_reset()


# ------------------------------------------------------------------ the forms' buttons


class TestTheFormsOfferTheRules(Base):
	def _version_buttons(self, name, user, **kwargs):
		def load():
			doc = frappe_module().get_doc(VERSION, name)
			doc.run_method("onload")
			return doc._onload["kb"]

		return request(load, user=user, **kwargs)

	def test_version_buttons_per_person(self):
		name = draft()
		self.assertEqual(self._version_buttons(name, AUTHOR)["actions"], ["submit_for_review", "discard", "review_diff"])
		self.assertEqual(self._version_buttons(name, TECH)["actions"], [])
		submitted(name)
		self.assertEqual(self._version_buttons(name, AUTHOR)["actions"], ["withdraw", "review_diff"])
		self.assertEqual(
			self._version_buttons(name, NIK)["actions"], ["approve_and_publish", "request_changes", "review_diff"]
		)
		self.assertEqual(self._version_buttons(name, NIK, token="token abc:def")["actions"], [])
		blocked = self._version_buttons(name, "Administrator")
		self.assertNotIn("approve_and_publish", blocked["actions"])
		self.assertTrue(any("Administrator" in b for b in blocked["approve_blockers"]))

	def test_article_buttons_per_person_and_readers_learn_nothing_of_drafts(self):
		name = draft(process_owner=SECOND)
		submitted(name)
		approve(name)
		revision = request(api.start_revision, "KB-0601", user=AUTHOR)["version"]

		def load(user, **kwargs):
			def go():
				doc = frappe_module().get_doc(ARTICLE, "KB-0601")
				doc.run_method("onload")
				return doc._onload["kb"]

			return request(go, user=user, **kwargs)

		self.assertEqual(load(TECH), {"actions": [], "open_version": None, "open_state": None})
		self.assertEqual(load(AUTHOR)["actions"], ["start_revision"])
		self.assertEqual(load(AUTHOR)["open_version"], revision)
		self.assertEqual(load(SECOND)["actions"], ["start_revision", "confirm_still_accurate"])
		self.assertEqual(load(NIK)["actions"], ["start_revision", "confirm_still_accurate"])


# ------------------------------------------------------------------ wiring


class TestWiring(Base):
	def _hooks(self, name):
		tree = ast.parse((APP / "hooks.py").read_text(encoding="utf-8"))
		return next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == name)

	def test_the_form_scripts_are_registered_and_exist(self):
		doctype_js = self._hooks("doctype_js")
		for doctype, path in (
			(VERSION, "public/js/knowledge_base/knowledge_article_version.js"),
			(ARTICLE, "public/js/knowledge_base/knowledge_article.js"),
		):
			with self.subTest(doctype=doctype):
				self.assertEqual(doctype_js[doctype], path)
				self.assertTrue((APP / path).exists())

	def test_the_doctype_folders_hold_no_form_script_of_their_own(self):
		for folder in ("knowledge_article", "knowledge_article_version"):
			with self.subTest(folder=folder):
				self.assertEqual(list((APP / "knowledge_base" / "doctype" / folder).glob("*.js")), [])

	def test_the_guards_are_registered_on_before_validate(self):
		events = self._hooks("doc_events")
		self.assertEqual(
			events["Comment"]["before_validate"], "erpnext_enhancements.knowledge_base.references.guard_comment"
		)
		self.assertEqual(events["ToDo"]["before_validate"], "erpnext_enhancements.knowledge_base.references.guard_todo")

	def test_every_endpoint_in_the_forms_exists(self):
		for script in ("knowledge_article_version.js", "knowledge_article.js"):
			source = (APP / "public" / "js" / "knowledge_base" / script).read_text(encoding="utf-8")
			for method in re.findall(r'kb_call\(\s*"([a-z_]+)"', source):
				with self.subTest(script=script, method=method):
					self.assertIn(method, TestEndpointsAreWhitelistedByMethod.EXPECTED)

	def test_approve_sends_the_modified_the_page_loaded(self):
		source = (APP / "public" / "js" / "knowledge_base" / "knowledge_article_version.js").read_text(encoding="utf-8")
		self.assertIn("{ version: frm.doc.name, modified: frm.doc.modified }", source)


if __name__ == "__main__":
	unittest.main()
