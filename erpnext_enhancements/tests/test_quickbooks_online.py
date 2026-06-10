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
from datetime import datetime
import hashlib
import hmac
import sys
import types


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
	sys.modules.setdefault("frappe", frappe)
	sys.modules.setdefault("frappe.utils", frappe_utils)
	sys.modules.setdefault("requests", types.ModuleType("requests"))
	return frappe


def test_ordered_entities_imports_masters_before_transactions():
	"""ordered_entities sorts master records (Account/Customer/Item) before transactions."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.sync import ordered_entities

	assert ordered_entities(["Invoice", "Customer", "Item", "Account"]) == [
		"Account",
		"Customer",
		"Item",
		"Invoice",
	]


def test_verify_intuit_signature_accepts_valid_hmac():
	"""verify_intuit_signature accepts a correct HMAC-SHA256 and rejects a wrong one."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.utils import (
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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.utils import parse_qbo_datetime

	assert parse_qbo_datetime("2025-04-28 10:25:02-07:00") == datetime(2025, 4, 28, 17, 25, 2)


def test_customer_mapping_uses_native_erpnext_fields():
	"""A QBO Customer maps onto native ERPNext Customer fields (name/type/group)."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import map_qbo_to_erpnext

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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import map_qbo_to_erpnext

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
	parent = types.SimpleNamespace(is_group=0, saved=False)
	parent.save = lambda **kwargs: setattr(parent, "saved", True)
	monkeypatch.setattr(
		frappe.db,
		"get_value",
		lambda doctype, name=None, fieldname=None, **kwargs: 0 if doctype == "Account" else None,
	)
	monkeypatch.setattr(frappe, "get_doc", lambda doctype, name: parent, raising=False)
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import (
		_ensure_group_parent,
	)

	_ensure_group_parent("Account", {"parent_account": "Job Expenses - SF"})

	assert parent.is_group == 1
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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import (
		_ensure_group_parent,
	)

	_ensure_group_parent("Account", {"parent_account": "Job Expenses - SF"})
	_ensure_group_parent("Customer", {"parent_account": "Job Expenses - SF"})
	_ensure_group_parent("Account", {})


def test_account_mapping_uses_existing_root_as_parent():
	"""A QBO Account maps under the matching ERPNext root account as a leaf."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import map_qbo_to_erpnext

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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import map_qbo_to_erpnext

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


def test_account_payload_query_marks_parents_without_polluting_raw_payload(monkeypatch):
	"""query_entity_payloads tags parents with _qbo_has_children but strips it from clean payloads."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online import sync

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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import map_qbo_to_erpnext

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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import map_qbo_to_erpnext

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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import validate_mapped_values

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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import validate_mapped_values

	assert validate_mapped_values("Bill", "Purchase Invoice", {"company": "Demo", "supplier": None, "items": []}) == [
		"Missing required field: items",
		"Missing required field: supplier",
	]


def test_customer_auto_match_uses_existing_customer_name():
	"""find_existing_match auto-matches a QBO customer to an existing one by name."""
	install_frappe_stub()
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.mapping import find_existing_match

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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.sync import _track_result

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
	from erpnext_enhancements.quickbooks_time_integration.quickbooks_online.sync import _track_result

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
