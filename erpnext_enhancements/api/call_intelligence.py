"""Call Intelligence — the stock Call Log becomes the system of record for calls.

Triton's post-call worker analyses every completed call (executive summary,
follow-up actions, sentiment, escalation risk, topics, compliance flags, CSAT)
and pushes the results to ERPNext. This module upserts that intelligence onto
the native **Call Log** doctype (Telephony module, docname == Twilio Call SID
via ``autoname: field:id``) so calls are browsable, filterable and chartable in
the desk without Triton's own frontend.

Callers:
        - ``api.telephony.process_unified_recording`` (the existing gateway
          webhook) calls :func:`upsert_call_log` after creating the transcript
          Communication and saving the recording File.
        - The Triton gateway may also POST directly to
          :func:`process_call_intelligence` (guest endpoint, Bearer-secret
          guarded) for calls that carry no recording payload — e.g. missed
          calls with only a voicemail URL.

Design rules:
        - **Idempotent by SID**: the Call Log docname is the call SID, so
          webhook re-deliveries update the same record instead of duplicating.
        - **Partial updates never blank fields**: only keys present (non-None)
          in the payload are written; a later status-only update leaves the
          intelligence fields untouched.
        - ``summary`` is written only while empty — the native "Call Summary"
          dialog writes the same field, and manual edits must survive webhook
          re-deliveries.
        - All ``custom_*`` intelligence fields are fixture-owned (read-only in
          the UI) and written exclusively by this module.
"""

import json

import frappe
from frappe.utils import cint, get_datetime

# Safe one-way import: telephony.py only imports this module lazily inside
# process_unified_recording, so no circular import at module load.
from erpnext_enhancements.api.telephony import get_caller_info, validate_webhook_secret

# Triton/Twilio call status -> stock Call Log status option
STATUS_MAP = {
    "completed": "Completed",
    "missed": "No Answer",
    "no-answer": "No Answer",
    "no_answer": "No Answer",
    "failed": "Failed",
    "busy": "Busy",
    "canceled": "Cancelled",
    "cancelled": "Cancelled",
    "in-progress": "In Progress",
    "in_progress": "In Progress",
    "ringing": "Ringing",
    "queued": "Queued",
}

SENTIMENTS = {"positive": "Positive", "neutral": "Neutral", "negative": "Negative"}
RISKS = {"low": "Low", "medium": "Medium", "high": "High"}

TRITON_USER = "triton@sapphirefountains.com"


def _is_valid_sid(call_sid):
    return bool(call_sid) and str(call_sid).strip().lower() not in ("undefined", "null", "none", "")


def _as_list(value):
    """Normalise a payload list field: accepts a list/tuple, a JSON-encoded
    array string, or a plain newline-separated string."""
    if not value:
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("["):
            try:
                return _as_list(json.loads(text))
            except Exception:
                pass
        return [line.strip() for line in text.splitlines() if line.strip()]
    return [str(value)]


def _as_dict(value):
    """Normalise a payload object field: accepts a dict or a JSON-encoded object."""
    if not value:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _map_status(status):
    if not status:
        return None
    return STATUS_MAP.get(str(status).strip().lower())


def _map_direction(direction):
    if not direction:
        return None
    d = str(direction).strip().lower()
    if d in ("inbound", "incoming", "in"):
        return "Incoming"
    if d in ("outbound", "outgoing", "out"):
        return "Outgoing"
    return None


def _get_or_create_call_type(label):
    """Resolve an IVR selection / intent label to a Telephony Call Type,
    creating (and submitting — the doctype is submittable) it when new.
    Returns the docname or None; failures are logged, never raised."""
    if not label:
        return None
    label = str(label).strip()
    if not label:
        return None
    existing = frappe.db.get_value("Telephony Call Type", {"call_type": label})
    if existing:
        return existing
    try:
        doc = frappe.get_doc({"doctype": "Telephony Call Type", "call_type": label})
        doc.insert(ignore_permissions=True)
        doc.submit()
        return doc.name
    except Exception:
        frappe.log_error(
            f"Failed to create Telephony Call Type '{label}'", "Call Intelligence"
        )
        return None


def _append_link(doc, link_doctype, link_name):
    if not link_name or not frappe.db.exists(link_doctype, link_name):
        return
    for row in doc.get("links") or []:
        if row.link_doctype == link_doctype and row.link_name == link_name:
            return
    doc.append("links", {"link_doctype": link_doctype, "link_name": link_name})


