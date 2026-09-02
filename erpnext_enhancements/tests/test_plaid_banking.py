"""Pure-Python (no Frappe site) tests for the Plaid bank-balances widget.

Plain pytest functions, like ``test_quickbooks_online`` / ``test_stripe_payments``.
:func:`install_frappe_stub` installs a minimal fake ``frappe`` / ``frappe.utils`` /
``frappe.model`` / ``requests`` into ``sys.modules`` and RE-ASSERTS every attribute it
needs on each call, so the suite is self-contained whether or not another stub-installing
suite ran first in the same process (CI still gives it its own step).

What is pinned here, and why each matters:

* the keys and the environment are read from ERPNext's **native** ``Plaid Settings``,
  never from our Single -- the two used to share a name and this is the split that
  un-collides them;
* the per-bank error policy: an Item error isolates ONE bank and never pauses, a config
  error pauses everything and stops, a transient error carries on;
* the mapping-helper refusals that keep native Link from stranding the Bank Account
  masters, and the absorb refusals around GL Entries / Bank Transactions;
* the rename patch's exact calls and SQL -- moving the wrong ``tabSingles`` rows would
  break the native Link silently and the migrate would still exit 0;
* the defaults backfill fills blanks only.

Run: python -m pytest erpnext_enhancements/tests/test_plaid_banking.py -q
"""

from __future__ import annotations

import datetime as _dt
import importlib
import json
import sys
import types

NOW = _dt.datetime(2026, 9, 2, 12, 0, 0)


class Thrown(Exception):
	"""What the stubbed ``frappe.throw`` raises."""


def _stub_throw(message=None, *args, **kwargs):
	raise Thrown(message if isinstance(message, str) else "frappe.throw")


class FakeDoc(dict):
	"""A dict with attribute access, ``get_password`` and a counting ``save``.

	Stands in for both Singles: ours (attribute writes from ``update_settings_status``)
	and the native one (``.get()`` reads + ``get_password``).
	"""

	_own = ("doctype", "_passwords", "saved")

	def __init__(self, doctype, values=None, passwords=None):
		super().__init__(values or {})
		object.__setattr__(self, "doctype", doctype)
		object.__setattr__(self, "_passwords", passwords or {})
		object.__setattr__(self, "saved", 0)

	def __getattr__(self, key):
		if key.startswith("__"):
			raise AttributeError(key)
		return self.get(key)

	def __setattr__(self, key, value):
		if key in self._own:
			object.__setattr__(self, key, value)
		else:
			self[key] = value

	def get_password(self, fieldname="password", raise_exception=True):
		value = self._passwords.get(fieldname)
		if not value and raise_exception:
			raise Exception(f"no password for {fieldname}")
		return value

	def save(self, *args, **kwargs):
		object.__setattr__(self, "saved", self.saved + 1)


class FakeDB:
	"""Records every write so tests can assert on order and arguments."""

	def __init__(self):
		self.sql_calls = []
		self.set_value_calls = []
		self.set_single_calls = []
		self.commits = 0
		self.savepoints = []
		self.rollbacks = []
		self.docs = {}  # (doctype, name) -> dict
		self.counts = {}  # (doctype, json filters) -> int
		self.exists_extra = set()
		self.missing_tables = set()  # doctypes whose table_exists() answers False
		self.missing_columns = set()  # (doctype, column) pairs whose has_column() answers False

	def table_exists(self, doctype, cached=True):
		return doctype not in self.missing_tables

	def has_column(self, doctype, column):
		return (doctype, column) not in self.missing_columns

	# -- reads ------------------------------------------------------------
	def exists(self, doctype, name=None, *args, **kwargs):
		if isinstance(name, dict):
			return self._match(doctype, name) is not None
		if (doctype, name) in self.docs:
			return name
		return name if (doctype, name) in self.exists_extra else None

	def get_value(self, doctype, name=None, fieldname="name", as_dict=False, **kwargs):
		row = self._match(doctype, name) if isinstance(name, dict) else self.docs.get((doctype, name))
		if row is None:
			return None
		if isinstance(fieldname, list | tuple):
			out = {f: row.get(f) for f in fieldname}
			# frappe returns a `_dict` (dict subclass with attribute access), so both
			# `row.get(...)` and `dict(row)` work in production; FakeDoc models that.
			return FakeDoc(doctype, out) if as_dict else tuple(out.values())
		return row.get(fieldname)

	def count(self, doctype, filters=None, **kwargs):
		return self.counts.get((doctype, json.dumps(filters, sort_keys=True, default=str)), 0)

	def _match(self, doctype, filters):
		for (dt, _name), row in self.docs.items():
			if dt != doctype:
				continue
			ok = True
			for key, cond in filters.items():
				if isinstance(cond, list | tuple):
					op, val = cond
					actual = row.get(key)
					if op == "!=" and not (actual != val):
						ok = False
					elif op == "not in" and actual in val:
						ok = False
					elif op == "in" and actual not in val:
						ok = False
				elif row.get(key) != cond:
					ok = False
			if ok:
				return row
		return None

	# -- writes -----------------------------------------------------------
	def sql(self, query, values=None, *args, **kwargs):
		self.sql_calls.append((" ".join(query.split()), values))
		return []

	def set_value(self, doctype, name, fieldname, value=None, **kwargs):
		values = dict(fieldname) if isinstance(fieldname, dict) else {fieldname: value}
		self.set_value_calls.append((doctype, name, values))
		if (doctype, name) in self.docs:
			self.docs[(doctype, name)].update(values)

	def set_single_value(self, doctype, fieldname, value):
		self.set_single_calls.append((doctype, fieldname, value))

	def commit(self):
		self.commits += 1

	def savepoint(self, name):
		self.savepoints.append(name)

	def rollback(self, save_point=None, **kwargs):
		self.rollbacks.append(save_point)


def install_frappe_stub():
	"""Install (or refresh) the fake ``frappe`` and return it."""
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
	frappe_utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")

	def _flt(value=0, precision=None):
		try:
			number = float(value or 0)
		except (TypeError, ValueError):
			return 0.0
		return round(number, precision) if precision is not None else number

	def _getdate(value=None):
		if value is None:
			return NOW.date()
		if isinstance(value, _dt.datetime):
			return value.date()
		if isinstance(value, _dt.date):
			return value
		return _dt.date.fromisoformat(str(value)[:10])

	frappe_utils.flt = _flt
	frappe_utils.cint = lambda value=0, *a, **k: int(_flt(value))
	frappe_utils.now_datetime = lambda: NOW
	frappe_utils.get_datetime = lambda value=None, *a, **k: value
	frappe_utils.add_to_date = lambda value=None, minutes=0, as_datetime=False, **k: value + _dt.timedelta(
		minutes=minutes
	)
	frappe_utils.getdate = _getdate
	frappe_utils.today = lambda: NOW.date().isoformat()
	frappe_utils.add_days = lambda value, days: _getdate(value) + _dt.timedelta(days=int(days))
	frappe.utils = frappe_utils

	frappe_model = sys.modules.get("frappe.model") or types.ModuleType("frappe.model")
	frappe_model.no_value_fields = (
		"Section Break",
		"Column Break",
		"Tab Break",
		"HTML",
		"Table",
		"Table MultiSelect",
		"Button",
		"Image",
		"Fold",
		"Heading",
	)
	frappe.model = frappe_model

	frappe.throw = _stub_throw
	frappe._ = lambda message=None, *a, **k: message
	frappe.whitelist = lambda *a, **k: (lambda fn: fn)
	frappe.only_for = lambda roles, *a, **k: None
	frappe.ValidationError = Thrown if not hasattr(frappe, "ValidationError") else frappe.ValidationError
	frappe.db = FakeDB()
	frappe.get_all = lambda *a, **k: []
	frappe.get_single = lambda doctype: FakeDoc(doctype)
	frappe.get_installed_apps = lambda: ["frappe", "erpnext", "erpnext_enhancements"]
	frappe.log_error = lambda *a, **k: None
	frappe.get_traceback = lambda *a, **k: ""
	frappe.logger = lambda *a, **k: types.SimpleNamespace(
		info=lambda *x, **y: None, warning=lambda *x, **y: None
	)
	frappe.clear_cache = lambda *a, **k: None
	frappe.clear_document_cache = lambda *a, **k: None
	frappe.cleared_messages = 0

	def _clear_last_message():
		frappe.cleared_messages += 1

	frappe.clear_last_message = _clear_last_message
	frappe.rename_doc = lambda *a, **k: None
	frappe.reload_doc = lambda *a, **k: None
	frappe.delete_doc = lambda *a, **k: None
	frappe.get_meta = lambda doctype: types.SimpleNamespace(fields=[])

	sys.modules.setdefault("frappe", frappe)
	sys.modules.setdefault("frappe.utils", frappe_utils)
	sys.modules.setdefault("frappe.model", frappe_model)

	requests = sys.modules.get("requests") or types.ModuleType("requests")
	if not hasattr(requests, "RequestException"):
		requests.RequestException = type("RequestException", (Exception,), {})
	requests.post = lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected HTTP call"))
	sys.modules.setdefault("requests", requests)
	return frappe


