"""AI-assisted email/SMS reply drafting for Communication records.

Wires the "Triton" AI persona into inbound communications:
        - ``after_insert_communication`` is a Communication ``after_insert``
          doc-event hook (registered in hooks.py). For inbound emails it
          enqueues background draft generation.
        - ``generate_draft_response`` is the background worker (run on the
          "long" queue) that drafts a reply email.
        - ``suggest_sms_reply`` is a whitelisted endpoint called from
          ``public/js/communication.js`` to draft an SMS reply on demand.

Prompts/persona are read from the ``Triton Settings`` Single DocType and sent
to Vertex AI via ``erpnext_enhancements.api.gemini.generate_content_with_vertex_ai``.
Generated drafts are written as new Communication docs (and the model's
"thoughts" as a Comment) with ``ignore_permissions=True``, since the work runs
either in a background job or on behalf of an interactive user.
"""

import frappe
from frappe import _


def after_insert_communication(doc, method=None):
    """Communication ``after_insert`` hook: queue an AI draft for inbound email.

    Args:
        doc: The newly inserted Communication document.
        method: Frappe doc-event hook arg (unused).

    Side effects: when the communication is a *received* Email, enqueues
    ``generate_draft_response`` on the "long" queue (after commit). No-op for
    any other medium/direction.
    """
    if doc.communication_medium == 'Email' and doc.sent_or_received == 'Received':
        frappe.enqueue(
            "erpnext_enhancements.api.communication.generate_draft_response",
            communication_name=doc.name,
            queue="long",
            enqueue_after_commit=True
        )


def generate_draft_response(communication_name):
    """Background worker: draft an AI reply to an inbound email Communication.

    Args:
        communication_name (str): Name of the received-email Communication.

    Behaviour: loads Triton Settings (master prompt + per-value-stream
    guidelines), builds a system instruction + prompt, and calls Vertex AI. On
    success it inserts a new *Draft* outbound Communication (HTML, ``in_reply_to``
    the original) and, if the model returned reasoning, a Comment containing the
    AI "thoughts".

    Side effects: external Vertex AI call; inserts Communication + optional
    Comment with ``ignore_permissions=True``. Silently returns (logging to the
    Error Log) if the source doc, Triton Settings, or the AI call is missing/fails.
    Not whitelisted — invoked only via the enqueue in ``after_insert_communication``.
    """
    try:
        inbound_email = frappe.get_doc("Communication", communication_name)
    except frappe.DoesNotExistError:
        return

    # Fetch API Key and system prompts from Triton Settings
    try:
        settings = frappe.get_doc("Triton Settings")
    except frappe.DoesNotExistError:
        frappe.log_error(message="Triton Settings not found", title="Email Draft Generation Failed")
        return

    # Master system prompt and relevant Value Stream Guidelines
    master_prompt = settings.master_system_prompt or ""
    design_guidelines = settings.design_guidelines or ""
    build_guidelines = settings.build_guidelines or ""
    rent_guidelines = settings.rent_guidelines or ""
    service_guidelines = settings.service_guidelines or ""

    system_instruction = f"""{master_prompt}

Value Stream Guidelines:
Design: {design_guidelines}
Build: {build_guidelines}
Rent: {rent_guidelines}
Service: {service_guidelines}
"""

    prompt = f"""Using the following company guidelines and value stream context, draft a professional, helpful, and technically accurate response to the email below. Adhere to our persona as Triton.

Email Subject: {inbound_email.subject}
Email Content:
{inbound_email.content}
"""

    try:
        from erpnext_enhancements.api.gemini import generate_content_with_vertex_ai
        response_text, thoughts = generate_content_with_vertex_ai(prompt, system_instruction, settings)
    except Exception as e:
        frappe.log_error(message=str(e), title="Vertex AI Communication Generation Failed")
        return

    # Create new Communication record
    subject = inbound_email.subject
    if subject and not subject.lower().startswith("re: "):
        subject = f"Re: {subject}"
    elif not subject:
        subject = "Re: "

    new_comm = frappe.get_doc({
        "doctype": "Communication",
        "communication_medium": "Email",
        "sent_or_received": "Sent",
        "subject": subject,
        "content": response_text,
        "status": "Draft",
        "reference_doctype": inbound_email.reference_doctype,
        "reference_name": inbound_email.reference_name,
        "in_reply_to": inbound_email.name,
        # Ensure the content is fully editable in the Frappe Email UI
        "content_type": "HTML"
    })

    new_comm.insert(ignore_permissions=True)

    # Insert a new Comment linked to the new draft Communication containing the "thoughts"
    if thoughts:
        frappe.get_doc({
            "doctype": "Comment",
            "comment_type": "Comment",
            "reference_doctype": "Communication",
            "reference_name": new_comm.name,
            "content": f"<b>Thoughts from AI:</b><br>{thoughts}"
        }).insert(ignore_permissions=True)


@frappe.whitelist()
def suggest_sms_reply(communication_name):
    """Whitelisted: draft a short AI SMS reply for an inbound SMS Communication.

    Args:
        communication_name (str): Name of the SMS Communication to reply to.

    Validates the record exists and has ``communication_medium == "SMS"``
    (else ``frappe.throw``). Builds the Triton prompt and calls Vertex AI.

    Returns:
        dict: ``{"status": "success", "suggested_reply": <text>}``.

    Side effects: external Vertex AI call; if the model returned reasoning,
    inserts a Comment with the AI "thoughts" on the SMS and commits. Called
    from ``public/js/communication.js``.
    """
    try:
        inbound_sms = frappe.get_doc("Communication", communication_name)
    except frappe.DoesNotExistError:
        frappe.throw(_("Communication record not found"))

    if inbound_sms.communication_medium != "SMS":
        frappe.throw(_("Can only suggest replies for SMS communications"))

    try:
        settings = frappe.get_doc("Triton Settings")
    except frappe.DoesNotExistError:
        frappe.throw(_("Triton Settings not found"))

    master_prompt = settings.master_system_prompt or ""
    design_guidelines = settings.design_guidelines or ""
    build_guidelines = settings.build_guidelines or ""
    rent_guidelines = settings.rent_guidelines or ""
    service_guidelines = settings.service_guidelines or ""

    system_instruction = f"""{master_prompt}

Value Stream Guidelines:
Design: {design_guidelines}
Build: {build_guidelines}
Rent: {rent_guidelines}
Service: {service_guidelines}
"""

    prompt = f"""Using the following company guidelines and value stream context, draft a professional, helpful, and concise SMS response to the message below. Keep it short as it is an SMS.

Sender: {inbound_sms.sender}
Message Content:
{inbound_sms.content}
"""

    try:
        from erpnext_enhancements.api.gemini import generate_content_with_vertex_ai
        response_text, thoughts = generate_content_with_vertex_ai(prompt, system_instruction, settings)
    except Exception as e:
        frappe.log_error(message=str(e), title="Vertex AI SMS Generation Failed")
        frappe.throw(_("Failed to generate AI response. Please try again."))

    if thoughts:
        frappe.get_doc({
            "doctype": "Comment",
            "comment_type": "Comment",
            "reference_doctype": "Communication",
            "reference_name": inbound_sms.name,
            "content": f"<b>Thoughts from AI:</b><br>{thoughts}"
        }).insert(ignore_permissions=True)
        frappe.db.commit()

    return {"status": "success", "suggested_reply": response_text}
