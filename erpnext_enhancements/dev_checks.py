# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Self-contained verification functions for a dev bench (``bench execute``).

``bench run-tests`` is broken under Python 3.14 on the dev bench (see
``product_configurator/dev_checks.py`` for the convention), so these are plain
functions that create data, assert, clean up, and return a result string:

    bench --site dev.localhost execute \
        erpnext_enhancements.dev_checks.check_last_activity_guard
    ... dev_checks.check_caller_upsert_idempotent

These cover the app-root hooks (``script_migrations``/``api``) that have no
bench-free harness.
"""

import frappe
from frappe.utils import today

TEST_PHONE = "+18015550999"


def _cleanup(phone=TEST_PHONE):
	for name in frappe.get_all(
		"Contact", filters={"custom_phone_number": phone}, pluck="name"
	):
		frappe.delete_doc("Contact", name, force=True, ignore_permissions=True)
	for name in frappe.get_all(
		"Customer", filters={"custom_accounts_phone_number": phone}, pluck="name"
	):
		frappe.delete_doc("Customer", name, force=True, ignore_permissions=True)


def _make_test_customer():
	cust = frappe.get_doc(
		{
			"doctype": "Customer",
			"customer_name": f"Unknown Caller - {TEST_PHONE}",
			"customer_type": "Residential",
			"custom_accounts_phone_number": TEST_PHONE,
		}
	)
	cust.flags.ignore_mandatory = True
	cust.insert(ignore_permissions=True)
	return cust


def check_last_activity_guard():
	"""A no-op re-save neither re-stamps custom_last_activity_date nor mints a
	Version; a real field edit still stamps today."""
	_cleanup()
	cust = _make_test_customer()
	assert str(cust.custom_last_activity_date) == today(), "insert should stamp"

	# backdate the stamp without touching modified, then re-save unchanged
	frappe.db.set_value(
		"Customer",
		cust.name,
		"custom_last_activity_date",
		"2026-06-23",
		update_modified=False,
	)
	versions_before = frappe.db.count(
		"Version", {"ref_doctype": "Customer", "docname": cust.name}
	)

	doc = frappe.get_doc("Customer", cust.name)
	doc.save(ignore_permissions=True)  # no-op re-save
	stamped = frappe.db.get_value("Customer", cust.name, "custom_last_activity_date")
	assert str(stamped) == "2026-06-23", f"no-op save manufactured a stamp: {stamped}"
	versions_after = frappe.db.count(
		"Version", {"ref_doctype": "Customer", "docname": cust.name}
	)
	assert versions_after == versions_before, "no-op save minted a Version"

	doc = frappe.get_doc("Customer", cust.name)
	doc.customer_details = "real edit"
	doc.save(ignore_permissions=True)  # genuine change
	stamped = frappe.db.get_value("Customer", cust.name, "custom_last_activity_date")
	assert str(stamped) == today(), "a real edit must stamp today"

	_cleanup()
	frappe.db.commit()
	return "OK — stamp fires on create/real edits only"


def check_caller_upsert_idempotent():
	"""Repeated caller resolution and a same-name caller_resolved replay must
	not bump Customer/Contact ``modified`` or mint a Version."""
	import inspect

	from erpnext_enhancements.api import telephony

	# strip @frappe.whitelist + @validate_webhook_secret (both functools.wraps
	# layers): the secret check reads frappe.request, absent under bench execute
	update_caller_info = inspect.unwrap(telephony.update_caller_info)

	_cleanup()
	first = telephony._get_caller_info(TEST_PHONE)  # pass 1: auto-creates
	cust = first.get("customer")
	assert cust, "first pass should auto-create the Customer"
	modified_1 = str(frappe.db.get_value("Customer", cust, "modified"))

	second = telephony._get_caller_info(TEST_PHONE)  # pass 2: pure lookup
	assert second.get("customer") == cust, "lookup should return the same Customer"
	assert (
		str(frappe.db.get_value("Customer", cust, "modified")) == modified_1
	), "read-only caller lookup bumped modified"

	# genuine rename first
	renamed = update_caller_info(TEST_PHONE, "Testy McTester")
	assert renamed["updated"], "a real rename must report updated=True"
	contact = renamed.get("contact")

	cust_modified = str(frappe.db.get_value("Customer", cust, "modified"))
	cont_modified = contact and str(frappe.db.get_value("Contact", contact, "modified"))
	versions = frappe.db.count("Version", {"ref_doctype": "Customer", "docname": cust})

	# gateway replay with the SAME name — must write nothing
	replay = update_caller_info(TEST_PHONE, "Testy McTester")
	assert not replay["updated"], "same-name replay must report updated=False"
	assert (
		str(frappe.db.get_value("Customer", cust, "modified")) == cust_modified
	), "no-op replay bumped Customer.modified"
	if contact:
		assert (
			str(frappe.db.get_value("Contact", contact, "modified")) == cont_modified
		), "no-op replay bumped Contact.modified"
	assert (
		frappe.db.count("Version", {"ref_doctype": "Customer", "docname": cust})
		== versions
	), "no-op replay minted a Version"

	_cleanup()
	frappe.db.commit()
	return "OK — repeated caller resolution leaves Customer/Contact untouched"
