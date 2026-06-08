import frappe

# Source Server Script: "Debug Customer Link Query" (API method run_debug_query).
#
# NOTE: As a native whitelisted method the endpoint changes from
#   /api/method/run_debug_query
# to
#   /api/method/erpnext_enhancements.script_migrations.debug.run_debug_query
# This is a developer debugging helper and can be removed if no longer needed.


@frappe.whitelist()
def run_debug_query(customer_name=None):
	"""Return the DocLink rows pointing at a given Customer."""
	customer_name = customer_name or frappe.form_dict.get("customer_name")

	if not customer_name:
		return {
			"error": "Please provide a customer_name. Example: ?customer_name=CUST-00001"
		}

	return frappe.get_all(
		"DocLink",
		filters={"linked_doctype": "Customer", "linked_name": customer_name},
		fields=["link_doctype", "link_name", "linked_doctype", "linked_name"],
	)
