"""Pure-Python (no Frappe site) unit tests for the QuickBooks Online sync.

These are plain pytest functions, not ``FrappeTestCase``. Because the QBO module
must be importable without a running bench, :func:`install_frappe_stub` installs
a fake ``frappe`` / ``frappe.utils`` / ``requests`` into ``sys.modules`` with just
enough behavior (canned ``get_value`` / ``get_all`` / ``get_meta``) for the
mapping, ordering, signature, datetime, preflight-validation and result-tracking
logic to run deterministically. ``monkeypatch`` is used where a test needs to
stub a module-level function (e.g. ``sync.query_all``).
"""
import base64
import contextlib
import hashlib
import hmac
import json
import math
import sys
import types
from datetime import datetime


def _stub_throw(message=None, *args, **kwargs):
	"""Stand-in for ``frappe.throw`` that raises a plain exception in tests."""
	raise Exception(message if isinstance(message, str) else "frappe.throw")


def install_frappe_stub():
	"""Install a minimal fake ``frappe``/``requests`` into sys.modules for import.

	Returns the stub ``frappe`` module so individual tests can further override
	attributes (e.g. ``get_meta``) before importing the code under test.
	"""
	frappe = sys.modules.get("frappe") or types.ModuleType("frappe")
	frappe_utils = sys.modules.get("frappe.utils") or types.ModuleType("frappe.utils")
	frappe_utils.now_datetime = lambda: None
	frappe_utils.get_datetime = lambda value: value
	frappe_utils.add_to_date = lambda value=None, **kwargs: value
	frappe_utils.get_system_timezone = lambda: "UTC"

	def _flt(value=0, precision=None):
		"""Model ``frappe.utils.flt``, which is NOT Python's ``round``.

		Frappe rounds money half-to-EVEN, after normalising the scaled value so a binary
		representation a hair under the half (6.175 * 100 == 617.4999...) still counts as
		a half. Python's ``round`` disagrees on exactly those values: flt(6.175, 2) is
		6.18 where round(6.175, 2) is 6.17.

		Modelling it faithfully is what lets this bench-free suite catch a rounding
		mismatch at all. A stub that rounded differently from Frappe is precisely how the
		Sales Invoice shortfall guard shipped its own rounding bug -- it agreed with the
		stub and disagreed with production.
		"""
		try:
			number = float(value or 0)
		except (TypeError, ValueError):
			return 0.0
		if precision is None:
			return number
		multiplier = 10 ** int(precision)
		scaled = number * multiplier
		floor_part = math.floor(scaled)
		fraction = scaled - floor_part
		if round(fraction, 8) == 0.5:
			scaled = floor_part if floor_part % 2 == 0 else floor_part + 1
		else:
			scaled = round(scaled)
		return scaled / multiplier

	frappe_utils.flt = _flt
	frappe_utils.cint = lambda value=0, *args, **kwargs: int(_flt(value))
	frappe_utils.getdate = lambda value=None: value
	frappe_utils.today = lambda: "2026-06-16"
	frappe.utils = frappe_utils

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Customer Group" and filters == {"is_group": 0}:
			return "Commercial"
		if doctype == "Supplier Group" and filters == {"is_group": 0}:
			return "Services"
		if doctype == "Territory" and filters == {"is_group": 0}:
			return "United States"
		if doctype == "QuickBooks Sync Mapping" and filters == {
			"qbo_entity_type": "Customer",
			"qbo_id": "1",
			"erpnext_doctype": "Customer",
		}:
			return "Acme Supply"
		return None

	def get_all(doctype, filters=None, fields=None, limit_page_length=None, **kwargs):
		if doctype == "Customer" and filters == {"customer_name": "Acme Supply"}:
			return [types.SimpleNamespace(name="Acme Supply")]
		if doctype == "Account" and filters == {
			"company": "Demo Company",
			"is_group": 1,
			"root_type": "Expense",
		}:
			return [types.SimpleNamespace(name="Expenses - DC")]
		return []

	frappe.db = types.SimpleNamespace(
		# Blank/absent System Settings precisions -> the mapper's ERPNext defaults (2, 3).
		get_single_value=lambda doctype, fieldname: None,
		exists=lambda doctype, name: (
			name
			in {"All Customer Groups", "All Territories", "All Supplier Groups", "All Item Groups", "Nos"}
		),
		get_value=get_value,
	)
	frappe.get_all = get_all
	frappe.get_meta = lambda doctype: types.SimpleNamespace(has_field=lambda fieldname: False)
	frappe.get_traceback = lambda: "Traceback\nValidationError: Missing required field"
	frappe._ = lambda message=None, *args, **kwargs: message
	frappe.throw = _stub_throw

	# Minimal exception hierarchy mirroring frappe.exceptions: TimestampMismatchError
	# is a ValidationError subclass (transient/concurrency) the sync re-raises.
	frappe_exceptions = sys.modules.get("frappe.exceptions") or types.ModuleType("frappe.exceptions")
	if not hasattr(frappe_exceptions, "ValidationError"):

		class ValidationError(Exception):
			pass

		class TimestampMismatchError(ValidationError):
			pass

		frappe_exceptions.ValidationError = ValidationError
		frappe_exceptions.TimestampMismatchError = TimestampMismatchError
	frappe.exceptions = frappe_exceptions
	sys.modules.setdefault("frappe.exceptions", frappe_exceptions)
	# Passthrough decorator so the @frappe.whitelist() RPC layer is importable.
	frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)

	# frappe.utils.synchronization.filelock -- a no-op context manager so the QBO
	# client (which locks around token refresh) is importable without a real bench.
	frappe_sync = sys.modules.get("frappe.utils.synchronization") or types.ModuleType(
		"frappe.utils.synchronization"
	)
	if not hasattr(frappe_sync, "filelock"):

		@contextlib.contextmanager
		def _noop_filelock(*args, **kwargs):
			yield

		frappe_sync.filelock = _noop_filelock
	frappe_utils.synchronization = frappe_sync

	sys.modules.setdefault("frappe", frappe)
	sys.modules.setdefault("frappe.utils", frappe_utils)
	sys.modules.setdefault("frappe.utils.synchronization", frappe_sync)
	sys.modules.setdefault("requests", types.ModuleType("requests"))
	return frappe


def test_refresh_access_token_reuses_token_from_concurrent_worker(monkeypatch):
	"""When another worker already refreshed, reuse its token instead of rotating again."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_mod

	monkeypatch.setattr(client_mod, "get_settings", lambda: types.SimpleNamespace())
	# Stored access token ("NEW") differs from the one our request used ("OLD"),
	# i.e. a concurrent worker refreshed while we waited for the lock.
	monkeypatch.setattr(client_mod, "get_secret", lambda settings, key: "NEW" if key == "access_token" else "rt")
	client = client_mod.QuickBooksClient(types.SimpleNamespace())
	calls = {"token_request": 0}
	monkeypatch.setattr(client, "_token_request", lambda payload: calls.__setitem__("token_request", calls["token_request"] + 1))

	result = client.refresh_access_token(previous_access_token="OLD")

	assert result == {"access_token": "NEW"}
	assert calls["token_request"] == 0  # no second rotation of the refresh token


def test_refresh_access_token_disconnects_on_invalid_grant(monkeypatch):
	"""A dead grant clears the stored tokens and raises QuickBooksDisconnectedError."""
	import pytest

	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_mod

	monkeypatch.setattr(client_mod, "get_settings", lambda: types.SimpleNamespace())
	monkeypatch.setattr(client_mod, "get_secret", lambda settings, key: "tok")
	cleared = {}
	monkeypatch.setattr(
		client_mod, "clear_oauth_tokens", lambda settings, message=None: cleared.update(message=message)
	)
	client = client_mod.QuickBooksClient(types.SimpleNamespace())

	def invalid_grant(payload):
		raise client_mod.QuickBooksAPIError('QuickBooks token request failed: 400 {"error":"invalid_grant"}')

	monkeypatch.setattr(client, "_token_request", invalid_grant)

	with pytest.raises(client_mod.QuickBooksDisconnectedError):
		client.refresh_access_token(previous_access_token="tok")
	assert "disconnect" in (cleared.get("message") or "").lower()


def test_ordered_entities_imports_masters_before_transactions():
	"""ordered_entities sorts master records (Account/Customer/Item) before transactions."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.sync import ordered_entities

	assert ordered_entities(["Invoice", "Customer", "Item", "Account"]) == [
		"Account",
		"Customer",
		"Item",
		"Invoice",
	]


def test_verify_intuit_signature_accepts_valid_hmac():
	"""verify_intuit_signature accepts a correct HMAC-SHA256 and rejects a wrong one."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.utils import (
		verify_intuit_signature,
	)

	body = b'{"eventNotifications":[]}'
	token = "secret"
	signature = base64.b64encode(hmac.new(token.encode(), body, hashlib.sha256).digest()).decode()

	assert verify_intuit_signature(body, signature, token)
	assert not verify_intuit_signature(body, "bad", token)


def test_parse_qbo_datetime_converts_offset_to_naive_utc():
	"""parse_qbo_datetime converts an offset timestamp to naive UTC."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.utils import parse_qbo_datetime

	assert parse_qbo_datetime("2025-04-28 10:25:02-07:00") == datetime(2025, 4, 28, 17, 25, 2)


def test_customer_mapping_uses_native_erpnext_fields():
	"""A QBO Customer maps onto native ERPNext Customer fields (name/type/group)."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Customer",
		{"Id": "1", "DisplayName": "Acme Supply", "CompanyName": "Acme Supply"},
		types.SimpleNamespace(company="Demo Company"),
	)

	assert doctype == "Customer"
	assert values["customer_name"] == "Acme Supply"
	assert values["customer_type"] == "Company"
	assert values["customer_group"] == "Commercial"


def test_customer_type_resolves_against_customized_select_options(monkeypatch):
	"""QBO company/individual translate to the site's customized customer_type options."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe,
		"get_meta",
		lambda doctype: types.SimpleNamespace(
			has_field=lambda fieldname: False,
			get_field=lambda fieldname: types.SimpleNamespace(
				options="Commercial\nResidential\nPartnership"
			),
		),
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, company_values = map_qbo_to_erpnext(
		"Customer",
		{"Id": "1", "DisplayName": "Acme Supply", "CompanyName": "Acme Supply"},
		types.SimpleNamespace(company="Demo Company"),
	)
	_, person_values = map_qbo_to_erpnext(
		"Customer",
		{"Id": "2", "DisplayName": "Jane Doe"},
		types.SimpleNamespace(company="Demo Company"),
	)

	assert company_values["customer_type"] == "Commercial"
	assert person_values["customer_type"] == "Residential"


def test_ensure_group_parent_promotes_ledger_parent(monkeypatch):
	"""_ensure_group_parent converts an existing ledger parent Account to a group."""
	frappe = install_frappe_stub()
	parent = types.SimpleNamespace(is_group=0, account_type="Expense Account", saved=False)
	parent.save = lambda **kwargs: setattr(parent, "saved", True)
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name=None, fieldname=None, **kwargs: 0 if doctype == "Account" else None,
	)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name: parent, raising=False)
	from erpnext_enhancements.quickbooks_online.core.mapping import (
		_ensure_group_parent,
	)

	_ensure_group_parent("Account", {"parent_account": "Job Expenses - SF"})

	assert parent.is_group == 1
	# A set Account Type blocks ERPNext's ledger->group conversion.
	assert parent.account_type is None
	assert parent.saved


def test_ensure_group_parent_leaves_groups_and_other_doctypes_alone(monkeypatch):
	"""_ensure_group_parent is a no-op for group parents and non-Account doctypes."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name=None, fieldname=None, **kwargs: 1,
	)
	monkeypatch.setattr(
		frappe,
		"get_doc",
		lambda doctype, name: (_ for _ in ()).throw(AssertionError("should not load the parent")),
		raising=False,
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import (
		_ensure_group_parent,
	)

	_ensure_group_parent("Account", {"parent_account": "Job Expenses - SF"})
	_ensure_group_parent("Customer", {"parent_account": "Job Expenses - SF"})
	_ensure_group_parent("Account", {})


def test_clear_account_type_for_group_conversion(monkeypatch):
	"""account_type is cleared only when an existing ledger Account becomes a group."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name=None, fieldname=None, **kwargs: 0,
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import (
		_clear_account_type_for_group_conversion,
	)

	def make_doc(**attrs):
		doc = types.SimpleNamespace(name="Automobile - SF", **attrs)
		doc.get = lambda fieldname: getattr(doc, fieldname, None)
		return doc

	converting = make_doc(is_group=1, account_type="Expense Account")
	assert _clear_account_type_for_group_conversion("Account", converting) is True
	assert converting.account_type is None

	staying_ledger = make_doc(is_group=0, account_type="Expense Account")
	assert _clear_account_type_for_group_conversion("Account", staying_ledger) is False
	assert staying_ledger.account_type == "Expense Account"

	no_type = make_doc(is_group=1, account_type=None)
	assert _clear_account_type_for_group_conversion("Account", no_type) is False

	non_account = make_doc(is_group=1, account_type="Expense Account")
	assert _clear_account_type_for_group_conversion("Customer", non_account) is False
	assert non_account.account_type == "Expense Account"

	# Already a group in the DB: no conversion is happening, leave the type alone.
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name=None, fieldname=None, **kwargs: 1,
	)
	already_group = make_doc(is_group=1, account_type="Expense Account")
	assert _clear_account_type_for_group_conversion("Account", already_group) is False
	assert already_group.account_type == "Expense Account"


def test_keep_account_as_group_when_erpnext_children_exist(monkeypatch):
	"""An Account QBO reports as a leaf stays a group when ERPNext has children.

	Demoting it would trip "Account with child nodes cannot be set as ledger".
	"""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db, "exists", lambda doctype, filters=None: doctype == "Account" and bool(filters)
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import _keep_account_as_group

	def make_doc(**attrs):
		doc = types.SimpleNamespace(name="2100 - Accounts Payable - SF", **attrs)
		doc.get = lambda fieldname: getattr(doc, fieldname, None)
		return doc

	leaf_with_children = make_doc(is_group=0)
	assert _keep_account_as_group("Account", leaf_with_children) is True
	assert leaf_with_children.is_group == 1

	# Already a group, or a non-Account doctype: no change, no DB lookup needed.
	assert _keep_account_as_group("Account", make_doc(is_group=1)) is False
	assert _keep_account_as_group("Customer", make_doc(is_group=0)) is False

	# A genuine leaf without children is left as a ledger.
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, filters=None: False)
	childless = make_doc(is_group=0)
	assert _keep_account_as_group("Account", childless) is False
	assert childless.is_group == 0


def test_drop_self_parent_account_clears_self_reference():
	"""A root Account whose parent resolves to itself has parent_account dropped."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _drop_self_parent_account

	values = {"parent_account": "Build Income - SF", "is_group": 1}
	_drop_self_parent_account("Account", values, "Build Income - SF")
	assert "parent_account" not in values

	# A parent that is a different account is left in place.
	other = {"parent_account": "Income - SF"}
	_drop_self_parent_account("Account", other, "Build Income - SF")
	assert other["parent_account"] == "Income - SF"

	# Non-Account doctypes are untouched even on a self reference.
	non_account = {"parent_account": "X"}
	_drop_self_parent_account("Customer", non_account, "X")
	assert non_account["parent_account"] == "X"


def test_preflight_blocks_journal_lines_posting_to_party_accounts(monkeypatch):
	"""A Journal-Entry-mapped entity with an A/R or A/P line routes to manual review."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name=None, fieldname=None, **kwargs: "Payable" if doctype == "Account" else None,
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import validate_mapped_values

	# A Deposit maps onto a Journal Entry; a party-less line posts to a Payable account.
	values = {
		"company": "Demo",
		"accounts": [
			{"account": "Capital One Spark Card - SF", "debit_in_account_currency": 100, "credit_in_account_currency": 0},
			{"account": "Undeposited Funds - SF", "debit_in_account_currency": 0, "credit_in_account_currency": 100},
		],
	}
	issues = validate_mapped_values("Deposit", "Journal Entry", values, include_doc_required=False)
	assert (
		"Journal Entry line requires a Party for Receivable/Payable account: Capital One Spark Card - SF"
		in issues
	)


def test_party_guard_skips_journal_lines_that_already_have_a_party(monkeypatch):
	"""An A/P line carrying a party (e.g. an expense-only Bill) is not blocked."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name=None, fieldname=None, **kwargs: (
			"Payable" if doctype == "Account" and name == "2110 - Creditors - SF" else ("Expense Account" if doctype == "Account" else None)
		),
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import validate_mapped_values

	values = {
		"company": "Demo",
		"accounts": [
			{"account": "2110 - Creditors - SF", "credit_in_account_currency": 150.0, "party_type": "Supplier", "party": "Acme"},
			{"account": "Build Materials - SF", "debit_in_account_currency": 150.0},
		],
	}
	issues = validate_mapped_values("Bill", "Journal Entry", values, include_doc_required=False)
	assert not any("requires a Party" in i for i in issues)
	assert issues == []  # balanced, party present -> insertable


def test_account_based_bill_maps_to_journal_entry(monkeypatch):
	"""An expense-account QBO Bill maps to a JE debiting expenses, crediting A/P."""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return "2110 - Creditors - SF" if fieldname == "default_payable_account" else None
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Vendor":
				return "Clegg Mabey Reimbursement"
			if f.get("qbo_entity_type") == "Account":
				return {"800": "Build Materials - SF", "801": "Shop Supplies - SF"}.get(f.get("qbo_id"))
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	payload = {
		"Id": "21135",
		"TxnDate": "2026-06-02",
		"TotalAmt": 150.0,
		"VendorRef": {"value": "2614"},
		"Line": [
			{"Amount": 100.0, "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "800"}}},
			{"Amount": 50.0, "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "801"}}},
		],
	}

	doctype, values = map_qbo_to_erpnext("Bill", payload, types.SimpleNamespace(company="Sapphire Fountains"))

	assert doctype == "Journal Entry"
	accounts = values["accounts"]
	ap = accounts[0]
	assert ap["account"] == "2110 - Creditors - SF"
	assert ap["credit_in_account_currency"] == 150.0
	assert ap["party_type"] == "Supplier" and ap["party"] == "Clegg Mabey Reimbursement"
	debits = {a["account"]: a["debit_in_account_currency"] for a in accounts[1:]}
	assert debits == {"Build Materials - SF": 100.0, "Shop Supplies - SF": 50.0}


def test_ledger_line_drops_zero_value_rows():
	"""_ledger_line skips rows with no posting so ERPNext won't reject a 0/0 line."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _ledger_line

	assert _ledger_line("Bank - SF", debit=100) == {
		"account": "Bank - SF",
		"debit_in_account_currency": 100.0,
		"credit_in_account_currency": 0.0,
	}
	assert _ledger_line("Bank - SF", debit=0, credit=0) is None
	assert _ledger_line(None, debit=5) is None


