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
import hashlib
import hmac
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
		try:
			number = float(value or 0)
		except (TypeError, ValueError):
			return 0.0
		return round(number, precision) if precision is not None else number

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
	# Passthrough decorator so the @frappe.whitelist() RPC layer is importable.
	frappe.whitelist = lambda *args, **kwargs: (lambda fn: fn)
	sys.modules.setdefault("frappe", frappe)
	sys.modules.setdefault("frappe.utils", frappe_utils)
	sys.modules.setdefault("requests", types.ModuleType("requests"))
	return frappe


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
