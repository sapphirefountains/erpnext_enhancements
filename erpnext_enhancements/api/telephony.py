import frappe
from frappe import _
import requests
import json
import re
import os
import base64
from twilio.request_validator import RequestValidator
from urllib.parse import urlparse, quote
from twilio.jwt.access_token import AccessToken
from twilio.jwt.access_token.grants import VoiceGrant

@frappe.whitelist(allow_guest=True)
def get_gateway_config():
    try:
        settings = frappe.get_doc("Poseidon Settings")
        return {
            "master_system_prompt": settings.master_system_prompt,
            "forwarding_phone_number": getattr(settings, "forwarding_phone_number", "+18018200044"),
            "voice_model_id": getattr(settings, "voice_model_id", "gemini-live-2.5-flash-native-audio"),
            "gemini_api_key": getattr(settings, "gemini_api_key", None)
        }
    except Exception as e:
        frappe.log_error(f"Failed to fetch Poseidon settings: {str(e)}", "Gateway Config Error")
        return {
            "master_system_prompt": "You are Poseidon.",
            "forwarding_phone_number": "+18018200044",
            "voice_model_id": "gemini-live-2.5-flash-native-audio"
        }

def validate_twilio_request(func):
    def wrapper(*args, **kwargs):
        settings = frappe.get_doc("Poseidon Settings")
        validator = RequestValidator(getattr(settings, "twilio_auth_token", ""))
        url = frappe.request.url
        post_vars = frappe.request.form
        signature = frappe.request.headers.get("X-Twilio-Signature", "")

        if not validator.validate(url, post_vars, signature):
            frappe.throw(_("Invalid Twilio Signature"), frappe.PermissionError)
        return func(*args, **kwargs)
    return wrapper

def validate_webhook_secret(func):
    def wrapper(*args, **kwargs):
        try:
            settings = frappe.get_doc("Poseidon Settings")
            secret = getattr(settings, "admin_webhook_secret", "")
        except:
            secret = ""

        auth_header = frappe.request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer ") and not auth_header.startswith("token "):
            frappe.throw(_("Missing or Invalid Authorization Header"), frappe.PermissionError)
            
        token = auth_header.split(" ")[1] if " " in auth_header else auth_header
        if not auth_header.startswith("token ") and token != secret:
            frappe.throw(_("Invalid Webhook Secret"), frappe.PermissionError)

        return func(*args, **kwargs)
    return wrapper

@frappe.whitelist(allow_guest=True)
def append_call_transcript(call_sid, transcript_chunk):
    frappe.set_user("poseidon@sapphirefountains.com")
    key = f"poseidon_transcript_{call_sid}"
    chunks = frappe.cache().get_value(key) or []
    chunks.append(transcript_chunk)
    frappe.cache().set_value(key, chunks, expires_in_sec=86400)
    return "OK"

@frappe.whitelist(allow_guest=True)
def get_call_transcript(call_sid):
    frappe.set_user("poseidon@sapphirefountains.com")
    key = f"poseidon_transcript_{call_sid}"
    chunks = frappe.cache().get_value(key) or []
    return "\n".join(chunks)

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

    if not customer_name:
        customers = frappe.db.sql("""
            SELECT name, customer_name FROM `tabCustomer` 
            WHERE custom_accounts_phone_number REGEXP %s 
            LIMIT 1""", (fuzzy_regex,), as_dict=True)
        if customers:
            customer_name = customers[0].name
            display_name = customers[0].customer_name

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

    # Check if this is an established customer or our "Unknown Caller" stub
    is_established = False
    if customer_name:
        current_cust_name = frappe.db.get_value("Customer", customer_name, "customer_name")
        if current_cust_name and not str(current_cust_name).startswith("Unknown Caller"):
            is_established = True

    # Only update spelling/names if it is NOT an established customer
    if not is_established:
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
    return {"status": "success", "customer": customer_name, "contact": contact_name, "updated": not is_established}

def locate_customer(phone_number):
    info = get_caller_info(phone_number)
    return info.get("customer")

