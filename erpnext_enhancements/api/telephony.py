"""Triton telephony integration (Twilio voice/SMS + the external Triton gateway).

This module bridges ERPNext with the "Triton" AI phone/SMS gateway and Twilio.
It is the trust boundary for inbound webhooks and the caller for outbound
voice/SMS.

Callers:
        - External Triton gateway / Twilio webhooks: ``append_call_transcript``,
          ``get_call_transcript``, ``get_caller_info``, ``update_caller_info``,
          ``process_unified_recording``, ``process_unified_sms``, ``receive_mms``,
          ``get_gateway_config`` (all ``allow_guest=True``).
        - Desk JS: ``get_softphone_token`` and ``trigger_outbound_call``
          (contact.js / customer.js / lead.js / telephony_client.js),
          ``send_sms`` (telephony_client.js).

External services: Twilio (request-signature validation, Voice access-token JWT
minting) and the Triton gateway HTTP API (``gateway_url`` in Triton Settings)
for outbound calls/SMS; it also fetches Twilio MMS media over HTTP.

SECURITY — read carefully:
        - Several endpoints are ``allow_guest=True`` because they are hit by
          server-to-server webhooks with no Frappe session. They are protected
          either by ``@validate_twilio_request`` (Twilio HMAC signature) or
          ``@validate_webhook_secret`` (Bearer/``token`` shared secret from
          ``admin_webhook_secret``). ``get_gateway_config`` is unauthenticated by
          design and therefore returns ONLY non-sensitive routing config.
        - Webhook handlers call ``frappe.set_user("triton@sapphirefountains.com")``
          to act as the Triton service user and write with ``ignore_permissions=True``.
        - Outbound endpoints (``send_sms``, ``trigger_outbound_call``) are normal
          authenticated whitelists and resolve the caller's own Employee /
          cell number from the session user.
"""

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
    """Return non-sensitive call-routing config to the Triton gateway.

    Guest-accessible (``allow_guest=True``) and intentionally unauthenticated.
    Returns only the master system prompt, forwarding phone number, and voice
    model id from Triton Settings — NEVER secrets/API keys. On any error,
    returns hard-coded safe defaults (and logs the failure).
    """
    try:
        settings = frappe.get_doc("Triton Settings")
        return {
            # NOTE: never return secrets (API keys / tokens) from this guest-
            # accessible endpoint. It exposes non-sensitive call-routing config only.
            "master_system_prompt": settings.master_system_prompt,
            "forwarding_phone_number": getattr(settings, "forwarding_phone_number", "+18018200044"),
            "voice_model_id": getattr(settings, "voice_model_id", "gemini-live-2.5-flash-native-audio"),
        }
    except Exception as e:
        frappe.log_error(f"Failed to fetch Triton settings: {str(e)}", "Gateway Config Error")
        return {
            "master_system_prompt": "You are Triton.",
            "forwarding_phone_number": "+18018200044",
            "voice_model_id": "gemini-live-2.5-flash-native-audio"
        }

def validate_twilio_request(func):
    """Decorator: reject requests whose Twilio HMAC signature is invalid.

    Validates the ``X-Twilio-Signature`` header against the request URL + POST
    body using the Twilio auth token from Triton Settings. ``frappe.throw(...
    PermissionError)`` on mismatch. Used to authenticate Twilio's own webhooks
    (e.g. ``receive_mms``) since they arrive with no Frappe session.
    """
    def wrapper(*args, **kwargs):
        settings = frappe.get_doc("Triton Settings")
        validator = RequestValidator(settings.get_password("twilio_auth_token", raise_exception=False) or "")
        url = frappe.request.url
        post_vars = frappe.request.form
        signature = frappe.request.headers.get("X-Twilio-Signature", "")

        if not validator.validate(url, post_vars, signature):
            frappe.throw(_("Invalid Twilio Signature"), frappe.PermissionError)
        return func(*args, **kwargs)
    return wrapper