def test_account_based_bill_skips_zero_amount_lines(monkeypatch):
	"""A QBO Bill's $0 expense line is dropped so the Journal Entry stays insertable."""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return "2110 - Creditors - SF" if fieldname == "default_payable_account" else None
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Vendor":
				return "C.A.R Automotive Repair"
			if f.get("qbo_entity_type") == "Account":
				return "Auto and Trailer Expense - SF"
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	payload = {
		"Id": "20892",
		"TxnDate": "2026-06-02",
		"TotalAmt": 64.35,
		"VendorRef": {"value": "2651"},
		"Line": [
			{"Amount": 60.0, "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "159"}}},
			{"Amount": 0, "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "159"}}},
			{"Amount": 4.35, "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "159"}}},
		],
	}
	_, values = map_qbo_to_erpnext("Bill", payload, types.SimpleNamespace(company="Sapphire Fountains"))

	# No row may have both sides zero, and the two non-zero expense lines remain.
	assert all(
		a["debit_in_account_currency"] or a["credit_in_account_currency"] for a in values["accounts"]
	)
	debits = [a for a in values["accounts"] if a["debit_in_account_currency"]]
	assert len(debits) == 2
	debit_total = sum(a["debit_in_account_currency"] for a in values["accounts"])
	credit_total = sum(a["credit_in_account_currency"] for a in values["accounts"])
	assert round(debit_total - credit_total, 2) == 0  # 60 + 4.35 == 64.35 A/P


def test_sales_items_set_cost_center_from_class(monkeypatch):
	"""Sales line cost_center comes from the line's mapped QBO Class; blank otherwise."""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Item":
				return "SERVICE - MAINTENANCE CONTRACT"
			if f.get("qbo_entity_type") == "Class":
				return "CL150 Service & Repair - SF"
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_items

	payload = {
		"Line": [
			{"Amount": 555.0, "Description": "labor", "SalesItemLineDetail": {"ItemRef": {"value": "279"}, "ClassRef": {"value": "100"}, "Qty": 3, "UnitPrice": 185}},
			{"Amount": 6.82, "Description": "chemicals", "SalesItemLineDetail": {"ItemRef": {"value": "279"}}},
		]
	}
	items = _sales_items(payload)

	assert items[0]["cost_center"] == "CL150 Service & Repair - SF"
	assert "cost_center" not in items[1]  # no ClassRef -> falls back to company default


def _item_resolver(item_code="SERVICE - MAINTENANCE CONTRACT"):
	"""Build a frappe.db.get_value that maps every QBO ItemRef to one ERPNext Item."""

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "QuickBooks Sync Mapping" and (filters or {}).get("qbo_entity_type") == "Item":
			return item_code
		return None

	return get_value


def _sales_line(amount, **detail):
	"""One QBO SalesItemLineDetail line; ``detail`` keys are written verbatim.

	Written verbatim matters: the mapper distinguishes an ABSENT ``Qty`` from a
	``Qty`` of 0, so a helper that defaulted the key would erase what is under test.
	"""
	return {"Amount": amount, "SalesItemLineDetail": dict({"ItemRef": {"value": "279"}}, **detail)}


def test_zero_quantity_sales_lines_are_not_billed_at_full_price(monkeypatch):
	"""QBO progress-billing lines (Qty 0) must not be billed at their unit price.

	Regression for the falsy-zero bug in ``_sales_items``: ``detail.get("Qty") or 1``
	read QBO's legitimate 0 as missing and substituted 1, and ERPNext then recomputed
	``amount = qty * rate`` on save. This is the shape of QBO invoice I100900, which
	totals $2,100.00 in QuickBooks and imported at $570,650.00.
	"""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _item_resolver())
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_items

	payload = {
		"Line": [
			_sales_line(2100.0, Qty=0.2, UnitPrice=10500),  # the only line billed this period
			_sales_line(0, Qty=0, UnitPrice=8000),  # contract line, not billed yet
			_sales_line(0, Qty=0, UnitPrice=None),  # ditto, and QBO prices it null
		]
	}
	items = _sales_items(payload)

	# Both Qty:0 lines are worth nothing, and ERPNext rejects a zero-quantity line.
	assert len(items) == 1
	# The whole point: the invoice bills QuickBooks' $2,100.00, not $2,100 + 8,000.
	assert sum(item["qty"] * item["rate"] for item in items) == 2100.0


def test_sales_line_qty_times_rate_reproduces_the_qbo_amount(monkeypatch):
	"""Every QBO sales-line shape yields a qty/rate pair whose product is QBO's Amount."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _item_resolver())
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_items

	payload = {
		"Line": [
			_sales_line(262.5, UnitPrice=262.5),  # Qty absent (QBO omits it for 1)
			_sales_line(5.91, UnitPrice=None),  # Qty absent, priced only by Amount
			_sales_line(555.0, Qty=3, UnitPrice=185),  # both given
			_sales_line(500.0, Qty=2),  # quantity, no unit price
			_sales_line(75.0, Qty=0),  # Qty 0 but the line carries money
		]
	}
	items = _sales_items(payload)

	assert [round(item["qty"] * item["rate"], 2) for item in items] == [262.5, 5.91, 555.0, 500.0, 75.0]
	# Second-order bug: a line with a quantity but no UnitPrice used to take the WHOLE
	# line amount as its rate, and was then multiplied by the quantity again (2 x 500).
	assert items[3]["rate"] == 250
	# Qty 0 with money on the line bills one unit, so the amount survives.
	assert (items[4]["qty"], items[4]["rate"]) == (1, 75.0)


def test_estimate_shares_the_sales_line_quantity_fix(monkeypatch):
	"""``_sales_items`` is shared with the Estimate -> Quotation path, so it is fixed too."""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return "USD" if fieldname == "default_currency" else None
		if doctype == "Price List":
			return "Standard Selling"
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Item":
				return "SERVICE - MAINTENANCE CONTRACT"
			if f.get("qbo_entity_type") == "Customer" and f.get("erpnext_doctype") == "Customer":
				return "Acme Supply"
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Estimate",
		{
			"Id": "9",
			"TxnDate": "2026-06-06",
			"CustomerRef": {"value": "1"},
			"Line": [_sales_line(400.0, Qty=2, UnitPrice=200), _sales_line(0, Qty=0, UnitPrice=9999)],
		},
		types.SimpleNamespace(company="SF"),
	)

	assert doctype == "Quotation"
	assert [(item["qty"], item["rate"]) for item in values["items"]] == [(2, 200)]


def test_bill_payment_sets_supplier_party_on_ap_line(monkeypatch):
	"""A BillPayment's A/P debit carries the vendor as Party and uses the default payable."""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return "2110 - Creditors - SF" if fieldname == "default_payable_account" else None
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Vendor":
				return "Plastic Works"
			if f.get("qbo_entity_type") == "Account":
				return "US Bank Checking - SF" if f.get("qbo_id") == "130" else None
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	payload = {
		"Id": "2955",
		"TxnDate": "2009-08-21",
		"TotalAmt": 87.5,
		"VendorRef": {"value": "1045"},
		"CheckPayment": {"BankAccountRef": {"value": "130"}},
		"Line": [{"Amount": 87.5}],
	}
	doctype, values = map_qbo_to_erpnext("BillPayment", payload, types.SimpleNamespace(company="Sapphire Fountains"))

	assert doctype == "Journal Entry"
	ap = values["accounts"][0]
	assert ap["account"] == "2110 - Creditors - SF"
	assert ap["debit_in_account_currency"] == 87.5
	assert ap["party_type"] == "Supplier" and ap["party"] == "Plastic Works"
	funding = values["accounts"][1]
	assert funding["account"] == "US Bank Checking - SF" and funding["credit_in_account_currency"] == 87.5


def test_heal_invalid_owned_selects_repairs_stale_value():
	"""A pre-existing invalid Select value is replaced with the valid mapped value."""
	frappe = install_frappe_stub()
	field = types.SimpleNamespace(fieldtype="Select", options="Commercial\nResidential\nPartnership")
	frappe.get_meta = lambda doctype: types.SimpleNamespace(
		get_field=lambda fieldname: field if fieldname == "customer_type" else None
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import _heal_invalid_owned_selects

	doc = types.SimpleNamespace(doctype="Customer", customer_type="Company", customer_name="Acme")
	doc.get = lambda fieldname: getattr(doc, fieldname, None)
	doc.set = lambda fieldname, value: setattr(doc, fieldname, value)

	healed = _heal_invalid_owned_selects(doc, {"customer_type": "Commercial", "customer_name": "Acme"})

	assert healed == ["customer_type"]
	assert doc.customer_type == "Commercial"

	# A value that is already valid (and non-Select fields) are left untouched.
	assert _heal_invalid_owned_selects(doc, {"customer_type": "Commercial"}) == []


def test_save_or_manual_review_parks_validation_errors(monkeypatch):
	"""A linked record's own validation failure becomes a manual_review action."""
	import pytest

	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	recorded = {}
	monkeypatch.setattr(
		mapping, "save_manual_review_mapping", lambda *args, **kwargs: recorded.update(issues=args[-1])
	)

	def make_doc(exc=None):
		# flags is required: the non-insert save path sets doc.flags.ignore_links.
		doc = types.SimpleNamespace(name="CUST-1", doctype="Customer", flags=types.SimpleNamespace())

		def save(**kwargs):
			if exc:
				raise exc

		doc.save = save
		return doc

	# A clean save returns None and records no review.
	assert mapping._save_or_manual_review("Customer", "1", {}, "Customer", make_doc()) is None
	assert recorded == {}

	# A ValidationError (e.g. a scheme-less website) is parked for manual review.
	err = frappe.exceptions.ValidationError("'www.x.com' is not a valid URL")
	result = mapping._save_or_manual_review("Customer", "1", {}, "Customer", make_doc(exc=err))
	assert result["action"] == "manual_review"
	assert "not a valid URL" in result["reason"]
	assert recorded["issues"] == ["'www.x.com' is not a valid URL"]

	# A concurrency conflict is re-raised so the normal retry path handles it.
	with pytest.raises(frappe.exceptions.TimestampMismatchError):
		mapping._save_or_manual_review(
			"Customer", "1", {}, "Customer", make_doc(exc=frappe.exceptions.TimestampMismatchError("locked"))
		)


def test_detect_conflicts_ignores_child_tables_and_flags_scalars():
	"""Conflict detection skips child tables but still catches scalar field edits."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import detect_conflicts

	owned = {
		# Stored snapshot of a Journal Entry: plain-dict child rows + scalars.
		"accounts": [{"account": "Bank - SF", "debit_in_account_currency": 0.0}],
		"posting_date": "2026-06-02",
		"remark": "Imported from QuickBooks Online Cash 21147",
	}
	mapping = types.SimpleNamespace(owned_fields=json.dumps(owned))
	incoming = {
		"accounts": [{"account": "Bank - SF", "debit_in_account_currency": 0.0}],
		"posting_date": "2026-06-02",
		"remark": "Imported from QuickBooks Online Cash 21147",
	}

	# The live doc returns child rows as objects (str() differs from the snapshot)
	# and an unchanged posting_date -- neither should be reported as a conflict.
	doc = types.SimpleNamespace(
		accounts=[types.SimpleNamespace(account="Bank - SF")],
		posting_date="2026-06-02",
		remark="Imported from QuickBooks Online Cash 21147",
	)
	doc.get = lambda fieldname: getattr(doc, fieldname, None)
	assert detect_conflicts(doc, incoming, mapping) == []

	# A genuine scalar edit (user changed the remark) is still detected.
	doc.remark = "Edited by a user"
	assert detect_conflicts(doc, incoming, mapping) == ["remark"]


def _stub_doc(**fields):
	"""A minimal doc double with get/set/flags, tracking save() calls."""
	doc = types.SimpleNamespace(flags=types.SimpleNamespace(), saves=[], **fields)
	doc.get = lambda fieldname: getattr(doc, fieldname, None)
	doc.set = lambda fieldname, value: setattr(doc, fieldname, value)
	doc.save = lambda **kwargs: doc.saves.append(kwargs)
	return doc


def test_apply_values_returns_changed_flag():
	"""apply_values reports whether any mapped value actually differs from the doc."""
	install_frappe_stub()
	from datetime import date

	from erpnext_enhancements.quickbooks_online.core.mapping import apply_values

	doc = _stub_doc(
		doctype="Customer",
		customer_name="Acme Supply",
		customer_type="Commercial",
		disabled=0,
		conversion_rate=1.0,
		posting_date=date(2026, 6, 2),
		account_number="0123",
	)

	# Value-identical payload -> unchanged, even across representations Frappe
	# would cast to the same stored value: int over float (1 vs 1.0), bool over
	# Check int (False vs 0), and a date object vs its YYYY-MM-DD string form.
	assert (
		apply_values(
			doc,
			{
				"customer_name": "Acme Supply",
				"customer_type": "Commercial",
				"disabled": False,
				"conversion_rate": 1,
				"posting_date": "2026-06-02",
				"skipped_none": None,  # None values are never applied nor compared
			},
		)
		is False
	)
	assert doc.saves == []

	# Two STRINGS that differ only numerically are a genuine change (a Data field
	# keeps leading zeros), so the numeric fallback must not swallow it.
	assert apply_values(doc, {"account_number": "123"}) is True
	assert doc.account_number == "123"

	# A real scalar change flags, and the value is applied.
	assert apply_values(doc, {"customer_type": "Residential"}) is True
	assert doc.customer_type == "Residential"

	# Child-table (list) values can't be compared reliably -> always "changed".
	doc.items = [types.SimpleNamespace(item_code="X")]
	assert apply_values(doc, {"items": [{"item_code": "X"}]}) is True


def test_update_branch_skips_save_when_nothing_changed(monkeypatch):
	"""A value-identical re-sync refreshes the Sync Mapping but never doc.save()s.

	This is the no-op-save guard: a full import / CDC replay used to re-save 1000+
	identical Customers per run, churning modified/modified_by and firing doc_update
	realtime events at anyone viewing them.
	"""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	values = {"customer_name": "Acme Supply", "customer_type": "Commercial"}
	# What the last sync stored (detect_conflicts' baseline) -- deliberately frozen
	# so the second, value-moved upsert below reads as QBO moving, not a user edit.
	owned = json.dumps(values)
	monkeypatch.setattr(mapping, "map_qbo_to_erpnext", lambda *args: ("Customer", dict(values)))
	monkeypatch.setattr(mapping, "validate_mapped_values", lambda *args, **kwargs: [])
	monkeypatch.setattr(mapping, "_ensure_group_parent", lambda *args: None)
	monkeypatch.setattr(
		mapping,
		"get_mapping",
		lambda *args: types.SimpleNamespace(
			erpnext_doctype="Customer",
			erpnext_name="Acme Supply",
			owned_fields=owned,
			match_status="Auto Matched",
			conflict_status="Clean",
		),
	)
	doc = _stub_doc(doctype="Customer", name="Acme Supply", **values)
	monkeypatch.setattr(frappe.db, "exists", lambda doctype, name: True, raising=False)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name: doc, raising=False)
	saved_mappings = []
	monkeypatch.setattr(mapping, "save_mapping", lambda *args, **kwargs: saved_mappings.append(kwargs))

	settings = types.SimpleNamespace(company="Sapphire Fountains")
	result = mapping.upsert_entity("Customer", {"Id": "1", "DisplayName": "Acme Supply"}, settings)

	assert result == {"action": "unchanged", "doctype": "Customer", "name": "Acme Supply"}
	assert doc.saves == []  # the whole point: no doc write, no modified churn
	# The mapping bookkeeping (SyncToken/cursor refresh, conflict status) still ran.
	assert saved_mappings == [{"conflict_status": "Clean"}]

	# The same payload with one moved value saves and reports "updated".
	values["customer_type"] = "Residential"
	result = mapping.upsert_entity("Customer", {"Id": "1", "DisplayName": "Acme Supply"}, settings)

	assert result == {"action": "updated", "doctype": "Customer", "name": "Acme Supply"}
	assert len(doc.saves) == 1
	assert doc.customer_type == "Residential"
	assert len(saved_mappings) == 2


def test_credit_card_account_is_untyped_liability():
	"""QBO Credit Card accounts map to an untyped Liability ledger, not a Payable.

	Typing them Payable made ERPNext demand a Party on every journal line funding a
	purchase or bill payment from the card, blocking those postings.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _account_root_type, _account_type

	assert _account_type("Credit Card") is None
	assert _account_root_type("Credit Card") == "Liability"
	# Genuine A/P is still typed Payable (it legitimately needs a party).
	assert _account_type("Accounts Payable") == "Payable"


def test_account_mapping_uses_existing_root_as_parent():
	"""A QBO Account maps under the matching ERPNext root account as a leaf."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Account",
		{"Id": "10", "Name": "Advertising", "AccountType": "Expense", "SubAccount": False},
		types.SimpleNamespace(company="Demo Company"),
	)

	assert doctype == "Account"
	assert values["parent_account"] == "Expenses - DC"
	assert values["is_group"] == 0
	assert values["root_type"] == "Expense"


def test_account_parent_with_qbo_children_is_group():
	"""A QBO Account flagged as having children maps to an ERPNext group account."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Account",
		{
			"Id": "10",
			"Name": "Job Materials",
			"AccountType": "Expense",
			"SubAccount": False,
			"_qbo_has_children": True,
		},
		types.SimpleNamespace(company="Demo Company"),
	)

	assert doctype == "Account"
	assert values["parent_account"] == "Expenses - DC"
	assert values["is_group"] == 1
	# Group accounts must not carry an account_type: it blocks ledger->group
	# conversion and groups never receive postings anyway.
	assert values["account_type"] is None


def test_account_payload_query_marks_parents_without_polluting_raw_payload(monkeypatch):
	"""query_entity_payloads tags parents with _qbo_has_children but strips it from clean payloads."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	monkeypatch.setattr(
		sync,
		"query_all",
		lambda entity_type, settings=None: iter(
			[
				{"Id": "10", "Name": "Job Materials"},
				{"Id": "11", "Name": "Plants", "ParentRef": {"value": "10"}},
			]
		),
	)

	payloads = list(sync.query_entity_payloads("Account"))

	assert payloads[0]["_qbo_has_children"] is True
	assert payloads[1]["_qbo_has_children"] is False
	assert sync._clean_payload(payloads[0]) == {"Id": "10", "Name": "Job Materials"}


def test_payment_mapping_sets_customer_party():
	"""A QBO Payment maps to a Payment Entry with the resolved Customer party."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Payment",
		{"Id": "99", "TxnDate": "2026-06-06", "CustomerRef": {"value": "1"}},
		types.SimpleNamespace(company="Demo Company"),
	)

	assert doctype == "Payment Entry"
	assert values["party_type"] == "Customer"
	assert values["party"] == "Acme Supply"


def test_payment_without_mapped_party_is_skipped():
	"""A QBO Payment with no resolvable party is skipped (returns None / empty values)."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Payment",
		{"Id": "99", "TxnDate": "2026-06-06"},
		types.SimpleNamespace(company="Demo Company"),
	)

	assert doctype is None
	assert values == {}


def test_preflight_flags_site_required_customer_fields_without_defaults():
	"""validate_mapped_values flags site-mandatory fields lacking defaults, unless opted out."""
	frappe = install_frappe_stub()
	frappe.get_meta = lambda doctype: types.SimpleNamespace(
		fields=[
			types.SimpleNamespace(fieldname="customer_name", fieldtype="Data", reqd=1, default=None),
			types.SimpleNamespace(fieldname="custom_lead_source", fieldtype="Link", reqd=1, default=None),
		],
		has_field=lambda fieldname: False,
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import validate_mapped_values

	assert validate_mapped_values("Customer", "Customer", {"customer_name": "Weiskopf Consulting"}) == [
		"Missing required field: custom_lead_source"
	]
	assert (
		validate_mapped_values(
			"Customer",
			"Customer",
			{"customer_name": "Weiskopf Consulting"},
			include_doc_required=False,
		)
		== []
	)


def test_preflight_flags_transactions_with_missing_links_and_rows():
	"""validate_mapped_values reports each missing required link/child-row on a transaction."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import validate_mapped_values

	assert validate_mapped_values("Bill", "Purchase Invoice", {"company": "Demo", "supplier": None, "items": []}) == [
		"Missing required field: items",
		"Missing required field: supplier",
	]


def test_customer_auto_match_uses_existing_customer_name():
	"""find_existing_match auto-matches a QBO customer to an existing one by name."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import find_existing_match

	match = find_existing_match(
		"Customer",
		{"Id": "1", "DisplayName": "Acme Supply", "CompanyName": "Acme Supply"},
		types.SimpleNamespace(company="Demo Company"),
	)

	assert match["status"] == "matched"
	assert match["name"] == "Acme Supply"
	assert match["rule"] == "customer_name"


def test_failed_result_updates_sync_log_error_message():
	"""_track_result increments failed_count and appends a concise entity error line."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.sync import _track_result

	log = types.SimpleNamespace(failed_count=0, error_message=None, entity_type=None)

	_track_result(
		log,
		{
			"action": "failed",
			"entity_type": "Customer",
			"qbo_id": "123",
			"reason": "Traceback\nValidationError: Missing customer group",
		},
	)

	assert log.failed_count == 1
	assert "Customer 123: ValidationError: Missing customer group" in log.error_message


def test_failed_result_error_message_is_capped():
	"""_track_result caps the accumulated error message, omitting overflow entries."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.sync import _track_result

	log = types.SimpleNamespace(failed_count=0, error_message=None, entity_type=None)

	for index in range(22):
		_track_result(
			log,
			{
				"action": "failed",
				"entity_type": "Item",
				"qbo_id": str(index),
				"reason": f"Traceback\nValidationError: Row {index}",
			},
		)

	assert log.failed_count == 22
	assert "Item 19: ValidationError: Row 19" in log.error_message
	assert "Additional failures omitted" in log.error_message
	assert "Item 21: ValidationError: Row 21" not in log.error_message


# ---------------------------------------------------------------------------
# CDC changedSince formatting + window clamp (the reported 400 ValidationFault).
# ---------------------------------------------------------------------------


def test_format_qbo_datetime_renders_iso_utc_with_z():
	"""format_qbo_datetime turns a naive (system-tz=UTC) datetime into ISO-8601 Z."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.utils import format_qbo_datetime

	assert format_qbo_datetime(datetime(2026, 6, 9, 20, 1, 2, 412672)) == "2026-06-09T20:01:02Z"
	assert format_qbo_datetime(None) is None


def test_format_qbo_datetime_converts_system_timezone_to_utc(monkeypatch):
	"""A naive datetime in a non-UTC system timezone is shifted to UTC before formatting."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import utils

	monkeypatch.setattr(utils, "get_system_timezone", lambda: "America/Denver")
	# 13:01:02 in Denver (MDT, -06:00 in June) is 19:01:02 UTC.
	assert utils.format_qbo_datetime(datetime(2026, 6, 9, 13, 1, 2)) == "2026-06-09T19:01:02Z"


def test_cdc_sends_iso_changed_since(monkeypatch):
	"""client.cdc serializes the cursor as ISO-8601 UTC (not a raw datetime string)."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_module

	client = client_module.QuickBooksClient(types.SimpleNamespace(realm_id="42", environment="Production"))
	captured = {}
	monkeypatch.setattr(
		client, "request", lambda method, path, **kwargs: captured.update(method=method, path=path, **kwargs) or {}
	)

	client.cdc(["Account", "Invoice"], datetime(2026, 6, 9, 20, 1, 2, 412672))

	assert captured["params"]["changedSince"] == "2026-06-09T20:01:02Z"
	assert captured["params"]["entities"] == "Account,Invoice"
	assert captured["path"].endswith("/cdc")


def test_clamp_cdc_cursor_limits_stale_cursor():
	"""_clamp_cdc_cursor keeps a recent cursor but pulls a stale/None one into the window."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.sync import _clamp_cdc_cursor

	now = datetime(2026, 6, 15, 12, 0, 0)
	earliest = datetime(2026, 5, 17, 12, 0, 0)  # now - 29 days (30-day limit, 1-day margin)

	recent = datetime(2026, 6, 14, 12, 0, 0)
	assert _clamp_cdc_cursor(recent, now) == recent
	assert _clamp_cdc_cursor(datetime(2026, 1, 1, 0, 0, 0), now) == earliest
	assert _clamp_cdc_cursor(None, now) == earliest


def test_query_all_includes_inactive_for_master_entities(monkeypatch):
	"""query_all adds the Active in (true,false) clause for masters only, not transactions."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	captured = []

	class FakeClient:
		def __init__(self, settings=None):
			pass

		def query(self, query):
			captured.append(query)
			return {"QueryResponse": {}}

	monkeypatch.setattr(sync, "QuickBooksClient", FakeClient)

	list(sync.query_all("Account", settings=types.SimpleNamespace()))
	list(sync.query_all("Invoice", settings=types.SimpleNamespace()))

	assert "from Account where Active in (true, false) startposition 1 maxresults 100" in captured[0]
	assert "where Active" not in captured[1]
	assert "from Invoice startposition 1 maxresults 100" in captured[1]


# ---------------------------------------------------------------------------
# Validation no longer flags fields ERPNext auto-populates (the missing-field
# errors reported for every transaction type).
# ---------------------------------------------------------------------------


def test_required_field_check_skips_autofilled_fields():
	"""naming_series, read_only totals and fetch_from fields are not flagged as missing."""
	frappe = install_frappe_stub()
	frappe.get_meta = lambda doctype: types.SimpleNamespace(
		fields=[
			types.SimpleNamespace(fieldname="naming_series", fieldtype="Select", reqd=1, default=None, read_only=0, fetch_from=None),
			types.SimpleNamespace(fieldname="grand_total", fieldtype="Currency", reqd=1, default=None, read_only=1, fetch_from=None),
			types.SimpleNamespace(
				fieldname="paid_from_account_currency", fieldtype="Link", reqd=1, default=None, read_only=0,
				fetch_from="paid_from.account_currency",
			),
			types.SimpleNamespace(fieldname="custom_audit_tag", fieldtype="Data", reqd=1, default=None, read_only=0, fetch_from=None),
		],
		has_field=lambda fieldname: False,
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import validate_mapped_values

	# Only the genuinely-unfillable custom field survives; the rest ERPNext fills itself.
	assert validate_mapped_values(
		"Invoice", "Sales Invoice", {"company": "X", "customer": "Y", "items": [{}]}
	) == ["Missing required field: custom_audit_tag"]


def test_sales_invoice_sets_currency_exchange_and_receivable(monkeypatch):
	"""Sales Invoice mapping fills currency/conversion_rate/debit_to/price list from defaults."""
	frappe = install_frappe_stub()

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return {"default_receivable_account": "Debtors - SF", "default_currency": "USD"}.get(fieldname)
		if doctype == "Price List":
			return "Standard Selling"
		if doctype == "QuickBooks Sync Mapping" and filters.get("qbo_entity_type") == "Customer":
			return "Acme Supply"
		return None

	monkeypatch.setattr(frappe.db, "get_value", get_value)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Invoice",
		{"Id": "5", "TxnDate": "2026-06-06", "CustomerRef": {"value": "1"}},
		types.SimpleNamespace(company="Sapphire Fountains LLC"),
	)

	assert doctype == "Sales Invoice"
	assert values["customer"] == "Acme Supply"
	assert values["currency"] == "USD"
	assert values["conversion_rate"] == 1
	assert values["debit_to"] == "Debtors - SF"
	assert values["selling_price_list"] == "Standard Selling"
	assert values["price_list_currency"] == "USD"


def test_purchase_invoice_sets_payable_account(monkeypatch):
	"""Purchase Invoice mapping fills credit_to from the company default payable account."""
	frappe = install_frappe_stub()

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return {"default_payable_account": "Creditors - SF", "default_currency": "USD"}.get(fieldname)
		if doctype == "QuickBooks Sync Mapping" and filters.get("qbo_entity_type") == "Vendor":
			return "ICS Supply"
		return None

	monkeypatch.setattr(frappe.db, "get_value", get_value)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Bill",
		{"Id": "6", "TxnDate": "2026-06-06", "VendorRef": {"value": "2"}},
		types.SimpleNamespace(company="SF"),
	)

	assert doctype == "Purchase Invoice"
	assert values["supplier"] == "ICS Supply"
	assert values["credit_to"] == "Creditors - SF"
	assert values["currency"] == "USD"