def _modules():
	"""Import (or re-import) the module chain under test against the current stub."""
	install_frappe_stub()
	names = [
		"erpnext_enhancements.plaid_banking.core.constants",
		"erpnext_enhancements.plaid_banking.core.utils",
		"erpnext_enhancements.plaid_banking.core.client",
		"erpnext_enhancements.plaid_banking.core.balances",
		"erpnext_enhancements.plaid_banking.core.link_accounts",
		"erpnext_enhancements.plaid_banking.core.api",
		"erpnext_enhancements.plaid_banking.core.tasks",
	]
	out = {}
	for name in names:
		if name in sys.modules:
			out[name.rsplit(".", 1)[1]] = importlib.reload(sys.modules[name])
		else:
			out[name.rsplit(".", 1)[1]] = importlib.import_module(name)
	return types.SimpleNamespace(**out)


def _native(client_id="cid-123", secret="sec-456", env="sandbox"):
	return FakeDoc(
		"Plaid Settings",
		{"plaid_client_id": client_id, "plaid_env": env, "enabled": 1},
		passwords={"plaid_secret": secret} if secret else {},
	)


def _singles(frappe, ours=None, native=None, snapshot=None):
	"""Route ``frappe.get_single`` by doctype and return the three docs."""
	docs = {
		"Plaid Banking Settings": ours or FakeDoc("Plaid Banking Settings", {"plaid_enabled": 1}),
		"Plaid Settings": native or _native(),
		"Bank Balance Snapshot": snapshot or FakeDoc("Bank Balance Snapshot"),
	}
	frappe.get_single = lambda doctype: docs[doctype]
	return docs


class FakeClient:
	"""Scripted PlaidClient: ``script[token]`` is a dict to return or an exception to raise."""

	def __init__(self, script):
		self.script = script
		self.calls = []
		# The real client holds the native settings doc; ``refresh_balances`` reads the keys
		# off it once before the bank loop. None routes ``get_credentials`` to get_single.
		self.native = None

	def __call__(self, *args, **kwargs):
		return self

	def _run(self, method, token):
		self.calls.append((method, token))
		outcome = self.script[token]
		if isinstance(outcome, Exception):
			raise outcome
		return outcome

	def get_balances(self, token):
		return self._run("balances", token)

	def get_accounts(self, token):
		return self._run("accounts", token)

	def item_get(self, token):
		return self._run("item", token)


def _plaid_balances(*names):
	return {
		"accounts": [
			{
				"account_id": f"acc-{n}",
				"name": n,
				"official_name": f"{n} Official",
				"mask": "1234",
				"type": "depository",
				"subtype": "checking",
				"balances": {"available": 10.0, "current": 12.5, "iso_currency_code": "USD"},
			}
			for n in names
		]
	}


# --- credentials + environment come from the NATIVE Single -------------------------


def test_credentials_read_from_native_single_not_ours():
	m = _modules()
	frappe = sys.modules["frappe"]
	asked = []

	def get_single(doctype):
		asked.append(doctype)
		return _native() if doctype == "Plaid Settings" else FakeDoc(doctype)

	frappe.get_single = get_single
	assert m.utils.get_credentials() == ("cid-123", "sec-456")
	assert asked == ["Plaid Settings"]
	assert m.constants.SETTINGS_DOCTYPE == "Plaid Banking Settings"
	assert m.constants.NATIVE_SETTINGS_DOCTYPE == "Plaid Settings"


def test_missing_native_keys_throw_pointing_at_the_native_form():
	m = _modules()
	for native in (_native(client_id=None), _native(secret=None)):
		try:
			m.utils.get_credentials(native)
			raise AssertionError("expected a throw")
		except Thrown as exc:
			assert "native Plaid Settings" in str(exc)
			assert "not in Plaid Banking Settings" in str(exc)


def test_environment_mapping_uses_native_values_and_retires_development():
	m = _modules()
	urls = m.constants.ENVIRONMENT_BASE_URLS
	assert m.client.PlaidClient(_native(env="sandbox")).get_base_url() == urls["sandbox"]
	assert m.client.PlaidClient(_native(env="production")).get_base_url() == "https://production.plaid.com"
	# Plaid retired Development; a native form can still say it, and it must not dead-end.
	assert m.client.PlaidClient(_native(env="development")).get_base_url() == urls["sandbox"]
	assert m.utils.get_environment(_native(env="Production ")) == "production"
	assert m.utils.get_environment(_native(env="")) == "sandbox"
	assert m.utils.get_environment(_native(env="bogus")) == "sandbox"


def test_client_error_envelope_never_echoes_secret_or_token():
	m = _modules()
	requests = sys.modules["requests"]
	sent = {}

	class Resp:
		status_code = 400
		text = '{"error_code": "ITEM_LOGIN_REQUIRED", "error_message": "the user must log in"}'

		def json(self):
			return json.loads(self.text)

	def post(url, json=None, headers=None, timeout=None):
		sent["url"] = url
		sent["body"] = json
		return Resp()

	requests.post = post
	try:
		m.client.PlaidClient(_native(env="production")).get_balances("access-TOKEN-xyz")
		raise AssertionError("expected PlaidError")
	except m.client.PlaidError as exc:
		assert exc.error_code == "ITEM_LOGIN_REQUIRED"
		assert exc.status_code == 400
		assert "sec-456" not in str(exc)
		assert "access-TOKEN-xyz" not in str(exc)
	assert sent["url"] == "https://production.plaid.com/accounts/balance/get"
	assert sent["body"] == {"client_id": "cid-123", "secret": "sec-456", "access_token": "access-TOKEN-xyz"}


def test_linked_banks_filters_empty_tokens_and_names_only_helper_is_safe():
	m = _modules()
	frappe = sys.modules["frappe"]
	frappe.get_all = lambda doctype, filters=None, fields=None, **k: [
		{"name": "Key Bank", "plaid_access_token": "tok-key"},
		{"name": "US Bank", "plaid_access_token": None},
		{"name": "America First", "plaid_access_token": ""},
	]
	assert m.utils.linked_banks() == [{"bank": "Key Bank", "access_token": "tok-key"}]
	assert m.utils.linked_bank_names() == ["Key Bank"]