def validate_webhook_secret(func):
    """Decorator: require the Triton shared-secret Bearer token.

    Reads the expected secret from Triton Settings ``admin_webhook_secret`` and
    checks the ``Authorization`` header. Accepts ``Bearer <secret>``; also
    permits a ``token ...`` scheme (Frappe API key/secret auth) to pass through.
    ``frappe.throw(... PermissionError)`` if the header is missing or the
    Bearer secret does not match. Authenticates the guest-accessible Triton
    gateway endpoints.
    """
    def wrapper(*args, **kwargs):
        try:
            settings = frappe.get_doc("Triton Settings")
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
@validate_webhook_secret
def append_call_transcript(call_sid, transcript_chunk):
    """Append a live transcript chunk to a per-call Redis cache buffer.

    Guest endpoint guarded by ``@validate_webhook_secret`` (Triton gateway).
    Buffers chunks under ``triton_transcript_<call_sid>`` in the Frappe cache
    with a 24h TTL, for later assembly. Acts as the Triton service user.
    """
    frappe.set_user("triton@sapphirefountains.com")
    key = f"triton_transcript_{call_sid}"
    chunks = frappe.cache().get_value(key) or []
    chunks.append(transcript_chunk)
    frappe.cache().set_value(key, chunks, expires_in_sec=86400)
    return "OK"

@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def get_call_transcript(call_sid):
    """Return the buffered live transcript for a call as one newline-joined string.

    Guest endpoint guarded by ``@validate_webhook_secret``. Reads the chunks
    cached by ``append_call_transcript``.
    """
    frappe.set_user("triton@sapphirefountains.com")
    key = f"triton_transcript_{call_sid}"
    chunks = frappe.cache().get_value(key) or []
    return "\n".join(chunks)

@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def get_caller_info(phone_number, twilio_caller_name=None, create_if_missing=True):
    """Resolve a phone number to a Customer/Contact, creating them if unknown.

    Guest endpoint guarded by ``@validate_webhook_secret``; also used as an
    internal helper by other functions in this module.

    Matching: normalises to digits and fuzzy-matches the last 10 digits via a
    REGEXP over ``Contact.custom_phone_number`` then
    ``Customer.custom_accounts_phone_number`` (the ``.*``-joined regex tolerates
    formatting differences). If neither is found, AUTO-CREATES a Residential
    Customer + primary Contact (with ``ignore_permissions``) named from the
    Twilio caller id or "Unknown Caller" — unless ``create_if_missing`` is
    falsy (missed-call ingestion passes False so robocalls don't mint junk
    Customers).

    Returns:
        dict: ``{"customer", "contact", "display_name", "context"}`` where
        ``context`` is a list of open Opportunity/Project labels for the customer.

    Side effects: may insert Customer + Contact docs and commits the transaction.
    """
    frappe.set_user("triton@sapphirefountains.com")
    return _get_caller_info(
        phone_number,
        twilio_caller_name=twilio_caller_name,
        create_if_missing=create_if_missing,
    )


def _default_customer_group():
    """A NON-GROUP Customer Group for auto-created callers. erpnext v16
    rejects group nodes ("Cannot select a Group type Customer Group"), which
    broke every unknown-caller auto-create while it hard-coded
    "All Customer Groups". Selling Settings' default wins when it's a leaf;
    otherwise the first leaf group (e.g. "Individual")."""
    default = frappe.db.get_single_value("Selling Settings", "customer_group")
    if default and not frappe.db.get_value("Customer Group", default, "is_group"):
        return default
    return frappe.db.get_value("Customer Group", {"is_group": 0}, "name") or default


def _default_territory():
    """Same leaf-node rule for Territory ("All Territories" is a group)."""
    default = frappe.db.get_single_value("Selling Settings", "territory")
    if default and not frappe.db.get_value("Territory", default, "is_group"):
        return default
    return frappe.db.get_value("Territory", {"is_group": 0}, "name") or default


