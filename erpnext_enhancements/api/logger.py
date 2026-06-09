"""Client-side error reporting endpoint.

Single whitelisted helper that lets browser JS forward unhandled front-end
errors into the server Error Log for diagnostics.

Security: ``allow_guest=True`` — callable WITHOUT an authenticated session, so
it can capture errors that occur on guest/login pages. It only writes to the
Error Log and returns a fixed status, so the blast radius is limited, but note
the message is attacker-controllable (do not trust its contents).
"""

import frappe

@frappe.whitelist(allow_guest=True)
def log_client_error(error_message):
    """
    Log client-side errors to the server's Error Log.

    Args:
        error_message (str): The browser-supplied error text/stack.

    Returns:
        dict: ``{"status": "success"}``.

    Side effects: writes one Error Log entry titled "Client-Side Error".
    Guest-accessible (``allow_guest=True``); the message is untrusted input.
    """
    frappe.log_error(title="Client-Side Error", message=error_message)
    return {"status": "success"}