def test_payment_entry_sets_accounts_amounts_and_rates(monkeypatch):
	"""A customer Payment becomes a Receive PE with bank/receivable accounts and amounts."""
	frappe = install_frappe_stub()

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return {
				"default_receivable_account": "Debtors - SF",
				"default_bank_account": "US Bank - SF",
				"default_currency": "USD",
			}.get(fieldname)
		if doctype == "QuickBooks Sync Mapping" and filters.get("qbo_entity_type") == "Customer":
			return "Acme Supply"
		return None

	monkeypatch.setattr(frappe.db, "get_value", get_value)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Payment",
		{"Id": "9", "TxnDate": "2026-06-06", "TotalAmt": "3000", "CustomerRef": {"value": "1"}},
		types.SimpleNamespace(company="SF"),
	)

	assert doctype == "Payment Entry"
	assert values["payment_type"] == "Receive"
	assert values["paid_from"] == "Debtors - SF"
	assert values["paid_to"] == "US Bank - SF"
	assert values["paid_amount"] == 3000.0
	assert values["received_amount"] == 3000.0
	assert values["source_exchange_rate"] == 1
	# ERPNext requires a reference no/date for bank transactions; falls back to the QBO id.
	assert values["reference_no"] == "9"
	assert values["reference_date"] == "2026-06-06"


# ---------------------------------------------------------------------------
# New cash-movement mappers -> balanced Journal Entries. Directions are verified
# against the real QBO Journal export (Sapphire Fountains LLC).
# ---------------------------------------------------------------------------


def _account_resolver(account_map):
	"""Build a frappe.db.get_value that resolves QBO account ids to ERPNext names."""

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "QuickBooks Sync Mapping" and (filters or {}).get("qbo_entity_type") == "Account":
			return account_map.get(str(filters.get("qbo_id")))
		return None

	return get_value


def _rows_by_account(values):
	return {row["account"]: row for row in values["accounts"]}


def test_purchase_maps_to_balanced_journal_entry(monkeypatch):
	"""A QBO Expense credits the funding account and debits the expense account."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _account_resolver({"30": "Amex - SF", "61": "Office Expense - SF"}))
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Purchase",
		{
			"Id": "7",
			"TxnDate": "2026-06-06",
			"TotalAmt": "1064.20",
			"PaymentType": "CreditCard",
			"AccountRef": {"value": "30"},
			"Line": [{"Amount": "1064.20", "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "61"}}}],
		},
		types.SimpleNamespace(company="SF"),
	)

	assert doctype == "Journal Entry"
	rows = _rows_by_account(values)
	assert rows["Amex - SF"]["credit_in_account_currency"] == 1064.20
	assert rows["Office Expense - SF"]["debit_in_account_currency"] == 1064.20
	assert sum(r["debit_in_account_currency"] for r in values["accounts"]) == sum(
		r["credit_in_account_currency"] for r in values["accounts"]
	)


def test_credit_card_credit_reverses_journal_entry(monkeypatch):
	"""A QBO Credit (refund) debits the card and credits the expense account."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _account_resolver({"22": "Capital One - SF", "60": "R&D - SF"}))
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, values = map_qbo_to_erpnext(
		"Purchase",
		{
			"Id": "8",
			"TxnDate": "2026-06-06",
			"TotalAmt": "16.54",
			"Credit": True,
			"AccountRef": {"value": "22"},
			"Line": [{"Amount": "16.54", "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "60"}}}],
		},
		types.SimpleNamespace(company="SF"),
	)

	rows = _rows_by_account(values)
	assert rows["Capital One - SF"]["debit_in_account_currency"] == 16.54
	assert rows["R&D - SF"]["credit_in_account_currency"] == 16.54


def test_transfer_debits_destination_credits_source(monkeypatch):
	"""A QBO Transfer debits ToAccountRef and credits FromAccountRef."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _account_resolver({"13": "Checking - SF", "99": "Equity - SF"}))
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, values = map_qbo_to_erpnext(
		"Transfer",
		{"Id": "3", "TxnDate": "2026-06-06", "Amount": "300", "ToAccountRef": {"value": "13"}, "FromAccountRef": {"value": "99"}},
		types.SimpleNamespace(company="SF"),
	)

	rows = _rows_by_account(values)
	assert rows["Checking - SF"]["debit_in_account_currency"] == 300
	assert rows["Equity - SF"]["credit_in_account_currency"] == 300


def test_bill_payment_debits_payable_credits_bank(monkeypatch):
	"""A QBO BillPayment (Check) debits A/P and credits the bank account."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _account_resolver({"20": "Creditors - SF", "13": "Checking - SF"}))
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, values = map_qbo_to_erpnext(
		"BillPayment",
		{
			"Id": "4",
			"TxnDate": "2026-06-06",
			"TotalAmt": "2628.93",
			"APAccountRef": {"value": "20"},
			"CheckPayment": {"BankAccountRef": {"value": "13"}},
		},
		types.SimpleNamespace(company="SF"),
	)

	rows = _rows_by_account(values)
	assert rows["Creditors - SF"]["debit_in_account_currency"] == 2628.93
	assert rows["Checking - SF"]["credit_in_account_currency"] == 2628.93


def test_credit_card_payment_debits_card_credits_bank(monkeypatch):
	"""A QBO CreditCardPayment debits the card liability and credits the funding bank."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _account_resolver({"22": "Spark Card - SF", "13": "Key Bank - SF"}))
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, values = map_qbo_to_erpnext(
		"CreditCardPayment",
		{"Id": "5", "TxnDate": "2026-06-06", "Amount": "4613.33", "CreditCardAccountRef": {"value": "22"}, "BankAccountRef": {"value": "13"}},
		types.SimpleNamespace(company="SF"),
	)

	rows = _rows_by_account(values)
	assert rows["Spark Card - SF"]["debit_in_account_currency"] == 4613.33
	assert rows["Key Bank - SF"]["credit_in_account_currency"] == 4613.33


def test_deposit_debits_bank_credits_source_lines(monkeypatch):
	"""A QBO Deposit debits the deposited-to account and credits each source line."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _account_resolver({"13": "Checking - SF", "138": "Undeposited - SF"}))
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Deposit",
		{
			"Id": "6",
			"TxnDate": "2026-06-06",
			"TotalAmt": "3000",
			"DepositToAccountRef": {"value": "13"},
			"Line": [{"Amount": "3000", "DepositLineDetail": {"AccountRef": {"value": "138"}}}],
		},
		types.SimpleNamespace(company="SF"),
	)

	assert doctype == "Journal Entry"
	rows = _rows_by_account(values)
	assert rows["Checking - SF"]["debit_in_account_currency"] == 3000
	assert rows["Undeposited - SF"]["credit_in_account_currency"] == 3000


def test_vendor_credit_debits_payable_credits_expense(monkeypatch):
	"""A QBO VendorCredit debits A/P and credits the expense account line."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _account_resolver({"20": "Creditors - SF", "51": "Build Materials - SF"}))
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, values = map_qbo_to_erpnext(
		"VendorCredit",
		{
			"Id": "7",
			"TxnDate": "2026-06-06",
			"TotalAmt": "168.54",
			"APAccountRef": {"value": "20"},
			"Line": [{"Amount": "168.54", "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "51"}}}],
		},
		types.SimpleNamespace(company="SF"),
	)

	rows = _rows_by_account(values)
	assert rows["Creditors - SF"]["debit_in_account_currency"] == 168.54
	assert rows["Build Materials - SF"]["credit_in_account_currency"] == 168.54


def _payment_stub(monkeypatch, frappe, invoice_docstatus):
	"""Stub the lookups ``_map_payment_entry`` makes; ``invoice_docstatus`` drives SI-1."""

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return {
				"default_currency": "USD",
				"default_bank_account": "Checking - SF",
				"default_receivable_account": "Debtors - SF",
			}.get(fieldname)
		if doctype == "Sales Invoice" and fieldname == "docstatus":
			return invoice_docstatus
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Customer" and f.get("erpnext_doctype") == "Customer":
				return "Acme Supply"
			if f.get("qbo_entity_type") == "Invoice":
				return {"21658": "SI-1", "19179": "SI-2"}.get(str(f.get("qbo_id")))
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)


def _qbo_payment(*linked):
	"""A QBO Payment payload whose lines carry ``(amount, invoice_txn_id)`` LinkedTxns."""
	return {
		"Id": "21659",
		"TxnDate": "2026-07-30",
		"TotalAmt": sum(amount for amount, _txn_id in linked),
		"CustomerRef": {"value": "1477"},
		"Line": [
			{"Amount": amount, "LinkedTxn": [{"TxnId": txn_id, "TxnType": "Invoice"}]}
			for amount, txn_id in linked
		],
	}


def test_payment_entry_allocates_against_submitted_invoices(monkeypatch):
	"""A Payment's LinkedTxn lines become Payment Entry references to their invoices."""
	frappe = install_frappe_stub()
	_payment_stub(monkeypatch, frappe, invoice_docstatus=1)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Payment", _qbo_payment((795.35, "21658"), (204.65, "19179")), types.SimpleNamespace(company="SF")
	)

	assert doctype == "Payment Entry"
	assert values["references"] == [
		{"reference_doctype": "Sales Invoice", "reference_name": "SI-1", "allocated_amount": 795.35},
		{"reference_doctype": "Sales Invoice", "reference_name": "SI-2", "allocated_amount": 204.65},
	]


def test_payment_entry_skips_references_to_draft_invoices(monkeypatch):
	"""A payment whose invoice is still a draft imports UNALLOCATED rather than failing.

	ERPNext refuses to allocate against a draft and this integration imports invoices
	as drafts, so the payment must survive the ordering and pick its allocation up on
	a later re-sync -- not raise and park every payment in the backlog.
	"""
	frappe = install_frappe_stub()
	_payment_stub(monkeypatch, frappe, invoice_docstatus=0)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_doctype, values = map_qbo_to_erpnext(
		"Payment", _qbo_payment((795.35, "21658")), types.SimpleNamespace(company="SF")
	)

	assert values["references"] == []
	assert values["paid_amount"] == 795.35  # the receipt itself still imports in full


def test_payment_entry_ignores_non_invoice_linked_txns(monkeypatch):
	"""A payment's top-level Deposit LinkedTxn is a bank sweep, not an allocation."""
	frappe = install_frappe_stub()
	_payment_stub(monkeypatch, frappe, invoice_docstatus=1)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	payload = _qbo_payment((500.0, "21658"))
	payload["LinkedTxn"] = [{"TxnId": "21583", "TxnType": "Deposit"}]
	_doctype, values = map_qbo_to_erpnext("Payment", payload, types.SimpleNamespace(company="SF"))

	assert [row["reference_name"] for row in values["references"]] == ["SI-1"]


def _sales_ledger_stub(monkeypatch, frappe):
	"""Stub the account/party/item lookups the credit-memo and refund mappers make."""

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return {
				"default_receivable_account": "Debtors - SF",
				"default_income_account": "4110 - Sales - SF",
			}.get(fieldname)
		if doctype == "Item Default":
			return None  # no imported Item carries one; the Company default is the real path
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Item":
				return "SERVICE - MAINTENANCE CONTRACT"
			if f.get("qbo_entity_type") == "Customer" and f.get("erpnext_doctype") == "Customer":
				return "Acme Supply"
			if f.get("qbo_entity_type") == "Account":
				return "Checking - SF" if str(f.get("qbo_id")) == "134" else None
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)


def test_credit_memo_credits_receivable_and_debits_income(monkeypatch):
	"""A QBO CreditMemo credits A/R (customer as Party) and debits each line's income account."""
	frappe = install_frappe_stub()
	_sales_ledger_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"CreditMemo",
		{
			"Id": "9001",
			"DocNumber": "CM-1",
			"TxnDate": "2026-06-06",
			"TotalAmt": 450.0,
			"CustomerRef": {"value": "1"},
			"Line": [_sales_line(300.0, Qty=2, UnitPrice=150), _sales_line(150.0, UnitPrice=150)],
		},
		types.SimpleNamespace(company="SF"),
	)

	assert doctype == "Journal Entry"
	receivable = values["accounts"][0]
	assert receivable["account"] == "Debtors - SF"
	assert receivable["credit_in_account_currency"] == 450.0
	assert (receivable["party_type"], receivable["party"]) == ("Customer", "Acme Supply")
	income = [row for row in values["accounts"] if row["account"] == "4110 - Sales - SF"]
	assert sum(row["debit_in_account_currency"] for row in income) == 450.0
	assert sum(row["debit_in_account_currency"] for row in values["accounts"]) == sum(
		row["credit_in_account_currency"] for row in values["accounts"]
	)


def test_refund_receipt_credits_bank_and_debits_income(monkeypatch):
	"""A QBO RefundReceipt credits the account it was paid from and never touches A/R."""
	frappe = install_frappe_stub()
	_sales_ledger_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"RefundReceipt",
		{
			"Id": "9002",
			"DocNumber": "RR-1",
			"TxnDate": "2026-06-06",
			"TotalAmt": 120.0,
			"CustomerRef": {"value": "1"},
			"DepositToAccountRef": {"value": "134"},
			"Line": [_sales_line(120.0, Qty=1, UnitPrice=120)],
		},
		types.SimpleNamespace(company="SF"),
	)

	assert doctype == "Journal Entry"
	rows = _rows_by_account(values)
	assert rows["Checking - SF"]["credit_in_account_currency"] == 120.0
	assert rows["4110 - Sales - SF"]["debit_in_account_currency"] == 120.0
	assert "Debtors - SF" not in rows  # QBO settles a refund receipt immediately


def test_credit_memo_and_refund_receipt_are_in_the_entity_catalogue():
	"""New sell-side entities are wired into every list a full/CDC import reads."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import constants

	for entity in ("CreditMemo", "RefundReceipt"):
		assert entity in constants.ACCOUNTING_ENTITIES
		assert entity in constants.TRANSACTION_ENTITIES
		assert entity in constants.CDC_ENTITIES
		assert constants.ENTITY_DOCTYPE_MAP[entity] == "Journal Entry"


# A miniature chart of accounts covering the three group-redirect cases: a group WITH
# a "- General" ledger child, a group WITHOUT one, and a plain ledger.
_CHART = {
	"60300 - Research & Development - SF": {"is_group": 1},
	"60301 - R&D - General - SF": {
		"is_group": 0,
		"parent_account": "60300 - Research & Development - SF",
		"account_name": "R&D - General",
	},
	"61000 - General & Administrative - SF": {"is_group": 1},  # no "- General" child
	"7010 - Office Supplies - SF": {"is_group": 0},
	"30 - Amex - SF": {"is_group": 0},
}


def _chart_resolver(qbo_accounts=None, company=None, item_default=None, chart=None):
	"""frappe.db.get_value over ``_CHART`` plus the QBO/Company/Item lookups mappers make."""
	chart = chart if chart is not None else _CHART

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Account":
			if isinstance(filters, str):
				return (chart.get(filters) or {}).get(fieldname)
			f = filters or {}
			# The "- General" child lookup: a ledger under this parent whose name ends
			# in the designated suffix.
			for name, meta in chart.items():
				if (
					meta.get("parent_account") == f.get("parent_account")
					and not meta.get("is_group")
					and str(meta.get("account_name", "")).endswith("- General")
				):
					return name
			return None
		if doctype == "Company":
			return (company or {}).get(fieldname)
		if doctype == "Item Default":
			return (item_default or {}).get(fieldname)
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Account":
				return (qbo_accounts or {}).get(str(f.get("qbo_id")))
			if f.get("qbo_entity_type") == "Item":
				return "PUMP-1"
		return None

	return get_value


def test_group_account_resolves_to_its_general_ledger_child(monkeypatch):
	"""A QBO account mapped onto a GROUP account posts to its "- General" child.

	QuickBooks permits posting to an account that also has sub-accounts; ERPNext
	refuses to submit such a line. Without this redirect the next CDC sync would
	rewrite a remapped Journal Entry line straight back onto the group parent.
	"""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db, "get_value", _chart_resolver(qbo_accounts={"77": "60300 - Research & Development - SF"})
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import _resolve_account

	assert _resolve_account(types.SimpleNamespace(company="SF"), "77") == "60301 - R&D - General - SF"


def test_ledger_account_resolves_to_itself_unchanged(monkeypatch):
	"""The redirect is a no-op for the ordinary case: a ledger account posts to itself."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _chart_resolver(qbo_accounts={"88": "7010 - Office Supplies - SF"}))
	from erpnext_enhancements.quickbooks_online.core.mapping import _resolve_account

	assert _resolve_account(types.SimpleNamespace(company="SF"), "88") == "7010 - Office Supplies - SF"


def test_group_account_without_a_general_child_parks_the_transaction(monkeypatch):
	"""A group with no "- General" child resolves to None, so the balance guard parks it.

	The mapper deliberately does NOT create the child: inventing ledger structure
	while transforming a payload is worse than parking for review.
	"""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		_chart_resolver(qbo_accounts={"99": "61000 - General & Administrative - SF", "30": "30 - Amex - SF"}),
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	settings = types.SimpleNamespace(company="SF")
	assert map_qbo_to_erpnext("Purchase", {"Id": "1", "AccountRef": {"value": "99"}}, settings)[1]["accounts"] == []

	payload = {
		"Id": "7",
		"TxnDate": "2026-06-06",
		"TotalAmt": "500.00",
		"AccountRef": {"value": "30"},
		"Line": [{"Amount": "500.00", "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "99"}}}],
	}
	_doctype, values = map_qbo_to_erpnext("Purchase", payload, settings)

	# The expense leg dropped out, so the entry is lopsided and routes to review.
	assert [row["account"] for row in values["accounts"]] == ["30 - Amex - SF"]
	assert any("unbalanced" in issue for issue in validate_mapped_values("Purchase", "Journal Entry", values, payload=payload))


def test_native_journal_entry_lines_also_redirect_off_group_accounts(monkeypatch):
	"""``_journal_accounts`` resolves through the redirect despite bypassing ``_resolve_account``.

	It calls ``_linked_name`` directly (it has no Company-default fallback), which is
	exactly how this path gets missed -- and it produced 52 of the 1,726 draft entries
	that needed the group-account remap.
	"""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		_chart_resolver(qbo_accounts={"77": "60300 - Research & Development - SF", "88": "7010 - Office Supplies - SF"}),
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_doctype, values = map_qbo_to_erpnext(
		"JournalEntry",
		{
			"Id": "50",
			"TxnDate": "2026-06-06",
			"Line": [
				{"Amount": 120.0, "JournalEntryLineDetail": {"AccountRef": {"value": "77"}, "PostingType": "Debit"}},
				{"Amount": 120.0, "JournalEntryLineDetail": {"AccountRef": {"value": "88"}, "PostingType": "Credit"}},
			],
		},
		types.SimpleNamespace(company="SF"),
	)

	assert [row["account"] for row in values["accounts"]] == [
		"60301 - R&D - General - SF",
		"7010 - Office Supplies - SF",
	]


def test_item_default_accounts_redirect_off_group_accounts(monkeypatch):
	"""An Item Default pointing at a group account redirects too, on both sides."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		_chart_resolver(
			item_default={
				"expense_account": "60300 - Research & Development - SF",
				"income_account": "61000 - General & Administrative - SF",
			}
		),
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import (
		_item_expense_account,
		_item_income_account,
	)

	assert _item_expense_account("PUMP-1", "SF") == "60301 - R&D - General - SF"
	# A group with no "- General" child yields None rather than sliding down to the
	# Company default, which would book the amount somewhere nobody chose.
	assert _item_income_account("PUMP-1", "SF") is None


def test_purchase_items_do_not_bill_zero_quantity_lines(monkeypatch):
	"""The buy side shares the sell side's qty/rate fix -- one function, no drift.

	Latent rather than active: no cached Bill or PurchaseOrder payload carries a
	``Qty: 0`` item line today. The point is that it cannot silently start to.
	"""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", _item_resolver("PUMP-1"))
	from erpnext_enhancements.quickbooks_online.core.mapping import _purchase_items

	def buy_line(amount, **detail):
		return {"Amount": amount, "ItemBasedExpenseLineDetail": dict({"ItemRef": {"value": "12"}}, **detail)}

	items = _purchase_items(
		{
			"Line": [
				buy_line(262.5, UnitPrice=262.5),  # Qty absent
				buy_line(555.0, Qty=3, UnitPrice=185),  # both given
				buy_line(500.0, Qty=2),  # quantity, no unit price
				buy_line(0, Qty=0, UnitPrice=8000),  # worth nothing -> dropped
			]
		}
	)

	assert [round(item["qty"] * item["rate"], 2) for item in items] == [262.5, 555.0, 500.0]
	assert items[2]["rate"] == 250  # not 500: the second-order double-multiply


def test_purchase_order_line_with_quantity_but_no_unit_price(monkeypatch):
	"""The one real PurchaseOrder line shape that hit the second-order bug.

	Quantity 1 and no ``UnitPrice`` -- harmless only because the quantity is 1. At any
	other quantity the old code billed ``Amount`` per unit.
	"""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		_chart_resolver(company={"default_currency": "USD"}, qbo_accounts={}),
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"PurchaseOrder",
		{
			"Id": "31",
			"TxnDate": "2026-06-06",
			"DueDate": "2026-06-20",
			"Line": [{"Amount": 1450.0, "ItemBasedExpenseLineDetail": {"ItemRef": {"value": "12"}, "Qty": 1}}],
		},
		types.SimpleNamespace(company="SF"),
	)

	assert doctype == "Purchase Order"
	assert (values["items"][0]["qty"], values["items"][0]["rate"]) == (1, 1450.0)
	assert values["items"][0]["schedule_date"] == "2026-06-20"


def test_group_account_remap_constants_reconcile_to_the_measured_population():
	"""The WI-068 remap table still totals what was measured against production.

	The script checks this at run time, but only on the site it is run against. Doing
	it here means a typo in a line count or an amount fails CI instead of surfacing as
	a confusing "population matches expected: False" halfway through a migration.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import group_account_remap as remap

	lines = sum(row[3] for row in remap.NEW_LEDGER_CHILDREN) + sum(row[2] for row in remap.MERGE_INTO_EXISTING)
	gross = sum(row[4] for row in remap.NEW_LEDGER_CHILDREN) + sum(row[3] for row in remap.MERGE_INTO_EXISTING)

	# Measured against production 2026-08-04: 22 group accounts, 1,813 draft pre-2026
	# lines, $724,230.37 gross, in 1,726 Journal Entries. 23 routes rather than 22
	# because 52000 was added for the 2026 window and carries no pre-2026 lines, so it
	# contributes 0 to both totals below -- which is the assertion that it is routing,
	# not a silent change to what the original run covered.
	assert len(remap.NEW_LEDGER_CHILDREN) + len(remap.MERGE_INTO_EXISTING) == 23
	assert lines == 1813
	assert round(gross, 2) == 724230.37
	# The table and WINDOWS are two representations of the same measurement; nothing but
	# this stops one being edited without the other.
	assert remap.WINDOWS["pre-2026"]["expected_lines"] == lines
	assert remap.WINDOWS["pre-2026"]["expected_gross"] == round(gross, 2)

	child_numbers = [row[1] for row in remap.NEW_LEDGER_CHILDREN]
	parent_numbers = [row[0] for row in remap.NEW_LEDGER_CHILDREN] + [row[0] for row in remap.MERGE_INTO_EXISTING]
	assert len(set(child_numbers)) == len(child_numbers)  # no two parents claim one child
	assert not set(child_numbers) & set(parent_numbers)  # and no child collides with a parent
	# Every child name ends in the suffix _ledger_for_posting matches on, or the forward
	# fix cannot find the account this script just created.
	assert all(row[2].endswith(remap.GENERAL_SUFFIX) for row in remap.NEW_LEDGER_CHILDREN)


def test_group_account_remap_windows_are_half_open_and_do_not_overlap():
	"""``pre-2026`` and ``2026`` tile the timeline exactly once, with no gap and no seam.

	They share a boundary date, so an off-by-one in either direction is invisible until
	a migration either double-moves a line or silently skips one: an inclusive upper
	bound would put 2026-01-01 in both windows, and a gap would leave it in neither.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import group_account_remap as remap

	assert remap.WINDOWS["pre-2026"]["to_date"] == remap.WINDOWS["2026"]["from_date"]

	pre = remap._window_sql(remap._resolve_window("pre-2026", None, None))
	current = remap._window_sql(remap._resolve_window("2026", None, None))

	# Half-open on the right, closed on the left -- the boundary date belongs to exactly
	# one window, and the pre-2026 clause still reduces to what the original run ran.
	assert pre == ("je.posting_date < %(to_date)s", {"to_date": "2026-01-01"})
	assert current == ("je.posting_date >= %(from_date)s", {"from_date": "2026-01-01"})


def test_group_account_remap_ad_hoc_window_never_asserts_a_population():
	"""An unmeasured date range reports what it finds instead of claiming a verdict.

	A range nobody surveyed has no expected population, so inheriting one from a
	different range would be worse than having no check: it would fail loudly on
	correct data, and the operator would learn to ignore the check that matters.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import group_account_remap as remap

	window = remap._resolve_window("pre-2026", "2026-03-01", "2026-04-01")

	assert window["measured"] is False
	assert window["expected_lines"] is None and window["expected_gross"] is None
	# The explicit pair wins over the named window rather than being silently ignored.
	assert (window["from_date"], window["to_date"]) == ("2026-03-01", "2026-04-01")
	assert remap._window_sql(window) == (
		"je.posting_date >= %(from_date)s AND je.posting_date < %(to_date)s",
		{"from_date": "2026-03-01", "to_date": "2026-04-01"},
	)

	assert remap._resolve_window("2026", None, None)["measured"] is True


def test_group_account_remap_routes_every_account_the_2026_window_measured():
	"""Every group account carrying 2026 lines has somewhere to go.

	Measured on production 2026-08-07: these 15 accounts hold all 315 blocked lines.
	Widening the date range without this list is exactly the bug being fixed -- 52000
	was in neither routing table, so 3 of the 315 would have stayed unpostable and the
	cutover would have failed on them after the remap reported success.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import group_account_remap as remap

	measured_2026 = {
		"20000", "50000", "51000", "52000", "53000", "53100", "60000", "60100",
		"60210", "60300", "60400", "60420", "60800", "60810", "61500",
	}
	routed = {row[0] for row in remap.NEW_LEDGER_CHILDREN} | {row[0] for row in remap.MERGE_INTO_EXISTING}

	assert measured_2026 <= routed
	assert len(measured_2026) == remap.WINDOWS["2026"]["expected_accounts"]


def test_group_account_remap_suffix_matches_the_mapper_redirect():
	"""The script and the mapper agree on what designates a "- General" ledger.

	They are separate constants in separate modules by design (the mapper must not
	import a one-off migration script), so nothing but this test stops them diverging
	-- and if they diverge, every QBO transaction on an affected account silently
	starts parking for manual review.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import group_account_remap as remap
	from erpnext_enhancements.quickbooks_online.core import mapping

	assert remap.GENERAL_SUFFIX == mapping.GENERAL_LEDGER_SUFFIX


def test_sales_receipt_maps_to_sales_invoice(monkeypatch):
	"""A QBO SalesReceipt is imported as a Sales Invoice with a receipt remark."""
	frappe = install_frappe_stub()

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return {"default_receivable_account": "Debtors - SF", "default_currency": "USD"}.get(fieldname)
		if doctype == "QuickBooks Sync Mapping" and filters.get("qbo_entity_type") == "Customer":
			return "Acme Supply"
		return None

	monkeypatch.setattr(frappe.db, "get_value", get_value)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"SalesReceipt",
		{"Id": "5", "TxnDate": "2026-06-06", "CustomerRef": {"value": "1"}},
		types.SimpleNamespace(company="SF"),
	)

	assert doctype == "Sales Invoice"
	assert "Sales Receipt" in values["remarks"]
	assert values["debit_to"] == "Debtors - SF"


def test_journal_imbalance_routes_to_manual_review():
	"""A Journal Entry whose lines don't balance reports an 'unbalanced' issue."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import validate_mapped_values

	balanced = {
		"company": "SF",
		"accounts": [
			{"account": "A", "debit_in_account_currency": 100, "credit_in_account_currency": 0},
			{"account": "B", "debit_in_account_currency": 0, "credit_in_account_currency": 100},
		],
	}
	assert validate_mapped_values("Transfer", "Journal Entry", balanced) == []

	unbalanced = {
		"company": "SF",
		"accounts": [{"account": "A", "debit_in_account_currency": 100, "credit_in_account_currency": 0}],
	}
	assert any("unbalanced" in issue for issue in validate_mapped_values("Transfer", "Journal Entry", unbalanced))


def test_unresolved_cash_transaction_flags_missing_accounts():
	"""A Purchase whose account refs don't resolve yields empty lines -> manual review."""
	install_frappe_stub()  # default stub resolves no account mappings
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	_, values = map_qbo_to_erpnext(
		"Purchase",
		{
			"Id": "7",
			"TxnDate": "2026-06-06",
			"TotalAmt": "100",
			"AccountRef": {"value": "30"},
			"Line": [{"Amount": "100", "AccountBasedExpenseLineDetail": {"AccountRef": {"value": "61"}}}],
		},
		types.SimpleNamespace(company="SF"),
	)

	assert values["accounts"] == []
	assert validate_mapped_values("Purchase", "Journal Entry", values, include_doc_required=False) == [
		"Missing required field: accounts"
	]


def test_display_name_prefers_fully_qualified_name():
	"""_display_name uses FullyQualifiedName so sub-customers keep parent context."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _display_name

	assert _display_name({"FullyQualifiedName": "Landmark Aquatics:Job 1", "DisplayName": "Job 1"}) == "Landmark Aquatics:Job 1"
	assert _display_name({"DisplayName": "Top Co"}) == "Top Co"


# ---------------------------------------------------------------------------
# New master entities: Payment Terms, Payment Methods and tracking Classes.
# ---------------------------------------------------------------------------


def test_term_maps_to_payment_terms_template_with_full_portion():
	"""A QBO STANDARD Term becomes a Payment Terms Template with one 100% term row."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Term", {"Id": "3", "Name": "Net 30", "Type": "STANDARD", "DueDays": 30}, types.SimpleNamespace(company="SF")
	)

	assert doctype == "Payment Terms Template"
	assert values["template_name"] == "Net 30"
	assert len(values["terms"]) == 1
	term = values["terms"][0]
	assert term["invoice_portion"] == 100
	assert term["credit_days"] == 30
	assert term["due_date_based_on"] == "Day(s) after invoice date"