@frappe.whitelist()
def log_call_transcript(call_sid, transcript, caller_number=None, **kwargs):
    frappe.set_user("poseidon@sapphirefountains.com")
    
    if not call_sid or str(call_sid).strip().lower() in ["undefined", "null", "none", ""]:
        call_sid = f"FALLBACK_{frappe.generate_hash(length=8)}"

    if not transcript:
        frappe.throw("Missing transcript")

    try:
        customer_name, contact_name = None, None
        if caller_number:
            info = get_caller_info(caller_number)
            customer_name = info.get('customer')
            contact_name = info.get('contact')

        comm = frappe.get_doc({
            "doctype": "Communication",
            "communication_medium": "Phone",
            "communication_type": "Communication",
            "sent_or_received": "Received",
            "sender": "poseidon@sapphirefountains.com",
            "sender_full_name": "Poseidon",
            "owner": "poseidon@sapphirefountains.com",
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

        # Immediately intercept and bury any auto-assigned ToDos triggered by reference_doctype
        todos = frappe.get_all("ToDo", filters={"reference_type": "Communication", "reference_name": comm.name})
        for t in todos:
            frappe.db.set_value("ToDo", t.name, "allocated_to", "poseidon@sapphirefountains.com")
            frappe.db.set_value("ToDo", t.name, "status", "Closed")

        frappe.db.commit()
        return {"status": "success", "communication_id": comm.name}
    except Exception as e:
        frappe.log_error(f"Failed to log transcript for {call_sid}: {str(e)}", "Poseidon Transcript Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def process_unified_recording(**kwargs):
    try:
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

        existing_comm = []
        if call_sid and str(call_sid).strip().lower() not in ["undefined", "null", "none", ""]:
            existing_comm = frappe.get_all("Communication", filters={"subject": ["like", f"%{call_sid}%"]}, limit=1)
        else:
            call_sid = f"FALLBACK_{frappe.generate_hash(length=8)}"

        if existing_comm:
            comm = frappe.get_doc("Communication", existing_comm[0].name)
            comm.content = f"**Executive Summary:**\n{summary}\n\n**Full Audio Transcript:**\n<pre>{transcript}</pre>\n\n<hr>\n**System & AI Log:**\n{comm.content}"
            comm.communication_type = "Communication"
            
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
                "communication_type": "Communication",
                "sent_or_received": "Received",
                "sender": "poseidon@sapphirefountains.com",
                "sender_full_name": "Poseidon",
                "owner": "poseidon@sapphirefountains.com",
                "subject": f"Call from {display_name or customer_phone} ({call_sid})",
                "content": f"**Executive Summary:**\n{summary}\n\n**Full Audio Transcript:**\n<pre>{transcript}</pre>",
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

            # Immediately intercept and bury any auto-assigned ToDos triggered by reference_doctype
            todos = frappe.get_all("ToDo", filters={"reference_type": "Communication", "reference_name": comm.name})
            for t in todos:
                frappe.db.set_value("ToDo", t.name, "allocated_to", "poseidon@sapphirefountains.com")
                frappe.db.set_value("ToDo", t.name, "status", "Closed")

        email_attachments = []

        if 'file' in frappe.request.files:
            try:
                uploaded_file = frappe.request.files.get('file')
                file_content = uploaded_file.read()
                file_doc = frappe.get_doc({
                    "doctype": "File",
                    "file_name": f"call_audio_{call_sid}.wav",
                    "attached_to_doctype": "Communication",
                    "attached_to_name": comm.name,
                    "content": file_content,
                    "is_private": 1
                })
                file_doc.save(ignore_permissions=True)
                
                email_attachments.append({
                    "fname": file_doc.file_name,
                    "fcontent": file_content
                })
            except Exception as fe:
                frappe.log_error(f"Failed to attach audio file: {str(fe)}", "Poseidon File Error")

        try:
            email_subject_type = "Voicemail" if is_voicemail else "Call Transcript"
            
            base_url = frappe.utils.get_url()
            links_html = "<br><br><strong>System Links:</strong><ul>"
            if customer_name:
                links_html += f'<li><a href="{base_url}/app/customer/{quote(customer_name)}">View Accounts in ERPNext</a></li>'
            if contact_name:
                links_html += f'<li><a href="{base_url}/app/contact/{quote(contact_name)}">View Contact in ERPNext</a></li>'
            if comm.name:
                links_html += f'<li><a href="{base_url}/app/communication/{quote(comm.name)}">View Communication in ERPNext</a></li>'
            links_html += "</ul>"

            message_html = f"<strong>Caller:</strong> {display_name} ({customer_phone})<br><br><strong>Summary:</strong><br>{summary}<br><br><strong>Full Transcript:</strong><br><pre>{transcript}</pre>{links_html}"
            
            frappe.sendmail(
                recipients=["info@sapphirefountains.com"],
                subject=f"New Poseidon {email_subject_type} from {display_name}",
                message=message_html,
                attachments=email_attachments,
                now=True
            )
        except Exception as ee:
            frappe.log_error(f"Failed to send email: {str(ee)}", "Poseidon Email Error")

        frappe.db.commit()
        return {"status": "success", "communication_id": comm.name}

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(f"Critical sync failure: {str(e)}", "Poseidon Sync Error")
        frappe.response["http_status_code"] = 500
        return {"status": "error", "message": str(e)}

@frappe.whitelist()
def get_softphone_token():
    settings = frappe.get_doc("Poseidon Settings")
    twilio_api_key_sid = getattr(settings, "twilio_api_key_sid", None)
    twilio_api_secret = settings.get_password("twilio_api_secret", raise_exception=False)
    twilio_twiml_app_sid = getattr(settings, "twilio_twiml_app_sid", None)

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
def send_voicemail_email(subject, body, caller_number=None, **kwargs):
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
    pass
