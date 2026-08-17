# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every call the ``/feedback`` SPA makes — employee intake, review, and the confirm step.

Answers one question: *how does an employee's complaint become tasks on a live dev board
without anybody transcribing it, and without a model ever writing to that board?*

The lifecycle, and where each part lives:

1. ``submit_request`` — anybody logged in files one. Nothing else happens.
2. ``review_decision`` — a System Manager approves, rejects, or closes it as a duplicate.
   Approving enqueues :mod:`product_feedback.breakdown`, which asks Triton for a proposal
   and writes it to a child table. **No ``Task`` exists at this point.**
3. ``save_proposal`` / ``create_tasks`` — the reviewer edits the proposal and confirms it.
   ``create_tasks`` is the human review in the sense ``api/training_ai.py`` established: the
   accept call is what stamps a named person against a model's output.

--------------------------------------------------------------------------------------
Things this module is careful about
--------------------------------------------------------------------------------------

**Every endpoint is ``methods=["POST"]``.** Not CSRF theatre — these carry a request id and
a reviewer's decision, and a GET writes both into the web server's access log, the browser's
history and the ``Referer`` header of whatever the reader clicks next. ``training.py`` made
the same change in v1.299.4 and ``tests/test_feedback_endpoint_surface.py`` asserts it here.

**Identity is derived, never accepted.** A ``requested_by`` in a payload is dropped. So is a
``status``, a ``decided_by`` and a ``created_task``. The allowlists below are the whole
mechanism, and — following ``training_author.save_draft_version`` — a field that is refused
is *reported back* in ``rejected`` rather than dropped in silence. Autosave that answers
"saved" while losing the reviewer's paragraph is worse than a save that fails, because the
loss is only discovered on reload.

**The requester never gets write permission on their own request.** They hold ``read`` with
``if_owner`` and nothing more. Granting write would let them move ``status`` themselves, and
``Submitted -> Approved`` is a *legal* transition — so the transition table would wave
through a self-approval. Attachments are linked here, server-side, for exactly that reason:
see :func:`_link_attachments`.

**Reads are gated on owner-or-reviewer, by hard equality.** No role bypass beyond
``System Manager``; the requester's own row is matched on ``requested_by ==
frappe.session.user``, the same shape ``training.py`` uses for attempt-scoped calls.

