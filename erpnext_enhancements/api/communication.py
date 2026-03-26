import frappe


def after_insert_communication(doc, method=None):
    if doc.communication_medium == 'Email' and doc.sent_or_received == 'Received':
        frappe.enqueue(
            "erpnext_enhancements.api.communication.generate_draft_response",
            communication_name=doc.name,
            queue="long",
            enqueue_after_commit=True
        )


def generate_draft_response(communication_name):
    try:
        inbound_email = frappe.get_doc("Communication", communication_name)
    except frappe.DoesNotExistError:
        return

    # Fetch API Key and system prompts from Poseidon Settings
    try:
        settings = frappe.get_doc("Poseidon Settings")
    except frappe.DoesNotExistError:
        frappe.log_error(message="Poseidon Settings not found", title="Email Draft Generation Failed")
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

    prompt = f"""Using the following company guidelines and value stream context, draft a professional, helpful, and technically accurate response to the email below. Adhere to our persona as Poseidon.

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
