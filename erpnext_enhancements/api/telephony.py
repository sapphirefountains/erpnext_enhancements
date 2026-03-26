import frappe
from frappe import _
import requests
import json
import google.generativeai as genai
from twilio.request_validator import RequestValidator
from urllib.parse import urlparse
import os

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

def locate_customer(phone_number):
    if not phone_number:
        return None

    # Search Customer by mobile_no or phone
    customer = frappe.get_all("Customer", filters={"mobile_no": phone_number}, limit=1)
    if not customer:
        customer = frappe.get_all("Customer", filters={"phone": phone_number}, limit=1)

    if customer:
        return customer[0].name

    # Search Contact by phone or mobile_no
    contact = frappe.get_all("Contact", filters={"phone": phone_number}, limit=1)
    if not contact:
        contact = frappe.get_all("Contact", filters={"mobile_no": phone_number}, limit=1)

    if contact:
        # Find linked customer
        links = frappe.get_all("Dynamic Link", filters={
            "parent": contact[0].name,
            "parenttype": "Contact",
            "link_doctype": "Customer"
        }, limit=1, fields=["link_name"])

        if links:
            return links[0].link_name

    # Fallback: Create a new Unknown Customer
    new_customer = frappe.get_doc({
        "doctype": "Customer",
        "customer_name": f"Unknown - {phone_number}",
        "customer_type": "Individual",
        "customer_group": "All Customer Groups",
        "territory": "All Territories",
        "mobile_no": phone_number
    })
    new_customer.insert(ignore_permissions=True)
    return new_customer.name

@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def process_unified_recording():
    # Expect Multipart Form-Data POST
    summary = frappe.form_dict.get("summary")
    transcript = frappe.form_dict.get("transcript")
    customer_phone = frappe.form_dict.get("customer_phone")
    call_type = frappe.form_dict.get("call_type")

    customer_name = locate_customer(customer_phone)

    # Save the interaction as a Communication record
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
    comm.insert(ignore_permissions=True)

    # Process audio file attachment
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