Indentation is 4 spaces, matching the majority of ``api/``.
"""

import json

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from erpnext_enhancements.product_feedback.doctype.product_feedback_settings.product_feedback_settings import (
    allowed_projects,
    get_settings,
)
from erpnext_enhancements.product_feedback.states import RequestState

DOCTYPE = "Enhancement Request"

REVIEWER_ROLE = "System Manager"

#: What a requester may set when filing. Everything else in the payload is refused and
#: reported. `requested_by` is deliberately absent — it comes from the session.
SUBMIT_ALLOWED_FIELDS = frozenset(
    {
        "title",
        "request_type",
        "description",
        "steps_to_reproduce",
        "impact",
        "context_url",
        "context_doctype",
        "context_docname",
        "context_user_agent",
        "context_app_version",
    }
)

#: What a reviewer may change on a proposed row. `created_task` is absent on purpose: it is
#: the idempotency key, and a client that could set it could make `create_tasks` skip rows.
PROPOSED_TASK_ALLOWED_FIELDS = frozenset(
    {
        "include",
        "subject",
        "project",
        "priority",
        "expected_hours",
        "parent_task",
        "group_subject",
        "depends_on_idx",
        "description",
    }
)

#: Server-owned mirrors the client echoes back. Skipped in silence rather than reported,
#: because echoing them is not lost work. Same idea as `training_author`'s `*_ECHOED_KEYS`.
_ECHOED_KEYS = frozenset(
    {"name", "idx", "doctype", "parent", "parenttype", "parentfield", "created_task", "owner"}
)

VALID_REQUEST_TYPES = ("Feature", "Bug")
VALID_IMPACTS = (
    "Blocking my work",
    "Painful but I can work around it",
    "Nice to have",
)

#: A screenshot or two. The cap is on the link step rather than the upload, because the
#: upload is Frappe's own endpoint and this is the seam we own.
MAX_ATTACHMENTS = 5

MAX_TITLE_CHARS = 200
MAX_BODY_CHARS = 20000
MIN_DESCRIPTION_CHARS = 20

#: Rejecting somebody's idea without saying why reads as being ignored, and the same request
#: arrives again next month. Long enough to be a sentence, short enough not to be a chore.
MIN_REJECTION_REASON_CHARS = 15


# --------------------------------------------------------------------------- read surface


@frappe.whitelist(methods=["POST"])
def get_bootstrap():
    """Everything the SPA needs on load: who you are, your requests, and the queue if you review.

    One call rather than three, because the shell deliberately carries no domain data — a
    cached back-navigation must not show a stale queue.
    """
    _require_session()
    settings = get_settings()
    reviewer = _is_reviewer()

    return {
        "user": frappe.session.user,
        "full_name": frappe.db.get_value("User", frappe.session.user, "full_name") or "",
        "is_reviewer": reviewer,
        "paused": settings["paused"],
        "request_types": list(VALID_REQUEST_TYPES),
        "impacts": list(VALID_IMPACTS),
        "my_requests": _my_requests(),
        "review_queue": _review_queue() if reviewer else [],
    }


@frappe.whitelist(methods=["POST"])
def get_request(name):
    """One request in full, including the proposal. Owner or reviewer only."""
    _require_session()
    doc = _readable(name)
    return _serialise(doc, full=True)


# ------------------------------------------------------------------------------- intake


@frappe.whitelist(methods=["POST"])
def submit_request(payload=None, attachments=None):
    """File a new request as the session user.

    Returns ``{"name", "rejected"}`` — ``rejected`` naming any payload key that was refused,
    so a field the SPA adds without a matching entry in ``SUBMIT_ALLOWED_FIELDS`` surfaces
    as a warning on the first submit rather than as silent data loss.
    """
    _require_session()
    if get_settings()["paused"]:
        frappe.throw(_("New requests are paused right now."), frappe.ValidationError)

    values, rejected = _filter_payload(_as_dict(payload), SUBMIT_ALLOWED_FIELDS)
    _validate_submission(values)

    doc = frappe.new_doc(DOCTYPE)
    doc.update(values)
    # Session-derived, always. See the module docstring.
    doc.requested_by = frappe.session.user
    doc.requested_at = now_datetime()
    doc.status = RequestState.SUBMITTED.value
    doc.insert(ignore_permissions=True)

    linked, attachment_problems = _link_attachments(doc.name, attachments)

    _notify("request_submitted", doc.name)
    return {"name": doc.name, "rejected": sorted(rejected) + attachment_problems, "attachments": linked}


# ------------------------------------------------------------------------------- review


@frappe.whitelist(methods=["POST"])
def review_decision(
    name,
    decision,
    reason=None,
    target_erpnext=None,
    target_triton=None,
    duplicate_of_task=None,
):
    """Approve, reject, or close a request as a duplicate. System Managers only.

    Approving is the only branch that costs anything: it enqueues the Triton call. Rejecting
    and de-duplicating are terminal and deliberately cheap — a request that is obviously not
    happening should not spend a model call to find that out.
    """
    _require_reviewer()
    doc = frappe.get_doc(DOCTYPE, name)
    decision = (decision or "").strip().lower()

    doc.decided_by = frappe.session.user
    doc.decided_at = now_datetime()
    doc.decision_reason = (reason or "").strip()[:1000]

    if decision == "approve":
        erpnext_target = cint(target_erpnext)
        triton_target = cint(target_triton)
        if not (erpnext_target or triton_target):
            frappe.throw(
                _("Pick at least one of ERPNext or Triton before approving — there is nothing to plan otherwise."),
                frappe.ValidationError,
            )
        doc.target_erpnext = erpnext_target
        doc.target_triton = triton_target
        doc.status = RequestState.APPROVED.value

    elif decision == "reject":
        if len(doc.decision_reason) < MIN_REJECTION_REASON_CHARS:
            frappe.throw(
                _("Say why in at least {0} characters — the requester is told, and a rejection with no reason reads as being ignored.").format(
                    MIN_REJECTION_REASON_CHARS
                ),
                frappe.ValidationError,
            )
        doc.status = RequestState.REJECTED.value

    elif decision == "duplicate":
        target = (duplicate_of_task or "").strip()
        if not target or not frappe.db.exists("Task", target):
            frappe.throw(_("Name the task this duplicates."), frappe.ValidationError)
        doc.duplicate_of_task = target
        doc.status = RequestState.DUPLICATE.value

    else:
        frappe.throw(_("Unknown decision {0}.").format(decision), frappe.ValidationError)

    doc.save(ignore_permissions=True)

    if doc.status == RequestState.APPROVED.value:
        _enqueue_breakdown(doc.name)
    _notify("decision_made", doc.name)

    return _serialise(doc, full=True)


@frappe.whitelist(methods=["POST"])
def rerun_breakdown(name):
    """Ask Triton again, from ``Breakdown Ready`` or ``Breakdown Failed``.

    Goes back to ``Approved`` rather than inventing a ``Regenerating`` state: that is the
    state the hourly sweeper already re-drives, so a re-run and a deploy-lost job are the
    same recovery. See ``product_feedback/states.py``.
    """
    _require_reviewer()
    doc = frappe.get_doc(DOCTYPE, name)
    if doc.status not in (RequestState.BREAKDOWN_READY.value, RequestState.BREAKDOWN_FAILED.value):
        frappe.throw(
            _("A breakdown can only be re-run once one has been attempted."), frappe.ValidationError
        )
    doc.breakdown_error = ""
    doc.status = RequestState.APPROVED.value
    doc.save(ignore_permissions=True)
    _enqueue_breakdown(doc.name)
    return _serialise(doc, full=True)


@frappe.whitelist(methods=["POST"])
def save_proposal(name, rows=None):
    """Persist the reviewer's edits without creating anything.

    Split from :func:`create_tasks` so a reviewer can put a proposal down and come back to
    it. Both share ``_apply_row_edits``, so the rows that get written are the rows that got
    saved — there is no second code path where the two could disagree.
    """
    _require_reviewer()
    doc = frappe.get_doc(DOCTYPE, name)
    _require_editable(doc)
    rejected = _apply_row_edits(doc, rows)
    doc.save(ignore_permissions=True)
    return {"request": _serialise(doc, full=True), "rejected": rejected}


@frappe.whitelist(methods=["POST"])
def create_tasks(name, rows=None):
    """**The confirm step.** Apply the reviewer's edits, then create the Tasks.

    Returns the created task names and any per-row failures. A partial run leaves the request
    in ``Breakdown Ready`` with the successful rows stamped, so pressing the button again
    creates only what is missing — see ``product_feedback/task_writer.py``.
    """
    _require_reviewer()
    from erpnext_enhancements.product_feedback import task_writer

    doc = frappe.get_doc(DOCTYPE, name)
    _require_editable(doc)
    rejected = _apply_row_edits(doc, rows)
    doc.save(ignore_permissions=True)

    try:
        result = task_writer.create_tasks_for(doc.name)
    except task_writer.ProjectRefused as exc:
        frappe.throw(str(exc), frappe.PermissionError)

    if result["created"]:
        _notify("tasks_created", doc.name, result["created"])

    fresh = frappe.get_doc(DOCTYPE, doc.name)
    return {
        "request": _serialise(fresh, full=True),
        "created": result["created"],
        "groups": result["groups"],
        "failures": result["failures"],
        "complete": result["complete"],
        "rejected": rejected,
    }


# -------------------------------------------------------------------------------- gates


def _require_session():
    """Refuse Guest explicitly rather than trusting ``@frappe.whitelist()``'s default.

    Same reasoning as ``chat/api/_common.require_session``: a whitelisted endpoint is
    reachable by whatever the framework currently considers authenticated, and the SPA's own
    page gate is a courtesy, not a control.
    """
    if frappe.session.user in ("", None, "Guest"):
        frappe.throw(_("Sign in to use this."), frappe.PermissionError)


def _is_reviewer():
    return REVIEWER_ROLE in frappe.get_roles()


def _require_reviewer():
    _require_session()
    if not _is_reviewer():
        frappe.throw(_("Only a System Manager can review requests."), frappe.PermissionError)


def _readable(name):
    """The request, if the caller owns it or reviews. Hard equality on the owner, no bypass."""
    doc = frappe.get_doc(DOCTYPE, name)
    if _is_reviewer() or (doc.requested_by or "") == frappe.session.user:
        return doc
    frappe.throw(_("Not permitted."), frappe.PermissionError)


def _require_editable(doc):
    if doc.status != RequestState.BREAKDOWN_READY.value:
        frappe.throw(
            _("The proposal can only be edited while the request is in Breakdown Ready."),
            frappe.ValidationError,
        )


# ----------------------------------------------------------------------------- payloads


def _as_dict(payload):
    """A dict from whatever the client sent. Frappe hands JSON bodies through as strings."""
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, str) and payload.strip():
        try:
            parsed = json.loads(payload)
        except ValueError:
            frappe.throw(_("Malformed payload."), frappe.ValidationError)
        if isinstance(parsed, dict):
            return parsed
    return {}


def _as_list(rows):
    if isinstance(rows, list):
        return rows
    if isinstance(rows, str) and rows.strip():
        try:
            parsed = json.loads(rows)
        except ValueError:
            frappe.throw(_("Malformed rows."), frappe.ValidationError)
        if isinstance(parsed, list):
            return parsed
    return []


def _filter_payload(payload, allowed):
    """``(values, rejected)`` — the allowlisted subset, and the names of what was refused."""
    values = {}
    rejected = []
    for key, value in payload.items():
        if key in allowed:
            values[key] = value
        elif key not in _ECHOED_KEYS:
            rejected.append(key)
    return values, rejected


def _validate_submission(values):
    """Shape and length checks on a new request. Everything user-facing."""
    title = (values.get("title") or "").strip()
    if not title:
        frappe.throw(_("Give it a title."), frappe.ValidationError)
    values["title"] = title[:MAX_TITLE_CHARS]

    description = (values.get("description") or "").strip()
    if len(frappe.utils.strip_html(description)) < MIN_DESCRIPTION_CHARS:
        frappe.throw(
            _("Describe it in at least {0} characters — a one-word report costs a round trip you have already forgotten the answer to.").format(
                MIN_DESCRIPTION_CHARS
            ),
            frappe.ValidationError,
        )
    values["description"] = description[:MAX_BODY_CHARS]
    values["steps_to_reproduce"] = (values.get("steps_to_reproduce") or "").strip()[:MAX_BODY_CHARS]

    request_type = (values.get("request_type") or "").strip()
    if request_type not in VALID_REQUEST_TYPES:
        frappe.throw(_("Pick Feature or Bug."), frappe.ValidationError)

    impact = (values.get("impact") or "").strip()
    if impact not in VALID_IMPACTS:
        frappe.throw(_("Pick an impact."), frappe.ValidationError)

    # Captured context is client-supplied and stays untrusted: truncated, stored, displayed
    # as text, never followed and never used in a query.
    for field, limit in (
        ("context_url", 500),
        ("context_doctype", 140),
        ("context_docname", 140),
        ("context_user_agent", 300),
        ("context_app_version", 140),
    ):
        values[field] = (values.get(field) or "").strip()[:limit]


def _apply_row_edits(doc, rows):
    """Apply the reviewer's edits to ``proposed_tasks``. Returns the refused field names.

    Rows are matched on the child row's ``name``, not on position: the SPA can reorder or
    hide rows and a positional match would then write one row's subject onto another. A row
    name the request does not carry is ignored.
    """
    if rows is None:
        return []

    permitted = set(allowed_projects())
    by_name = {row.name: row for row in (doc.get("proposed_tasks") or [])}
    rejected = set()

    for raw in _as_list(rows):
        if not isinstance(raw, dict):
            continue
        target = by_name.get((raw.get("name") or "").strip())
        if target is None:
            continue
        if (target.created_task or "").strip():
            # Already written. Editing it here would make the proposal disagree with the Task
            # it produced, which is the one thing the audit trail cannot survive.
            continue

        values, refused = _filter_payload(raw, PROPOSED_TASK_ALLOWED_FIELDS)
        rejected.update(refused)

        if "project" in values:
            project = (values.get("project") or "").strip()
            if project not in permitted:
                rejected.add("project")
                values.pop("project")

        if "include" in values:
            values["include"] = cint(values["include"])
        if "expected_hours" in values:
            values["expected_hours"] = max(0.0, flt(values["expected_hours"]))
        if "depends_on_idx" in values:
            values["depends_on_idx"] = max(0, cint(values["depends_on_idx"]))
        if "priority" in values and values["priority"] not in ("Low", "Medium", "High", "Urgent"):
            rejected.add("priority")
            values.pop("priority")

        for key, value in values.items():
            target.set(key, value)

    return sorted(rejected)


# -------------------------------------------------------------------------- attachments


def _link_attachments(request_name, attachments):
    """Attach already-uploaded private Files to the request. Returns ``(linked, problems)``.

    **Why this exists rather than a plain upload against the request.** Frappe's
    ``upload_file`` calls ``check_write_permission(doctype, docname)``, and a requester holds
    only ``read`` on their own request — deliberately, because write would let them move
    ``status``, and ``Submitted -> Approved`` is a legal transition. So the SPA uploads with
    **no** ``doctype``/``docname`` (permitted for any logged-in user: ``check_write_permission``
    returns immediately when ``doctype`` is empty, confirmed against the v16 tree) and this
    links the result.

    Three checks before linking, and the owner one is the important one: a File the caller
    does not own is somebody else's, and re-pointing an already-attached File would move an
    attachment off whatever document currently holds it.
    """
    names = [n for n in (_as_list(attachments) or []) if isinstance(n, str) and n.strip()]
    if not names:
        return [], []

    linked = []
    problems = []
    for file_name in names[:MAX_ATTACHMENTS]:
        row = frappe.db.get_value(
            "File",
            file_name.strip(),
            ["name", "owner", "attached_to_doctype", "file_url"],
            as_dict=True,
        )
        if not row:
            problems.append(f"attachment {file_name}: not found")
            continue
        if row.owner != frappe.session.user:
            problems.append(f"attachment {file_name}: not yours")
            continue
        if row.attached_to_doctype:
            problems.append(f"attachment {file_name}: already attached to something else")
            continue
        try:
            frappe.db.set_value(
                "File",
                row.name,
                {
                    "attached_to_doctype": DOCTYPE,
                    "attached_to_name": request_name,
                    "is_private": 1,
                },
                update_modified=False,
            )
            linked.append(row.file_url)
        except Exception:
            problems.append(f"attachment {file_name}: could not be linked")

    if len(names) > MAX_ATTACHMENTS:
        problems.append(f"only the first {MAX_ATTACHMENTS} attachments were linked")
    return linked, problems


# ------------------------------------------------------------------------ serialisation


def _my_requests():
    return frappe.get_all(
        DOCTYPE,
        filters={"requested_by": frappe.session.user},
        fields=["name", "title", "status", "request_type", "impact", "creation", "modified"],
        order_by="creation desc",
        limit=100,
    )


def _review_queue():
    """Everything a reviewer still has to do something about, oldest first.

    Terminal states are excluded: the queue is work, not history. ``Approved`` stays in it on
    purpose — a request sitting there with no proposal is a job a deploy destroyed, and the
    queue is where that becomes visible.
    """
    return frappe.get_all(
        DOCTYPE,
        filters={
            "status": [
                "in",
                [
                    RequestState.SUBMITTED.value,
                    RequestState.APPROVED.value,
                    RequestState.BREAKDOWN_READY.value,
                    RequestState.BREAKDOWN_FAILED.value,
                ],
            ]
        },
        fields=[
            "name",
            "title",
            "status",
            "request_type",
            "impact",
            "requested_by",
            "creation",
            "breakdown_error",
        ],
        order_by="creation asc",
        limit=200,
    )


def _serialise(doc, full=False):
    """A request as plain JSON for the SPA. Never leaks a field the reader may not have."""
    out = {
        "name": doc.name,
        "title": doc.title,
        "status": doc.status,
        "request_type": doc.request_type,
        "impact": doc.impact,
        "requested_by": doc.requested_by,
        "requester_name": frappe.db.get_value("User", doc.requested_by, "full_name") or doc.requested_by,
        "creation": str(doc.creation or ""),
        "decided_by": doc.decided_by,
        "decided_at": str(doc.decided_at or ""),
        "decision_reason": doc.decision_reason,
        "duplicate_of_task": doc.duplicate_of_task,
        "target_erpnext": cint(doc.target_erpnext),
        "target_triton": cint(doc.target_triton),
    }
    if not full:
        return out

    out.update(
        {
            "description": doc.description,
            "steps_to_reproduce": doc.steps_to_reproduce,
            "context": {
                "url": doc.context_url,
                "doctype": doc.context_doctype,
                "docname": doc.context_docname,
                "user_agent": doc.context_user_agent,
                "app_version": doc.context_app_version,
            },
            "breakdown_summary": doc.breakdown_summary,
            "breakdown_model": doc.breakdown_model,
            "breakdown_error": doc.breakdown_error,
            "projects": dict(zip(("erpnext", "triton"), allowed_projects())),
            "attachments": frappe.get_all(
                "File",
                filters={"attached_to_doctype": DOCTYPE, "attached_to_name": doc.name},
                fields=["name", "file_name", "file_url"],
                order_by="creation asc",
            ),
            "proposed_tasks": [
                {
                    "name": row.name,
                    "idx": row.idx,
                    "include": cint(row.include),
                    "subject": row.subject,
                    "project": row.project,
                    "priority": row.priority,
                    "expected_hours": flt(row.expected_hours),
                    "parent_task": row.parent_task,
                    "group_subject": row.group_subject,
                    "depends_on_idx": cint(row.depends_on_idx),
                    "description": row.description,
                    "created_task": row.created_task,
                }
                for row in (doc.get("proposed_tasks") or [])
            ],
            "duplicate_candidates": [
                {
                    "task": row.task,
                    "task_subject": row.task_subject,
                    "confidence": row.confidence,
                    "why": row.why,
                }
                for row in (doc.get("duplicate_candidates") or [])
            ],
        }
    )
    return out


# ------------------------------------------------------------------------------- shared


def _enqueue_breakdown(name):
    """Queue the Triton call. A failure here leaves the request in ``Approved``, which the
    hourly sweeper re-drives — so it is logged, not raised into the reviewer's face."""
    try:
        from erpnext_enhancements.product_feedback import breakdown

        breakdown.enqueue_breakdown(name)
    except Exception:
        try:
            frappe.log_error(frappe.get_traceback(), "Enhancement Request enqueue failed")
        except Exception:
            pass


def _notify(event, *args):
    """Notifications never fail the decision that triggered them."""
    try:
        from erpnext_enhancements.product_feedback import notify

        getattr(notify, event)(*args)
    except Exception:
        try:
            frappe.log_error(frappe.get_traceback(), f"Enhancement Request notify.{event} failed")
        except Exception:
            pass