def _get_caller_info(phone_number, twilio_caller_name=None, create_if_missing=True):
    """Internal, auth-free implementation of ``get_caller_info`` — call THIS
    from server-side code. The whitelisted wrapper above exists for the Triton
    gateway HTTP boundary only: its ``@validate_webhook_secret`` reads the
    REQUEST's Authorization header, so calling the wrapper from a
    session-authenticated endpoint (desk ``send_sms``) or a Twilio-signature
    webhook (``receive_mms`` → ``locate_customer``) threw "Missing or Invalid
    Authorization Header". Runs as the CURRENT user — callers that need the
    Triton service user set it themselves.
    """
    if isinstance(create_if_missing, str):
        create_if_missing = create_if_missing.strip().lower() not in ("0", "false", "no", "")

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

    if not customer_name and not contact_name and create_if_missing:
        fallback_name = twilio_caller_name if twilio_caller_name else f"Unknown Caller - {phone_number}"
        
        cust = frappe.get_doc({
            "doctype": "Customer",
            "customer_name": fallback_name,
            "customer_type": "Residential",
            "customer_group": _default_customer_group(),
            "territory": _default_territory(),
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
@validate_webhook_secret
def notify_incoming_call(event=None, call_sid=None, from_number=None, caller_name=None,
                         intent=None, stage=None, agent_name=None, answered_via=None,
                         reason=None, **kwargs):
    """Realtime call-lifecycle fan-out from the Triton gateway to desk users.

    Guest endpoint guarded by ``@validate_webhook_secret``. The Triton voice
    gateway POSTs one of these per call state change:

    - ``ringing`` (stage ``menu`` while the caller is in the IVR, then stage
      ``agents`` once browsers/phones are being rung, with the chosen intent)
    - ``caller_resolved`` (Triton's CRM fuzzy-match finished — name update)
    - ``answered`` (someone picked up; ``agent_name``/``answered_via``)
    - ``ended`` (terminal; ``reason`` of no-answer/busy means it was missed)

    Each event is republished as the ``triton_incoming_call`` realtime event
    (broadcast — telephony_client.js renders/dismisses the floating call panel
    on every open desk). On the first ``ringing`` event with a number, the
    caller is enriched against the CRM via get_caller_info with
    ``create_if_missing=False`` — a merely-ringing robocall must not mint a
    junk Customer; auto-create still happens later in the recording/SMS
    pipelines for calls that actually connect.
    """
    frappe.set_user("triton@sapphirefountains.com")
    if not call_sid or not event:
        return {"status": "ignored"}

    payload = {
        "event": event,
        "call_sid": call_sid,
        "from_number": from_number,
        "caller_name": caller_name,
        "intent": intent,
        "stage": stage,
        "agent_name": agent_name,
        "answered_via": answered_via,
        "reason": reason,
    }

    if event == "ringing" and from_number:
        try:
            info = _get_caller_info(
                from_number, twilio_caller_name=caller_name, create_if_missing=False
            )
            payload["caller_name"] = info.get("display_name") or caller_name
            payload["customer"] = info.get("customer")
            payload["contact"] = info.get("contact")
            payload["context"] = (info.get("context") or [])[:5]
        except Exception:
            frappe.log_error(frappe.get_traceback(), "notify_incoming_call enrich failed")

    frappe.publish_realtime("triton_incoming_call", payload)
    return {"status": "ok"}

@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def update_caller_info(phone_number, new_name):
    """Rename the Customer/Contact for a number, unless it's already established.

    Guest endpoint guarded by ``@validate_webhook_secret``. Looks up (or
    creates, via ``get_caller_info``) the records for ``phone_number``. If the
    Customer name does NOT start with "Unknown Caller" it is treated as
    established and left untouched (``updated: False``). Otherwise the Customer
    name and Contact first/last name are set from ``new_name``. Commits.
    """
    frappe.set_user("triton@sapphirefountains.com")

    info = _get_caller_info(phone_number)
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
    """Internal helper: return just the Customer name for a phone number.

    Thin wrapper over ``get_caller_info`` (so it may also create records as a
    side effect). Not whitelisted.
    """
    info = _get_caller_info(phone_number)
    return info.get("customer")

@frappe.whitelist()
def log_call_transcript(call_sid, transcript, caller_number=None, **kwargs):
    """Persist a finished call's transcript as a Phone Communication record.

    Authenticated whitelist (no ``allow_guest``), but immediately switches to
    the Triton service user. Generates a fallback ``call_sid`` if missing.
    Resolves the caller (via ``get_caller_info``) and links the Communication to
    the Customer/Contact through ``timeline_links``. Also closes/reassigns any
    auto-created ToDos to the Triton user. Commits; returns
    ``{"status", "communication_id"}`` (or an error dict).
    """
    frappe.set_user("triton@sapphirefountains.com")

    if not call_sid or str(call_sid).strip().lower() in ["undefined", "null", "none", ""]:
        call_sid = f"FALLBACK_{frappe.generate_hash(length=8)}"

    if not transcript:
        frappe.throw("Missing transcript")

    try:
        customer_name, contact_name = None, None
        if caller_number:
            info = _get_caller_info(caller_number)
            customer_name = info.get('customer')
            contact_name = info.get('contact')

        comm = frappe.get_doc({
            "doctype": "Communication",
            "communication_medium": "Phone",
            "communication_type": "Communication",
            "sent_or_received": "Received",
            "sender": "triton@sapphirefountains.com",
            "sender_full_name": "Triton",
            "owner": "triton@sapphirefountains.com",
            "subject": f"Triton Live Transcript ({call_sid})",
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
            frappe.db.set_value("ToDo", t.name, "allocated_to", "triton@sapphirefountains.com")
            frappe.db.set_value("ToDo", t.name, "status", "Closed")

        frappe.db.commit()
        return {"status": "success", "communication_id": comm.name}
    except Exception as e:
        frappe.log_error(f"Failed to log transcript for {call_sid}: {str(e)}", "Triton Transcript Error")
        return {"status": "error", "message": str(e)}

@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def process_unified_recording(**kwargs):
    """Ingest a completed call recording: summary, transcript, audio, and email.

    Guest endpoint guarded by ``@validate_webhook_secret`` (Triton gateway).
    Accepts params via kwargs or ``frappe.form_dict``. If a Communication for
    the ``call_sid`` already exists it is enriched (summary + transcript
    prepended); otherwise a new Phone Communication is created and linked to the
    resolved Customer/Contact, and related ToDos are closed.

    Audio handling: accepts a base64 ``file_content`` field OR a multipart
    ``file`` upload, saved as a private File attached to the Communication.

    Call Intelligence: also upserts the stock Call Log for the SID (see
    ``api.call_intelligence``) with the AI analysis fields the gateway sends
    (sentiment, escalation risk, follow-ups, topics, compliance flags, CSAT,
    IVR intent, agent) and rewrites ``recording_url`` to the private File URL
    so the native audio player works in the desk.

    Side effects: writes Communication + File + Call Log docs
    (``ignore_permissions``); emails a summary (with audio attachment) to
    info@sapphirefountains.com unless Triton Settings unchecks "Send Call
    Email Digest"; commits. On any error rolls back, logs, and returns HTTP 500.
    """
    try:
        frappe.set_user("triton@sapphirefountains.com")

        def val(key, default=None):
            v = kwargs.get(key)
            if v is None:
                v = frappe.form_dict.get(key)
            return v if v is not None else default

        call_sid = val("call_sid")
        summary = val("summary")
        transcript = val("transcript")
        customer_phone = val("customer_phone")
        is_voicemail = val("is_voicemail") in [True, "true", "True", 1, "1"]
        direction = val("direction") or "Inbound"
        # Call-intelligence payload (all optional — older gateway builds omit them)
        caller_name = val("caller_name")
        to_number = val("to_number")
        call_status = val("status") or "completed"
        duration = val("duration")
        start_time = val("start_time")
        follow_up_actions = val("follow_up_actions")
        sentiment = val("sentiment")
        escalation_risk = val("escalation_risk")
        analysis = val("analysis")
        ivr_selection = val("ivr_selection")
        agent_user = val("agent_user")
        agent_name = val("agent_name")
        voicemail_url = val("voicemail_url")

        is_missed = str(call_status).strip().lower() == "missed"
        info = _get_caller_info(
            customer_phone, twilio_caller_name=caller_name, create_if_missing=not is_missed
        )
        customer_name = info.get('customer')
        contact_name = info.get('contact')
        display_name = info.get('display_name')

        existing_comm = []
        if call_sid and str(call_sid).strip().lower() not in ["undefined", "null", "none", ""]:
            # Canonical cross-ref first: the Call Log for this SID already knows
            # its Communication. Fall back to the legacy subject-LIKE match.
            linked_comm = None
            if frappe.db.exists("Call Log", str(call_sid).strip()):
                linked_comm = frappe.db.get_value(
                    "Call Log", str(call_sid).strip(), "custom_communication"
                )
            if linked_comm and frappe.db.exists("Communication", linked_comm):
                existing_comm = [frappe._dict(name=linked_comm)]
            else:
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
                "sender": "triton@sapphirefountains.com",
                "sender_full_name": "Triton",
                "owner": "triton@sapphirefountains.com",
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
                frappe.db.set_value("ToDo", t.name, "allocated_to", "triton@sapphirefountains.com")
                frappe.db.set_value("ToDo", t.name, "status", "Closed")

        email_attachments = []
        recording_file_url = None
        recording_file_docname = None

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
                recording_file_url = file_doc.file_url
                recording_file_docname = file_doc.name

                email_attachments.append({
                    "fname": file_doc.file_name,
                    "fcontent": file_content
                })
            except Exception as fe:
                frappe.log_error(f"Failed to attach audio file: {str(fe)}", "Triton File Error")
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
                recording_file_url = file_doc.file_url
                recording_file_docname = file_doc.name

                email_attachments.append({
                    "fname": file_doc.file_name,
                    "fcontent": file_content
                })
            except Exception as fe:
                frappe.log_error(f"Failed to attach multipart audio file: {str(fe)}", "Triton File Error")

        # Mirror the recording into the Operations Shared Drive (monthly
        # YYYY_MM folders). Queued in the background and gated on
        # Triton Settings.call_recordings_drive_folder, so Drive latency or
        # misconfiguration never affects this webhook.
        if recording_file_docname:
            from erpnext_enhancements.api.call_recording_export import enqueue_recording_export

            enqueue_recording_export(
                call_sid=call_sid,
                when=start_time,
                direction=direction,
                caller_name=caller_name or display_name,
                caller_number=customer_phone,
                file_docname=recording_file_docname,
            )

        # --- Call Intelligence: upsert the stock Call Log for this call -----
        # Never lets an intelligence failure break the webhook (the
        # Communication + recording are already committed work).
        try:
            from erpnext_enhancements.api.call_intelligence import upsert_call_log

            if direction and str(direction).strip().lower() in ("outbound", "outgoing"):
                ci_from, ci_to = to_number, customer_phone
            else:
                ci_from, ci_to = customer_phone, to_number

            upsert_call_log(
                call_sid,
                direction=direction,
                from_number=ci_from,
                to_number=ci_to,
                status=call_status,
                duration=duration,
                start_time=start_time,
                caller_name=caller_name or display_name,
                customer=customer_name,
                contact=contact_name,
                summary=summary,
                follow_up_actions=follow_up_actions,
                sentiment=sentiment,
                escalation_risk=escalation_risk,
                analysis=analysis,
                ivr_selection=ivr_selection,
                agent_user=agent_user,
                agent_name=agent_name,
                recording_file_url=recording_file_url,
                voicemail_url=voicemail_url,
                communication=comm.name,
            )
        except Exception as ce:
            frappe.log_error(f"Call Log upsert failed for {call_sid}: {str(ce)}", "Call Intelligence")

        try:
            settings = frappe.get_cached_doc("Triton Settings")
            digest = settings.get("send_call_email_digest")
            send_digest = True if digest is None else bool(frappe.utils.cint(digest))
        except Exception:
            send_digest = True

        if not send_digest:
            frappe.db.commit()
            return {"status": "success", "communication_id": comm.name}

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
                subject=f"New Triton {email_subject_type} from {display_name}",
                message=message_html,
                attachments=email_attachments,
                now=True
            )
        except Exception as ee:
            frappe.log_error(f"Failed to send email: {str(ee)}", "Triton Email Error")

        frappe.db.commit()
        return {"status": "success", "communication_id": comm.name}

    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(f"Critical sync failure: {str(e)}", "Triton Sync Error")
        frappe.response["http_status_code"] = 500
        return {"status": "error", "message": str(e)}

# The single shared desk identity from before per-user identities existed.
# Still used when Triton Settings.softphone_users is empty, and dialed by older
# Triton builds via their TWILIO_CLIENT_IDENTITY env fallback.
LEGACY_SOFTPHONE_IDENTITY = "nikolas_erpnext"


def _softphone_users(settings):
    """The configured answerer emails from ``Triton Settings.softphone_users``
    (comma/newline separated), original case preserved, [] when unset."""
    allowed = (getattr(settings, "softphone_users", "") or "").strip()
    if not allowed:
        return []
    return [u.strip() for u in allowed.replace("\n", ",").split(",") if u.strip()]


def _softphone_identity(user_email):
    """Stable per-user Twilio Client identity for a desk softphone, e.g.
    ``erpnext_jane_doe_sapphirefountains_com`` (Twilio identities allow only
    alphanumerics and a few safe characters, so everything else becomes ``_``)."""
    return "erpnext_" + re.sub(r"[^A-Za-z0-9_]", "_", (user_email or "").strip().lower())


@frappe.whitelist()
def get_softphone_token():
    """Mint a short-lived Twilio Voice access-token JWT for the browser softphone.

    Authenticated whitelist, called from ``public/js/telephony_client.js``.
    Reads Twilio API key SID/secret, TwiML app SID, and account SID from Triton
    Settings (with fallbacks to ``frappe.conf`` / env for the account SID);
    ``frappe.throw`` if any are missing. Grants both outgoing (via the TwiML
    app) and incoming voice.

    Identity: when ``Triton Settings.softphone_users`` is set, each listed user
    registers their OWN identity (``_softphone_identity``) so every answerer's
    desk rings independently — the Triton gateway fetches the identity list via
    ``get_telephony_routing`` and dials them all in parallel. Users not in the
    list get ``None`` and the client skips device setup (realtime call
    notifications still work for them). When the field is empty, everyone
    shares the legacy single identity (last registration wins) for backward
    compatibility. Returns the JWT as a string, or None for non-answerers.
    """
    settings = frappe.get_doc("Triton Settings")

    users = _softphone_users(settings)
    if users:
        if frappe.session.user.lower() not in {u.lower() for u in users}:
            return None
        identity = _softphone_identity(frappe.session.user)
    else:
        identity = LEGACY_SOFTPHONE_IDENTITY

    twilio_api_key_sid = getattr(settings, "twilio_api_key_sid", None)
    twilio_api_secret = settings.get_password("twilio_api_secret", raise_exception=False)
    twilio_twiml_app_sid = getattr(settings, "twilio_twiml_app_sid", None)

    if not all([twilio_api_key_sid, twilio_api_secret, twilio_twiml_app_sid]):
        frappe.throw("Twilio softphone credentials are not fully configured in Triton Settings.")

    account_sid = getattr(settings, "twilio_account_sid", None) or frappe.conf.get("twilio_account_sid") or os.environ.get("TWILIO_ACCOUNT_SID")
    if not account_sid:
        frappe.throw("Twilio Account SID is missing. Please configure it in Triton Settings.")

    token = AccessToken(account_sid, twilio_api_key_sid, twilio_api_secret, identity=identity)
    voice_grant = VoiceGrant(outgoing_application_sid=twilio_twiml_app_sid, incoming_allow=True)
    token.add_grant(voice_grant)

    jwt_token = token.to_jwt()
    return jwt_token.decode("utf-8") if isinstance(jwt_token, bytes) else str(jwt_token)


@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def get_telephony_routing():
    """Routing config for the Triton voice gateway (Bearer/``token``-guarded).

    Returns the Twilio Client identities the gateway should dial for the
    ERPNext desk softphone(s) — per-user identities for every configured
    ``softphone_users`` entry, or the legacy shared identity when the field is
    empty — plus the business caller-ID number (``primary_twilio_number``)
    outbound calls and SMS should present. Triton caches this briefly and
    falls back to its env config if the fetch fails, so editing Triton
    Settings is the only configuration step needed on this side.
    """
    settings = frappe.get_doc("Triton Settings")
    users = _softphone_users(settings)
    if users:
        identities = [_softphone_identity(u) for u in users]
    else:
        identities = [LEGACY_SOFTPHONE_IDENTITY]
    return {
        "erpnext_client_identities": identities,
        "primary_number": (getattr(settings, "primary_twilio_number", "") or "").strip() or None,
    }

@frappe.whitelist(allow_guest=True)
@validate_twilio_request
def receive_mms():
    """Twilio MMS webhook: download inbound media and attach it to the Customer.

    Guest endpoint guarded by ``@validate_twilio_request`` (Twilio signature).
    Locates the Customer for the ``From`` number, fetches ``MediaUrl0`` over
    HTTP, and saves it as a private File attached to that Customer. Returns "OK".
    """
    frappe.set_user("triton@sapphirefountains.com")

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
    """Email a voicemail / message summary to info@sapphirefountains.com.

    Authenticated whitelist. Side effect: sends one email immediately
    (``now=True``). Returns ``{"status": ...}``; errors are logged and returned.
    """
    try:
        message_html = f"<strong>Caller Number:</strong> {caller_number}<br><br><strong>Message/Summary:</strong><br>{body}"
        
        frappe.sendmail(
            recipients=["info@sapphirefountains.com"],
            subject=f"Triton Message: {subject}",
            message=message_html,
            now=True
        )
        return {"status": "success"}
    except Exception as e:
        frappe.log_error(f"Failed to send email: {str(e)}", "Triton Email Error")
        return {"status": "error", "message": str(e)}

def analyze_transfer_transcript(transcript, customer_name):
    """Placeholder hook for future call-transfer transcript analysis. No-op."""
    pass

@frappe.whitelist()
def trigger_outbound_call(doctype, docname, target_number):
    """Initiate a click-to-call via the Triton gateway from a Desk form.

    Authenticated whitelist, called from contact.js / customer.js / lead.js.
    Resolves the *calling* user's own active Employee and cell number from the
    session (``frappe.throw`` if absent). For Customer/Contact references it
    prefers the stored phone field over ``target_number``. POSTs to
    ``<gateway_url>/api/outbound-call`` with the ``admin_webhook_secret`` as a
    Bearer token. The gateway then bridges the employee's cell to the target.
    Returns a success dict or throws on gateway failure (logged).
    """
    try:
        user = frappe.session.user
        employee_map = frappe.get_all("Employee", filters={"user_id": user, "status": "Active"}, fields=["name", "cell_number"])

        # The rep's own number to ring first. Employee Cell Number is the
        # source of truth (kept synced onto User.phone by
        # sync_contact.sync_employee_phone_to_user); the User fields cover
        # users without an Employee record or whose sync hasn't run yet.
        employee_number = (employee_map[0].cell_number or "").strip() if employee_map else ""
        if not employee_number:
            user_phone, user_mobile = frappe.db.get_value(
                "User", user, ["phone", "mobile_no"]
            ) or (None, None)
            employee_number = (user_phone or "").strip() or (user_mobile or "").strip()
        if not employee_number:
            frappe.throw(_(
                "No phone number found for your user. Set the Cell Number on your "
                "Employee profile (it syncs to your User profile) before placing calls."
            ))

        settings = frappe.get_doc("Triton Settings")
        
        # Use the correct field 'gateway_url' instead of 'triton_base_url'
        triton_url = getattr(settings, "gateway_url", None)
        
        # Use the webhook secret for authentication as there is no specific API key field
        api_secret = settings.get_password("admin_webhook_secret", raise_exception=False)

        if not triton_url:
            frappe.throw(_("Gateway URL is not configured in Triton Settings. Please update the Triton Settings page."))

        if doctype == "Customer":
            target_number = frappe.db.get_value("Customer", docname, "custom_accounts_phone_number") or target_number
        elif doctype == "Contact":
            target_number = frappe.db.get_value("Contact", docname, "custom_phone_number") or target_number

        endpoint = f"{triton_url.rstrip('/')}/api/outbound-call"

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

        return {"status": "success", "message": _("Call initiated via Triton")}

    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Failed to trigger outbound call via Triton: {str(e)}", "Triton Outbound Call Error")
        frappe.throw(_("Failed to initiate call via Triton. Please check error logs."))
    except Exception as e:
        frappe.log_error(f"Unexpected error in trigger_outbound_call: {str(e)}", "Triton Outbound Call Error")
        frappe.throw(str(e))


@frappe.whitelist()
def get_employee_number(employee_name):
    """Fuzzy-lookup an active Employee's cell number by name.

    Authenticated whitelist used by Triton for inbound call routing (e.g.
    "transfer me to <name>"). ``employee_name`` is matched with a ``LIKE
    %name%`` query against active Employees. Returns the cell number string or
    ``None`` (also ``None`` on error, which is logged).
    """
    # Used by Triton for inbound routing via fuzzy search on Employee
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
        frappe.log_error(f"Failed to get employee number for '{employee_name}': {str(e)}", "Triton Routing Error")
        return None


@frappe.whitelist()
def log_call_details(call_sid, direction, from_number, to_number, duration, transcript, summary, reference_doctype=None, reference_docname=None):
    """Create a Phone Communication for a completed (typically softphone) call.

    Authenticated whitelist. ``direction`` ("Outbound"/"Received") drives
    sent/received and sender fields and the display name (from the to/from
    number, overridden by the reference doc's name when provided). Customer /
    Contact / Lead references are attached via ``timeline_links``; any other
    reference type is set as ``reference_doctype``/``reference_name``. Commits;
    returns ``{"status", "communication_id"}`` (rollback + error dict on failure).
    """
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
            "sender": "triton@sapphirefountains.com" if direction == "Received" else from_number,
            "sender_full_name": "Triton" if direction == "Received" else None,
            "owner": "triton@sapphirefountains.com",
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
        frappe.log_error(f"Failed to log call details for {call_sid}: {str(e)}", "Triton Log Call Error")
        return {"status": "error", "message": str(e)}


@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def process_unified_sms(**kwargs):
    """Ingest an inbound SMS: log it, attach media, auto-assign, and alert.

    Guest endpoint guarded by ``@validate_webhook_secret`` (Triton gateway).
    Creates a received SMS Communication linked to the resolved Customer/Contact;
    optionally records AI analysis/sentiment as a Comment and decodes base64
    media into private File attachments.

    Intelligent assignment: assigns the Communication (via ``assign_to``) to (1)
    whoever last *sent* an SMS to this number, else (2) whoever a prior inbound
    SMS was assigned to, else (3) the info@ inbox — non-fallback assignments get
    High priority. If ``is_urgent``, creates Notification Log alerts for the
    assignee and every "Production Team" role holder.

    Side effects: many doc writes (``ignore_permissions``) + commit; on error,
    rollback and HTTP 500.
    """
    try:
        frappe.set_user("triton@sapphirefountains.com")

        from_number = kwargs.get("from_number") or frappe.form_dict.get("from_number")
        to_number = kwargs.get("to_number") or frappe.form_dict.get("to_number")
        content = kwargs.get("content") or frappe.form_dict.get("content")
        media = kwargs.get("media") or frappe.form_dict.get("media") or []
        sentiment = kwargs.get("sentiment") or frappe.form_dict.get("sentiment")
        is_urgent = kwargs.get("is_urgent") or frappe.form_dict.get("is_urgent") in [True, "true", "True", 1, "1"]
        ai_analysis = kwargs.get("ai_analysis") or frappe.form_dict.get("ai_analysis")

        info = _get_caller_info(from_number)
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
            "owner": "triton@sapphirefountains.com",
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

        if last_sent and last_sent[0].owner != "triton@sapphirefountains.com":
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
        frappe.log_error(f"Critical sync failure in process_unified_sms: {str(e)}", "Triton Sync Error")
        frappe.response["http_status_code"] = 500
        return {"status": "error", "message": str(e)}



def send_system_sms(target_number, message):
    """Send a system-initiated SMS via the Triton gateway. NOT whitelisted.

    Internal-alert variant of :func:`send_sms` for hook/scheduler callers
    (``erpnext_enhancements.status_alerts``): no session user, so no Employee
    resolution and no signature logic, and no Communication record — these are
    internal team alerts, not customer correspondence (the in-app trail is the
    caller's Notification Log). Raises on a missing gateway config or a gateway
    failure; callers decide whether one bad send aborts the batch.
    """
    if not target_number or not message:
        frappe.throw(_("Target number and message are required."))

    settings = frappe.get_doc("Triton Settings")
    triton_url = getattr(settings, "gateway_url", None)
    if not triton_url:
        frappe.throw(_("Gateway URL is not configured in Triton Settings."))
    api_secret = settings.get_password("admin_webhook_secret", raise_exception=False)

    headers = {"Content-Type": "application/json"}
    if api_secret:
        headers["Authorization"] = f"Bearer {api_secret}"

    response = requests.post(
        f"{triton_url.rstrip('/')}/api/send-sms",
        json={"to_number": target_number, "content": message, "media_urls": []},
        headers=headers,
        timeout=15,
    )
    response.raise_for_status()


@frappe.whitelist()
def send_sms(target_number, message, media_urls=None, reference_doctype=None, reference_docname=None):
    """Send an outbound SMS via the Triton gateway and log it.

    Authenticated whitelist, called from ``public/js/telephony_client.js``.
    Resolves the sending user's active Employee (``frappe.throw`` if none).
    Appends a " - [Employee Name]" signature only when no SMS has been sent to
    this number in the last 24h (avoids repeating the signature in a thread).
    POSTs to ``<gateway_url>/api/send-sms`` with the ``admin_webhook_secret``
    Bearer token, then records a *Sent* SMS Communication owned by the user,
    linked to the reference doc or resolved Customer/Contact. Commits; throws on
    gateway failure (logged).
    """
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

        # create_if_missing=False: texting an arbitrary number must not mint a
        # junk "Unknown Caller" Customer — link the CRM records when they
        # exist, otherwise just log the Communication unlinked.
        info = _get_caller_info(target_number, create_if_missing=False)
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

        settings = frappe.get_doc("Triton Settings")
        triton_url = getattr(settings, "gateway_url", None)
        api_secret = settings.get_password("admin_webhook_secret", raise_exception=False)

        if not triton_url:
            frappe.throw(_("Gateway URL is not configured in Triton Settings. Please update the Triton Settings page."))

        endpoint = f"{triton_url.rstrip('/')}/api/send-sms"

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

        # NOTE: no local `import requests` here — the module-level import is in
        # scope, and a function-local import made `requests` a local name for
        # the WHOLE function, so the `except requests.exceptions...` below
        # raised UnboundLocalError whenever an exception fired before this
        # line (e.g. the get_caller_info auth throw this masked).
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

        return {"status": "success", "message": _("SMS sent successfully via Triton."), "communication_id": comm.name}

    except requests.exceptions.RequestException as e:
        frappe.log_error(f"Failed to send SMS via Triton: {str(e)}", "Triton Outbound SMS Error")
        frappe.throw(_("Failed to send SMS via Triton. Please check error logs."))
    except Exception as e:
        frappe.log_error(f"Unexpected error in send_sms: {str(e)}", "Triton Outbound SMS Error")
        frappe.throw(str(e))