def test_date_driven_term_uses_end_of_month_basis():
	"""A QBO DATE_DRIVEN Term maps to the end-of-invoice-month due basis."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, values = map_qbo_to_erpnext(
		"Term",
		{"Id": "4", "Name": "Due 15th", "Type": "DATE_DRIVEN", "DayOfMonthDue": 15},
		types.SimpleNamespace(company="SF"),
	)

	assert values["terms"][0]["due_date_based_on"] == "Day(s) after the end of the invoice month"
	assert values["terms"][0]["credit_days"] == 15


def test_payment_method_maps_type_by_credit_card_flag():
	"""QBO CREDIT_CARD methods become Bank-type Modes of Payment; others default to Cash."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	card_doctype, card = map_qbo_to_erpnext(
		"PaymentMethod", {"Id": "1", "Name": "Visa", "Type": "CREDIT_CARD"}, types.SimpleNamespace(company="SF")
	)
	_, cash = map_qbo_to_erpnext(
		"PaymentMethod", {"Id": "2", "Name": "Check", "Type": "NON_CREDIT_CARD"}, types.SimpleNamespace(company="SF")
	)

	assert card_doctype == "Mode of Payment"
	assert card["mode_of_payment"] == "Visa"
	assert card["type"] == "Bank"
	assert card["enabled"] == 1
	assert cash["type"] == "Cash"


def test_class_maps_to_cost_center_under_root(monkeypatch):
	"""A leaf QBO Class maps to a ledger Cost Center under the company root."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, filters=None, fieldname=None, **kwargs: "Main - SF" if doctype == "Cost Center" else None,
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Class", {"Id": "5", "Name": "Residential"}, types.SimpleNamespace(company="SF")
	)

	assert doctype == "Cost Center"
	assert values["cost_center_name"] == "Residential"
	assert values["company"] == "SF"
	assert values["parent_cost_center"] == "Main - SF"
	assert values["is_group"] == 0


def test_class_with_children_is_group(monkeypatch):
	"""A QBO Class flagged as a parent maps to a group Cost Center."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, filters=None, fieldname=None, **kwargs: "Main - SF" if doctype == "Cost Center" else None,
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, values = map_qbo_to_erpnext(
		"Class", {"Id": "6", "Name": "Divisions", "_qbo_has_children": True}, types.SimpleNamespace(company="SF")
	)

	assert values["is_group"] == 1


def test_customer_links_payment_terms_when_term_is_mapped(monkeypatch):
	"""A QBO Customer's SalesTermRef links to the already-imported Payment Terms Template."""
	frappe = install_frappe_stub()

	def get_value(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "QuickBooks Sync Mapping" and (filters or {}).get("qbo_entity_type") == "Term":
			return "Net 30"
		if doctype == "Customer Group" and filters == {"is_group": 0}:
			return "Commercial"
		if doctype == "Territory" and filters == {"is_group": 0}:
			return "United States"
		return None

	monkeypatch.setattr(frappe.db, "get_value", get_value)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, values = map_qbo_to_erpnext(
		"Customer",
		{"Id": "7", "DisplayName": "Acme", "CompanyName": "Acme", "SalesTermRef": {"value": "3"}},
		types.SimpleNamespace(company="SF"),
	)

	assert values["payment_terms"] == "Net 30"


def test_ordered_entities_places_new_masters_before_transactions():
	"""ordered_entities sorts Term/PaymentMethod/Account/Class ahead of transactions."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.sync import ordered_entities

	assert ordered_entities(["Invoice", "Class", "Term", "Account", "PaymentMethod"]) == [
		"Term",
		"PaymentMethod",
		"Account",
		"Class",
		"Invoice",
	]


def test_class_payload_query_marks_parents():
	"""query_entity_payloads flags parent Classes with _qbo_has_children like Accounts."""
	install_frappe_stub()
	import pytest

	from erpnext_enhancements.quickbooks_online.core import sync

	original = sync.query_all
	sync.query_all = lambda entity_type, settings=None: iter(
		[{"Id": "10", "Name": "Divisions"}, {"Id": "11", "Name": "East", "ParentRef": {"value": "10"}}]
	)
	try:
		payloads = list(sync.query_entity_payloads("Class"))
	finally:
		sync.query_all = original

	assert payloads[0]["_qbo_has_children"] is True
	assert payloads[1]["_qbo_has_children"] is False


# ---------------------------------------------------------------------------
# Reconciliation: Trial Balance parsing and transaction-total extraction.
# ---------------------------------------------------------------------------


def test_parse_trial_balance_reads_signed_balances_and_recurses():
	"""_parse_trial_balance yields signed (debit-credit) balances and walks sections."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.reconcile import _parse_trial_balance

	response = {
		"Rows": {
			"Row": [
				{"ColData": [{"value": "Checking", "id": "35"}, {"value": "1000.00"}, {"value": ""}]},
				{
					# A section: its child rows are data, its own header is ignored.
					"Header": {"ColData": [{"value": "Liabilities"}]},
					"Rows": {
						"Row": [{"ColData": [{"value": "Loan", "id": "40"}, {"value": ""}, {"value": "500.00"}]}]
					},
				},
				{"ColData": [{"value": "Total"}, {"value": "1000.00"}, {"value": "500.00"}]},
			]
		}
	}

	balances = _parse_trial_balance(response)

	assert balances["35"]["qb_balance"] == 1000.0
	assert balances["40"]["qb_balance"] == -500.0
	# The "Total" summary row has no account id and is excluded.
	assert len(balances) == 2


def test_parse_trial_balance_resolves_amount_columns_from_header():
	"""v2: Debit/Credit are keyed off the Columns header, not a fixed ColData index.

	The modernized Reports service tells integrators "do not rely on index
	positions". Here the header lists Credit *before* Debit; positional parsing
	would invert every balance, header-driven parsing gets the sign right.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.reconcile import _parse_trial_balance

	response = {
		"Columns": {
			"Column": [
				{"ColTitle": "", "ColType": "Account"},
				{"ColTitle": "Credit", "ColType": "Money"},
				{"ColTitle": "Debit", "ColType": "Money"},
			]
		},
		"Rows": {
			"Row": [
				# Debit lives in column 2 here; Checking holds 1000 there => +1000.
				{"ColData": [{"value": "Checking", "id": "35"}, {"value": ""}, {"value": "1000.00"}]},
				# Loan holds 500 in the Credit column (index 1) => -500.
				{"ColData": [{"value": "Loan", "id": "40"}, {"value": "500.00"}, {"value": ""}]},
			]
		},
	}

	balances = _parse_trial_balance(response)
	assert balances["35"]["qb_balance"] == 1000.0
	assert balances["40"]["qb_balance"] == -500.0


def test_parse_trial_balance_matches_allcaps_titles():
	"""v1 sometimes returned ColTitle in ALL CAPS; column matching is case-insensitive."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.reconcile import _parse_trial_balance

	response = {
		"Columns": {"Column": [{"ColTitle": ""}, {"ColTitle": "DEBIT"}, {"ColTitle": "CREDIT"}]},
		"Rows": {"Row": [{"ColData": [{"value": "Checking", "id": "35"}, {"value": "250.00"}, {"value": ""}]}]},
	}

	assert _parse_trial_balance(response)["35"]["qb_balance"] == 250.0


def test_extract_total_prefers_header_amount_then_sums_journal_debits():
	"""_extract_total reads TotalAmt/Amount, falling back to summed JE debit lines."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.reconcile import _extract_total

	assert _extract_total("Invoice", {"TotalAmt": "500.00"}) == 500.0
	assert _extract_total("Transfer", {"Amount": "300"}) == 300.0
	assert (
		_extract_total(
			"JournalEntry",
			{
				"Line": [
					{"Amount": "100", "JournalEntryLineDetail": {"PostingType": "Debit"}},
					{"Amount": "100", "JournalEntryLineDetail": {"PostingType": "Credit"}},
				]
			},
		)
		== 100.0
	)


# ---------------------------------------------------------------------------
# Opening balances: pure line builders and the balancing plug.
# ---------------------------------------------------------------------------


def test_opening_account_line_places_signed_balance():
	"""_opening_account_line debits a positive balance and credits a negative one."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.opening_balances import _opening_account_line

	debit = _opening_account_line("Checking - SF", 1000)
	assert debit["debit_in_account_currency"] == 1000
	assert debit["credit_in_account_currency"] == 0

	credit = _opening_account_line("Loan - SF", -500)
	assert credit["credit_in_account_currency"] == 500
	assert credit["debit_in_account_currency"] == 0


def test_party_opening_line_honours_side_and_sign():
	"""_party_opening_line debits customer balances, credits vendor balances, flips negatives."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.opening_balances import _party_opening_line

	customer = _party_opening_line("Debtors - SF", "Customer", "Acme", 300, "debit")
	assert customer["party"] == "Acme"
	assert customer["party_type"] == "Customer"
	assert customer["debit_in_account_currency"] == 300

	vendor = _party_opening_line("Creditors - SF", "Supplier", "ICS", 200, "credit")
	assert vendor["credit_in_account_currency"] == 200

	credit_balance = _party_opening_line("Debtors - SF", "Customer", "Acme", -50, "debit")
	assert credit_balance["credit_in_account_currency"] == 50
	assert credit_balance["debit_in_account_currency"] == 0


def test_plug_line_returns_none_when_balanced_and_offsets_when_not(monkeypatch):
	"""_plug_line is a no-op for balanced rows and otherwise squares off via Temporary Opening."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.opening_balances import _plug_line

	balanced = [
		{"debit_in_account_currency": 100, "credit_in_account_currency": 0},
		{"debit_in_account_currency": 0, "credit_in_account_currency": 100},
	]
	assert _plug_line(balanced, "SF") is None

	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, filters=None, fieldname=None, **kwargs: "Temporary Opening - SF"
		if doctype == "Account"
		else None,
	)
	unbalanced = [{"debit_in_account_currency": 100, "credit_in_account_currency": 0}]
	plug = _plug_line(unbalanced, "SF")
	assert plug["account"] == "Temporary Opening - SF"
	# More debits than credits => the plug must be a credit.
	assert plug["credit_in_account_currency"] == 100
	assert plug["debit_in_account_currency"] == 0


def test_client_report_builds_reports_endpoint_path(monkeypatch):
	"""client.report targets /reports/{name} and passes report params through."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_module

	client = client_module.QuickBooksClient(types.SimpleNamespace(realm_id="42", environment="Production"))
	captured = {}
	monkeypatch.setattr(
		client, "request", lambda method, path, **kwargs: captured.update(method=method, path=path, **kwargs) or {}
	)

	client.report("TrialBalance", {"end_date": "2026-06-16"})

	assert captured["method"] == "GET"
	assert captured["path"].endswith("/reports/TrialBalance")
	assert captured["params"]["end_date"] == "2026-06-16"
	# Off by default: the v2 preview flag is absent unless explicitly requested.
	assert "testing_migration" not in captured["params"]


def test_client_report_adds_testing_migration_flag_when_requested(monkeypatch):
	"""client.report appends Intuit's testing_migration flag to preview the v2 service."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_module

	client = client_module.QuickBooksClient(types.SimpleNamespace(realm_id="42", environment="Production"))
	captured = {}
	monkeypatch.setattr(
		client, "request", lambda method, path, **kwargs: captured.update(method=method, path=path, **kwargs) or {}
	)

	client.report("TrialBalance", {"end_date": "2026-06-16"}, testing_migration=True)

	assert captured["params"]["testing_migration"] == "true"
	assert captured["params"]["end_date"] == "2026-06-16"


def test_api_exposes_reconcile_and_opening_endpoints():
	"""The whitelisted RPC layer surfaces the new reconcile / opening-balance endpoints."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import api

	for endpoint in ("compare_account_balances", "reconcile_transactions", "sync_opening_balances"):
		assert callable(getattr(api, endpoint))


# ---------------------------------------------------------------------------
# Disconnect / revoke: the OAuth2 grant teardown at Intuit.
# ---------------------------------------------------------------------------


def test_revoke_tokens_posts_refresh_token_to_revoke_endpoint(monkeypatch):
	"""client.revoke_tokens POSTs the refresh token to Intuit's revoke endpoint with basic auth."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_module
	from erpnext_enhancements.quickbooks_online.core.constants import REVOKE_URL

	settings = types.SimpleNamespace(realm_id="42", environment="Production", client_id="abc")
	# get_secret reads via the doc's get_password; stub it to supply the secrets.
	settings.get_password = lambda fieldname, *args, **kwargs: {
		"refresh_token": "rt-1",
		"client_secret": "cs",
	}.get(fieldname)

	captured = {}

	class FakeResponse:
		status_code = 200
		text = ""

	def fake_post(url, headers=None, json=None, timeout=None, **kwargs):
		captured.update(url=url, headers=headers, json=json)
		return FakeResponse()

	monkeypatch.setattr(client_module.requests, "post", fake_post, raising=False)

	client = client_module.QuickBooksClient(settings)
	assert client.revoke_tokens() is True
	assert captured["url"] == REVOKE_URL
	assert captured["json"] == {"token": "rt-1"}
	assert captured["headers"]["Authorization"].startswith("Basic ")


def test_revoke_tokens_returns_false_when_no_token_stored():
	"""client.revoke_tokens is a no-op (False) when there is nothing to revoke."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_module

	settings = types.SimpleNamespace(realm_id=None, environment="Sandbox", client_id="abc")
	settings.get_password = lambda fieldname, *args, **kwargs: None

	assert client_module.QuickBooksClient(settings).revoke_tokens() is False


def test_api_exposes_disconnect_endpoints():
	"""The whitelisted RPC layer surfaces the disconnect + Intuit Disconnect-URL endpoints."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import api

	for endpoint in ("disconnect", "disconnect_callback"):
		assert callable(getattr(api, endpoint))


# ---------------------------------------------------------------------------
# Access control: privileged RPCs are gated on the QBO operator roles, and
# API error bodies are bounded so QuickBooks data can't spill into logs.
# ---------------------------------------------------------------------------


def test_require_qbo_operator_enforces_operator_roles():
	"""_require_qbo_operator gates the privileged RPCs on the QBO operator roles."""
	frappe = install_frappe_stub()
	captured = {}
	frappe.only_for = lambda roles, *args, **kwargs: captured.update(roles=roles)
	from erpnext_enhancements.quickbooks_online.core import api

	api._require_qbo_operator()

	assert "System Manager" in captured["roles"]
	assert "Accounts Manager" in captured["roles"]


def test_import_all_enqueues_after_role_guard(monkeypatch):
	"""import_all runs the operator guard, then enqueues the import on the long queue.

	The import is backgrounded (it pages the QBO API for minutes; running it inline
	returned a 504), so the guard must still fire *before* the work is dispatched.
	"""
	frappe = install_frappe_stub()
	order = []
	captured = {}
	frappe.only_for = lambda roles, *args, **kwargs: order.append("guard")
	frappe.db.exists = lambda *args, **kwargs: False  # no import already running
	frappe.enqueue = lambda method, **kwargs: order.append("enqueue") or captured.update(
		method=method, kwargs=kwargs
	)
	from erpnext_enhancements.quickbooks_online.core import api

	result = api.import_all()

	assert order == ["guard", "enqueue"]
	assert result == {"status": "queued"}
	assert captured["method"] is api.run_import_all
	assert captured["kwargs"].get("queue") == "long"


def test_import_all_skips_when_already_running():
	"""import_all no-ops (no enqueue) when an Import All is already running."""
	frappe = install_frappe_stub()
	frappe.only_for = lambda roles, *args, **kwargs: None
	frappe.db.exists = lambda *args, **kwargs: True  # an import is in progress
	enqueued = []
	frappe.enqueue = lambda method, **kwargs: enqueued.append(method)
	from erpnext_enhancements.quickbooks_online.core import api

	result = api.import_all()

	assert result == {"status": "already_running"}
	assert enqueued == []


def test_preview_resync_enqueues_with_pending_log(monkeypatch):
	"""preview_resync guards, pre-creates a log, and enqueues the dry run on the long queue."""
	frappe = install_frappe_stub()
	order = []
	captured = {}
	frappe.only_for = lambda roles, *args, **kwargs: order.append("guard")
	frappe.enqueue = lambda method, **kwargs: order.append("enqueue") or captured.update(
		method=method, kwargs=kwargs
	)
	from erpnext_enhancements.quickbooks_online.core import api

	monkeypatch.setattr(
		api, "create_pending_log", lambda sync_type: order.append("log:" + sync_type) or "QBO-PREVIEW-1"
	)
	result = api.preview_resync(entity_types="Account,Customer")

	assert order == ["guard", "log:Preview Resync", "enqueue"]
	assert result == {"preview_id": "QBO-PREVIEW-1", "status": "queued"}
	assert captured["method"] is api.run_preview_resync
	assert captured["kwargs"].get("queue") == "long"
	assert captured["kwargs"].get("log_name") == "QBO-PREVIEW-1"
	assert captured["kwargs"].get("entity_types") == ["Account", "Customer"]


def test_get_sync_log_summary_maps_counters(monkeypatch):
	"""get_sync_log_summary returns the log status and its per-action counters."""
	frappe = install_frappe_stub()
	frappe.only_for = lambda *args, **kwargs: None
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name, fields, as_dict=False: types.SimpleNamespace(
			status="Completed",
			created_count=3,
			updated_count=1,
			linked_count=0,
			deleted_count=0,
			conflict_count=2,
			manual_review_count=0,
			ignored_count=4,
			failed_count=0,
			error_message=None,
		),
	)
	from erpnext_enhancements.quickbooks_online.core import api

	out = api.get_sync_log_summary("QBO-PREVIEW-1")

	assert out["status"] == "Completed"
	assert out["summary"]["created"] == 3
	assert out["summary"]["conflicts"] == 2
	assert out["summary"]["ignored"] == 4


