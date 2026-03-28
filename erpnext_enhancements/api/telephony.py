import frappe
from frappe import _
import requests
import json
import re
import os
import base64
import google.generativeai as genai
from twilio.request_validator import RequestValidator
from urllib.parse import urlparse
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

def get_poseidon_settings(field):
    return frappe.db.get_single_value("Poseidon Settings", field)

def validate_twilio_request(func):
    def wrapper(*args, **kwargs):
        validator = RequestValidator(get_poseidon_settings("twilio_auth_token") or "")
        url = frappe.request.url
        post_vars = frappe.request.form
        signature = frappe.request.headers.get("X-Twilio-Signature", "")

        if not validator.validate(url, post_vars, signature):
            frappe.throw(_("Invalid Twilio Signature"), frappe.PermissionError)
        return func(*args, **kwargs)
    return wrapper

def validate_webhook_secret(func):
    def wrapper(*args, **kwargs):
        secret = get_poseidon_settings("admin_webhook_secret") or ""
        auth_header = frappe.request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer ") and not auth_header.startswith("token "):
            frappe.throw(_("Missing or Invalid Authorization Header"), frappe.PermissionError)
            
        token = auth_header.split(" ")[1] if " " in auth_header else auth_header
        if not auth_header.startswith("token ") and token != secret:
            frappe.throw(_("Invalid Webhook Secret"), frappe.PermissionError)

        return func(*args, **kwargs)
    return wrapper

@frappe.whitelist(allow_guest=True)
def get_caller_info(phone_number):
    frappe.set_user("poseidon@sapphirefountains.com")
    
    if not phone_number:
        return {"customer": None, "contact": None, "display_name": "Unknown Caller", "context": []}

    clean_number = re.sub(r'\D', '', phone_number)
    match_suffix = clean_number[-10:] if len(clean_number) >= 10 else clean_number
    fuzzy_regex = ".*".join(list(match_suffix))

    contact_name = None
    customer_name = None
    display_name = None

    # 1. Search Contacts via custom_phone_number
    contacts = frappe.db.sql("""
        SELECT name, first_name, last_name FROM `tabContact` 
        WHERE custom_phone_number REGEXP %s 
        LIMIT 1""", (fuzzy_regex,), as_dict=True)
    
    if contacts:
        contact_name = contacts[0].name
        display_name = f"{contacts[0].first_name or ''} {contacts[0].last_name or ''}".strip()
        links = frappe.get_all("Dynamic Link", filters={"parent": contact_name, "parenttype": "Contact", "link_doctype": "Customer"}, fields=["link_name"])
        if links:
            customer_name = links[0].link_name

    # 2. Search Customers via custom_accounts_phone_number
    if not customer_name:
        customers = frappe.db.sql("""
            SELECT name, customer_name FROM `tabCustomer` 
            WHERE custom_accounts_phone_number REGEXP %s 
            LIMIT 1""", (fuzzy_regex,), as_dict=True)
        if customers:
            customer_name = customers[0].name
            display_name = customers[0].customer_name

    # 3. Create missing records if absolutely no match is found
    if not customer_name and not contact_name:
        cust = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": f"Unknown Caller - {phone_number}",
            "customer_type": "Residential",
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
            "custom_accounts_phone_number": phone_number
        })
        cust.insert(ignore_permissions=True)
        customer_name = cust.name
        display_name = cust.customer_name

        cont = frappe.get_doc({
            "doctype": "Contact",
            "first_name": f"Caller",
            "last_name": phone_number,
            "custom_phone_number": phone_number,
            "is_primary_contact": 1
        })
        cont.append("links", {
            "link_doctype": "Customer",
            "link_name": customer_name
        })
        cont.insert(ignore_permissions=True)
        contact_name = cont.name
    else:
        if not display_name and customer_name:
            display_name = frappe.db.get_value("Customer", customer_name, "customer_name")
        elif not display_name and contact_name:
            first, last = frappe.db.get_value("Contact", contact_name, ["first_name", "last_name"])
            display_name = f"{first or ''} {last or ''}".strip()

    context_items = []
    if customer_name:
        opps = frappe.get_all("Opportunity", 
            filters={"party_name": customer_name, "status": ["not in", ["Closed", "Lost"]]}, 
            fields=["name", "opportunity_from", "title"])
        for o in opps:
            context_items.append(f"Opportunity: {o.title or o.name}")

        projs = frappe.get_all("Project", 
            filters={"customer": customer_name, "status": ["!=", "Completed"]}, 
            fields=["name", "project_name"])
        for p in projs:
            context_items.append(f"Project: {p.project_name or p.name}")

    frappe.db.commit()

    return {
        "customer": customer_name,
        "contact": contact_name,
        "display_name": display_name or "Unknown Caller",
        "context": context_items
    }

