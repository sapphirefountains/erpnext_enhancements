import frappe
from frappe import _
import requests
import json
import re
import os
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

        if not auth_header.startswith("Bearer "):
            frappe.throw(_("Missing or Invalid Authorization Header"), frappe.PermissionError)

        token = auth_header.split(" ")[1]
        if token != secret:
            frappe.throw(_("Invalid Webhook Secret"), frappe.PermissionError)

        return func(*args, **kwargs)
    return wrapper

@frappe.whitelist(allow_guest=True)
def get_caller_info(phone_number):
    if not phone_number:
        return {"customer": None, "contact": None, "display_name": "Unknown Caller", "context": []}

    clean_number = re.sub(r'\D', '', phone_number)
    match_suffix = clean_number[-10:] if len(clean_number) >= 10 else clean_number
    fuzzy_regex = ".*".join(list(match_suffix))

    contact_name = None
    customer_name = None
    display_name = None

    # 1. Search Contacts with Regex using custom field
    contacts = frappe.db.sql("""
        SELECT name FROM `tabContact` 
        WHERE custom_phone_number REGEXP %s 
        LIMIT 1""", (fuzzy_regex,), as_dict=True)
    
    if contacts:
        contact_name = contacts[0].name
        links = frappe.get_all("Dynamic Link", filters={"parent": contact_name, "parenttype": "Contact", "link_doctype": "Customer"}, fields=["link_name"])
        if links:
            customer_name = links[0].link_name

    # 2. Search Customers with Regex using custom field
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
            "customer_type": "Residential", # Updated to align with customized schema requirements
            "customer_group": "All Customer Groups",
            "territory": "All Territories",
            "custom_accounts_phone_number": phone_number # Mapped to custom field
        })
        cust.insert(ignore_permissions=True)
        customer_name = cust.name
        display_name = cust.customer_name

        cont = frappe.get_doc({
            "doctype": "Contact",
            "first_name": f"Caller",
            "last_name": phone_number,
            "custom_phone_number": phone_number, # Mapped to custom field
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

    # 4. GATHER CONTEXT
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

def locate_customer(phone_number):
    info = get_caller_info(phone_number)
    return info.get("customer")

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

@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def process_unified_recording():
    summary = frappe.form_dict.get("summary")
    transcript = frappe.form_dict.get("transcript")
    customer_phone = frappe.form_dict.get("customer_phone")
    call_type = frappe.form_dict.get("call_type")

    info = get_caller_info(customer_phone)
    customer_name = info.get('customer')
    contact_name = info.get('contact')

    comm = frappe.get_doc({
        "doctype": "Communication",
        "communication_medium": "Phone",
        "sent_or_received": "Received",
        "subject": f"Call from {customer_phone}",
        "content": f"**Summary:**\n{summary}\n\n**Transcript:**\n{transcript}",
        "reference_doctype": "Customer",
        "reference_name": customer_name,
        "communication_date": frappe.utils.now_datetime()
    })

    if contact_name:
        comm.append("timeline_links", {
            "link_doctype": "Contact",
            "link_name": contact_name
        })

    comm.insert(ignore_permissions=True)

    if 'call_audio.wav' in frappe.request.files:
        file_content = frappe.request.files.get('call_audio.wav').read()
        file_doc = frappe.get_doc({
            "doctype": "File",
            "file_name": f"call_audio_{frappe.utils.now()}.wav",
            "attached_to_doctype": "Communication",
            "attached_to_name": comm.name,
            "content": file_content,
            "is_private": 1
        })
        file_doc.save(ignore_permissions=True)

    if call_type == 'human_transfer':
        frappe.enqueue(
            'erpnext_enhancements.api.telephony.analyze_transfer_transcript',
            transcript=transcript,
            customer_name=customer_name,
            queue='long'
        )

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
        try:
            sms_settings = frappe.get_single("SMS Settings")
            if sms_settings.sms_gateway_url and "twilio" in sms_settings.sms_gateway_url.lower():
                pass
        except Exception:
            pass

    if not account_sid:
        frappe.throw("Twilio Account SID is missing. Please set 'twilio_account_sid' in site_config.json or as an environment variable.")

    identity = "client:nikolas_erpnext"

    token = AccessToken(
        account_sid,
        twilio_api_key_sid,
        twilio_api_secret,
        identity=identity
    )

    voice_grant = VoiceGrant(
        outgoing_application_sid=twilio_twiml_app_sid,
        incoming_allow=True
    )
    token.add_grant(voice_grant)

    return token.to_jwt()

@frappe.whitelist(allow_guest=True)
@validate_twilio_request
def receive_mms():
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

def analyze_transfer_transcript(transcript, customer_name):
    api_key = get_poseidon_settings("gemini_api_key")
    if not api_key:
        frappe.log_error("Gemini API key is not configured in Poseidon Settings", "Telephony Analysis Error")
        return

    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-3.1-pro-preview')

    prompt = f"""
    Analyze the following transcript of a human agent resolving an issue with a customer.
    Extract the following information:
    1. original_issue: The core problem the customer called about.
    2. human_resolution_path: The step-by-step logic and actions the human agent took to solve the issue.
    3. suggested_heuristic: A concise rule or heuristic that an AI could follow to solve similar issues in the future.

    Return the result strictly as a JSON object with these three keys.

    Transcript:
    {transcript}
    """

    try:
        response = model.generate_content(prompt)
        result = json.loads(response.text.strip('```json\n').strip('```').strip())

        insight = frappe.get_doc({
            "doctype": "Training Insight",
            "customer": customer_name,
            "original_issue": result.get("original_issue", ""),
            "human_resolution_path": result.get("human_resolution_path", ""),
            "suggested_heuristic": result.get("suggested_heuristic", "")
        })
        insight.insert(ignore_permissions=True)
    except Exception as e:
        frappe.log_error(f"Failed to analyze transfer transcript: {str(e)}", "Telephony Analysis Error")

@frappe.whitelist()
def log_call_transcript(call_sid, transcript, caller_number=None):
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