def test_error_snippet_bounds_response_bodies():
	"""_error_snippet truncates long API error bodies and tolerates an empty body."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.client import _error_snippet

	assert _error_snippet("short") == "short"
	assert _error_snippet(None) == ""
	long_body = "x" * 600
	snippet = _error_snippet(long_body)
	assert snippet.endswith("(truncated)")
	assert len(snippet) < len(long_body)


# ---------------------------------------------------------------------------
# Sync resilience: retry de-amplification, the CDC/import concurrency guard,
# stale-run reaping, create-path manual review and the bounded 401 refresh.
# These cover the runaway-CDC failure cluster (hundreds of failed runs that
# re-spawned each other and raced on the same mapping rows).
# ---------------------------------------------------------------------------


def test_retry_failed_reruns_each_operation_once(monkeypatch):
	"""retry_failed re-runs the global CDC/import once per call, not once per failed log.

	The old behaviour re-ran the global operation once per failed log; since every
	failed run creates another failed log, N failures spawned N re-runs that spawned
	more -- the observed storm of hundreds of failed CDC runs.
	"""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	failed = [types.SimpleNamespace(name=f"CDC-{i}", sync_type="CDC", retry_count=0) for i in range(5)]
	failed.append(types.SimpleNamespace(name="IMP-1", sync_type="Import All", retry_count=0))

	def get_all(doctype, filters=None, fields=None, **kwargs):
		# The failed-log query returns the batch; the reaper's Running/Queued query is empty.
		if filters and filters.get("status") == "Failed":
			return failed
		return []

	saved = []

	def get_doc(doctype, name):
		doc = types.SimpleNamespace(
			name=name, retry_count=0, sync_type="Import All" if name.startswith("IMP") else "CDC"
		)
		doc.save = lambda **kwargs: saved.append(name)
		return doc

	monkeypatch.setattr(frappe, "get_all", get_all)
	monkeypatch.setattr(frappe, "get_doc", get_doc, raising=False)
	monkeypatch.setattr(sync, "get_settings", lambda: types.SimpleNamespace(retry_limit=3))
	calls = {"cdc": 0, "import": 0}
	monkeypatch.setattr(sync, "run_cdc", lambda: calls.__setitem__("cdc", calls["cdc"] + 1))
	monkeypatch.setattr(sync, "import_all", lambda: calls.__setitem__("import", calls["import"] + 1))

	sync.retry_failed()

	# One CDC + one import, regardless of the five failed CDC logs (no amplification)...
	assert calls == {"cdc": 1, "import": 1}
	# ...while every eligible log still records its retry attempt.
	assert len(saved) == 6


def test_retry_failed_skips_logs_past_retry_limit(monkeypatch):
	"""A failed log already at retry_limit is skipped, triggering no re-run."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	failed = [types.SimpleNamespace(name="CDC-1", sync_type="CDC", retry_count=3)]
	monkeypatch.setattr(
		frappe,
		"get_all",
		lambda doctype, filters=None, **kwargs: failed if (filters or {}).get("status") == "Failed" else [],
	)
	monkeypatch.setattr(sync, "get_settings", lambda: types.SimpleNamespace(retry_limit=3))
	calls = {"cdc": 0}
	monkeypatch.setattr(sync, "run_cdc", lambda: calls.__setitem__("cdc", calls["cdc"] + 1))
	monkeypatch.setattr(sync, "import_all", lambda: None)

	sync.retry_failed()

	assert calls["cdc"] == 0


def test_run_in_progress_ignores_stale_runs(monkeypatch):
	"""run_in_progress sees a fresh run but treats an orphaned (stale) one as not running."""
	from datetime import datetime, timedelta

	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	now = datetime(2026, 6, 21, 12, 0, 0)
	monkeypatch.setattr(sync, "now_datetime", lambda: now)
	monkeypatch.setattr(sync, "get_datetime", lambda value: value)

	stale = now - timedelta(hours=3)  # past the 1h CDC staleness window
	monkeypatch.setattr(frappe, "get_all", lambda *a, **k: [types.SimpleNamespace(started_at=stale, modified=stale)])
	assert sync.run_in_progress("CDC") is False

	fresh = now - timedelta(minutes=5)
	monkeypatch.setattr(frappe, "get_all", lambda *a, **k: [types.SimpleNamespace(started_at=fresh, modified=fresh)])
	assert sync.run_in_progress("CDC") is True


def test_reap_stale_runs_fails_only_orphaned_logs(monkeypatch):
	"""reap_stale_runs marks a long-orphaned Running log Failed but leaves a live one."""
	from datetime import datetime, timedelta

	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	now = datetime(2026, 6, 21, 12, 0, 0)
	monkeypatch.setattr(sync, "now_datetime", lambda: now)
	monkeypatch.setattr(sync, "get_datetime", lambda value: value)

	rows = [
		types.SimpleNamespace(
			name="IMP-stuck",
			sync_type="Import All",
			started_at=now - timedelta(hours=12),  # past the 11h import window
			modified=now - timedelta(hours=12),
		),
		types.SimpleNamespace(
			name="CDC-live",
			sync_type="CDC",
			started_at=now - timedelta(minutes=10),  # inside the 1h CDC window
			modified=now - timedelta(minutes=10),
		),
	]
	monkeypatch.setattr(frappe, "get_all", lambda *a, **k: rows)
	saved = {}

	def get_doc(doctype, name):
		doc = types.SimpleNamespace(name=name, status="Running", error_message=None, finished_at=None)
		doc.save = lambda **kwargs: saved.__setitem__(doc.name, doc.status)
		return doc

	monkeypatch.setattr(frappe, "get_doc", get_doc, raising=False)

	reaped = sync.reap_stale_runs()

	assert reaped == 1
	assert saved == {"IMP-stuck": "Failed"}


def test_safe_upsert_retries_once_on_timestamp_mismatch(monkeypatch):
	"""A transient TimestampMismatchError is retried once, then succeeds (not parked Failed)."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	calls = {"n": 0}

	def flaky(entity_type, payload, settings, **kwargs):
		calls["n"] += 1
		if calls["n"] == 1:
			raise frappe.exceptions.TimestampMismatchError("modified after open")
		return {"action": "created"}

	monkeypatch.setattr(sync, "upsert_entity", flaky)

	result = sync.safe_upsert("Customer", {"Id": "1"}, types.SimpleNamespace())

	assert result == {"action": "created"}
	assert calls["n"] == 2


def test_safe_upsert_marks_failed_after_persistent_mismatch(monkeypatch):
	"""A persistent concurrency error falls through to a logged failed result."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	monkeypatch.setattr(frappe, "log_error", lambda *a, **k: None, raising=False)
	monkeypatch.setattr(
		sync,
		"upsert_entity",
		lambda *a, **k: (_ for _ in ()).throw(frappe.exceptions.TimestampMismatchError("still modified")),
	)

	result = sync.safe_upsert("Customer", {"Id": "7"}, types.SimpleNamespace())

	assert result["action"] == "failed"
	assert result["qbo_id"] == "7"


def test_insert_or_manual_review_parks_validation_error(monkeypatch):
	"""A create-time ValidationError (e.g. date outside any Fiscal Year) -> manual review."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	parked = {}
	monkeypatch.setattr(
		mapping,
		"save_manual_review_mapping",
		lambda et, qid, payload, dt, issues: parked.update(entity=et, qbo_id=qid, issues=issues),
	)

	class Doc:
		name = "QTN-0001"

		def insert(self, **kwargs):
			raise frappe.exceptions.ValidationError(
				"Date 01-28-2022 is not in any active Fiscal Year for Sapphire Fountains"
			)

	result = mapping._insert_or_manual_review("Estimate", "8932", {"Id": "8932"}, "Quotation", Doc())

	assert result["action"] == "manual_review"
	assert "Fiscal Year" in result["reason"]
	assert parked["qbo_id"] == "8932"


def test_insert_or_manual_review_reraises_timestamp_mismatch():
	"""TimestampMismatchError on insert is re-raised (left for the retry path, not parked)."""
	import pytest

	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	class Doc:
		name = "QTN-1"

		def insert(self, **kwargs):
			raise frappe.exceptions.TimestampMismatchError("modified after open")

	with pytest.raises(frappe.exceptions.TimestampMismatchError):
		mapping._insert_or_manual_review("Estimate", "1", {"Id": "1"}, "Quotation", Doc())


def test_insert_or_manual_review_returns_none_on_success():
	"""A clean insert returns None so the caller proceeds to record the mapping."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	class Doc:
		name = "QTN-2"

		def insert(self, **kwargs):
			return None

	assert mapping._insert_or_manual_review("Estimate", "2", {"Id": "2"}, "Quotation", Doc()) is None


def test_journal_accounts_skips_line_with_unknown_posting_type(monkeypatch):
	"""A JE line with an amount but no PostingType is dropped (it would be a 0/0 row)."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(frappe.db, "get_value", lambda *a, **k: "Some Account - SF")
	from erpnext_enhancements.quickbooks_online.core.mapping import _journal_accounts

	rows = _journal_accounts(
		{
			"Line": [
				{"Amount": 100, "JournalEntryLineDetail": {"PostingType": "Debit", "AccountRef": {"value": "1"}}},
				{"Amount": 50, "JournalEntryLineDetail": {"AccountRef": {"value": "2"}}},  # no PostingType
			]
		}
	)

	assert len(rows) == 1
	assert rows[0]["debit_in_account_currency"] == 100
	assert rows[0]["credit_in_account_currency"] == 0


def test_request_refreshes_and_retries_at_most_once(monkeypatch):
	"""A 401 triggers exactly one refresh+retry; a still-401 response raises, not loops.

	An unbounded refresh/retry loop would re-rotate (and thus invalidate) the refresh
	token on every pass -- the very failure the serialized refresh exists to prevent.
	"""
	import pytest

	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import client as client_mod

	monkeypatch.setattr(client_mod, "get_secret", lambda settings, key: "tok")
	client = client_mod.QuickBooksClient(types.SimpleNamespace(realm_id="42", environment="Production"))
	refreshes = {"n": 0}
	monkeypatch.setattr(client, "refresh_access_token", lambda **kwargs: refreshes.__setitem__("n", refreshes["n"] + 1))

	class Resp:
		status_code = 401
		text = '{"fault":{"error":[{"message":"unauthorized"}]}}'

		def json(self):
			return {}

	monkeypatch.setattr(client_mod.requests, "request", lambda *a, **k: Resp(), raising=False)

	with pytest.raises(client_mod.QuickBooksAPIError):
		client.request("GET", "/v3/company/42/query", params={"query": "select * from Account"})

	assert refreshes["n"] == 1


# ---------------------------------------------------------------------------
# QBO sub-customers / "jobs" -> ERPNext Projects (not flat colon-named Customers).
# A job (Job/IsProject/ParentRef/Level>0) imported as a Customer produced names
# like "4th West Apartments:PRJ-401 ..." and, via the Customer after_insert Drive
# hook, orphan top-level Drive folders. Jobs now route to a Project under the
# parent Customer; their transactions bill the parent, tagged with the Project.
# ---------------------------------------------------------------------------


def test_is_qbo_customer_job_detects_subcustomers():
	"""_is_qbo_customer_job flags QBO sub-customers/jobs but not top-level customers."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _is_qbo_customer_job

	assert _is_qbo_customer_job({"Job": True})
	assert _is_qbo_customer_job({"IsProject": True})
	assert _is_qbo_customer_job({"ParentRef": {"value": "1225"}})
	assert _is_qbo_customer_job({"Level": 1})
	assert not _is_qbo_customer_job({"DisplayName": "Acme Supply", "Level": 0})
	assert not _is_qbo_customer_job({"DisplayName": "Acme Supply"})


def test_prj_number_normalizes_padding():
	"""_prj_number reduces a 'PRJ-###' label to its digits, stripping zero-padding."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _prj_number

	assert _prj_number("PRJ-401 4th West Fountain Control & Pump Repair") == "401"
	assert _prj_number("PRJ-00401") == "401"  # QBO leaf "401" matches ERPNext id "PRJ-00401"
	assert _prj_number("PRJ419 Jan Trost - Fountain repair") == "419"
	assert _prj_number("4th West Apartments") is None


def test_strip_prj_prefix_handles_every_format():
	"""strip_prj_prefix removes a single leading PRJ-### token across the formats the
	QBO data carries, leaves clean titles alone, and never blanks a number-only title."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import strip_prj_prefix

	# Leading prefix in its various real-world spellings -> bare human title.
	assert strip_prj_prefix("PRJ-401 - 4th West Fountain Control & Pump Repair") == "4th West Fountain Control & Pump Repair"
	assert strip_prj_prefix("PRJ000062 - Terror Ride Fountain") == "Terror Ride Fountain"
	assert strip_prj_prefix("PRJ-96 - Hardware West Courtyard Fountains") == "Hardware West Courtyard Fountains"
	assert strip_prj_prefix("PRJ-111 District Heightsr (deleted)") == "District Heightsr (deleted)"  # space-only separator
	assert strip_prj_prefix("PRJ-00581 Myers Mortuary") == "Myers Mortuary"
	# An embedded " - " in the real title survives (only the leading token is stripped).
	assert strip_prj_prefix("PRJ-112 - Salt Hardware East Lobby - CO Manifold Replacement") == "Salt Hardware East Lobby - CO Manifold Replacement"
	# Already clean, no PRJ-number prefix, or a name that merely starts with "PRJ" letters.
	assert strip_prj_prefix("Myers Mortuary") == "Myers Mortuary"
	assert strip_prj_prefix("Pristine Fountains") == "Pristine Fountains"
	# Degenerate: a title that is only the number -> never blanked (returned unchanged).
	assert strip_prj_prefix("PRJ-00614") == "PRJ-00614"
	assert strip_prj_prefix("") == ""
	assert strip_prj_prefix(None) is None


def test_protect_existing_project_title_drops_only_when_title_set():
	"""_protect_existing_project_title removes project_name from the update values only
	when the Project already has a non-blank title (so the QBO job DisplayName never
	clobbers a curated title), and leaves it to fill an empty one."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _protect_existing_project_title

	class _Doc:
		def __init__(self, title):
			self._title = title

		def get(self, fieldname):
			return self._title if fieldname == "project_name" else None

	# Existing title -> project_name is dropped (not overwritten); other fields untouched.
	values = {"project_name": "PRJ-00581 Myers Mortuary", "customer": "Acme"}
	_protect_existing_project_title("Project", values, _Doc("Myers Mortuary"))
	assert "project_name" not in values
	assert values["customer"] == "Acme"

	# Blank existing title -> project_name is kept (the create/link path fills it).
	values = {"project_name": "Myers Mortuary"}
	_protect_existing_project_title("Project", values, _Doc(""))
	assert values["project_name"] == "Myers Mortuary"

	# Non-Project doctype -> never touched.
	values = {"project_name": "x"}
	_protect_existing_project_title("Customer", values, _Doc("anything"))
	assert values["project_name"] == "x"


def test_qbo_job_maps_to_project_under_parent_customer(monkeypatch):
	"""A QBO job routes to a Project (title = leaf DisplayName) under the parent Customer."""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if (
			doctype == "QuickBooks Sync Mapping"
			and isinstance(filters, dict)
			and filters.get("qbo_entity_type") == "Customer"
			and filters.get("qbo_id") == "1225"
			and filters.get("erpnext_doctype") == "Customer"
		):
			return "4th West Apartments"
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	payload = {
		"Id": "2054",
		"DisplayName": "PRJ-401 4th West Fountain Control & Pump Repair",
		"FullyQualifiedName": "4th West Apartments:PRJ-401 4th West Fountain Control & Pump Repair",
		"Job": True,
		"IsProject": True,
		"Level": 1,
		"ParentRef": {"value": "1225"},
	}
	doctype, values = map_qbo_to_erpnext("Customer", payload, types.SimpleNamespace(company="Sapphire Fountains"))

	assert doctype == "Project"
	# The colon path is gone and the redundant leading PRJ-### is stripped -- the title
	# is the bare human name (the number is already the Project's `name`).
	assert values["project_name"] == "4th West Fountain Control & Pump Repair"
	assert values["customer"] == "4th West Apartments"
	assert values["status"] == "Open"


def test_qbo_job_project_status_resolves_against_customized_options(monkeypatch):
	"""A new Project's status is resolved to a valid option of the site's customized
	Project status Select (no hard-coded 'Open' that would fail validation)."""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe,
		"get_meta",
		lambda doctype: types.SimpleNamespace(
			has_field=lambda fieldname: False,
			get_field=lambda fieldname: types.SimpleNamespace(
				options="Active\nClient Hold\nParked\nCompleted\nInvoiced\nPaid\nCanceled"
			)
			if fieldname == "status"
			else None,
		),
	)

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if (
			doctype == "QuickBooks Sync Mapping"
			and isinstance(filters, dict)
			and filters.get("qbo_id") == "1225"
			and filters.get("erpnext_doctype") == "Customer"
		):
			return "4th West Apartments"
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Customer",
		{"Id": "2054", "DisplayName": "PRJ-401 X", "Job": True, "Level": 1, "ParentRef": {"value": "1225"}},
		types.SimpleNamespace(company="SF"),
	)
	assert doctype == "Project"
	# "Open" is not a valid option here, so it falls to the next preference, "Active".
	assert values["status"] == "Active"
	assert values["status"] in ("Active", "Client Hold", "Parked", "Completed", "Invoiced", "Paid", "Canceled")


def test_resolve_customer_ref_redirects_job_to_parent_and_tags_project(monkeypatch):
	"""_resolve_customer_ref: a Customer ref stays a customer; a job ref -> (parent, project)."""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "QuickBooks Sync Mapping" and isinstance(filters, dict):
			# A top-level customer ("1") maps to a Customer; a job ("2054") to a Project.
			if filters.get("qbo_id") == "1" and filters.get("erpnext_doctype") == "Customer":
				return "Acme Supply"
			if filters.get("qbo_id") == "2054" and filters.get("erpnext_doctype") == "Project":
				return "PRJ-00401"
			return None
		if doctype == "Project" and filters == "PRJ-00401" and fieldname == "customer":
			return "4th West Apartments"
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import _resolve_customer_ref

	assert _resolve_customer_ref("1") == ("Acme Supply", None)
	assert _resolve_customer_ref("2054") == ("4th West Apartments", "PRJ-00401")
	assert _resolve_customer_ref(None) == (None, None)


def test_sales_invoice_for_job_bills_parent_and_tags_project(monkeypatch):
	"""A QBO Invoice billed to a job posts to the parent Customer, tagged with the Project."""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return {"default_receivable_account": "Debtors - SF", "default_currency": "USD"}.get(fieldname)
		if doctype == "Price List":
			return "Standard Selling"
		if doctype == "QuickBooks Sync Mapping" and isinstance(filters, dict):
			if filters.get("qbo_id") == "2054" and filters.get("erpnext_doctype") == "Project":
				return "PRJ-00401"
			return None  # the job has no Customer mapping anymore
		if doctype == "Project" and filters == "PRJ-00401" and fieldname == "customer":
			return "4th West Apartments"
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"Invoice",
		{"Id": "5", "TxnDate": "2026-06-06", "CustomerRef": {"value": "2054"}},
		types.SimpleNamespace(company="Sapphire Fountains"),
	)

	assert doctype == "Sales Invoice"
	assert values["customer"] == "4th West Apartments"
	assert values["project"] == "PRJ-00401"


def test_match_project_links_existing_job_by_prj_number(monkeypatch):
	"""find_existing_match routes a job to _match_project, linking the ERPNext project by number."""
	frappe = install_frappe_stub()

	def fake_sql(query, params=None, as_dict=False, **kwargs):
		assert "regexp" in query.lower()
		assert params["pat"].startswith("PRJ-?0*401")  # zero-pad-agnostic, number bound as a param
		return [types.SimpleNamespace(name="PRJ-00401")]

	monkeypatch.setattr(frappe.db, "sql", fake_sql, raising=False)
	from erpnext_enhancements.quickbooks_online.core.mapping import find_existing_match

	match = find_existing_match(
		"Customer",
		{
			"Id": "2054",
			"DisplayName": "PRJ-401 4th West Fountain Control & Pump Repair",
			"Job": True,
			"ParentRef": {"value": "1225"},
		},
		types.SimpleNamespace(company="Sapphire Fountains"),
	)

	assert match["status"] == "matched"
	assert match["name"] == "PRJ-00401"
	assert match["rule"] == "prj_number"


def _upsert_job_no_match(monkeypatch, parent_customer):
	"""Drive upsert_entity with a NEW QBO job (no mapping, no matching Project).

	Returns (result, recorded) where recorded captures save_mapping /
	save_manual_review_mapping calls. frappe.new_doc is trapped so any attempt to
	CREATE a record fails the test.
	"""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	monkeypatch.setattr(
		mapping,
		"map_qbo_to_erpnext",
		lambda *args: ("Project", {"project_name": "Daybreak Splash Pad", "status": "Open"}),
	)
	monkeypatch.setattr(mapping, "validate_mapped_values", lambda *args, **kwargs: [])
	monkeypatch.setattr(mapping, "_ensure_group_parent", lambda *args: None)
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: None)
	monkeypatch.setattr(mapping, "find_existing_match", lambda *args: None)
	monkeypatch.setattr(mapping, "_top_level_customer", lambda *args: parent_customer)

	def trap_new_doc(doctype):
		raise AssertionError(f"live sync must never create a {doctype} for a QBO job")

	monkeypatch.setattr(frappe, "new_doc", trap_new_doc, raising=False)
	recorded = {}
	monkeypatch.setattr(
		mapping,
		"save_mapping",
		lambda entity_type, qbo_id, payload, doctype, name, values, **extra: recorded.update(
			mapping=dict(doctype=doctype, name=name, values=values, **extra)
		),
	)
	monkeypatch.setattr(
		mapping,
		"save_manual_review_mapping",
		lambda entity_type, qbo_id, payload, doctype, issues: recorded.update(review=issues),
	)

	payload = {
		"Id": "3001",
		"DisplayName": "PRJ-777 Daybreak Splash Pad",
		"Job": True,
		"ParentRef": {"value": "1225"},
	}
	result = mapping.upsert_entity("Customer", payload, types.SimpleNamespace(company="Sapphire Fountains"))
	return result, recorded


def test_new_job_consolidates_to_parent_instead_of_creating_project(monkeypatch):
	"""Link-only: a new QBO job with no matching Project never mints one -- it is
	consolidated onto its top-level parent Customer, mirroring the colon-bug
	remediation's no-project policy (job_merge_no_project)."""
	result, recorded = _upsert_job_no_match(monkeypatch, parent_customer="Landmark Aquatics")

	assert result["action"] == "skipped"
	assert result["doctype"] == "Customer"
	assert result["name"] == "Landmark Aquatics"
	assert "never creates Projects" in result["reason"]
	assert recorded["mapping"] == {
		"doctype": "Customer",
		"name": "Landmark Aquatics",
		"values": {},
		"conflict_status": "Clean",
		"match_status": "Manual Matched",
		"match_rule": "job_merge_no_project",
	}


def test_new_job_without_imported_parent_parks_for_review(monkeypatch):
	"""Link-only: when the job's parent Customer isn't imported yet there is nothing
	safe to link to, so the job parks for manual review rather than creating a Project."""
	result, recorded = _upsert_job_no_match(monkeypatch, parent_customer=None)

	assert result["action"] == "manual_review"
	assert result["doctype"] == "Project"
	assert "link-only" in result["reason"]
	assert recorded["review"] == [result["reason"]]
	assert "mapping" not in recorded