# --- multi-bank refresh + the per-bank error policy --------------------------------


def _refresh(monkeypatch, m, banks, script):
	frappe = sys.modules["frappe"]
	docs = _singles(frappe)
	statuses = []

	def update_settings_status(status, message=None, **fields):
		statuses.append((status, message, fields))
		return docs["Plaid Banking Settings"]

	client = FakeClient(script)
	monkeypatch.setattr(m.balances, "linked_banks", lambda: banks)
	monkeypatch.setattr(m.balances, "PlaidClient", client)
	monkeypatch.setattr(m.balances, "update_settings_status", update_settings_status)
	return docs, statuses, client


def test_multi_bank_snapshot_shape_and_status():
	m = _modules()
	monkeypatch = _Monkeypatch()
	try:
		banks = [
			{"bank": "Key Bank", "access_token": "tok-key"},
			{"bank": "US Bank", "access_token": "tok-us"},
		]
		docs, statuses, client = _refresh(
			monkeypatch,
			m,
			banks,
			{"tok-key": _plaid_balances("Checking", "Savings"), "tok-us": _plaid_balances("Checking")},
		)
		snapshot = m.balances.refresh_balances()

		assert [b["bank"] for b in snapshot["banks"]] == ["Key Bank", "US Bank"]
		assert all(b["status"] == "Connected" and b["message"] is None for b in snapshot["banks"])
		assert [len(b["accounts"]) for b in snapshot["banks"]] == [2, 1]
		acct = snapshot["banks"][0]["accounts"][0]
		assert acct == {
			"account_id": "acc-Checking",
			"name": "Checking Official",
			"mask": "1234",
			"subtype": "checking",
			"type": "depository",
			"available": 10.0,
			"current": 12.5,
			"currency": "USD",
		}
		assert snapshot["fetched_at"] == str(NOW)
		# the cache got the banks list, not the accounts of one bank
		cached = json.loads(docs["Bank Balance Snapshot"].snapshot_json)
		assert [c["bank"] for c in cached] == ["Key Bank", "US Bank"]
		assert docs["Bank Balance Snapshot"].saved == 1
		# and nothing in the cache or the snapshot is a token
		assert (
			"tok-key" not in json.dumps(snapshot)
			and "tok-key" not in docs["Bank Balance Snapshot"].snapshot_json
		)
		assert statuses == [
			(
				"Connected",
				"Balances refreshed for 2 bank(s).",
				{"plaid_last_sync": NOW, "plaid_auth_blocked": 0},
			)
		]
		assert client.calls == [("balances", "tok-key"), ("balances", "tok-us")]
	finally:
		monkeypatch.undo()


def test_item_error_isolates_one_bank_and_does_not_pause():
	m = _modules()
	monkeypatch = _Monkeypatch()
	try:
		banks = [
			{"bank": "Key Bank", "access_token": "tok-key"},
			{"bank": "US Bank", "access_token": "tok-us"},
		]
		dead = m.client.PlaidError(
			"Plaid API error (400/ITEM_LOGIN_REQUIRED)", error_code="ITEM_LOGIN_REQUIRED"
		)
		docs, statuses, client = _refresh(
			monkeypatch, m, banks, {"tok-key": dead, "tok-us": _plaid_balances("Checking")}
		)
		snapshot = m.balances.refresh_balances()

		key, us = snapshot["banks"]
		assert key["status"] == "Reconnect Required" and key["accounts"] == []
		assert "Refresh Plaid Link" in key["message"] and "Key Bank" in key["message"]
		assert us["status"] == "Connected" and len(us["accounts"]) == 1
		assert client.calls == [("balances", "tok-key"), ("balances", "tok-us")]  # loop continued
		status, message, fields = statuses[-1]
		assert status == "Connected"
		assert "1 of 2" in message and "Key Bank (Reconnect Required)" in message
		assert fields["plaid_auth_blocked"] == 0
	finally:
		monkeypatch.undo()


def test_config_error_pauses_globally_and_stops():
	m = _modules()
	monkeypatch = _Monkeypatch()
	try:
		banks = [
			{"bank": "Key Bank", "access_token": "tok-key"},
			{"bank": "US Bank", "access_token": "tok-us"},
		]
		bad = m.client.PlaidError("Plaid API error (400/INVALID_API_KEYS)", error_code="INVALID_API_KEYS")
		docs, statuses, client = _refresh(
			monkeypatch, m, banks, {"tok-key": bad, "tok-us": _plaid_balances("Checking")}
		)
		try:
			m.balances.refresh_balances()
			raise AssertionError("expected PlaidError to propagate")
		except m.client.PlaidError:
			pass
		assert client.calls == [("balances", "tok-key")]  # stopped: the second bank was never tried
		assert statuses == [
			(
				"Error",
				"Plaid configuration error (INVALID_API_KEYS). Check the client id / secret / "
				"environment on the native Plaid Settings.",
				{"plaid_auth_blocked": 1},
			)
		]
		assert docs["Bank Balance Snapshot"].saved == 0  # last good numbers kept
	finally:
		monkeypatch.undo()


def test_transient_error_marks_bank_error_and_continues():
	m = _modules()
	monkeypatch = _Monkeypatch()
	try:
		banks = [
			{"bank": "Key Bank", "access_token": "tok-key"},
			{"bank": "US Bank", "access_token": "tok-us"},
		]
		flaky = m.client.PlaidError("Plaid API error (500/None): upstream", status_code=500)
		docs, statuses, client = _refresh(
			monkeypatch, m, banks, {"tok-key": flaky, "tok-us": _plaid_balances("Checking")}
		)
		snapshot = m.balances.refresh_balances()
		assert snapshot["banks"][0]["status"] == "Error"
		assert "upstream" in snapshot["banks"][0]["message"]
		assert snapshot["banks"][1]["status"] == "Connected"
		assert statuses[-1][0] == "Connected"
		assert statuses[-1][2]["plaid_auth_blocked"] == 0
	finally:
		monkeypatch.undo()


def test_every_bank_needing_reconnect_reports_it_without_pausing():
	m = _modules()
	monkeypatch = _Monkeypatch()
	try:
		banks = [{"bank": "Key Bank", "access_token": "tok-key"}]
		dead = m.client.PlaidError("x", error_code="INVALID_ACCESS_TOKEN")
		docs, statuses, client = _refresh(monkeypatch, m, banks, {"tok-key": dead})
		m.balances.refresh_balances()
		status, message, fields = statuses[-1]
		assert status == "Reconnect Required"
		assert "Key Bank" in message
		assert "plaid_auth_blocked" not in fields and "plaid_last_sync" not in fields
	finally:
		monkeypatch.undo()


def test_no_linked_banks_is_not_connected_and_points_at_native_link():
	m = _modules()
	monkeypatch = _Monkeypatch()
	try:
		docs, statuses, client = _refresh(monkeypatch, m, [], {})
		snapshot = m.balances.refresh_balances()
		assert snapshot["banks"] == []
		assert client.calls == []
		assert statuses[-1][0] == "Not Connected"
		assert "Link a new bank account" in statuses[-1][1]
		assert json.loads(docs["Bank Balance Snapshot"].snapshot_json) == []
	finally:
		monkeypatch.undo()