@frappe.whitelist(allow_guest=True)
def update_caller_info(phone_number, new_name):
    frappe.set_user("poseidon@sapphirefountains.com")
    
    info = get_caller_info(phone_number)
    customer_name = info.get("customer")
    contact_name = info.get("contact")

    if customer_name:
        frappe.db.set_value("Customer", customer_name, "customer_name", new_name)
    else:
        cust = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": new_name,
            "customer_type": "Residential",
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
            "custom_accounts_phone_number": phone_number
        })
        cust.insert(ignore_permissions=True)
        customer_name = cust.name

    parts = new_name.split(" ", 1)
    first = parts[0]
    last = parts[1] if len(parts) > 1 else ""

    if contact_name:
        frappe.db.set_value("Contact", contact_name, "first_name", first)
        frappe.db.set_value("Contact", contact_name, "last_name", last)
    else:
        cont = frappe.get_doc({
            "doctype": "Contact",
            "first_name": first,
            "last_name": last or phone_number,
            "custom_phone_number": phone_number,
            "is_primary_contact": 1
        })
        cont.append("links", {"link_doctype": "Customer", "link_name": customer_name})
        cont.insert(ignore_permissions=True)
        contact_name = cont.name
    
    frappe.db.commit()
    return {"status": "success", "customer": customer_name, "contact": contact_name}

def locate_customer(phone_number):
    info = get_caller_info(phone_number)
    return info.get("customer")

@frappe.whitelist()
def log_call_transcript(call_sid, transcript, caller_number=None):
    frappe.set_user("poseidon@sapphirefountains.com")
    
    if not call_sid or not transcript:
        frappe.throw("Missing call_sid or transcript")

    try:
        customer_name, contact_name = None, None
        if caller_number:
            info = get_caller_info(caller_number)
            customer_name = info.get('customer')
            contact_name = info.get('contact')

        comm = frappe.get_doc({
            "doctype": "Communication",
            "communication_medium": "Phone",
            "sent_or_received": "Received",
            "sender": "poseidon@sapphirefountains.com",
            "sender_full_name": "Poseidon",
            "subject": f"Poseidon Live Transcript ({call_sid})",
            "content": f"<pre>{transcript}</pre>",
            "status": "Linked",
            "reference_doctype": "Customer" if customer_name else None,
            "reference_name": customer_name,
            "communication_date": frappe.utils.now_datetime()
        })

        if contact_name:
            comm.append("timeline_links", {
                "link_doctype": "Contact",
                "link_name": contact_name
            })

        comm.insert(ignore_permissions=True)
        frappe.db.commit()
        return {"status": "success", "communication_id": comm.name}
    except Exception as e:
        frappe.log_error(f"Failed to log transcript for {call_sid}: {str(e)}", "Poseidon Transcript Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def process_unified_recording():
    frappe.set_user("poseidon@sapphirefountains.com")
    
    call_sid = frappe.form_dict.get("call_sid")
    summary = frappe.form_dict.get("summary")
    transcript = frappe.form_dict.get("transcript")
    customer_phone = frappe.form_dict.get("customer_phone")
    is_voicemail = frappe.form_dict.get("is_voicemail") in [True, "true", "True", 1, "1"]
    
    info = get_caller_info(customer_phone)
    customer_name = info.get('customer')
    contact_name = info.get('contact')
    display_name = info.get('display_name')

    existing_comm = frappe.get_all("Communication", filters={"subject": ["like", f"%{call_sid}%"]}, limit=1)

    if existing_comm:
        comm = frappe.get_doc("Communication", existing_comm[0].name)
        comm.content = f"**Executive Summary:**\n{summary}\n\n**Full Audio Transcript:**\n<pre>{transcript}</pre>\n\n<hr>\n**System & AI Log:**\n{comm.content}"
        
        # Ensure the appended transcript also links to the contact timeline
        if contact_name and not any(link.link_name == contact_name for link in comm.timeline_links):
            comm.append("timeline_links", {
                "link_doctype": "Contact",
                "link_name": contact_name
            })
            
        comm.save(ignore_permissions=True)
    else:
        comm = frappe.get_doc({
            "doctype": "Communication",
            "communication_medium": "Phone",
            "sent_or_received": "Received",
            "sender": "poseidon@sapphirefountains.com",
            "sender_full_name": "Poseidon",
            "subject": f"Call from {display_name or customer_phone} ({call_sid})",
            "content": f"**Executive Summary:**\n{summary}\n\n**Full Audio Transcript:**\n<pre>{transcript}</pre>",
            "reference_doctype": "Customer" if customer_name else None,
            "reference_name": customer_name,
            "communication_date": frappe.utils.now_datetime()
        })

        if contact_name:
            comm.append("timeline_links", {
                "link_doctype": "Contact",
                "link_name": contact_name
            })
        comm.insert(ignore_permissions=True)

    if 'file' in frappe.request.files:
        uploaded_file = frappe.request.files.get('file')
        file_content = uploaded_file.read()
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": f"call_audio_{frappe.utils.now()}.wav",
            "attached_to_doctype": "Communication",
            "attached_to_name": comm.name,
            "content": file_content,
            "is_private": 1
        })
        file_doc.save(ignore_permissions=True)

    if is_voicemail:
        message_html = f"<strong>Caller:</strong> {display_name} ({customer_phone})<br><br><strong>Summary:</strong><br>{summary}<br><br><strong>Full Transcript:</strong><br><pre>{transcript}</pre>"
        frappe.sendmail(
            recipients=["info@sapphirefountains.com"],
            subject=f"New Poseidon Voicemail from {display_name}",
            message=message_html,
            now=True
        )

    frappe.db.commit()
    return {"status": "success", "communication_id": comm.name}