def test_customers_imported_top_level_first(monkeypatch):
	"""query_entity_payloads yields customers sorted by QBO Level so parents precede jobs."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import sync

	monkeypatch.setattr(
		sync,
		"query_all",
		lambda entity_type, settings=None: iter(
			[
				{"Id": "2054", "DisplayName": "PRJ-401 job", "Level": 1, "ParentRef": {"value": "1225"}},
				{"Id": "1225", "DisplayName": "4th West Apartments", "Level": 0},
				{"Id": "9", "DisplayName": "Customer without a Level"},  # missing Level -> treated as 0
			]
		),
	)

	payloads = list(sync.query_entity_payloads("Customer"))

	levels = [payload.get("Level") or 0 for payload in payloads]
	assert levels == sorted(levels)
	assert payloads[-1]["Id"] == "2054"  # the only job sorts last


# ---------------------------------------------------------------------------
# Remediation of the legacy Parent:Job Customers (job_remediation.py).
# ---------------------------------------------------------------------------


def test_remediation_enumerates_only_jobs_top_level_first(monkeypatch):
	"""_enumerate_jobs returns only QBO-job mappings, sorted by Level ascending."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import job_remediation

	rows = [
		types.SimpleNamespace(name="QBO-MAP-Customer-2054", qbo_id="2054", erpnext_doctype="Customer", erpnext_name="4th West Apartments:PRJ-401 job"),
		types.SimpleNamespace(name="QBO-MAP-Customer-1225", qbo_id="1225", erpnext_doctype="Customer", erpnext_name="4th West Apartments"),
		types.SimpleNamespace(name="QBO-MAP-Customer-2099", qbo_id="2099", erpnext_doctype="Project", erpnext_name="PRJ-00099"),
	]
	monkeypatch.setattr(frappe, "get_all", lambda *a, **k: rows)
	payloads = {
		"2054": {"Job": True, "Level": 1, "ParentRef": {"value": "1225"}, "DisplayName": "PRJ-401 job"},
		"1225": {"DisplayName": "4th West Apartments"},  # top-level customer, NOT a job
		"2099": {"Job": True, "Level": 2, "ParentRef": {"value": "2054"}, "DisplayName": "deep job"},
	}
	monkeypatch.setattr(job_remediation, "_raw_payload_dict", lambda entity, qbo_id: payloads.get(str(qbo_id)))

	jobs = job_remediation._enumerate_jobs()
	ids = [mapping_row.qbo_id for mapping_row, _payload in jobs]

	assert "1225" not in ids  # the top-level parent is excluded
	assert ids == ["2054", "2099"]  # only jobs, Level 1 before Level 2


def test_remediation_tally_reports_would_actions_in_dry_run():
	"""_tally counts would_merge/invoice-count in dry-run and actuals in apply mode."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.job_remediation import _new_report, _tally

	dry = _new_report("dry-run", 1)
	_tally(dry, {
		"outcome": "dry-run", "project": "PRJ-00401", "project_created": False,
		"would_merge": True, "invoices": 3, "folder_plan": "empty",
	})
	assert dry["project_linked"] == 1 and dry["merged"] == 1
	assert dry["invoices_tagged"] == 3 and dry["folders_trashed"] == 1

	live = _new_report("apply", 1)
	_tally(live, {
		"outcome": "consolidated", "project": "PRJ-9", "project_created": True,
		"merged": True, "invoices": 5, "invoices_tagged": 2, "folder_action": "moved",
	})
	assert live["project_created"] == 1 and live["merged"] == 1
	assert live["invoices_tagged"] == 2 and live["folders_moved"] == 1  # actual tagged, not the count of 5

	skip = _new_report("apply", 1)
	_tally(skip, {"outcome": "skip-no-parent"})
	assert skip["no_parent"] == 1 and skip["merged"] == 0


def test_remediation_project_folder_name_for_relocated_folder():
	"""_project_folder_name prefixes the leaf with the project id, avoiding duplication."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.job_remediation import _project_folder_name

	assert _project_folder_name("PRJ-00401", {"DisplayName": "4th West Fountain"}) == "PRJ-00401 - 4th West Fountain"
	# leaf already carries the id -> no double prefix
	assert _project_folder_name("PRJ-00401", {"DisplayName": "PRJ-00401 4th West"}) == "PRJ-00401 4th West"
	# a not-yet-created project ("(new) ...") -> just the leaf
	assert _project_folder_name("(new) Foo", {"DisplayName": "Foo"}) == "Foo"


# A verbatim QuickBooks Bill payload pulled from the production raw-payload store
# (QBO-RAW-2026-249022). Bill 19019 is the mixed-shape case: two item-based lines
# (285.27 + 60.20) plus one account-based freight line (67.54) that the mapper used
# to drop, importing the invoice at 345.47 against a QuickBooks TotalAmt of 413.01.
BILL_19019 = {
	"APAccountRef": {"name": "Accounts Payable", "value": "124"},
	"Balance": 0,
	"CurrencyRef": {"name": "United States Dollar", "value": "USD"},
	"DocNumber": "2132402.01",
	"DueDate": "2025-12-13",
	"Id": "19019",
	"Line": [
		{
			"Amount": 285.27,
			"Description": "SCE-30EL2408LP EL Enclosure, 30H X 24W  X 8D",
			"DetailType": "ItemBasedExpenseLineDetail",
			"Id": "1",
			"ItemBasedExpenseLineDetail": {
				"BillableStatus": "NotBillable",
				"CustomerRef": {"value": "2380"},
				"ItemRef": {
					"name": "BUILD -  FOUNTAIN MATERIALS & EQUIPMENT:BUILD -  FOUNTAIN MATERIALS & EQUIPMENT",
					"value": "312",
				},
				"Qty": 1,
				"TaxCodeRef": {"value": "NON"},
				"UnitPrice": 285.27,
			},
			"LineNum": 1,
		},
		{
			"Amount": 60.2,
			"Description": "SCE-30P24 Subpanel, Bent; 27H X 21W  X 0.88D",
			"DetailType": "ItemBasedExpenseLineDetail",
			"Id": "2",
			"ItemBasedExpenseLineDetail": {
				"BillableStatus": "NotBillable",
				"CustomerRef": {"value": "2380"},
				"ItemRef": {
					"name": "BUILD -  FOUNTAIN MATERIALS & EQUIPMENT:BUILD -  FOUNTAIN MATERIALS & EQUIPMENT",
					"value": "312",
				},
				"Qty": 1,
				"TaxCodeRef": {"value": "NON"},
				"UnitPrice": 60.2,
			},
			"LineNum": 2,
		},
		{
			"AccountBasedExpenseLineDetail": {
				"AccountRef": {"name": "51300 Build COGS:Build Freight & Delivery", "value": "210"},
				"BillableStatus": "NotBillable",
				"CustomerRef": {"value": "2380"},
				"TaxCodeRef": {"value": "NON"},
			},
			"Amount": 67.54,
			"Description": "SHIPPING EXPENSE",
			"DetailType": "AccountBasedExpenseLineDetail",
			"Id": "3",
			"LineNum": 3,
		},
	],
	"MetaData": {"CreateTime": "2025-11-22T14:20:53-08:00", "LastUpdatedTime": "2026-01-07T09:05:03-08:00"},
	"SyncToken": "3",
	"TotalAmt": 413.01,
	"TxnDate": "2025-11-13",
	"VendorRef": {"name": "SCE Saginaw Control and Engineering", "value": "2211"},
	"domain": "QBO",
	"sparse": False,
}


def _bill_stub(monkeypatch, *, account_210="Build Freight & Delivery - SF", item_312="BUILD -  FOUNTAIN MATERIALS & EQUIPMENT"):
	"""Frappe stub resolving Bill 19019's vendor/item/account as production does.

	Pass ``account_210=None`` or ``item_312=None`` to simulate a reference that is
	not imported into ERPNext yet.
	"""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return "2110 - Creditors - SF" if fieldname == "default_payable_account" else None
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Vendor" and f.get("qbo_id") == "2211":
				return "SCE Saginaw Control and Engineering"
			if f.get("qbo_entity_type") == "Account" and f.get("qbo_id") == "210":
				return account_210
			if f.get("qbo_entity_type") == "Item" and f.get("qbo_id") == "312":
				return item_312
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	return frappe