def test_blank_native_keys_with_a_linked_bank_pause_like_bad_keys():
	"""``get_credentials`` throws a plain ValidationError (not a PlaidError) from inside
	the first request; unhandled it would skip the status stamp, leave the throttle
	anchor unmoved and hand the scheduler an Error Log every hour. Refresh must treat
	it as the config failure it is: pause, stamp, raise PlaidError, spend no call."""
	m = _modules()
	monkeypatch = _Monkeypatch()
	try:
		banks = [{"bank": "Key Bank", "access_token": "tok-key"}]
		docs, statuses, client = _refresh(monkeypatch, m, banks, {"tok-key": _plaid_balances("Checking")})
		docs["Plaid Settings"] = _native(secret=None)
		client.native = docs["Plaid Settings"]
		try:
			m.balances.refresh_balances()
			raise AssertionError("expected PlaidError")
		except m.client.PlaidError as exc:
			assert exc.error_code == m.balances.MISSING_KEYS_CODE
			assert "native Plaid Settings" in str(exc)
		assert client.calls == []
		assert docs["Bank Balance Snapshot"].saved == 0
		assert len(statuses) == 1
		status, message, fields = statuses[0]
		assert status == "Error" and "native Plaid Settings" in message
		assert fields == {"plaid_auth_blocked": 1}

		# the scheduler swallows it as a PlaidError -- no Error Log, the pause does the rest
		frappe = sys.modules["frappe"]
		logged = []
		monkeypatch.setattr(frappe, "log_error", lambda *a, **k: logged.append(a))
		monkeypatch.setattr(m.tasks, "linked_banks", lambda: banks)
		monkeypatch.setattr(m.tasks, "refresh_balances", m.balances.refresh_balances)
		frappe.get_single = lambda doctype: docs[doctype]
		m.tasks.scheduled_balance_refresh()
		assert logged == []
		assert statuses[-1][2] == {"plaid_auth_blocked": 1}
	finally:
		monkeypatch.undo()


def test_read_cache_treats_pre_multibank_shape_as_empty():
	m = _modules()
	frappe = sys.modules["frappe"]
	legacy = FakeDoc(
		"Bank Balance Snapshot",
		{"snapshot_json": json.dumps([{"name": "Checking", "current": 1.0}]), "fetched_at": NOW},
	)
	_singles(frappe, snapshot=legacy)
	assert m.balances.read_cache() == {"banks": [], "fetched_at": str(NOW)}
	fresh = FakeDoc(
		"Bank Balance Snapshot",
		{"snapshot_json": json.dumps([{"bank": "Key Bank", "status": "Connected", "accounts": []}])},
	)
	_singles(frappe, snapshot=fresh)
	assert m.balances.read_cache()["banks"][0]["bank"] == "Key Bank"


# --- the API surface --------------------------------------------------------------


def test_get_bank_balances_groups_by_bank_and_leaks_nothing():
	m = _modules()
	frappe = sys.modules["frappe"]
	ours = FakeDoc(
		"Plaid Banking Settings",
		{"plaid_enabled": 1, "plaid_status": "Connected", "plaid_last_sync": NOW, "plaid_auth_blocked": 0},
	)
	snap = FakeDoc(
		"Bank Balance Snapshot",
		{
			"snapshot_json": json.dumps(
				[
					{
						"bank": "Key Bank",
						"status": "Reconnect Required",
						"message": "re-link",
						"accounts": [],
					},
					{
						"bank": "US Bank",
						"status": "Connected",
						"message": None,
						"accounts": [{"name": "Checking"}],
					},
				]
			)
		},
	)
	_singles(frappe, ours=ours, snapshot=snap)
	out = m.api.get_bank_balances()
	assert out["enabled"] is True and out["status"] == "Connected"
	assert out["reconnect_required"] is True and out["paused"] is False
	assert [b["bank"] for b in out["banks"]] == ["Key Bank", "US Bank"]
	assert out["last_sync"] == str(NOW)
	blob = json.dumps(out).lower()
	assert "access_token" not in blob and "secret" not in blob and "client_id" not in blob
	assert set(out) == {
		"enabled",
		"status",
		"status_message",
		"paused",
		"reconnect_required",
		"banks",
		"last_sync",
	}

	_singles(frappe, ours=FakeDoc("Plaid Banking Settings", {"plaid_enabled": 0}))
	assert m.api.get_bank_balances() == {"enabled": False}


def test_removed_link_flow_endpoints_are_gone():
	m = _modules()
	for name in ("create_link_token", "exchange_public_token", "disconnect"):
		assert not hasattr(m.api, name), name
	assert not hasattr(m.client.PlaidClient, "create_link_token")
	assert not hasattr(m.client.PlaidClient, "item_remove")
	import importlib.util

	assert importlib.util.find_spec("erpnext_enhancements.plaid_banking.core.connect") is None
	for name in ("LINK_TOKEN_CREATE", "PUBLIC_TOKEN_EXCHANGE", "ITEM_REMOVE", "PLAID_PRODUCTS"):
		assert not hasattr(m.constants, name), name
	assert m.constants.ACCOUNTS_GET == "/accounts/get"


def test_test_connection_reports_keys_then_probes_each_bank(monkeypatch):
	m = _modules()
	statuses = []
	monkeypatch.setattr(
		m.api, "update_settings_status", lambda s, message=None, **f: statuses.append((s, message, f))
	)

	# missing keys -> clear failure naming the native form, no HTTP
	monkeypatch.setattr(m.api, "get_credentials", lambda: _stub_throw("keys missing: native Plaid Settings"))
	out = m.api.test_connection()
	assert out["ok"] is False and "native Plaid Settings" in out["message"]
	assert statuses[-1][0] == "Error"

	# keys present, nothing linked -> honest "link a bank" with no probe
	monkeypatch.setattr(m.api, "get_credentials", lambda: ("cid", "sec"))
	monkeypatch.setattr(m.api, "linked_banks", lambda: [])
	out = m.api.test_connection()
	assert out["ok"] is True and "Link a new bank account" in out["message"] and out["banks"] == []
	assert statuses[-1][0] == "Not Connected"

	# links -> /item/get per bank; one failure is reported by name and does not pause
	banks = [{"bank": "Key Bank", "access_token": "tok-key"}, {"bank": "US Bank", "access_token": "tok-us"}]
	monkeypatch.setattr(m.api, "linked_banks", lambda: banks)
	client = FakeClient(
		{"tok-key": {"item": {}}, "tok-us": m.client.PlaidError("nope", error_code="ITEM_LOGIN_REQUIRED")}
	)
	monkeypatch.setattr(m.api, "PlaidClient", client)
	out = m.api.test_connection()
	assert out["ok"] is False and "US Bank" in out["message"]
	assert [r["ok"] for r in out["banks"]] == [True, False]
	assert statuses[-1] == ("Error", "Connection failed for US Bank.", {})

	# all good -> Connected and the pause lifted
	client = FakeClient({"tok-key": {"item": {}}, "tok-us": {"item": {}}})
	monkeypatch.setattr(m.api, "PlaidClient", client)
	out = m.api.test_connection()
	assert out["ok"] is True
	assert statuses[-1] == ("Connected", "Test Connection OK for 2 bank(s).", {"plaid_auth_blocked": 0})
	assert client.calls == [("item", "tok-key"), ("item", "tok-us")]
	assert "tok-key" not in json.dumps(out)


