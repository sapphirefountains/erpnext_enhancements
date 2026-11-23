"""Email from a Water Feature Design goes out in the email design system's chrome (phase G, Send design).

frappe's composer is a rich-text editor, so Send design hands it plain paragraphs
(``api/designer.email_draft``). The chrome (``email_style.wrap``, the Design pillar) is put on here, in
the Communication's ``after_insert``, **in memory only**:

* ``frappe.core.doctype.communication.email.make`` inserts the Communication and then calls
  ``send_email`` on the same object, which builds the outgoing mail from ``self.content``. So the
  recipient gets the shell, with its ``<style>`` block and Outlook comments.
* It cannot go on earlier: ``before_insert`` runs ahead of ``Document._sanitize_content``, which strips
  the ``<style>`` and the comments from a Text Editor field before the mail is built.
* The stored Communication keeps the plain text, so the Desk timeline never renders the email's CSS.

A module of its own, light on imports, because the hook runs for every Communication on the site.
"""

import frappe

DESIGN_DOCTYPE = "Water Feature Design"


def wrap_design_email(doc, method=None):
    """Communication ``after_insert``: put an email sent from a Water Feature Design into the email
    chrome for sending. Returns at once for anything else; never raises (a failed wrap must not stop
    an email, which then goes out as the person wrote it)."""
    try:
        if doc.get("reference_doctype") != DESIGN_DOCTYPE or doc.get("communication_medium") != "Email":
            return
        if doc.get("sent_or_received") != "Sent":
            return
        from erpnext_enhancements import email_style

        subject = str(doc.get("subject") or "").split(" (#")[0]
        # No eyebrow: the shell already names the Design pillar beside the wordmark.
        doc.content = email_style.wrap(doc.get("content") or "", title=subject, pillar="design", tagline=True)
    except Exception:
        frappe.log_error(title="Water Feature Design email wrap", message=frappe.get_traceback())
