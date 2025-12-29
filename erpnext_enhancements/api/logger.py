import frappe

@frappe.whitelist(allow_guest=True)
def log_client_error(error_message):
    """
    Log client-side errors to the server's Error Log.
    """
    frappe.log_error(title="Client-Side Error", message=error_message)
    return {"status": "success"}