def test_scheduler_gates_enabled_pause_throttle_and_links(monkeypatch):
	m = _modules()
	frappe = sys.modules["frappe"]
	calls = []
	monkeypatch.setattr(m.tasks, "refresh_balances", lambda settings: calls.append("refresh"))
	linked = [{"bank": "Key Bank", "access_token": "t"}]

	def run(ours, links=linked):
		_singles(frappe, ours=ours)
		monkeypatch.setattr(m.tasks, "linked_banks", lambda: links)
		calls.clear()
		m.tasks.scheduled_balance_refresh()
		return list(calls)

	assert run(FakeDoc("S", {"plaid_enabled": 0})) == []
	assert run(FakeDoc("S", {"plaid_enabled": 1, "plaid_auth_blocked": 1})) == []
	recent = NOW - _dt.timedelta(minutes=30)
	assert (
		run(FakeDoc("S", {"plaid_enabled": 1, "plaid_last_sync": recent, "refresh_poll_minutes": 240})) == []
	)
	stale = NOW - _dt.timedelta(minutes=300)
	assert run(FakeDoc("S", {"plaid_enabled": 1, "plaid_last_sync": stale, "refresh_poll_minutes": 240})) == [
		"refresh"
	]
	assert run(FakeDoc("S", {"plaid_enabled": 1}), links=[]) == []
	assert run(FakeDoc("S", {"plaid_enabled": 1})) == ["refresh"]


# --- the mapping helper -------------------------------------------------------------


def _seed_bank_accounts(frappe):
	db = frappe.db
	db.docs[("Bank", "Key Bank")] = {"name": "Key Bank", "plaid_access_token": "tok-key"}
	db.docs[("Bank", "US Bank")] = {"name": "US Bank", "plaid_access_token": None}
	db.docs[("Bank Account", "Key Bank Checking - Key Bank")] = {
		"name": "Key Bank Checking - Key Bank",
		"bank": "Key Bank",
		"account": "13100 - Key Bank Checking - SF",
		"account_name": "Key Bank Checking",
		"is_company_account": 1,
		"integration_id": None,
	}
	db.docs[("Bank Account", "Key Bank Savings - Key Bank")] = {
		"name": "Key Bank Savings - Key Bank",
		"bank": "Key Bank",
		"account": "13101 - Key Bank Savings - SF",
		"account_name": "Key Bank Savings",
		"is_company_account": 1,
		"integration_id": "acc-already",
	}
	db.docs[("Bank Account", "US Bank Checking - US Bank")] = {
		"name": "US Bank Checking - US Bank",
		"bank": "US Bank",
		"account": "13000 - US Bank Checking - SF",
		"account_name": "US Bank Checking",
		"is_company_account": 1,
		"integration_id": None,
	}
	db.docs[("Bank Account", "Vendor Acme - Key Bank")] = {
		"name": "Vendor Acme - Key Bank",
		"bank": "Key Bank",
		"is_company_account": 0,
		"integration_id": None,
	}
	return db


def _expect_throw(fn, *needles):
	try:
		fn()
	except Thrown as exc:
		for needle in needles:
			assert needle in str(exc), (needle, str(exc))
		return str(exc)
	raise AssertionError("expected a throw")


def test_map_plaid_account_validations_and_success():
	m = _modules()
	frappe = sys.modules["frappe"]
	db = _seed_bank_accounts(frappe)
	map_ = m.link_accounts.map_plaid_account

	_expect_throw(lambda: map_("Nope - Key Bank", "acc-1"), "does not exist")
	_expect_throw(lambda: map_("Vendor Acme - Key Bank", "acc-1"), "not a company account")
	_expect_throw(
		lambda: map_("US Bank Checking - US Bank", "acc-1"),
		"US Bank",
		"not linked to Plaid",
		"rename the Bank to Plaid's exact institution name",
	)
	_expect_throw(
		lambda: map_("Key Bank Checking - Key Bank", "acc-already"),
		"already mapped to Bank Account 'Key Bank Savings - Key Bank'",
	)
	_expect_throw(lambda: map_("Key Bank Checking - Key Bank", ""), "account id is required")
	assert db.set_value_calls == []

	out = map_("Key Bank Checking - Key Bank", "acc-new", mask="4321")
	yesterday = NOW.date() - _dt.timedelta(days=1)
	assert db.set_value_calls == [
		(
			"Bank Account",
			"Key Bank Checking - Key Bank",
			{"integration_id": "acc-new", "last_integration_date": yesterday, "mask": "4321"},
		)
	]
	assert out["last_integration_date"] == str(yesterday)

	# an explicit start date wins, and re-stamping the SAME account is allowed (idempotent)
	map_("Key Bank Checking - Key Bank", "acc-new", start_date="2026-08-01")
	assert db.set_value_calls[-1][2]["last_integration_date"] == _dt.date(2026, 8, 1)
	assert "mask" not in db.set_value_calls[-1][2]
	assert "bank" not in db.set_value_calls[-1][2] and out["bank_repointed_from"] is None


def test_map_and_absorb_when_native_link_named_the_bank_after_plaids_institution():
	"""The prod shape: native Link stores the token on a NEW Bank named exactly as Plaid
	names the institution ("KeyBank"), while the masters sit under ours ("Key Bank")
	with no token. Mapping with ``bank=`` moves the master under the token-holding Bank;
	absorb does the same for a Link-created duplicate; both refuse when the master's own
	Bank holds a token of its own (a second Item, not a naming mismatch)."""
	m = _modules()
	frappe = sys.modules["frappe"]
	db = _seed_bank_accounts(frappe)
	db.docs[("Bank", "Key Bank")]["plaid_access_token"] = None  # ours: never linked
	db.docs[("Bank", "KeyBank")] = {"name": "KeyBank", "plaid_access_token": "tok-keybank"}
	map_ = m.link_accounts.map_plaid_account

	# without bank= the master's own Bank must hold the token -- and the message says why not
	_expect_throw(lambda: map_("Key Bank Checking - Key Bank", "acc-1"), "Bank 'Key Bank' is not linked")
	# a bank= that holds no token is refused too
	_expect_throw(
		lambda: map_("Key Bank Checking - Key Bank", "acc-1", bank="US Bank"), "US Bank", "not linked"
	)
	# bank= the token holder: mapped AND moved under it
	out = map_("Key Bank Checking - Key Bank", "acc-1", mask="1111", bank="KeyBank")
	assert db.set_value_calls[-1] == (
		"Bank Account",
		"Key Bank Checking - Key Bank",
		{
			"integration_id": "acc-1",
			"last_integration_date": NOW.date() - _dt.timedelta(days=1),
			"mask": "1111",
			"bank": "KeyBank",
		},
	)
	assert out["bank"] == "KeyBank" and out["bank_repointed_from"] == "Key Bank"
	# bank= equal to the master's own Bank is the plain path (no re-point)
	map_("Key Bank Checking - Key Bank", "acc-1", bank="KeyBank")
	assert "bank" not in db.set_value_calls[-1][2]

	# the master's own Bank holds its own token -> that is two Items; refused
	db.docs[("Bank", "US Bank")]["plaid_access_token"] = "tok-us"
	_expect_throw(
		lambda: map_("US Bank Checking - US Bank", "acc-2", bank="KeyBank"),
		"holds its own Plaid link",
		"cannot also be moved",
	)

	# absorb: the duplicate sits under "KeyBank", the master under un-tokened "Key Bank"
	db.docs[("Bank Account", "Checking - KeyBank")] = {
		"name": "Checking - KeyBank",
		"bank": "KeyBank",
		"account": "Checking - KeyBank - SF",
		"account_name": "Checking",
		"integration_id": "acc-dup",
		"mask": "9999",
		"last_integration_date": None,
	}
	db.docs[("Bank Account", "Key Bank Savings - Key Bank")]["integration_id"] = None
	frappe.delete_doc = lambda doctype, name, **k: db.docs.pop((doctype, name), None)
	out = m.link_accounts.absorb_native_duplicate("Checking - KeyBank", "Key Bank Savings - Key Bank")
	assert out["absorbed"] is True and out["bank"] == "KeyBank" and out["bank_repointed_from"] == "Key Bank"
	assert db.docs[("Bank Account", "Key Bank Savings - Key Bank")]["bank"] == "KeyBank"
	assert db.docs[("Bank Account", "Key Bank Savings - Key Bank")]["integration_id"] == "acc-dup"
	# ...but not onto a master whose own Bank is linked
	db.docs[("Bank Account", "Checking - KeyBank")] = {
		"name": "Checking - KeyBank",
		"bank": "KeyBank",
		"account_name": "Checking",
		"integration_id": "acc-dup2",
	}
	_expect_throw(
		lambda: m.link_accounts.absorb_native_duplicate("Checking - KeyBank", "US Bank Checking - US Bank"),
		"holds its own Plaid link",
		"cannot move between institutions",
	)