def test_mixed_bill_folds_account_lines_into_purchase_charges(monkeypatch):
	"""A Bill with BOTH item and expense-account lines keeps the account lines.

	Production Bill 19019: the 67.54 freight line used to vanish, leaving a 345.47
	invoice against a 413.01 QuickBooks total with nothing to flag it.
	"""
	_bill_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	doctype, values = map_qbo_to_erpnext(
		"Bill", BILL_19019, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert doctype == "Purchase Invoice"
	assert [i["amount"] for i in values["items"]] == [285.27, 60.2]
	assert values["taxes"] == [
		{
			"charge_type": "Actual",
			"account_head": "Build Freight & Delivery - SF",
			"description": "SHIPPING EXPENSE",
			"tax_amount": 67.54,
			"category": "Total",
			"add_deduct_tax": "Add",
		}
	]
	# The whole point: the invoice now totals what QuickBooks says the bill totals.
	items_total = sum(i["amount"] for i in values["items"])
	charges_total = sum(t["tax_amount"] for t in values["taxes"])
	assert round(items_total + charges_total, 2) == 413.01
	assert validate_mapped_values("Bill", doctype, values, payload=BILL_19019) == []


def test_mixed_bill_mapping_is_replayable(monkeypatch):
	"""Re-mapping the same Bill yields identical values, and always maps ``taxes``.

	These are the two properties that make a re-sync idempotent rather than
	double-counting: the transform is pure, and ``taxes`` is present on EVERY
	Purchase Invoice (empty included), so ``apply_values``' wholesale child-table
	rewrite replaces the charges table instead of appending beside a stale row.
	"""
	_bill_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	settings = types.SimpleNamespace(company="Sapphire Fountains")
	first = map_qbo_to_erpnext("Bill", BILL_19019, settings)[1]
	second = map_qbo_to_erpnext("Bill", BILL_19019, settings)[1]

	assert first == second

	item_only = json.loads(json.dumps(BILL_19019))
	item_only["Line"] = item_only["Line"][:2]
	item_only["TotalAmt"] = 345.47
	_, values = map_qbo_to_erpnext("Bill", item_only, settings)
	assert values["taxes"] == []  # mapped, not omitted -- so a stale row gets cleared


def test_zero_amount_expense_line_adds_no_charge(monkeypatch):
	"""A $0.00 account line contributes no charge row (QBO emits placeholder lines)."""
	_bill_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	payload = json.loads(json.dumps(BILL_19019))
	payload["Line"][2]["Amount"] = 0
	payload["TotalAmt"] = 345.47

	_, values = map_qbo_to_erpnext("Bill", payload, types.SimpleNamespace(company="Sapphire Fountains"))

	assert values["taxes"] == []
	assert validate_mapped_values("Bill", "Purchase Invoice", values, payload=payload) == []


def test_mixed_bill_with_unimported_expense_account_is_parked(monkeypatch):
	"""An unresolved expense AccountRef parks the bill naming it -- never a fallback.

	No balancing account is invented: a wrong-but-plausible invoice that posts to the
	ledger is worse than a parked one, because nobody looks at it again.
	"""
	_bill_stub(monkeypatch, account_210=None)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	_, values = map_qbo_to_erpnext("Bill", BILL_19019, types.SimpleNamespace(company="Sapphire Fountains"))

	assert values["taxes"] == []  # dropped rather than booked to a guessed account
	issues = validate_mapped_values("Bill", "Purchase Invoice", values, payload=BILL_19019)
	assert len(issues) == 1
	assert "does not reconcile" in issues[0]
	assert "mapped 345.47" in issues[0] and "TotalAmt 413.01" in issues[0] and "off by 67.54" in issues[0]
	assert "210 (51300 Build COGS:Build Freight & Delivery)" in issues[0]


def test_purchase_invoice_totals_guard_names_unimported_items(monkeypatch):
	"""A Bill with one un-imported ItemRef is parked, not imported short.

	The dangerous shape: enough lines map that the invoice looks plausible, so only
	a totals check against QuickBooks catches the gap.
	"""
	_bill_stub(monkeypatch)  # item 312 resolves, item 999 does not
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	payload = json.loads(json.dumps(BILL_19019))
	payload["Line"][1]["ItemBasedExpenseLineDetail"]["ItemRef"] = {"value": "999", "name": "SHOP SUPPLIES"}

	doctype, values = map_qbo_to_erpnext("Bill", payload, types.SimpleNamespace(company="Sapphire Fountains"))

	assert doctype == "Purchase Invoice"
	assert [i["amount"] for i in values["items"]] == [285.27]  # the 60.20 line dropped out
	issues = validate_mapped_values("Bill", doctype, values, payload=payload)
	assert len(issues) == 1
	assert "off by 60.20" in issues[0]
	assert "1 item line(s) totalling 60.20 reference QuickBooks items" in issues[0]
	assert "999 (SHOP SUPPLIES)" in issues[0]


def test_bill_with_no_mappable_items_falls_through_to_a_journal_entry(monkeypatch):
	"""When no ItemRef resolves, a mixed Bill takes the expense-only JE branch.

	The freight line alone cannot carry the 413.01 payable, so the balance guard
	catches it -- and names the un-imported Item rather than blaming the accounts,
	which all resolve.
	"""
	_bill_stub(monkeypatch, item_312=None)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	doctype, values = map_qbo_to_erpnext(
		"Bill", BILL_19019, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert doctype == "Journal Entry"
	issues = validate_mapped_values("Bill", doctype, values, include_doc_required=False, payload=BILL_19019)
	assert any("2 item-based line(s) reference QuickBooks items not imported" in issue for issue in issues)


def test_purchase_invoice_guard_reports_an_unexplained_difference(monkeypatch):
	"""A shortfall no QBO line explains is still parked, and says exactly that."""
	_bill_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	# Every line maps, but QuickBooks says the bill is 25.00 more than its lines --
	# e.g. tax carried in TxnTaxDetail, which the importer does not model.
	payload = json.loads(json.dumps(BILL_19019))
	payload["TotalAmt"] = 438.01

	_, values = map_qbo_to_erpnext("Bill", payload, types.SimpleNamespace(company="Sapphire Fountains"))
	issues = validate_mapped_values("Bill", "Purchase Invoice", values, payload=payload)

	assert len(issues) == 1
	assert "off by 25.00" in issues[0]
	assert "does not account for the difference" in issues[0]


def test_purchase_invoice_guard_is_skipped_without_a_quickbooks_total(monkeypatch):
	"""With no TotalAmt there is nothing authoritative to reconcile against."""
	_bill_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	payload = json.loads(json.dumps(BILL_19019))
	payload.pop("TotalAmt")

	_, values = map_qbo_to_erpnext("Bill", payload, types.SimpleNamespace(company="Sapphire Fountains"))

	assert validate_mapped_values("Bill", "Purchase Invoice", values, payload=payload) == []


def test_journal_imbalance_names_the_unimported_item_not_the_accounts(monkeypatch):
	"""An item line that can't resolve names the ITEM, not the accounts.

	The two need opposite fixes. The original wording asserted "accounts not yet
	imported" for every imbalance, which sent triage after accounts that were all
	correctly mapped.
	"""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "QuickBooks Sync Mapping" and (filters or {}).get("qbo_entity_type") == "Account":
			return "Chase Checking - SF"  # every account on this payload IS imported
		return None  # ...but the Item is not

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	payload = {
		"Id": "9001",
		"TxnDate": "2026-06-02",
		"TotalAmt": 2352.35,
		"AccountRef": {"value": "40", "name": "Chase Checking"},
		"Line": [
			{
				"Amount": 2352.35,
				"DetailType": "ItemBasedExpenseLineDetail",
				"ItemBasedExpenseLineDetail": {"ItemRef": {"value": "312", "name": "PUMP"}},
			}
		],
	}
	_, values = map_qbo_to_erpnext("Purchase", payload, types.SimpleNamespace(company="Sapphire Fountains"))
	issues = validate_mapped_values("Purchase", "Journal Entry", values, include_doc_required=False, payload=payload)

	assert len(issues) == 1
	assert "unbalanced (debit 0.00 vs credit 2352.35)" in issues[0]
	assert "1 item-based line(s) reference QuickBooks items not imported" in issues[0]
	assert "312 (PUMP)" in issues[0]


def test_journal_imbalance_names_an_item_with_no_expense_account(monkeypatch):
	"""An imported Item lacking a default expense account is named specifically.

	This is the second failure mode and needs a different fix from the first: the
	Item exists, so importing nothing helps -- somebody has to set the account.
	"""
	frappe = install_frappe_stub()

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		f = filters or {}
		if doctype == "QuickBooks Sync Mapping" and f.get("qbo_entity_type") == "Account":
			return "Chase Checking - SF"
		if doctype == "QuickBooks Sync Mapping" and f.get("qbo_entity_type") == "Item":
			return "PUMP-100"
		return None  # no Item Default row, and no Company default_expense_account

	monkeypatch.setattr(frappe.db, "get_value", gv)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	payload = {
		"Id": "9003",
		"TxnDate": "2026-06-02",
		"TotalAmt": 75.0,
		"AccountRef": {"value": "40", "name": "Chase Checking"},
		"Line": [
			{
				"Amount": 75.0,
				"DetailType": "ItemBasedExpenseLineDetail",
				"ItemBasedExpenseLineDetail": {"ItemRef": {"value": "312", "name": "PUMP"}},
			}
		],
	}
	_, values = map_qbo_to_erpnext("Purchase", payload, types.SimpleNamespace(company="Sapphire Fountains"))
	issues = validate_mapped_values("Purchase", "Journal Entry", values, include_doc_required=False, payload=payload)

	assert len(issues) == 1
	assert 'Item "PUMP-100" has no default expense account for company Sapphire Fountains' in issues[0]
	assert "not imported into ERPNext" not in issues[0]  # the Item exists; that's not the problem


def test_journal_imbalance_names_the_accounts_that_are_missing():
	"""When an AccountRef genuinely doesn't resolve, the message names it."""
	install_frappe_stub()  # default stub resolves no account mappings
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	payload = {
		"Id": "9002",
		"TxnDate": "2026-06-02",
		"TotalAmt": 100,
		"AccountRef": {"value": "40", "name": "Chase Checking"},
		"Line": [
			{
				"Amount": 100,
				"DetailType": "AccountBasedExpenseLineDetail",
				"AccountBasedExpenseLineDetail": {"AccountRef": {"value": "61", "name": "Shop Supplies"}},
			}
		],
	}
	_, values = map_qbo_to_erpnext("Purchase", payload, types.SimpleNamespace(company="SF"))
	# Both legs dropped -> caught by the required-accounts check, not the balance one.
	assert values["accounts"] == []

	# One leg resolves, the other doesn't: now it's an imbalance, named precisely.
	values["accounts"] = [{"account": "Chase Checking - SF", "debit_in_account_currency": 0, "credit_in_account_currency": 100}]
	issues = validate_mapped_values("Purchase", "Journal Entry", values, include_doc_required=False, payload=payload)

	assert len(issues) == 1
	assert "unbalanced" in issues[0]
	assert "61 (Shop Supplies)" in issues[0] and "40 (Chase Checking)" in issues[0]
	assert "skipped during mapping" not in issues[0]


def _ignored_mapping(**overrides):
	"""A Sync Mapping double for a record a human closed out as Ignored."""
	fields = {
		"erpnext_doctype": "Journal Entry",
		"erpnext_name": None,
		"conflict_status": "Ignored",
		"match_status": "Pending Review",
		"sync_token": "2",
		"last_qbo_updated_at": datetime(2026, 7, 20, 12, 0, 0),
		"owned_fields": json.dumps({"issues": ["Missing required field: accounts"], "ignored_at": "2026-07-27"}),
	}
	fields.update(overrides)
	return types.SimpleNamespace(**fields)


def test_ignored_mapping_survives_a_full_import(monkeypatch):
	"""An Ignored mapping is returned untouched: no preflight, no mapping write.

	Without this a single full Import All silently reverts every record a human
	closed out -- save_manual_review_mapping resets BOTH statuses to Pending Review.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	touched = []
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: _ignored_mapping())
	monkeypatch.setattr(
		mapping, "validate_mapped_values", lambda *a, **k: touched.append("preflight") or []
	)
	monkeypatch.setattr(mapping, "save_manual_review_mapping", lambda *a, **k: touched.append("parked"))
	monkeypatch.setattr(mapping, "save_mapping", lambda *a, **k: touched.append("saved"))

	# SyncToken "2" matches what the mapping stored -> nothing changed in QuickBooks.
	payload = {"Id": "9786", "SyncToken": "2", "TotalAmt": 0, "TxnDate": "2026-06-02"}
	result = mapping.upsert_entity("Purchase", payload, types.SimpleNamespace(company="SF"))

	assert result["action"] == "ignored"
	assert result["qbo_id"] == "9786"
	assert touched == []  # preflight never ran; the mapping was never rewritten


def test_ignored_mapping_reopens_when_quickbooks_moves(monkeypatch):
	"""A SyncToken past the stored one re-evaluates the record normally.

	Someone un-voiding a QuickBooks transaction must not stay permanently invisible.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	parked = []
	monkeypatch.setattr(mapping, "get_mapping", lambda *args: _ignored_mapping())
	monkeypatch.setattr(mapping, "save_manual_review_mapping", lambda *a, **k: parked.append(a[1]))

	payload = {"Id": "9786", "SyncToken": "3", "TotalAmt": 0, "TxnDate": "2026-06-02"}
	result = mapping.upsert_entity("Purchase", payload, types.SimpleNamespace(company="SF"))

	assert result["action"] == "manual_review"
	assert parked == ["9786"]


def test_qbo_record_advanced_falls_back_to_last_updated_time():
	"""With no comparable SyncToken, QBO's LastUpdatedTime decides; neither -> False."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _qbo_record_advanced

	stored = _ignored_mapping(sync_token=None)
	assert _qbo_record_advanced(stored, {"MetaData": {"LastUpdatedTime": "2026-07-25 12:00:00+00:00"}}) is True
	assert _qbo_record_advanced(stored, {"MetaData": {"LastUpdatedTime": "2026-07-01 12:00:00+00:00"}}) is False
	# No signal at all -> "not advanced", so a closed-out record stays closed out.
	assert _qbo_record_advanced(stored, {}) is False
	# A non-numeric token that simply differs counts as movement.
	assert _qbo_record_advanced(_ignored_mapping(sync_token="abc"), {"SyncToken": "abd"}) is True


def test_ignored_results_are_counted_in_the_sync_log():
	"""_track_result counts 'ignored' so closed-out records are a visible category."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.sync import _track_result

	log = types.SimpleNamespace(ignored_count=0)
	_track_result(log, {"action": "ignored", "qbo_id": "9786"})
	_track_result(log, {"action": "ignored", "qbo_id": "9785"})

	assert log.ignored_count == 2


def test_payload_without_an_id_is_skipped(monkeypatch):
	"""An Id-less QBO payload is skipped -- it must never key a write on "None".

	str(None) is the truthy string "None", so the ``if not qbo_id`` guard was dead
	code: such a payload sailed past it and every downstream write keyed on the
	literal id "None", giving one QBO-MAP-<entity>-None mapping per entity type,
	each silently overwriting the last.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	touched = []
	monkeypatch.setattr(mapping, "map_qbo_to_erpnext", lambda *args: ("Customer", {"customer_name": "Acme"}))
	monkeypatch.setattr(mapping, "get_mapping", lambda *a: touched.append("lookup"))
	monkeypatch.setattr(mapping, "save_manual_review_mapping", lambda *a, **k: touched.append("parked"))
	monkeypatch.setattr(mapping, "save_mapping", lambda *a, **k: touched.append("saved"))

	for payload in ({"DisplayName": "Acme"}, {"Id": None, "DisplayName": "Acme"}, {"Id": ""}):
		assert mapping.upsert_entity("Customer", payload, types.SimpleNamespace(company="SF")) == {
			"action": "skipped",
			"reason": "QBO payload has no Id",
		}
	assert touched == []  # nothing was looked up, parked or written


def test_mark_deleted_without_an_id_is_skipped(monkeypatch):
	"""A CDC delete with no Id is skipped, not reported as a clean delete."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core import mapping

	looked_up = []
	monkeypatch.setattr(mapping, "get_mapping", lambda *a: looked_up.append(a))

	assert mapping.mark_deleted("Customer", None) == {
		"action": "skipped",
		"reason": "QBO payload has no Id",
	}
	assert mapping.mark_deleted("Customer", "")["action"] == "skipped"
	assert looked_up == []  # no lookup that would match nothing and read as success


# Verbatim QuickBooks Purchase payloads from the production raw-payload store, for
# three of the 18 records the item-line gap kept parked. QBO's PurchaseEx/RemitToAddr
# noise is dropped; every field the mapper reads is untouched.
#
# 3815 -- one item line, no account line. Produced a credit and no debit at all.
PURCHASE_3815 = {
	"AccountRef": {"name": "US Bank Checking", "value": "130"},
	"CurrencyRef": {"name": "United States Dollar", "value": "USD"},
	"Id": "3815",
	"Line": [
		{
			"Amount": 75.0,
			"DetailType": "ItemBasedExpenseLineDetail",
			"Id": "1",
			"ItemBasedExpenseLineDetail": {
				"BillableStatus": "NotBillable",
				"ClassRef": {"name": "S - Sales Group Admin", "value": "100000000001199157"},
				"CustomerRef": {"name": "RTL-000040 - Emily Mattson Wedding", "value": "1491"},
				"ItemRef": {"name": "RENT - FOUNTAIN PILLAR", "value": "166"},
				"Qty": 1,
				"TaxCodeRef": {"value": "NON"},
				"UnitPrice": 75,
			},
		}
	],
	"MetaData": {"LastUpdatedTime": "2014-03-18T00:00:00-07:00"},
	"PaymentType": "Check",
	"SyncToken": "0",
	"TotalAmt": 75.0,
	"TxnDate": "2014-03-18",
}

# 4058 -- mixed: item 186.64 + account 52.96 = 239.60. Debited only the 52.96.
PURCHASE_4058 = {
	"AccountRef": {"name": "US Bank Checking", "value": "130"},
	"CurrencyRef": {"name": "United States Dollar", "value": "USD"},
	"Id": "4058",
	"Line": [
		{
			"Amount": 186.64,
			"DetailType": "ItemBasedExpenseLineDetail",
			"Id": "1",
			"ItemBasedExpenseLineDetail": {
				"BillableStatus": "NotBillable",
				"ItemRef": {"name": "Sales Tax (deleted)", "value": "162"},
				"TaxCodeRef": {"value": "NON"},
			},
		},
		{
			"AccountBasedExpenseLineDetail": {
				"AccountRef": {"name": "Utah State Tax Commission Payable", "value": "190"},
				"BillableStatus": "NotBillable",
				"TaxCodeRef": {"value": "NON"},
			},
			"Amount": 52.96,
			"DetailType": "AccountBasedExpenseLineDetail",
			"Id": "2",
		},
	],
	"MetaData": {"LastUpdatedTime": "2016-10-21T00:00:00-07:00"},
	"PaymentType": "Check",
	"SyncToken": "0",
	"TotalAmt": 239.6,
	"TxnDate": "2016-10-21",
}

# 3545 -- item 481.02 against a NEGATIVE account line -215.42, totalling 265.60.
# The negative debit is QBO's own sign convention, not a Credit-type purchase
# (Credit is absent here, as it is on all 18).
PURCHASE_3545 = {
	"AccountRef": {"name": "US Bank Checking", "value": "130"},
	"CurrencyRef": {"name": "United States Dollar", "value": "USD"},
	"DocNumber": "online",
	"Id": "3545",
	"Line": [
		{
			"Amount": 481.02,
			"DetailType": "ItemBasedExpenseLineDetail",
			"Id": "1",
			"ItemBasedExpenseLineDetail": {
				"BillableStatus": "NotBillable",
				"ItemRef": {"name": "Sales Tax (deleted)", "value": "162"},
				"TaxCodeRef": {"value": "NON"},
			},
		},
		{
			"AccountBasedExpenseLineDetail": {
				"AccountRef": {"name": "Utah State Tax Commission Payable", "value": "190"},
				"BillableStatus": "NotBillable",
				"TaxCodeRef": {"value": "NON"},
			},
			"Amount": -215.42,
			"DetailType": "AccountBasedExpenseLineDetail",
			"Id": "2",
		},
	],
	"MetaData": {"LastUpdatedTime": "2011-05-02T00:00:00-07:00"},
	"PaymentType": "Check",
	"SyncToken": "0",
	"TotalAmt": 265.6,
	"TxnDate": "2011-05-02",
}

# The production chart these payloads resolve against.
_PURCHASE_ACCOUNTS = {"130": "US Bank Checking - SF", "190": "Utah State Tax Commission Payable - SF"}
_PURCHASE_ITEMS = {"162": "Sales Tax (deleted)", "166": "SRV-414", "182": "SR201 - Fountain Sales Design (deleted)"}
_ITEM_EXPENSE_ACCOUNTS = {
	"Sales Tax (deleted)": "Out of State Sales Tax Payable - SF",
	"SRV-414": "Rent Inventory - SF",
	"SR201 - Fountain Sales Design (deleted)": "Design Professional Services & Subcontractors - SF",
}


def _purchase_stub(monkeypatch, *, item_expense_accounts=None, company_default_expense=None, classes=None):
	"""Frappe stub resolving the accounts, Items and Item Defaults production has.

	``item_expense_accounts`` overrides the Item -> expense account map (pass ``{}``
	to simulate Items that carry no default), and ``company_default_expense`` sets
	the Company-level fallback.
	"""
	frappe = install_frappe_stub()
	expense = _ITEM_EXPENSE_ACCOUNTS if item_expense_accounts is None else item_expense_accounts

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		f = filters or {}
		if doctype == "Company":
			return company_default_expense if fieldname == "default_expense_account" else None
		if doctype == "Item Default":
			return expense.get(f.get("parent"))
		if doctype == "QuickBooks Sync Mapping":
			if f.get("qbo_entity_type") == "Account":
				return _PURCHASE_ACCOUNTS.get(f.get("qbo_id"))
			if f.get("qbo_entity_type") == "Item":
				return _PURCHASE_ITEMS.get(f.get("qbo_id"))
			if f.get("qbo_entity_type") == "Class":
				return (classes or {}).get(f.get("qbo_id"))
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)
	return frappe


def _totals(values):
	"""(total debit, total credit) of a mapped Journal Entry, rounded like the guard."""
	rows = values.get("accounts") or []
	return (
		round(sum(row.get("debit_in_account_currency") or 0 for row in rows), 2),
		round(sum(row.get("credit_in_account_currency") or 0 for row in rows), 2),
	)


def _first_debit(values):
	"""The first journal row carrying a debit."""
	return next(row for row in values["accounts"] if row["debit_in_account_currency"])


def _first_credit(values):
	"""The first journal row carrying a credit."""
	return next(row for row in values["accounts"] if row["credit_in_account_currency"])


def test_purchase_item_line_debits_the_items_expense_account(monkeypatch):
	"""Purchase 3815: a lone item line now produces the debit it always lacked.

	Before, this credited the bank 75.00 against no debit at all and the balance
	guard parked the whole transaction.
	"""
	_purchase_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	doctype, values = map_qbo_to_erpnext(
		"Purchase", PURCHASE_3815, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert doctype == "Journal Entry"
	assert _totals(values) == (75.0, 75.0)
	debit = _first_debit(values)
	assert debit["account"] == "Rent Inventory - SF"  # from the Item's Item Default
	assert validate_mapped_values("Purchase", doctype, values, include_doc_required=False, payload=PURCHASE_3815) == []


def test_purchase_mixes_item_and_account_lines(monkeypatch):
	"""Purchase 4058: item 186.64 + account 52.96 both debit, totalling 239.60.

	Before, only the 52.96 account line was debited.
	"""
	_purchase_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	doctype, values = map_qbo_to_erpnext(
		"Purchase", PURCHASE_4058, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert _totals(values) == (239.6, 239.6)
	debits = {row["account"]: row["debit_in_account_currency"] for row in values["accounts"] if row["debit_in_account_currency"]}
	assert debits == {"Utah State Tax Commission Payable - SF": 52.96, "Out of State Sales Tax Payable - SF": 186.64}
	assert validate_mapped_values("Purchase", doctype, values, include_doc_required=False, payload=PURCHASE_4058) == []


def test_purchase_keeps_a_negative_account_line_negative(monkeypatch):
	"""Purchase 3545: 481.02 item debit against a -215.42 account line = 265.60.

	The negative debit is QuickBooks' own sign convention for a reducing line, NOT a
	Credit-type purchase -- `Credit` is absent here and on all 18 records. Flipping
	the sign would balance the entry at the wrong number, so the line is carried
	through as-is and the total lands on TotalAmt.
	"""
	_purchase_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	doctype, values = map_qbo_to_erpnext(
		"Purchase", PURCHASE_3545, types.SimpleNamespace(company="Sapphire Fountains")
	)

	debits = {row["account"]: row["debit_in_account_currency"] for row in values["accounts"] if row["debit_in_account_currency"]}
	assert debits["Out of State Sales Tax Payable - SF"] == 481.02
	assert debits["Utah State Tax Commission Payable - SF"] == -215.42
	assert _totals(values) == (265.6, 265.6)
	assert validate_mapped_values("Purchase", doctype, values, include_doc_required=False, payload=PURCHASE_3545) == []


def test_credit_purchase_reverses_the_item_line_too(monkeypatch):
	"""A `Credit` purchase credits its item lines, matching the account-line side.

	None of the 18 blocked records is a Credit, so this is the untested direction --
	an item line must reverse with everything else or the entry balances backwards.
	"""
	_purchase_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	payload = json.loads(json.dumps(PURCHASE_3815))
	payload["Credit"] = True

	doctype, values = map_qbo_to_erpnext("Purchase", payload, types.SimpleNamespace(company="Sapphire Fountains"))

	rows = {row["account"]: row for row in values["accounts"]}
	assert rows["US Bank Checking - SF"]["debit_in_account_currency"] == 75.0  # funding debited
	assert rows["Rent Inventory - SF"]["credit_in_account_currency"] == 75.0  # expense credited
	assert _totals(values) == (75.0, 75.0)
	assert validate_mapped_values("Purchase", doctype, values, include_doc_required=False, payload=payload) == []


def test_purchase_item_line_carries_its_class_as_cost_center(monkeypatch):
	"""A line's ClassRef becomes the row's Cost Center, as it does on invoice lines."""
	_purchase_stub(monkeypatch, classes={"100000000001199157": "S - Sales Group Admin - SF"})
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_, values = map_qbo_to_erpnext("Purchase", PURCHASE_3815, types.SimpleNamespace(company="Sapphire Fountains"))

	debit = _first_debit(values)
	assert debit["cost_center"] == "S - Sales Group Admin - SF"
	# The funding row carries none -- account-based rows keep their existing shape.
	funding = _first_credit(values)
	assert "cost_center" not in funding


def test_purchase_falls_back_to_the_company_expense_account(monkeypatch):
	"""An Item with no default of its own uses the Company's default expense account."""
	_purchase_stub(monkeypatch, item_expense_accounts={}, company_default_expense="Miscellaneous Expenses - SF")
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	doctype, values = map_qbo_to_erpnext(
		"Purchase", PURCHASE_3815, types.SimpleNamespace(company="Sapphire Fountains")
	)

	debit = _first_debit(values)
	assert debit["account"] == "Miscellaneous Expenses - SF"
	assert validate_mapped_values("Purchase", doctype, values, include_doc_required=False, payload=PURCHASE_3815) == []


def test_purchase_parks_when_no_expense_account_resolves(monkeypatch):
	"""No Item default and no Company default -> park, naming the item. Never a guess.

	A wrong-but-balanced journal entry posts to the ledger and nobody looks at it
	again; a parked one gets fixed. So the debit is simply not emitted, the entry
	fails the balance guard, and the issue says which Item to fix.
	"""
	_purchase_stub(monkeypatch, item_expense_accounts={}, company_default_expense=None)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	doctype, values = map_qbo_to_erpnext(
		"Purchase", PURCHASE_3815, types.SimpleNamespace(company="Sapphire Fountains")
	)

	# Only the funding credit survives -- no stand-in account was invented.
	assert _totals(values) == (0.0, 75.0)
	issues = validate_mapped_values("Purchase", doctype, values, include_doc_required=False, payload=PURCHASE_3815)
	assert len(issues) == 1
	assert 'Item "SRV-414" has no default expense account for company Sapphire Fountains' in issues[0]


def test_purchase_zero_amount_item_line_adds_no_row(monkeypatch):
	"""A $0.00 item line is dropped -- ERPNext rejects a 0/0 journal row."""
	_purchase_stub(monkeypatch)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	payload = json.loads(json.dumps(PURCHASE_4058))
	payload["Line"][0]["Amount"] = 0
	payload["TotalAmt"] = 52.96

	_, values = map_qbo_to_erpnext("Purchase", payload, types.SimpleNamespace(company="Sapphire Fountains"))

	assert all(
		row["debit_in_account_currency"] or row["credit_in_account_currency"] for row in values["accounts"]
	)
	assert _totals(values) == (52.96, 52.96)


def test_item_expense_account_prefers_the_item_default(monkeypatch):
	"""The resolution order is Item Default, then Company, then nothing."""
	_purchase_stub(monkeypatch, company_default_expense="Miscellaneous Expenses - SF")
	from erpnext_enhancements.quickbooks_online.core.mapping import _item_expense_account

	assert _item_expense_account("SRV-414", "Sapphire Fountains") == "Rent Inventory - SF"
	assert _item_expense_account("NOT-AN-ITEM", "Sapphire Fountains") == "Miscellaneous Expenses - SF"
	assert _item_expense_account(None, "Sapphire Fountains") is None
	assert _item_expense_account("SRV-414", None) is None


# ---------------------------------------------------------------------------
# Sales tax (TxnTaxDetail) and the Sales Invoice shortfall guard
# ---------------------------------------------------------------------------

# QBO invoice I100549 (Myers Mortuary, 2022-09-08), trimmed to the fields the mapper
# reads. $385.56 in QuickBooks: $360.00 of lines plus $25.56 of Utah tax at 7.1%, which
# imported as $360.00 because the tax lives OUTSIDE the Line array.
#
# TxnTaxCodeRef 8 and TaxRateRef 15 are the whole point of this fixture: they are
# different QBO id spaces, and reading 15 as a TaxCode silently yields a real account for
# the wrong city.
INVOICE_I100549 = {
	"Id": "9100549",
	"DocNumber": "I100549",
	"TxnDate": "2022-09-08",
	"CustomerRef": {"name": "Myers Mortuary", "value": "1380"},
	"CurrencyRef": {"name": "United States Dollar", "value": "USD"},
	"Line": [
		{
			"Amount": 360.00,
			"Description": "Fountain service call",
			"DetailType": "SalesItemLineDetail",
			"SalesItemLineDetail": {
				"ItemRef": {"name": "Service", "value": "279"},
				"Qty": 1,
				"UnitPrice": 360,
				# Line-level TaxCodeRef is a STRING enum ("TAX"/"NON"), not a numeric id, so
				# it is not resolvable through the TaxCode mapping table the way
				# TxnTaxCodeRef is. The mapper reads only the transaction-level ref.
				"TaxCodeRef": {"value": "TAX"},
			},
		},
		# Four zero-amount description lines, as the real invoice carries.
		{
			"Amount": 0,
			"Description": "note",
			"DetailType": "SalesItemLineDetail",
			"SalesItemLineDetail": {"ItemRef": {"value": "279"}, "TaxCodeRef": {"value": "NON"}},
		},
		{
			"Amount": 0,
			"DetailType": "SalesItemLineDetail",
			"SalesItemLineDetail": {"ItemRef": {"value": "279"}, "TaxCodeRef": {"value": "NON"}},
		},
		{
			"Amount": 0,
			"DetailType": "SalesItemLineDetail",
			"SalesItemLineDetail": {"ItemRef": {"value": "279"}, "TaxCodeRef": {"value": "NON"}},
		},
		{
			"Amount": 0,
			"DetailType": "SalesItemLineDetail",
			"SalesItemLineDetail": {"ItemRef": {"value": "279"}, "TaxCodeRef": {"value": "NON"}},
		},
		# QBO repeats the net as a SubTotalLineDetail row carrying a REAL amount, so
		# summing Line[].Amount naively double-counts the invoice (720.00, not 360.00).
		# The mapper reads only SalesItemLineDetail, which is what makes it immune.
		{"Amount": 360.00, "DetailType": "SubTotalLineDetail", "SubTotalLineDetail": {}},
	],
	"TxnTaxDetail": {
		"TotalTax": 25.56,
		"TxnTaxCodeRef": {"value": "8"},
		"TaxLine": [
			{
				"Amount": 25.56,
				"DetailType": "TaxLineDetail",
				"TaxLineDetail": {
					"NetAmountTaxable": 360.0,
					"PercentBased": True,
					"TaxPercent": 7.1,
					"TaxRateRef": {"value": "15"},
				},
			}
		],
	},
	"TotalAmt": 385.56,
}

# The trap, as it exists in production: TaxCode 8 is Ogden, TaxCode 15 is Sandy. If the
# mapper ever resolves TaxRateRef (15) instead of TxnTaxCodeRef (8) it gets a real account
# for the wrong jurisdiction and nothing errors.
_TAX_CODES = {"8": "Utah - Weber - Ogden - Inactive - SF", "15": "Sandy Utah - SF"}

_SALES_TAX_ACCOUNT = "25010 - Sales Tax Agency Payable - SF"


def _sales_tax_stub(
	monkeypatch,
	frappe,
	tax_account=_SALES_TAX_ACCOUNT,
	is_group=0,
	disabled=0,
	item_code="SERVICE - MAINTENANCE CONTRACT",
	income_account="4110 - Sales - SF",
	accounts=None,
):
	"""Stub the lookups the sales-tax path makes; ``tax_account=None`` means 25010 is absent."""
	accounts = accounts or {}

	def gv(doctype, filters=None, fieldname=None, **kwargs):
		if doctype == "Company":
			return {
				"default_currency": "USD",
				"default_receivable_account": "1310 - Debtors - SF",
				"default_income_account": income_account,
			}.get(fieldname)
		if doctype == "Price List":
			return "Standard Selling"
		if doctype == "Item Default":
			return None
		if doctype == "Account":
			# The 25010-by-number fallback.
			if isinstance(filters, dict) and filters.get("account_number") == "25010":
				return tax_account
			# _sales_tax_account's postability check, and _ledger_for_posting's is_group probe.
			if isinstance(filters, str):
				if kwargs.get("as_dict"):
					return {"is_group": is_group, "disabled": disabled}
				return 0
			return None
		if doctype == "QuickBooks Sync Mapping":
			f = filters or {}
			if f.get("qbo_entity_type") == "Item":
				return item_code
			if f.get("qbo_entity_type") == "TaxCode":
				return _TAX_CODES.get(str(f.get("qbo_id")))
			if f.get("qbo_entity_type") == "Account":
				return accounts.get(str(f.get("qbo_id")))
			if f.get("qbo_entity_type") == "Customer" and f.get("erpnext_doctype") == "Customer":
				return "Myers Mortuary"
		return None

	monkeypatch.setattr(frappe.db, "get_value", gv)


def test_invoice_tax_becomes_an_actual_charge_reconciling_to_qbo_total(monkeypatch):
	"""The I100549 shape: one Actual charge of 25.56, and items + tax == TotalAmt 385.56."""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	doctype, values = map_qbo_to_erpnext(
		"Invoice", INVOICE_I100549, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert doctype == "Sales Invoice"
	assert values["taxes"] == [
		{
			"charge_type": "Actual",
			"account_head": _SALES_TAX_ACCOUNT,
			# The jurisdiction survives here, because every jurisdiction posts to one account.
			"description": "Utah - Weber - Ogden - Inactive - SF",
			"tax_amount": 25.56,
		}
	]
	# Sales Taxes and Charges has no category / add_deduct_tax field (those are
	# Purchase-only); setting them would be silently dropped by Frappe, not rejected.
	assert "category" not in values["taxes"][0]
	assert "add_deduct_tax" not in values["taxes"][0]

	items_total = sum(round(row["qty"] * row["rate"], 2) for row in values["items"])
	assert items_total == 360.00
	assert round(items_total + values["taxes"][0]["tax_amount"], 2) == INVOICE_I100549["TotalAmt"]
	# The SubTotalLineDetail row repeats the net (360.00). Summing Line[].Amount naively
	# would give 720.00; reading only SalesItemLineDetail is what avoids double-counting.
	assert sum(line["Amount"] for line in INVOICE_I100549["Line"]) == 720.00
	# And the guard agrees it reconciles.
	assert (
		validate_mapped_values(
			"Invoice", "Sales Invoice", values, include_doc_required=False, payload=INVOICE_I100549
		)
		== []
	)


def test_tax_identity_comes_from_txn_tax_code_ref_not_tax_rate_ref(monkeypatch):
	"""TaxRateRef must never resolve an account: it is a different QBO id space.

	On I100549, TxnTaxCodeRef is 8 (Ogden) and TaxRateRef is 15 (which as a TaxCode is
	Sandy -- a real account for the wrong city, resolved with no error). This asserts the
	description follows 8, and that swapping the two ids changes the answer, so a future
	edit that reads TaxRateRef fails here instead of silently mis-booking jurisdictions.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_charges

	settings = types.SimpleNamespace(company="Sapphire Fountains")
	assert _sales_charges(INVOICE_I100549, settings)[0]["description"] == "Utah - Weber - Ogden - Inactive - SF"

	# Same payload, TxnTaxCodeRef swapped to the TaxRateRef value: a DIFFERENT answer.
	# If these two ever agree, the mapper is reading the wrong ref.
	swapped = json.loads(json.dumps(INVOICE_I100549))
	swapped["TxnTaxDetail"]["TxnTaxCodeRef"] = {"value": "15"}
	assert _sales_charges(swapped, settings)[0]["description"] == "Sandy Utah - SF"


def test_untaxed_invoice_produces_no_taxes_row(monkeypatch):
	"""An invoice with no TxnTaxDetail maps an empty taxes table, not a zero-value row."""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	untaxed = json.loads(json.dumps(INVOICE_I100549))
	del untaxed["TxnTaxDetail"]
	untaxed["TotalAmt"] = 360.00
	_doctype, values = map_qbo_to_erpnext("Invoice", untaxed, types.SimpleNamespace(company="Sapphire Fountains"))

	# Empty, but PRESENT: always mapping the key is what makes a re-sync replace the
	# child table rather than leave a stale tax row behind.
	assert values["taxes"] == []
	assert "taxes" in values


def test_unresolvable_tax_account_omits_the_row_and_parks_the_invoice(monkeypatch):
	"""No usable tax account: omit the charge and let the shortfall guard park it.

	Deliberately never falls back to a guessed account -- a wrong-but-plausible invoice
	posts to the ledger and nobody looks at it again.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe, tax_account=None)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	_doctype, values = map_qbo_to_erpnext(
		"Invoice", INVOICE_I100549, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert values["taxes"] == []
	issues = validate_mapped_values(
		"Invoice", "Sales Invoice", values, include_doc_required=False, payload=INVOICE_I100549
	)
	assert any("does not reconcile to QuickBooks" in issue for issue in issues)
	assert any("25.56 of sales tax that could not be booked" in issue for issue in issues)


def test_group_or_disabled_tax_account_is_refused(monkeypatch):
	"""A group or disabled account is not postable, so it resolves to None like a missing one."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_tax_account

	settings = types.SimpleNamespace(company="Sapphire Fountains")
	_sales_tax_stub(monkeypatch, frappe, is_group=1)
	assert _sales_tax_account(settings) is None
	_sales_tax_stub(monkeypatch, frappe, disabled=1)
	assert _sales_tax_account(settings) is None
	_sales_tax_stub(monkeypatch, frappe)
	assert _sales_tax_account(settings) == _SALES_TAX_ACCOUNT


def test_configured_sales_tax_account_overrides_the_default_number(monkeypatch):
	"""The Settings field wins over the 25010 fallback, so the destination needs no deploy."""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_charges

	settings = types.SimpleNamespace(company="Sapphire Fountains", sales_tax_account="29999 - Other Tax - SF")
	assert _sales_charges(INVOICE_I100549, settings)[0]["account_head"] == "29999 - Other Tax - SF"


def test_unimported_tax_code_still_imports_the_tax_amount(monkeypatch):
	"""TaxCode is not a CDC entity, so a new one only arrives on a full import.

	Until then its invoices must still carry the correct tax AMOUNT -- only the
	jurisdiction label is deferred.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_charges

	unknown = json.loads(json.dumps(INVOICE_I100549))
	unknown["TxnTaxDetail"]["TxnTaxCodeRef"] = {"value": "9999"}  # never imported
	row = _sales_charges(unknown, types.SimpleNamespace(company="Sapphire Fountains"))[0]

	assert row["tax_amount"] == 25.56
	assert row["description"] == "Sales Tax"


def test_sales_receipt_inherits_the_tax_mapping(monkeypatch):
	"""_map_sales_receipt delegates to _map_sales_invoice, so it gets taxes for free."""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	doctype, values = map_qbo_to_erpnext(
		"SalesReceipt", INVOICE_I100549, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert doctype == "Sales Invoice"
	assert values["taxes"][0]["tax_amount"] == 25.56
	assert "Sales Receipt" in values["remarks"]


def test_sales_invoice_shortfall_guard_names_unmapped_items(monkeypatch):
	"""An unimported item leaves the invoice short, and the guard says which item."""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe, item_code=None)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	_doctype, values = map_qbo_to_erpnext(
		"Invoice", INVOICE_I100549, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert values["items"] == []
	issues = validate_mapped_values(
		"Invoice", "Sales Invoice", values, include_doc_required=False, payload=INVOICE_I100549
	)
	assert any("reference QuickBooks items not imported" in issue for issue in issues)


def test_qbo_discount_maps_to_the_header_discount_amount(monkeypatch):
	"""A QBO DiscountLineDetail becomes ERPNext's header discount, and the invoice reconciles.

	QuickBooks models a discount as a transaction-level line with a POSITIVE Amount that
	is subtracted from the subtotal, so it maps to Sales Invoice.discount_amount with
	apply_discount_on "Net Total" -- not spread across item rows (QBO records no per-line
	discount to spread) and not as a negative tax charge (that would post a discount into
	a tax account). 36 pre-2026 invoices carry one, $89,561.00 in total.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	discounted = json.loads(json.dumps(INVOICE_I100549))
	discounted["Line"].append(
		{"Amount": 50.0, "DetailType": "DiscountLineDetail", "DiscountLineDetail": {"PercentBased": False}}
	)
	discounted["TotalAmt"] = 335.56  # 360.00 items - 50.00 discount + 25.56 tax
	_doctype, values = map_qbo_to_erpnext(
		"Invoice", discounted, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert values["discount_amount"] == 50.0
	assert values["apply_discount_on"] == "Net Total"
	# The guard nets the discount off, so a discounted invoice now reconciles.
	assert (
		validate_mapped_values(
			"Invoice", "Sales Invoice", values, include_doc_required=False, payload=discounted
		)
		== []
	)


def test_percent_based_qbo_discount_uses_the_amount_quickbooks_resolved(monkeypatch):
	"""QBO already resolves a percentage to an amount, so the mapper never recomputes it.

	I100725 records 33% of $150.00 as an Amount of 49.50. Re-deriving it from
	DiscountPercent would risk disagreeing with QuickBooks in the last cent.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_discount

	payload = {
		"Line": [
			{
				"Amount": 49.5,
				"DetailType": "DiscountLineDetail",
				"DiscountLineDetail": {"PercentBased": True, "DiscountPercent": 33},
			}
		]
	}
	assert _sales_discount(payload) == 49.5
	assert _sales_discount({"Line": []}) == 0


# I101635's real shape, trimmed to one ordinary line plus its billable-expense passthrough
# lines. QuickBooks writes these when a Bill line flagged billable to a customer is
# reinvoiced: a SalesItemLineDetail whose ItemRef is EMPTY, naming its destination account
# on ItemAccountRef instead, with MarkupInfo/ServiceDate marking it as a reimbursement.
# Note the zero-amount passthrough row and the SubTotalLineDetail carrying a real amount --
# both are in the production payloads and both must be ignored.
_PASSTHROUGH_ACCOUNTS = {
	"288": "52100 - Service Materials - SF",
	"196": "46300 - Markup on Billable Expenses - SF",
}

INVOICE_I101635 = {
	"Id": "21509",
	"DocNumber": "I101635",
	"TxnDate": "2025-06-11",
	"CustomerRef": {"name": "Myers Mortuary", "value": "1380"},
	"CurrencyRef": {"name": "United States Dollar", "value": "USD"},
	"Line": [
		{
			"Amount": 360.00,
			"Description": "Fountain service call",
			"DetailType": "SalesItemLineDetail",
			"SalesItemLineDetail": {"ItemRef": {"name": "Service", "value": "279"}, "Qty": 1, "UnitPrice": 360},
		},
		{
			"Amount": 70.26,
			"Description": "HAS15841 HASA MURIATIC ACID DISPOSABLE S",
			"DetailType": "SalesItemLineDetail",
			"SalesItemLineDetail": {
				"ItemRef": {},
				"ItemAccountRef": {"name": "52100 Service COGS:Service Materials", "value": "288"},
				"MarkupInfo": {"PercentBased": True, "Percent": 25},
				"ServiceDate": "2025-06-09",
			},
		},
		{
			"Amount": 17.57,
			"Description": "25% markup for HAS15841 HASA MURIATIC ACID DISPOSABLE S",
			"DetailType": "SalesItemLineDetail",
			"SalesItemLineDetail": {
				"ItemRef": {},
				"ItemAccountRef": {"name": "46300 Service Income:Markup on Billable Expenses", "value": "196"},
				"MarkupInfo": {"PercentBased": True, "Percent": 25},
			},
		},
		{
			"Amount": 0,
			"Description": "placeholder",
			"DetailType": "SalesItemLineDetail",
			"SalesItemLineDetail": {"ItemRef": {}, "ItemAccountRef": {"value": "288"}},
		},
		{"Amount": 447.83, "DetailType": "SubTotalLineDetail", "SubTotalLineDetail": {}},
	],
	"TxnTaxDetail": {"TotalTax": 25.56, "TxnTaxCodeRef": {"value": "8"}},
	"TotalAmt": 473.39,  # 360.00 items + 70.26 + 17.57 passthrough + 25.56 tax
}


def test_billable_expense_lines_become_charges_on_the_account_quickbooks_named(monkeypatch):
	"""A passthrough line books to its own ItemAccountRef, exactly as _purchase_charges does.

	``_sales_items`` requires a resolvable ItemRef, so these lines were dropped in silence:
	1,035 lines across 158 invoices, $33,024.34. Crediting 52100 reduces the COGS the
	expense was originally booked to -- which is what reimbursing a billable expense means
	-- while the markup credits income.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe, accounts=_PASSTHROUGH_ACCOUNTS)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	_doctype, values = map_qbo_to_erpnext(
		"Invoice", INVOICE_I101635, types.SimpleNamespace(company="Sapphire Fountains")
	)

	# Only the ordinary line becomes an item row; the passthrough lines become charges.
	assert [row["item_code"] for row in values["items"]] == ["SERVICE - MAINTENANCE CONTRACT"]
	# Billable expenses first, tax last -- and the zero-amount placeholder is dropped.
	assert values["taxes"] == [
		{
			"charge_type": "Actual",
			"account_head": "52100 - Service Materials - SF",
			"description": "HAS15841 HASA MURIATIC ACID DISPOSABLE S",
			"tax_amount": 70.26,
		},
		{
			"charge_type": "Actual",
			"account_head": "46300 - Markup on Billable Expenses - SF",
			"description": "25% markup for HAS15841 HASA MURIATIC ACID DISPOSABLE S",
			"tax_amount": 17.57,
		},
		{
			"charge_type": "Actual",
			"account_head": _SALES_TAX_ACCOUNT,
			"description": "Utah - Weber - Ogden - Inactive - SF",
			"tax_amount": 25.56,
		},
	]
	# Purchase-only fields stay absent here too; Frappe would drop them silently.
	assert all("category" not in row and "add_deduct_tax" not in row for row in values["taxes"])


def test_billable_expense_charges_reconcile_the_invoice_to_the_qbo_total(monkeypatch):
	"""End to end: items + passthrough + tax == TotalAmt, so the guard passes."""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe, accounts=_PASSTHROUGH_ACCOUNTS)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	_doctype, values = map_qbo_to_erpnext(
		"Invoice", INVOICE_I101635, types.SimpleNamespace(company="Sapphire Fountains")
	)

	mapped = sum(round(row["qty"] * row["rate"], 2) for row in values["items"])
	mapped += sum(row["tax_amount"] for row in values["taxes"])
	assert round(mapped, 2) == INVOICE_I101635["TotalAmt"]
	assert (
		validate_mapped_values(
			"Invoice", "Sales Invoice", values, include_doc_required=False, payload=INVOICE_I101635
		)
		== []
	)


def test_a_line_naming_an_unimported_item_is_not_treated_as_passthrough(monkeypatch):
	"""The discrimination that makes this safe: no ItemRef VALUE, not "ItemRef didn't resolve".

	Routing an unimported Item's line to its ItemAccountRef would book it to an account and
	bury the missing Item forever. It must keep parking the invoice and naming the Item.
	Over every cached payload the two populations do not overlap: 1,035 lines carry no
	ItemRef value and zero lines name an unimported Item.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe, item_code=None, accounts=_PASSTHROUGH_ACCOUNTS)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	_doctype, values = map_qbo_to_erpnext(
		"Invoice", INVOICE_I101635, types.SimpleNamespace(company="Sapphire Fountains")
	)

	# Item 279 did not resolve -- and it is NOT quietly rerouted to a charge.
	assert values["items"] == []
	assert [row["tax_amount"] for row in values["taxes"]] == [70.26, 17.57, 25.56]
	issues = validate_mapped_values(
		"Invoice", "Sales Invoice", values, include_doc_required=False, payload=INVOICE_I101635
	)
	assert any("1 line(s) totalling 360.00 reference QuickBooks items not imported" in i for i in issues)


def test_shortfall_guard_no_longer_blames_missing_items_for_passthrough_lines(monkeypatch):
	"""The message bug this release fixes: passthrough lines were reported as missing Items.

	The cause tested only whether ItemRef RESOLVED, so a line carrying no ItemRef at all was
	reported as referencing "QuickBooks items not imported into ERPNext" -- on all 157
	invoices the first post-fix resync parked, while all 265 QBO Items were in fact imported.
	The ids even rendered as ``None, None``, which was the only visible tell.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe, accounts=_PASSTHROUGH_ACCOUNTS)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	# Everything maps, but QuickBooks says the invoice is $100 bigger, so the guard fires.
	short = json.loads(json.dumps(INVOICE_I101635))
	short["TotalAmt"] = 573.39
	_doctype, values = map_qbo_to_erpnext("Invoice", short, types.SimpleNamespace(company="Sapphire Fountains"))
	issues = validate_mapped_values(
		"Invoice", "Sales Invoice", values, include_doc_required=False, payload=short
	)

	assert any("does not reconcile to QuickBooks" in i for i in issues)
	assert not any("not imported into ERPNext" in i for i in issues)
	assert not any("None" in i for i in issues)
	# With nothing left unexplained it falls to the caller's catch-all.
	assert any("Every QuickBooks line on this invoice was carried across" in i for i in issues)


def test_unbookable_billable_expense_line_is_omitted_and_named_as_its_own_cause(monkeypatch):
	"""An unresolvable ItemAccountRef omits the charge and parks the invoice naming the ACCOUNT.

	Fixed by importing or mapping the account, not by importing an item, so it reads as a
	separate cause from a missing Item.
	"""
	frappe = install_frappe_stub()
	# 196 (the markup account) resolves; 288 does not.
	_sales_tax_stub(monkeypatch, frappe, accounts={"196": "46300 - Markup on Billable Expenses - SF"})
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	_doctype, values = map_qbo_to_erpnext(
		"Invoice", INVOICE_I101635, types.SimpleNamespace(company="Sapphire Fountains")
	)

	assert [row["tax_amount"] for row in values["taxes"]] == [17.57, 25.56]
	issues = validate_mapped_values(
		"Invoice", "Sales Invoice", values, include_doc_required=False, payload=INVOICE_I101635
	)
	# The zero-amount placeholder on the same account is not counted as a lost line.
	assert any("1 billable-expense line(s) totalling 70.26 could not be booked" in i for i in issues)
	assert any("288 (52100 Service COGS:Service Materials)" in i for i in issues)
	assert not any("not imported into ERPNext: None" in i for i in issues)


def test_billable_expense_account_redirects_a_group_to_its_general_child(monkeypatch):
	"""A passthrough line onto a GROUP account posts to its "- General" child, like every other.

	ERPNext refuses to submit a line naming a group account, and this path reaches a posting
	account, so it must go through ``_ledger_for_posting`` rather than around it.
	"""
	frappe = install_frappe_stub()
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		_chart_resolver(qbo_accounts={"288": "60300 - Research & Development - SF", "196": None}),
	)
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_passthrough_charges

	charges = _sales_passthrough_charges(INVOICE_I101635)

	# 288 redirects to the "- General" ledger child; 196 maps to nothing and is omitted.
	assert [row["account_head"] for row in charges] == ["60301 - R&D - General - SF"]
	assert charges[0]["tax_amount"] == 70.26


def test_credit_memo_books_a_passthrough_line_to_the_account_qbo_named(monkeypatch):
	"""The Journal Entry twin: a passthrough line on a CreditMemo debits its own account.

	Dormant on this site -- CreditMemo gained a mapper in v1.244.0 and has no cached
	payloads yet -- but the line shape is identical, and leaving one of two identical paths
	unfixed is exactly how the zero-quantity bug survived a release.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe, accounts=_PASSTHROUGH_ACCOUNTS)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	memo = json.loads(json.dumps(INVOICE_I101635))
	memo["DocNumber"] = "CM-11"
	doctype, values = map_qbo_to_erpnext("CreditMemo", memo, types.SimpleNamespace(company="Sapphire Fountains"))

	assert doctype == "Journal Entry"
	rows = _rows_by_account(values)
	assert rows["1310 - Debtors - SF"]["credit_in_account_currency"] == 473.39
	assert rows["4110 - Sales - SF"]["debit_in_account_currency"] == 360.00
	assert rows["52100 - Service Materials - SF"]["debit_in_account_currency"] == 70.26
	assert rows["46300 - Markup on Billable Expenses - SF"]["debit_in_account_currency"] == 17.57
	assert sum(r["debit_in_account_currency"] for r in values["accounts"]) == sum(
		r["credit_in_account_currency"] for r in values["accounts"]
	)
	assert (
		validate_mapped_values("CreditMemo", "Journal Entry", values, include_doc_required=False, payload=memo)
		== []
	)


def test_sales_invoice_shortfall_guard_uses_qty_times_rate_not_the_carried_amount(monkeypatch):
	"""The guard sums qty * rate, because that is what ERPNext recomputes and posts.

	A line whose carried ``amount`` disagrees with ``qty * rate`` would sail past a guard
	that trusted ``amount`` -- and ``amount`` is exactly the field ERPNext overwrites.
	"""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_invoice_shortfall

	values = {
		"items": [{"qty": 2, "rate": 100.0, "amount": 500.0}],  # amount lies; qty*rate is 200
		"taxes": [],
	}
	issue = _sales_invoice_shortfall("Sales Invoice", values, {"TotalAmt": 500.0})

	assert issue is not None
	assert "mapped 200.00 vs TotalAmt 500.00" in issue


def test_sales_invoice_shortfall_guard_skipped_without_a_qbo_total():
	"""No TotalAmt means nothing authoritative to reconcile against; do not invent one."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_invoice_shortfall

	values = {"items": [{"qty": 1, "rate": 10.0}], "taxes": []}
	assert _sales_invoice_shortfall("Sales Invoice", values, {}) is None
	assert _sales_invoice_shortfall("Sales Invoice", values, None) is None
	# And it never fires on a doctype it does not own.
	assert _sales_invoice_shortfall("Purchase Invoice", values, {"TotalAmt": 999.0}) is None


def test_credit_memo_tax_leg_balances_the_journal_entry(monkeypatch):
	"""A taxed CreditMemo credits A/R for the tax-inclusive total, so it needs a tax debit."""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe)
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext, validate_mapped_values

	memo = json.loads(json.dumps(INVOICE_I100549))
	memo["DocNumber"] = "CM-9"
	doctype, values = map_qbo_to_erpnext("CreditMemo", memo, types.SimpleNamespace(company="Sapphire Fountains"))

	assert doctype == "Journal Entry"
	rows = _rows_by_account(values)
	assert rows["1310 - Debtors - SF"]["credit_in_account_currency"] == 385.56
	assert rows["4110 - Sales - SF"]["debit_in_account_currency"] == 360.00
	assert rows[_SALES_TAX_ACCOUNT]["debit_in_account_currency"] == 25.56
	assert sum(r["debit_in_account_currency"] for r in values["accounts"]) == sum(
		r["credit_in_account_currency"] for r in values["accounts"]
	)
	assert (
		validate_mapped_values("CreditMemo", "Journal Entry", values, include_doc_required=False, payload=memo)
		== []
	)


def test_refund_receipt_tax_leg_balances_the_journal_entry(monkeypatch):
	"""Same for a RefundReceipt, whose credit leg is the bank rather than A/R."""
	frappe = install_frappe_stub()
	_sales_tax_stub(monkeypatch, frappe, accounts={"134": "1110 - Checking - SF"})
	from erpnext_enhancements.quickbooks_online.core.mapping import map_qbo_to_erpnext

	refund = json.loads(json.dumps(INVOICE_I100549))
	refund["DepositToAccountRef"] = {"value": "134"}
	_doctype, values = map_qbo_to_erpnext(
		"RefundReceipt", refund, types.SimpleNamespace(company="Sapphire Fountains")
	)

	rows = _rows_by_account(values)
	assert rows["1110 - Checking - SF"]["credit_in_account_currency"] == 385.56
	assert rows["4110 - Sales - SF"]["debit_in_account_currency"] == 360.00
	assert rows[_SALES_TAX_ACCOUNT]["debit_in_account_currency"] == 25.56
	assert sum(r["debit_in_account_currency"] for r in values["accounts"]) == sum(
		r["credit_in_account_currency"] for r in values["accounts"]
	)


def test_shortfall_guard_rounds_the_way_erpnext_does_before_multiplying():
	"""The guard must reproduce ERPNext's rounding ORDER, not just round somewhere.

	ERPNext's ``calculate_item_values`` calls ``round_floats_in(item)`` FIRST -- rate to
	currency precision, qty to float precision -- and only then multiplies. Rounding after
	multiplying blesses invoices that are wrong.

	This is QBO invoice I101613 (Id 21151), real production data: two lines whose
	quantities carry seven decimals. ERPNext stores qty 0.667 and posts $54,527.25 against
	QuickBooks' $54,502.72 -- $24.53 out. A multiply-then-round guard computes exactly
	54,502.72 and calls it reconciled. The predicted 54,527.25 is the grand_total already
	sitting on the imported draft, so this is not a hypothetical.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_invoice_shortfall

	values = {
		"items": [
			{"qty": 0.6666999, "rate": 69250.0},
			{"qty": 0.6667, "rate": 12500.0},
		],
		"taxes": [],
	}
	issue = _sales_invoice_shortfall("Sales Invoice", values, {"TotalAmt": 54502.72})

	assert issue is not None, "guard must not bless an invoice ERPNext posts $24.53 higher"
	assert "mapped 54527.25 vs TotalAmt 54502.72" in issue
	assert "off by -24.53" in issue


def test_shortfall_guard_rounds_the_rate_erpnext_will_store():
	"""ERPNext stores rate at 2 dp, so the guard must reconcile against the STORED rate.

	QBO invoice I100352 prices a line at 2051.9872727; ERPNext stores 2051.99. At the real
	quantity that is a cent, now inside the rounding tolerance -- so this uses a quantity
	large enough for the rate rounding to clear it, which is what makes the assertion
	about the rounding MODEL rather than about the tolerance.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_invoice_shortfall

	values = {"items": [{"qty": 550, "rate": 2051.9872727}], "taxes": []}
	# Unrounded: 550 * 2051.9872727 == 1,128,593.00. ERPNext: 550 * 2051.99 == 1,128,594.50.
	issue = _sales_invoice_shortfall("Sales Invoice", values, {"TotalAmt": 1128593.00})

	assert issue is not None
	assert "mapped 1128594.50" in issue


def test_shortfall_guard_tolerates_a_penny_per_line_but_not_more():
	"""Rounding drift is bounded by half a cent per row, so a per-line tolerance is honest.

	One cent per item row, floor two cents -- tight enough that a 2-line invoice still
	parks over real differences, loose enough that sub-10c rounding on a long invoice
	does not generate noise nobody reads.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import (
		_sales_invoice_shortfall,
		_sales_rounding_tolerance,
	)

	assert _sales_rounding_tolerance([]) == 0.02
	assert _sales_rounding_tolerance([{}] * 2) == 0.02
	assert round(_sales_rounding_tolerance([{}] * 17), 2) == 0.17

	two_line = {"items": [{"qty": 1, "rate": 10.0}, {"qty": 1, "rate": 10.0}], "taxes": []}
	assert _sales_invoice_shortfall("Sales Invoice", two_line, {"TotalAmt": 20.02}) is None
	# A real difference on the same short invoice still parks.
	assert _sales_invoice_shortfall("Sales Invoice", two_line, {"TotalAmt": 42.98}) is not None


def test_shortfall_guard_does_not_park_over_bankers_rounding():
	"""Half-to-even is ERPNext's rounding, so the guard must not use Python's round().

	Qty 0.5 at $12.35 -- half an hour of labour at an odd-cent rate -- is $6.175. ERPNext
	posts 6.18; Python's round() gives 6.17 and would park a perfectly correct invoice.
	"""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _sales_invoice_shortfall

	values = {"items": [{"qty": 0.5, "rate": 12.35}], "taxes": []}
	assert _sales_invoice_shortfall("Sales Invoice", values, {"TotalAmt": 6.18}) is None


def test_erpnext_item_precisions_fall_back_to_erpnext_defaults():
	"""Blank System Settings precisions mean ERPNext's own defaults, not zero."""
	frappe = install_frappe_stub()
	from erpnext_enhancements.quickbooks_online.core.mapping import _erpnext_item_precisions

	assert _erpnext_item_precisions() == (2, 3)

	frappe.db.get_single_value = lambda doctype, fieldname: {
		"currency_precision": 3,
		"float_precision": 4,
	}.get(fieldname)
	assert _erpnext_item_precisions() == (3, 4)