def upsert_call_log(
    call_sid,
    *,
    direction=None,
    from_number=None,
    to_number=None,
    status=None,
    duration=None,
    start_time=None,
    caller_name=None,
    customer=None,
    contact=None,
    lead=None,
    summary=None,
    follow_up_actions=None,
    sentiment=None,
    escalation_risk=None,
    analysis=None,
    ivr_selection=None,
    agent_user=None,
    agent_name=None,
    recording_file_url=None,
    voicemail_url=None,
    communication=None,
):
    """Create or update the Call Log for ``call_sid``. Returns the docname.

    Only non-None arguments are written, so partial payloads (e.g. a later
    status correction) never blank previously stored intelligence. Saves via
    the document API (not ``db_set``) so Value Change Notifications — High
    Escalation Risk Call, Compliance Flag on Call — fire.
    """
    if not _is_valid_sid(call_sid):
        return None
    call_sid = str(call_sid).strip()

    if frappe.db.exists("Call Log", call_sid):
        doc = frappe.get_doc("Call Log", call_sid)
        is_new = False
    else:
        doc = frappe.new_doc("Call Log")
        doc.id = call_sid
        doc.medium = "Triton"
        is_new = True

    if direction and (mapped := _map_direction(direction)):
        doc.type = mapped
    if from_number:
        doc.set("from", from_number)
    if to_number:
        doc.to = to_number
    if status and (mapped := _map_status(status)):
        doc.status = mapped
    if duration is not None and str(duration) != "":
        doc.duration = cint(duration)
    if start_time:
        try:
            doc.start_time = get_datetime(start_time)
            if doc.duration:
                doc.end_time = frappe.utils.add_to_date(doc.start_time, seconds=cint(doc.duration))
        except Exception:
            pass

    # Executive summary: the native "Call Summary" dialog writes the same
    # field — only fill while empty so manual edits survive re-deliveries.
    if summary and not (doc.get("summary") or "").strip():
        doc.summary = summary

    if recording_file_url:
        doc.recording_url = recording_file_url

    if customer:
        if frappe.db.exists("Customer", customer):
            doc.customer = customer
        _append_link(doc, "Customer", customer)
    _append_link(doc, "Contact", contact)
    _append_link(doc, "Lead", lead)

    if agent_user and frappe.db.exists("User", agent_user):
        doc.employee_user_id = agent_user
        employee = frappe.db.get_value("Employee", {"user_id": agent_user})
        if employee:
            doc.call_received_by = employee
    if call_type := _get_or_create_call_type(ivr_selection):
        doc.type_of_call = call_type

    # --- AI intelligence (fixture-owned custom fields) ---------------------
    analysis_data = _as_dict(analysis)

    if caller_name:
        doc.custom_caller_name = caller_name
    if agent_name:
        doc.custom_agent_name = agent_name
    if voicemail_url:
        doc.custom_voicemail_url = voicemail_url
    if communication and frappe.db.exists("Communication", communication):
        doc.custom_communication = communication

    sentiment = sentiment or analysis_data.get("sentiment")
    if sentiment and (mapped := SENTIMENTS.get(str(sentiment).strip().lower())):
        doc.custom_sentiment = mapped

    escalation_risk = escalation_risk or analysis_data.get("escalation_risk")
    if escalation_risk and (mapped := RISKS.get(str(escalation_risk).strip().lower())):
        doc.custom_escalation_risk = mapped

    if actions := _as_list(follow_up_actions):
        doc.custom_follow_up_actions = "\n".join(actions)

    if analysis_data:
        csat = analysis_data.get("customer_satisfaction")
        if csat is not None and str(csat) != "":
            doc.custom_csat_score = cint(csat)
        if topics := _as_list(analysis_data.get("topics")):
            doc.custom_topics = ", ".join(topics)
        flags = _as_list(analysis_data.get("compliance_flags"))
        if flags:
            doc.custom_compliance_flags = "\n".join(flags)
            doc.custom_has_compliance_flags = 1
        doc.custom_analysis_json = json.dumps(analysis_data, indent=1, sort_keys=True)

    if is_new:
        doc.insert(ignore_permissions=True)
    else:
        doc.save(ignore_permissions=True)
    return doc.name


@frappe.whitelist(allow_guest=True)
@validate_webhook_secret
def process_call_intelligence(**kwargs):
    """Standalone ingest endpoint for call intelligence without a recording.

    Guest endpoint guarded by the Triton webhook Bearer secret (same scheme as
    ``api.telephony``). Used for payloads that don't go through
    ``process_unified_recording`` — e.g. missed calls (``status: "missed"`` +
    ``voicemail_url``). Resolves the caller like the other webhook handlers,
    but never auto-creates Customers for missed calls (robocall protection).
    """
    try:
        frappe.set_user(TRITON_USER)

        def val(key):
            v = kwargs.get(key)
            return v if v is not None else frappe.form_dict.get(key)

        call_sid = val("call_sid")
        if not _is_valid_sid(call_sid):
            frappe.throw("Missing call_sid")

        status = (val("status") or "").strip().lower()
        direction = _map_direction(val("direction")) or "Incoming"
        customer_phone = val("customer_phone")

        customer = contact = display_name = None
        if customer_phone:
            info = get_caller_info(
                customer_phone,
                twilio_caller_name=val("caller_name"),
                create_if_missing=status != "missed",
            )
            customer = info.get("customer")
            contact = info.get("contact")
            display_name = info.get("display_name")

        if direction == "Outgoing":
            from_number, to_number = val("from_number"), customer_phone or val("to_number")
        else:
            from_number, to_number = customer_phone or val("from_number"), val("to_number")

        name = upsert_call_log(
            call_sid,
            direction=direction,
            from_number=from_number,
            to_number=to_number,
            status=status,
            duration=val("duration"),
            start_time=val("start_time"),
            caller_name=val("caller_name") or display_name,
            customer=customer,
            contact=contact,
            summary=val("summary"),
            follow_up_actions=val("follow_up_actions"),
            sentiment=val("sentiment"),
            escalation_risk=val("escalation_risk"),
            analysis=val("analysis"),
            ivr_selection=val("ivr_selection"),
            agent_user=val("agent_user"),
            agent_name=val("agent_name"),
            voicemail_url=val("voicemail_url"),
        )

        frappe.db.commit()
        return {"status": "success", "call_log": name}
    except Exception as e:
        frappe.db.rollback()
        frappe.log_error(f"Call intelligence ingest failed: {e}", "Call Intelligence")
        frappe.response["http_status_code"] = 500
        return {"status": "error", "message": str(e)}