def test_mapping_overview_offers_masters_under_unlinked_banks_as_candidates(monkeypatch):
	m = _modules()
	frappe = sys.modules["frappe"]
	monkeypatch.setattr(m.link_accounts, "linked_banks", lambda: [{"bank": "KeyBank", "access_token": "t"}])
	accounts = [
		{"name": "Checking - KeyBank", "bank": "KeyBank", "integration_id": "acc-dup"},
		{"name": "Key Bank Checking - Key Bank", "bank": "Key Bank", "integration_id": None},
		{"name": "US Bank Checking - US Bank", "bank": "US Bank", "integration_id": None},
	]
	frappe.get_all = lambda doctype, filters=None, fields=None, **k: [
		a for a in accounts if not (filters or {}).get("bank") or a["bank"] == filters["bank"]
	]
	monkeypatch.setattr(m.link_accounts, "PlaidClient", FakeClient({"t": {"accounts": []}}))
	out = m.link_accounts.mapping_overview()
	assert [ba["name"] for ba in out["banks"][0]["bank_accounts"]] == ["Checking - KeyBank"]
	assert [ba["name"] for ba in out["unlinked_bank_accounts"]] == [
		"Key Bank Checking - Key Bank",
		"US Bank Checking - US Bank",
	]
	# nothing linked -> nothing to offer, and no Bank Account query at all
	monkeypatch.setattr(m.link_accounts, "linked_banks", lambda: [])
	assert m.link_accounts.mapping_overview() == {"banks": [], "unlinked_bank_accounts": []}


def test_absorb_refuses_bank_transactions_and_cross_bank():
	m = _modules()
	frappe = sys.modules["frappe"]
	db = _seed_bank_accounts(frappe)
	db.docs[("Bank", "US Bank")]["plaid_access_token"] = "tok-us"  # its own Item
	db.docs[("Bank Account", "Checking - Key Bank")] = {
		"name": "Checking - Key Bank",
		"bank": "Key Bank",
		"account": "Checking - Key Bank - SF",
		"account_name": "Checking",
		"integration_id": "acc-dup",
		"mask": "9999",
		"last_integration_date": _dt.date(2026, 8, 1),
	}
	absorb = m.link_accounts.absorb_native_duplicate

	_expect_throw(
		lambda: absorb("Checking - Key Bank", "US Bank Checking - US Bank"),
		"cannot move between institutions",
	)
	_expect_throw(
		lambda: absorb("Key Bank Checking - Key Bank", "Checking - Key Bank"), "carries no Plaid link"
	)
	db.counts[("Bank Transaction", json.dumps({"bank_account": "Checking - Key Bank"}, sort_keys=True))] = 3
	_expect_throw(
		lambda: absorb("Checking - Key Bank", "Key Bank Checking - Key Bank"), "Bank Transaction rows"
	)
	assert db.set_value_calls == []


def test_absorb_moves_link_and_only_deletes_a_provably_auto_created_unused_gl_account():
	m = _modules()
	frappe = sys.modules["frappe"]
	db = _seed_bank_accounts(frappe)
	deleted = []

	def delete_doc(doctype, name, **k):
		# Like the real thing, the row is gone once delete_doc returns -- the "does
		# another Bank Account still use this GL Account" guard depends on that order.
		deleted.append((doctype, name))
		db.docs.pop((doctype, name), None)

	frappe.delete_doc = delete_doc

	def reset_dup():
		db.docs[("Bank Account", "Checking - Key Bank")] = {
			"name": "Checking - Key Bank",
			"bank": "Key Bank",
			"account": "Checking - Key Bank - SF",
			"account_name": "Checking",
			"integration_id": "acc-dup",
			"mask": "9999",
			"last_integration_date": _dt.date(2026, 8, 1),
		}
		db.docs[("Bank Account", "Key Bank Checking - Key Bank")]["integration_id"] = None
		db.docs.setdefault(
			("Account", "Checking - Key Bank - SF"),
			{"name": "Checking - Key Bank - SF", "account_name": "Checking - Key Bank"},
		)
		deleted.clear()

	reset_dup()

	# GL Entries exist -> link moves, duplicate goes, GL Account stays with a reason
	real_exists = db.exists
	db.exists = lambda doctype, name=None, *a, **k: (
		True
		if doctype == "GL Entry" and name == {"account": "Checking - Key Bank - SF"}
		else real_exists(doctype, name)
	)
	out = m.link_accounts.absorb_native_duplicate("Checking - Key Bank", "Key Bank Checking - Key Bank")
	assert db.set_value_calls == [
		("Bank Account", "Checking - Key Bank", {"integration_id": None, "mask": None}),
		(
			"Bank Account",
			"Key Bank Checking - Key Bank",
			{"integration_id": "acc-dup", "mask": "9999", "last_integration_date": _dt.date(2026, 8, 1)},
		),
	]
	assert deleted == [("Bank Account", "Checking - Key Bank")]
	assert out["gl_account_deleted"] is False and "GL Entry" in out["gl_account_note"]
	assert out["last_integration_date"] == "2026-08-01" and out["bank_repointed_from"] is None

	# no GL Entries, native pattern, nothing else linked -> the GL Account is deleted too
	db.exists = real_exists
	reset_dup()
	out = m.link_accounts.absorb_native_duplicate("Checking - Key Bank", "Key Bank Checking - Key Bank")
	assert deleted == [("Bank Account", "Checking - Key Bank"), ("Account", "Checking - Key Bank - SF")]
	assert out["gl_account_deleted"] is True and db.savepoints == ["plaid_absorb_gl"]
	assert ("Account", "Checking - Key Bank - SF") not in db.docs

	# a Link-created duplicate that never synced carries NULL: the master's own date must
	# survive (NULL means "twelve months, submitted" to the native sync) ...
	reset_dup()
	db.docs[("Bank Account", "Checking - Key Bank")]["last_integration_date"] = None
	db.docs[("Bank Account", "Key Bank Checking - Key Bank")]["last_integration_date"] = _dt.date(2026, 9, 1)
	out = m.link_accounts.absorb_native_duplicate("Checking - Key Bank", "Key Bank Checking - Key Bank")
	assert db.set_value_calls[-1][2]["last_integration_date"] == _dt.date(2026, 9, 1)
	assert out["last_integration_date"] == "2026-09-01"
	# ... and with neither set, yesterday -- the same default map_plaid_account uses
	reset_dup()
	db.docs[("Bank Account", "Checking - Key Bank")]["last_integration_date"] = None
	db.docs[("Bank Account", "Key Bank Checking - Key Bank")]["last_integration_date"] = None
	out = m.link_accounts.absorb_native_duplicate("Checking - Key Bank", "Key Bank Checking - Key Bank")
	yesterday = NOW.date() - _dt.timedelta(days=1)
	assert db.set_value_calls[-1][2]["last_integration_date"] == yesterday
	assert out["last_integration_date"] == str(yesterday)
	db.docs[("Bank Account", "Key Bank Checking - Key Bank")]["last_integration_date"] = None

	# a second master still on that GL Account -> left alone, and it says which guard
	reset_dup()
	db.docs[("Bank Account", "Key Bank Savings - Key Bank")]["account"] = "Checking - Key Bank - SF"
	out = m.link_accounts.absorb_native_duplicate("Checking - Key Bank", "Key Bank Checking - Key Bank")
	assert deleted == [("Bank Account", "Checking - Key Bank")]
	assert out["gl_account_deleted"] is False and "another Bank Account" in out["gl_account_note"]
	db.docs[("Bank Account", "Key Bank Savings - Key Bank")]["account"] = "13101 - Key Bank Savings - SF"

	# a hand-made GL Account (not the native name pattern) is never deleted
	reset_dup()
	db.docs[("Account", "Checking - Key Bank - SF")]["account_name"] = "Operating Checking"
	out = m.link_accounts.absorb_native_duplicate("Checking - Key Bank", "Key Bank Checking - Key Bank")
	assert deleted == [("Bank Account", "Checking - Key Bank")]
	assert out["gl_account_deleted"] is False and "not auto-created" in out["gl_account_note"]

	# the framework refusing the delete (something links to it) is reported, not raised
	reset_dup()
	db.docs[("Account", "Checking - Key Bank - SF")]["account_name"] = "Checking - Key Bank"

	def refusing_delete_doc(doctype, name, **k):
		if doctype == "Account":
			raise Thrown("Cannot delete: linked with Payment Entry")
		delete_doc(doctype, name, **k)

	frappe.delete_doc = refusing_delete_doc
	frappe.cleared_messages = 0
	out = m.link_accounts.absorb_native_duplicate("Checking - Key Bank", "Key Bank Checking - Key Bank")
	assert out["gl_account_deleted"] is False and "Payment Entry" in out["gl_account_note"]
	assert db.rollbacks == ["plaid_absorb_gl"]
	assert ("Account", "Checking - Key Bank - SF") in db.docs
	# frappe.throw queued its red message before raising; a whitelisted caller must not
	# see "Cannot delete ..." over a response that says absorbed: True
	assert frappe.cleared_messages == 1