@frappe.whitelist()
def get_softphone_token():
    settings = frappe.get_doc("Poseidon Settings")
    twilio_api_key_sid = settings.twilio_api_key_sid
    twilio_api_secret = settings.get_password("twilio_api_secret", raise_exception=False)
    twilio_twiml_app_sid = settings.twilio_twiml_app_sid

    if not all([twilio_api_key_sid, twilio_api_secret, twilio_twiml_app_sid]):
        frappe.throw("Twilio softphone credentials are not fully configured in Poseidon Settings.")

    account_sid = frappe.conf.get("twilio_account_sid") or os.environ.get("TWILIO_ACCOUNT_SID")
    if not account_sid:
        frappe.throw("Twilio Account SID is missing.")

    identity = "client:nikolas_erpnext"
    token = AccessToken(account_sid, twilio_api_key_sid, twilio_api_secret, identity=identity)
    voice_grant = VoiceGrant(outgoing_application_sid=twilio_twiml_app_sid, incoming_allow=True)
    token.add_grant(voice_grant)

    return token.to_jwt()

@frappe.whitelist(allow_guest=True)
@validate_twilio_request
def receive_mms():
    frappe.set_user("poseidon@sapphirefountains.com")
    
    sender_number = frappe.form_dict.get("From")
    media_url = frappe.form_dict.get("MediaUrl0")

    customer_name = locate_customer(sender_number)

    if media_url:
        response = requests.get(media_url)
        if response.status_code == 200:
            parsed_url = urlparse(media_url)
            filename = os.path.basename(parsed_url.path) or f"mms_image_{frappe.utils.now()}.jpg"

            file_doc = frappe.get_doc({
                "doctype": "File",
                "file_name": filename,
                "attached_to_doctype": "Customer",
                "attached_to_name": customer_name,
                "content": response.content,
                "is_private": 1
            })
            file_doc.save(ignore_permissions=True)

    return "OK"

@frappe.whitelist()
def send_voicemail_email(subject, body, caller_number=None):
    try:
        message_html = f"<strong>Caller Number:</strong> {caller_number}<br><br><strong>Message/Summary:</strong><br>{body}"
        
        frappe.sendmail(
            recipients=["info@sapphirefountains.com"],
            subject=f"Poseidon Message: {subject}",
            message=message_html,
            now=True
        )
        return {"status": "success"}
    except Exception as e:
        frappe.log_error(f"Failed to send email: {str(e)}", "Poseidon Email Error")
        return {"status": "error", "message": str(e)}

def analyze_transfer_transcript(transcript, customer_name):
    # Retained for future background jobs
    pass
