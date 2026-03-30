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
def get_caller_info(phone_number, twilio_caller_name=None):
    frappe.set_user("poseidon@sapphirefountains.com")
    
    if not phone_number:
        return {"customer": None, "contact": None, "display_name": twilio_caller_name or "Unknown Caller", "context": []}

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
        fallback_name = twilio_caller_name if twilio_caller_name else f"Unknown Caller - {phone_number}"
        
        cust = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": fallback_name,
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
            "first_name": "Caller",
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

    is_established = False
    if customer_name:
        current_cust_name = frappe.db.get_value("Customer", customer_name, "customer_name")
        if current_cust_name and not str(current_cust_name).startswith("Unknown Caller"):
            is_established = True

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
            "communication_date": frappe.utils.now_datetime()
        })

        if customer_name:
            comm.append("timeline_links", {
                "link_doctype": "Customer",
                "link_name": customer_name
            })

        if contact_name:
            comm.append("timeline_links", {
                "link_doctype": "Contact",
                "link_name": contact_name
            })

        comm.insert(ignore_permissions=True)

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
        
        call_sid = kwargs.get("call_sid") or frappe.form_dict.get("call_sid")
        summary = kwargs.get("summary") or frappe.form_dict.get("summary")
        transcript = kwargs.get("transcript") or frappe.form_dict.get("transcript")
        customer_phone = kwargs.get("customer_phone") or frappe.form_dict.get("customer_phone")
        is_voicemail = kwargs.get("is_voicemail") or frappe.form_dict.get("is_voicemail") in [True, "true", "True", 1, "1"]
        direction = kwargs.get("direction") or frappe.form_dict.get("direction") or "Inbound"
        
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
            
            if customer_name and not any(link.link_name == customer_name for link in comm.timeline_links):
                comm.append("timeline_links", {
                    "link_doctype": "Customer",
                    "link_name": customer_name
                })

            if contact_name and not any(link.link_name == contact_name for link in comm.timeline_links):
                comm.append("timeline_links", {
                    "link_doctype": "Contact",
                    "link_name": contact_name
                })
                
            comm.save(ignore_permissions=True)
        else:
            sent_status = "Sent" if direction == "Outbound" else "Received"
            subject_prefix = "Outbound Call to" if direction == "Outbound" else "Call from"

            comm = frappe.get_doc({
                "doctype": "Communication",
                "communication_medium": "Phone",
                "communication_type": "Communication",
                "sent_or_received": sent_status,
                "sender": "poseidon@sapphirefountains.com",
                "sender_full_name": "Poseidon",
                "owner": "poseidon@sapphirefountains.com",
                "subject": f"{subject_prefix} {display_name or customer_phone} ({call_sid})",
                "content": f"**Executive Summary:**\n{summary}\n\n**Full Audio Transcript:**\n<pre>{transcript}</pre>",
                "status": "Linked",
                "communication_date": frappe.utils.now_datetime()
            })

            if customer_name:
                comm.append("timeline_links", {
                    "link_doctype": "Customer",
                    "link_name": customer_name
                })

            if contact_name:
                comm.append("timeline_links", {
                    "link_doctype": "Contact",
                    "link_name": contact_name
                })

            comm.insert(ignore_permissions=True)

            todos = frappe.get_all("ToDo", filters={"reference_type": "Communication", "reference_name": comm.name})
            for t in todos:
                frappe.db.set_value("ToDo", t.name, "allocated_to", "poseidon@sapphirefountains.com")
                frappe.db.set_value("ToDo", t.name, "status", "Closed")

        email_attachments = []

        file_content_b64 = kwargs.get("file_content")
        if file_content_b64:
            try:
                file_content = base64.b64decode(file_content_b64)
                file_doc = frappe.get_doc({
                    "doctype": "File",
                    "file_name": kwargs.get("file_name", f"call_audio_{call_sid}.wav"),
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
        elif 'file' in frappe.request.files:
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
                frappe.log_error(f"Failed to attach multipart audio file: {str(fe)}", "Poseidon File Error")

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

@frappe.whitelist()
def trigger_outbound_call(doctype, docname, target_number):
    try:
        user = frappe.session.user
        employee_map = frappe.get_all("Employee", filters={"user_id": user, "status": "Active"}, fields=["name", "cell_number"])

        if not employee_map:
            frappe.throw(_("No active Employee record found for your user. Cannot determine your cell phone number."))

        employee_number = employee_map[0].cell_number
        if not employee_number:
            frappe.throw(_("Your Employee record does not have a Cell Number configured. Cannot initiate call."))

        settings = frappe.get_doc("Poseidon Settings")
        
        # Use the correct field 'gateway_url' instead of 'poseidon_base_url'
        poseidon_url = getattr(settings, "gateway_url", None)
        
        # Use the webhook secret for authentication as there is no specific API key field
        api_secret = settings.get_password("admin_webhook_secret", raise_exception=False)

        if not poseidon_url:
            frappe.throw(_("Gateway URL is not configured in Poseidon Settings. Please update the Poseidon Settings page."))

        if doctype == "Customer":
            target_number = frappe.db.get_value("Customer", docname, "custom_accounts_phone_number") or target_number
        elif doctype == "Contact":
            target_number = frappe.db.get_value("Contact", docname, "custom_phone_number") or target_number

        endpoint = f"{poseidon_url.rstrip('/')}/api/outbound-call"

        payload = {
            "employee_number": employee_number,
            "target_number": target_number,
            "reference_doctype": doctype,
            "reference_docname": docname
        }

        headers = {
            "Content-Type": "application/json"
        }
        if api_secret:
            headers["Authorization"] = f"Bearer {api_secret}"

        response = requests.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()

        return {"status": "success", "message": _("Call initiated via Poseidon")}

    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Failed to trigger outbound call via Poseidon: {str(e)}", "Poseidon Outbound Call Error")
        frappe.throw(_("Failed to initiate call via Poseidon. Please check error logs."))
    except Exception as e:
        frappe.log_error(f"Unexpected error in trigger_outbound_call: {str(e)}", "Poseidon Outbound Call Error")
        frappe.throw(str(e))


@frappe.whitelist()
def get_employee_number(employee_name):
    # Used by Poseidon for inbound routing via fuzzy search on Employee
    try:
        if not employee_name:
            return None

        employees = frappe.db.sql("""
            SELECT name, cell_number
            FROM `tabEmployee`
            WHERE status = 'Active'
            AND employee_name LIKE %s
            LIMIT 1
        """, (f"%{employee_name}%",), as_dict=True)

        if employees and employees[0].cell_number:
            return employees[0].cell_number

        return None
    except Exception as e:
        frappe.log_error(f"Failed to get employee number for '{employee_name}': {str(e)}", "Poseidon Routing Error")
        return None


@frappe.whitelist()
def log_call_details(call_sid, direction, from_number, to_number, duration, transcript, summary, reference_doctype=None, reference_docname=None):
    try:
        if not call_sid or str(call_sid).strip().lower() in ["undefined", "null", "none", ""]:
            call_sid = f"FALLBACK_{frappe.generate_hash(length=8)}"

        display_name = to_number if direction == "Outbound" else from_number
        if reference_doctype and reference_docname:
            if reference_doctype == "Customer":
                display_name = frappe.db.get_value("Customer", reference_docname, "customer_name") or display_name
            elif reference_doctype == "Contact":
                first, last = frappe.db.get_value("Contact", reference_docname, ["first_name", "last_name"])
                display_name = f"{first or ''} {last or ''}".strip() or display_name
            elif reference_doctype == "Lead":
                display_name = frappe.db.get_value("Lead", reference_docname, "lead_name") or display_name

        comm = frappe.get_doc({
            "doctype": "Communication",
            "communication_medium": "Phone",
            "communication_type": "Communication",
            "sent_or_received": "Sent" if direction == "Outbound" else "Received",
            "sender": "poseidon@sapphirefountains.com" if direction == "Received" else from_number,
            "sender_full_name": "Poseidon" if direction == "Received" else None,
            "owner": "poseidon@sapphirefountains.com",
            "subject": f"{direction} Call with {display_name} ({call_sid})",
            "content": f"**Duration:** {duration}s\n\n**Executive Summary:**\n{summary}\n\n**Full Audio Transcript:**\n<pre>{transcript}</pre>",
            "status": "Linked",
            "communication_date": frappe.utils.now_datetime()
        })

        # Link to reference document if provided
        if reference_doctype and reference_docname:
            if reference_doctype in ["Customer", "Contact", "Lead"]:
                comm.append("timeline_links", {
                    "link_doctype": reference_doctype,
                    "link_name": reference_docname
                })
            else:
                comm.reference_doctype = reference_doctype
                comm.reference_name = reference_docname

        comm.insert(ignore_permissions=True)
        frappe.db.commit()

        return {"status": "success", "communication_id": comm.name}
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(f"Failed to log call details for {call_sid}: {str(e)}", "Poseidon Log Call Error")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def process_unified_sms(**kwargs):
    try:
        frappe.set_user("poseidon@sapphirefountains.com")

        from_number = kwargs.get("from_number") or frappe.form_dict.get("from_number")
        to_number = kwargs.get("to_number") or frappe.form_dict.get("to_number")
        content = kwargs.get("content") or frappe.form_dict.get("content")
        media = kwargs.get("media") or frappe.form_dict.get("media") or []
        sentiment = kwargs.get("sentiment") or frappe.form_dict.get("sentiment")
        is_urgent = kwargs.get("is_urgent") or frappe.form_dict.get("is_urgent") in [True, "true", "True", 1, "1"]
        ai_analysis = kwargs.get("ai_analysis") or frappe.form_dict.get("ai_analysis")

        info = get_caller_info(from_number)
        customer_name = info.get('customer')
        contact_name = info.get('contact')
        display_name = info.get('display_name')

        comm = frappe.get_doc({
            "doctype": "Communication",
            "communication_medium": "SMS",
            "communication_type": "Communication",
            "sent_or_received": "Received",
            "sender": from_number,
            "sender_full_name": display_name,
            "owner": "poseidon@sapphirefountains.com",
            "subject": f"SMS from {display_name}",
            "content": content,
            "status": "Linked",
            "communication_date": frappe.utils.now_datetime()
        })

        if customer_name:
            comm.append("timeline_links", {
                "link_doctype": "Customer",
                "link_name": customer_name
            })
        elif contact_name:
            comm.append("timeline_links", {
                "link_doctype": "Contact",
                "link_name": contact_name
            })

        comm.insert(ignore_permissions=True)

        if ai_analysis or sentiment:
            comment_content = ""
            if ai_analysis:
                comment_content += f"<b>AI Analysis:</b><br>{ai_analysis}<br><br>"
            if sentiment:
                comment_content += f"<b>Sentiment:</b> {sentiment.title()}"

            if comment_content:
                frappe.get_doc({
                    "doctype": "Comment",
                    "comment_type": "Comment",
                    "reference_doctype": "Communication",
                    "reference_name": comm.name,
                    "content": comment_content
                }).insert(ignore_permissions=True)

        # Attach media
        import base64
        import os
        from urllib.parse import urlparse

        if isinstance(media, str):
            import json
            try:
                media = json.loads(media)
            except:
                media = []

        for m in media:
            file_name = m.get("file_name", f"media_{frappe.utils.now()}.bin")
            file_content = m.get("file_content")
            if file_content:
                try:
                    decoded = base64.b64decode(file_content)
                    file_doc = frappe.get_doc({
                        "doctype": "File",
                        "file_name": file_name,
                        "attached_to_doctype": "Communication",
                        "attached_to_name": comm.name,
                        "content": decoded,
                        "is_private": 1
                    })
                    file_doc.db_set('attached_to_doctype', "Communication", update_modified=False)
                    file_doc.db_set('attached_to_name', comm.name, update_modified=False)
                    file_doc.insert(ignore_permissions=True)
                except Exception as ex:
                    frappe.log_error(f"Failed to attach media to SMS: {str(ex)}")

        # Intelligent Assignment
        last_assignee = None

        # 1. Check who last SENT an SMS to this number
        last_sent = frappe.get_all("Communication",
            filters={
                "communication_medium": "SMS",
                "sent_or_received": "Sent",
                "phone_no": from_number
            },
            order_by="creation desc",
            limit=1,
            fields=["owner"]
        )

        if last_sent and last_sent[0].owner != "poseidon@sapphirefountains.com":
            last_assignee = last_sent[0].owner
        else:
            # 2. Check if a past inbound SMS was assigned to someone via ToDo
            past_inbound = frappe.get_all("Communication",
                filters={
                    "communication_medium": "SMS",
                    "sent_or_received": "Received",
                    "sender": from_number,
                    "name": ["!=", comm.name]
                },
                order_by="creation desc",
                limit=1,
                fields=["name"]
            )
            if past_inbound:
                past_todos = frappe.get_all("ToDo",
                    filters={
                        "reference_type": "Communication",
                        "reference_name": past_inbound[0].name
                    },
                    order_by="creation desc",
                    limit=1,
                    fields=["allocated_to"]
                )
                if past_todos and past_todos[0].allocated_to != "info@sapphirefountains.com":
                    last_assignee = past_todos[0].allocated_to

        assignee = last_assignee or "info@sapphirefountains.com"

        from frappe.desk.form.assign_to import add as assign_to
        try:
            assign_to({
                "assign_to": [assignee],
                "doctype": "Communication",
                "name": comm.name,
                "description": "New SMS Received",
                "priority": "High" if not last_assignee else "Medium"
            })
        except Exception as e:
            frappe.log_error(f"Failed to assign SMS {comm.name} to {assignee}: {str(e)}")

        # Urgency Handling
        if is_urgent:
            # Trigger System Notification (Notification Log) for assigned user
            frappe.get_doc({
                "doctype": "Notification Log",
                "subject": f"URGENT SMS from {display_name}",
                "document_type": "Communication",
                "document_name": comm.name,
                "for_user": assignee,
                "type": "Alert"
            }).insert(ignore_permissions=True)

            # Trigger System Notification (Notification Log) for Production Team
            prod_users = frappe.get_all("Has Role", filters={"role": "Production Team"}, fields=["parent"])
            for u in prod_users:
                if u.parent != assignee:
                    frappe.get_doc({
                        "doctype": "Notification Log",
                        "subject": f"URGENT SMS from {display_name}",
                        "document_type": "Communication",
                        "document_name": comm.name,
                        "for_user": u.parent,
                        "type": "Alert"
                    }).insert(ignore_permissions=True)

        frappe.db.commit()
        return {"status": "success", "communication_id": comm.name}
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(f"Critical sync failure in process_unified_sms: {str(e)}", "Poseidon Sync Error")
        frappe.response["http_status_code"] = 500
        return {"status": "error", "message": str(e)}



@frappe.whitelist()
def send_sms(target_number, message, media_urls=None, reference_doctype=None, reference_docname=None):
    try:
        if not target_number or not message:
            frappe.throw(_("Target number and message are required."))

        user = frappe.session.user
        employee_map = frappe.get_all("Employee", filters={"user_id": user, "status": "Active"}, fields=["name", "employee_name"])

        if not employee_map:
            frappe.throw(_("No active Employee record found for your user. Cannot send SMS."))

        employee_name = employee_map[0].employee_name

        # Clean target number for lookup
        import re
        clean_number = re.sub(r'\D', '', target_number)
        match_suffix = clean_number[-10:] if len(clean_number) >= 10 else clean_number

        info = get_caller_info(target_number)
        customer_name = info.get('customer')
        contact_name = info.get('contact')
        display_name = info.get('display_name') or target_number

        # Check if an outgoing SMS has been sent to this number in the last 24 hours
        from frappe.utils import add_days, now_datetime
        twenty_four_hours_ago = add_days(now_datetime(), -1)

        recent_sms = frappe.get_all("Communication",
            filters={
                "communication_medium": "SMS",
                "sent_or_received": "Sent",
                "creation": [">=", twenty_four_hours_ago],
                "phone_no": ["like", f"%{match_suffix}"]
            },
            limit=1
        )

        # Determine if we should append signature
        if not recent_sms:
            message = f"{message.strip()} - [{employee_name}]"

        settings = frappe.get_doc("Poseidon Settings")
        poseidon_url = getattr(settings, "gateway_url", None)
        api_secret = settings.get_password("admin_webhook_secret", raise_exception=False)

        if not poseidon_url:
            frappe.throw(_("Gateway URL is not configured in Poseidon Settings. Please update the Poseidon Settings page."))

        endpoint = f"{poseidon_url.rstrip('/')}/api/send-sms"

        if isinstance(media_urls, str):
            import json
            try:
                media_urls = json.loads(media_urls)
            except:
                media_urls = []
        elif not media_urls:
            media_urls = []

        payload = {
            "to_number": target_number,
            "content": message,
            "media_urls": media_urls
        }

        headers = {
            "Content-Type": "application/json"
        }
        if api_secret:
            headers["Authorization"] = f"Bearer {api_secret}"

        import requests
        response = requests.post(endpoint, json=payload, headers=headers)
        response.raise_for_status()

        # Create outgoing Communication record
        comm = frappe.get_doc({
            "doctype": "Communication",
            "communication_medium": "SMS",
            "communication_type": "Communication",
            "sent_or_received": "Sent",
            "sender": user,
            "sender_full_name": employee_name,
            "owner": user,
            "phone_no": target_number,
            "subject": f"Outbound SMS to {display_name}",
            "content": message,
            "status": "Linked",
            "communication_date": now_datetime()
        })

        if reference_doctype and reference_docname:
            if reference_doctype in ["Customer", "Contact", "Lead"]:
                comm.append("timeline_links", {
                    "link_doctype": reference_doctype,
                    "link_name": reference_docname
                })
            else:
                comm.reference_doctype = reference_doctype
                comm.reference_name = reference_docname
        elif customer_name:
             comm.append("timeline_links", {
                "link_doctype": "Customer",
                "link_name": customer_name
            })
        elif contact_name:
             comm.append("timeline_links", {
                "link_doctype": "Contact",
                "link_name": contact_name
            })

        comm.insert(ignore_permissions=True)
        frappe.db.commit()

        return {"status": "success", "message": _("SMS sent successfully via Poseidon."), "communication_id": comm.name}

    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Failed to send SMS via Poseidon: {str(e)}", "Poseidon Outbound SMS Error")
        frappe.throw(_("Failed to send SMS via Poseidon. Please check error logs."))
    except Exception as e:
        frappe.log_error(f"Unexpected error in send_sms: {str(e)}", "Poseidon Outbound SMS Error")
        frappe.throw(str(e))