def test_prune_deletes_only_the_gl_accounts_a_native_relink_left_behind(monkeypatch):
	"""After the masters hold the ids, every native 'Refresh Plaid Link' inserts a GL
	Account per shared account and then fails the Bank Account insert on the unique
	integration_id (swallowed). The prune finds them by Plaid's names under the same
	guards as the absorb, and reports what it kept and why."""
	m = _modules()
	frappe = sys.modules["frappe"]
	db = _seed_bank_accounts(frappe)
	db.docs[("Account", "Checking - Key Bank - SF")] = {
		"name": "Checking - Key Bank - SF",
		"account_name": "Checking - Key Bank",
	}
	db.docs[("Account", "Savings - Key Bank - SF")] = {
		"name": "Savings - Key Bank - SF",
		"account_name": "Savings - Key Bank",
	}
	gl_by_name = {
		"Checking - Key Bank": ["Checking - Key Bank - SF"],
		"Savings - Key Bank": ["Savings - Key Bank - SF"],
	}
	queried = []

	def get_all(doctype, filters=None, fields=None, pluck=None, **k):
		queried.append((doctype, dict(filters or {})))
		assert doctype == "Account" and pluck == "name"
		assert filters["account_type"] == "Bank" and filters["is_group"] == 0
		return list(gl_by_name.get(filters["account_name"], []))

	frappe.get_all = get_all
	deleted = []
	frappe.delete_doc = lambda doctype, name, **k: (
		deleted.append((doctype, name)),
		db.docs.pop((doctype, name)),
	)
	client = FakeClient(
		{
			"tok-key": {
				"accounts": [
					{"account_id": "a1", "name": "Checking"},
					{"account_id": "a2", "name": "Savings"},
					{"account_id": "a3", "name": "Money Market"},  # Link made none for this one
				]
			}
		}
	)
	monkeypatch.setattr(m.link_accounts, "PlaidClient", client)
	# Savings' stray is referenced by a master -> kept
	db.docs[("Bank Account", "Key Bank Savings - Key Bank")]["account"] = "Savings - Key Bank - SF"

	out = m.link_accounts.prune_link_created_gl_accounts("Key Bank")
	assert client.calls == [("accounts", "tok-key")]
	assert [q[1]["account_name"] for q in queried] == [
		"Checking - Key Bank",
		"Savings - Key Bank",
		"Money Market - Key Bank",
	]
	assert deleted == [("Account", "Checking - Key Bank - SF")]
	assert out["bank"] == "Key Bank" and out["deleted"] == ["Checking - Key Bank - SF"]
	assert [k["account"] for k in out["kept"]] == ["Savings - Key Bank - SF"]
	assert "another Bank Account" in out["kept"][0]["note"]
	# an unlinked Bank spends nothing
	_expect_throw(lambda: m.link_accounts.prune_link_created_gl_accounts("US Bank"), "not linked to Plaid")
	assert client.calls == [("accounts", "tok-key")]


def test_mapping_overview_isolates_a_failing_bank_and_carries_no_token(monkeypatch):
	m = _modules()
	frappe = sys.modules["frappe"]
	db = _seed_bank_accounts(frappe)
	db.docs[("Bank", "US Bank")]["plaid_access_token"] = "tok-us"
	monkeypatch.setattr(
		m.link_accounts,
		"linked_banks",
		lambda: [
			{"bank": "Key Bank", "access_token": "tok-key"},
			{"bank": "US Bank", "access_token": "tok-us"},
		],
	)
	frappe.get_all = lambda doctype, filters=None, fields=None, **k: [
		{
			"name": f"{(filters or {}).get('bank', 'any')} X",
			"bank": (filters or {}).get("bank"),
			"integration_id": None,
		}
	]
	client = FakeClient(
		{
			"tok-key": {
				"accounts": [{"account_id": "a1", "name": "Checking", "mask": "1111", "type": "depository"}]
			},
			"tok-us": m.client.PlaidError("boom", error_code="ITEM_LOGIN_REQUIRED"),
		}
	)
	monkeypatch.setattr(m.link_accounts, "PlaidClient", client)
	out = m.link_accounts.mapping_overview()
	assert out["banks"][0]["plaid_accounts"][0]["account_id"] == "a1" and out["banks"][0]["error"] is None
	assert out["banks"][1]["plaid_accounts"] == [] and "boom" in out["banks"][1]["error"]
	assert "tok-" not in json.dumps(out)


# --- the rename patch -------------------------------------------------------------


def _patch_env(doctypes):
	"""``doctypes``: {name: module} present in tabDocType."""
	frappe = install_frappe_stub()
	calls = []
	frappe.db.docs = {
		("DocType", name): {"name": name, "module": module} for name, module in doctypes.items()
	}
	frappe.rename_doc = lambda *a, **k: calls.append(("rename_doc", a, k))
	frappe.reload_doc = lambda *a, **k: calls.append(("reload_doc", a, k))
	frappe.clear_cache = lambda *a, **k: calls.append(("clear_cache", a, k))
	name = "erpnext_enhancements.patches.rename_plaid_settings_doctype"
	patch = importlib.reload(sys.modules[name]) if name in sys.modules else importlib.import_module(name)
	return frappe, patch, calls


def test_rename_patch_noop_when_missing_or_already_native_or_already_renamed():
	frappe, patch, calls = _patch_env({})
	patch.execute()
	assert calls == [] and frappe.db.sql_calls == []

	frappe, patch, calls = _patch_env({"Plaid Settings": "ERPNext Integrations"})
	patch.execute()
	assert calls == [] and frappe.db.sql_calls == []

	frappe, patch, calls = _patch_env(
		{"Plaid Settings": "Plaid Banking", "Plaid Banking Settings": "Plaid Banking"}
	)
	patch.execute()
	assert calls == [] and frappe.db.sql_calls == []


def test_rename_patch_renames_moves_native_rows_back_purges_token_and_reloads_native():
	frappe, patch, calls = _patch_env({"Plaid Settings": "Plaid Banking"})
	patch.execute()

	assert calls[0] == (
		"rename_doc",
		("DocType", "Plaid Settings", "Plaid Banking Settings"),
		{"force": True},
	)
	assert calls[1] == ("reload_doc", ("erpnext_integrations", "doctype", "plaid_settings"), {"force": True})
	assert calls[2][0] == "clear_cache"

	sql = frappe.db.sql_calls
	assert len(sql) == 8
	nav_updates, singles_update, name_reset, auth_move, auth_delete = sql[:4], sql[4], sql[5], sql[6], sql[7]

	# erpnext's own navigation rows (Invoicing card, Banking sidebar item) go back to the
	# native name right after rename_dynamic_links moved them; the type column differs
	# per table (Workspace Shortcut's is `type`), and nothing of ours references the name
	assert [q[0] for q in nav_updates] == [
		"update `tabWorkspace Link` set link_to = %s where `link_type` = 'DocType' and link_to = %s",
		"update `tabWorkspace Sidebar Item` set link_to = %s where `link_type` = 'DocType' and link_to = %s",
		"update `tabWorkspace Shortcut` set link_to = %s where `type` = 'DocType' and link_to = %s",
		"update `tabDesktop Icon` set link_to = %s where `link_type` = 'DocType' and link_to = %s",
	]
	assert all(q[1] == ("Plaid Settings", "Plaid Banking Settings") for q in nav_updates)

	# everything that is NOT one of our fields returns to the native name: the six native
	# values AND the Single's meta rows, so our renamed Single is left with no rows and
	# loads with its declared defaults
	assert singles_update[0].startswith(
		"update `tabSingles` set doctype = %s where doctype = %s and field not in %s"
	)
	assert singles_update[1][:2] == ("Plaid Settings", "Plaid Banking Settings")
	ours = set(singles_update[1][2])
	meta_rows = ("name", "modified", "modified_by", "owner", "creation", "docstatus", "idx")
	for native in (*patch.NATIVE_FIELDS, *meta_rows):
		assert native not in ours, native
	for kept_or_retired in (
		"plaid_enabled",
		"plaid_access_token",
		"plaid_status",
		"refresh_poll_minutes",
		"plaid_environment",
		"plaid_item_id",
		"plaid_institution_name",
		"plaid_auth_blocked",
	):
		assert kept_or_retired in ours, kept_or_retired
	# after_rename rewrote the `name` row's value to the new name; it is native again
	assert name_reset[0] == "update `tabSingles` set value = %s where doctype = %s and field = 'name'"
	assert name_reset[1] == ("Plaid Settings", "Plaid Settings")
	assert "update `__Auth`" in auth_move[0] and "fieldname = 'plaid_secret'" in auth_move[0]
	assert auth_move[1] == (
		"Plaid Settings",
		"Plaid Settings",
		"Plaid Banking Settings",
		"Plaid Banking Settings",
	)
	assert auth_delete[0].startswith("delete from `__Auth` where doctype in %s and fieldname in %s")
	assert auth_delete[1] == (("Plaid Settings", "Plaid Banking Settings"), ("plaid_access_token",))


def test_rename_patch_skips_native_reload_when_erpnext_is_absent():
	frappe, patch, calls = _patch_env({"Plaid Settings": "Plaid Banking"})
	frappe.get_installed_apps = lambda: ["frappe", "erpnext_enhancements"]
	patch.execute()
	assert [c[0] for c in calls] == ["rename_doc", "clear_cache"]


def test_rename_patch_skips_navigation_tables_the_framework_does_not_have():
	# A table that does not exist, or a table whose type column does not exist, is skipped
	# rather than raising mid-migrate -- the desk navigation doctypes vary by framework
	# version (Desktop Icon has no DocType link type in v16 at all).
	frappe, patch, calls = _patch_env({"Plaid Settings": "Plaid Banking"})
	frappe.db.missing_tables = {"Desktop Icon", "Workspace Shortcut"}
	frappe.db.missing_columns = {("Workspace Sidebar Item", "link_type")}
	patch.execute()
	nav = [q[0] for q in frappe.db.sql_calls if "link_to" in q[0]]
	assert [q.split("`")[1] for q in nav] == ["tabWorkspace Link"]


# --- the defaults backfill --------------------------------------------------------


def test_backfill_fills_blanks_only_and_is_safe_twice():
	frappe = install_frappe_stub()
	frappe.db.docs[("DocType", "Plaid Banking Settings")] = {"name": "Plaid Banking Settings"}
	fields = [
		types.SimpleNamespace(fieldname="widget_section", fieldtype="Section Break"),
		types.SimpleNamespace(fieldname="plaid_enabled", fieldtype="Check"),
		types.SimpleNamespace(fieldname="refresh_poll_minutes", fieldtype="Int"),
		types.SimpleNamespace(fieldname="plaid_status", fieldtype="Select"),
		types.SimpleNamespace(fieldname="plaid_auth_blocked", fieldtype="Check"),
	]
	frappe.get_meta = lambda doctype: types.SimpleNamespace(fields=fields)
	stored = {"plaid_status", "plaid_auth_blocked"}  # plaid_auth_blocked stored as a deliberate 0
	frappe.db.sql = lambda query, values=None, *a, **k: [(f,) for f in sorted(stored)]
	name = "erpnext_enhancements.patches.backfill_plaid_banking_settings_defaults"
	patch = importlib.reload(sys.modules[name]) if name in sys.modules else importlib.import_module(name)

	assert patch.backfill_plaid_banking_settings_defaults() == 2
	assert frappe.db.set_single_calls == [
		("Plaid Banking Settings", "plaid_enabled", "0"),
		("Plaid Banking Settings", "refresh_poll_minutes", "240"),
	]

	stored |= {"plaid_enabled", "refresh_poll_minutes"}
	frappe.db.set_single_calls.clear()
	assert patch.backfill_plaid_banking_settings_defaults() == 0
	assert frappe.db.set_single_calls == []

	frappe.db.docs.clear()
	assert patch.backfill_plaid_banking_settings_defaults() == 0


# --- a tiny monkeypatch for the tests that build their own ----------------------------


class _Monkeypatch:
	"""Minimal stand-in so helper-built fixtures can be undone without pytest's fixture."""

	def __init__(self):
		self._undo = []

	def setattr(self, target, name, value):
		self._undo.append((target, name, getattr(target, name)))
		setattr(target, name, value)

	def undo(self):
		for target, name, value in reversed(self._undo):
			setattr(target, name, value)
		self._undo.clear()
