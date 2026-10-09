# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Telling the customer the date we are coming (Project Planner Phase 5, P5.4): both planners.

When a customer-facing date is set or changes, the customer gets one email saying when the crew
will be there. It covers both planners (Nik, 2026-10-09: "that should apply to the maintenance
planner and this planner"):

* a **project Task** marked *Customer-facing visit* (``Task.custom_customer_visit``) whose start
  date is set or changes while it is firm: not a pencil (``custom_tentative``), not finished, not a
  template or group task. A Planner Draft never reaches here at all: drafting never saves the Task,
  and publishing does, which is the moment it becomes real;
* a **maintenance visit** (Sapphire Maintenance Record, customer-facing by nature) whose
  ``scheduled_visit_date`` is set or changes while it is a draft that has not been started.

**Behind a switch that stays off** (Nik: "Set a toggle for that feature, so we can fully flesh it
out before we send our first email"). Project Planner Settings ``customer_date_confirmations``
defaults to 0, and while it is 0 nothing is queued and nothing is sent. :func:`preview` works
either way and never sends: it renders exactly what would go out, and to whom.

**The words live in the Desk**, in the Email Template "Planner Date Confirmation" (seeded once,
insert-only, by ``patches/seed_planner_date_confirmation_template.py``), so Nik designs the
message without a release. Its subject and body are Jinja with these variables: ``customer``,
``contact_name``, ``site``, ``site_address``, ``date``, ``date_iso``, ``arrival_window``,
``arrival_start``, ``arrival_end`` (blank unless the task has a time slot), ``crew`` (first names),
``crew_names`` ("Austin, Korben and Jesse"), ``contact_phone``, ``company`` and ``kind``
("project" or "maintenance"). The body is content only: it is wrapped in the shared email shell
(``email_style.wrap``) like every email this app sends. When the record is missing the built-in
default below is used.

Things this module is careful about, some of which look like bugs:

* **At most once per (document, date), through a database stamp.** ``customer_confirmed_for``
  (``custom_customer_confirmed_for`` on Task) is the date the customer was last told. The send
  locks the row (``for_update``), re-reads the stamp, sends and writes the stamp in one
  transaction; ``frappe.sendmail`` only queues an Email Queue row, so the email and the stamp
  commit or roll back together. A date moved away and back is a new change and is told again.
* **Re-drivable after a deploy.** The production deploy ``FLUSHDB``s redis, which destroys queued
  jobs (CLAUDE.md). So the trigger first writes the date into a second persisted field,
  ``customer_confirmation_due``, and only then enqueues the send after commit; a sweep every ten
  minutes (``scheduler_events``) sends whatever is still due. A lost job delays an email by ten
  minutes; it never loses it.
* **The form cannot write the stamps.** A Desk save posts every field back, read-only ones
  included, so a form opened before a send would otherwise put the old stamp back and the next
  sweep would email the customer twice. :func:`keep_stamps` (``validate``) always reloads both
  fields from the database on an existing document.
* **Stamps are written without touching ``modified``**, so a send never refuses the next drag on
  the planner with "changed by someone else".
* **ERPNext's template copy does not email anyone.** A Task inserted with ``template_task`` was
  dated by ``Project.create_task_from_template`` from the project's start date, not by a person,
  so its insert is skipped; the first deliberate date change after that is told.
* **No recipient, no noise.** The address is the customer's primary Contact's email (project →
  customer → primary contact; for a visit, the visit's own customer, else its contract's, else
  its project's), then the Customer's own email, then the project's ``custom_customer_email``. With
  none the send is skipped and the due date cleared, with nothing in the Error Log; the preview
  says why.
* **Never raises into a save.** The hooks log and return; the job logs a broken template at most
  once an hour per document and leaves it due, so fixing the template sends it.

The date, wording and decision helpers take plain values so ``tests/test_planner_phase5.py`` runs
them without a bench.
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext_enhancements.project_enhancements import crew_availability as engine

SETTINGS = "Project Planner Settings"
TOGGLE = "customer_date_confirmations"
PHONE_SETTING = "customer_confirmation_phone"
TEMPLATE = "Planner Date Confirmation"
TASK = "Task"
VISIT = "Sapphire Maintenance Record"
#: ``doctype: (due field, stamp field)``.
FIELDS = {
	TASK: ("custom_customer_confirmation_due", "custom_customer_confirmed_for"),
	VISIT: ("customer_confirmation_due", "customer_confirmed_for"),
}
JOB = "erpnext_enhancements.project_enhancements.customer_confirmations.send_confirmation"
PREVIEW_ROLES = {"System Manager", "Projects Manager"}
SWEEP_LIMIT = 200
FINISHED_STATUSES = engine.FINISHED_STATUSES
#: Flags that mean "not a person changing a date": nothing is sent from these.
QUIET_FLAGS = ("in_import", "in_patch", "in_install", "in_migrate")
RENDER_LOG_SECONDS = 3600
RENDER_LOG_FLAG = "ee_customer_confirmation_render_failed:"
DEFAULT_COMPANY = "Sapphire Fountains"

DEFAULT_SUBJECT = "Your {{ company }} visit on {{ date }}"
DEFAULT_RESPONSE = """<p>Hello{% if contact_name %} {{ contact_name }}{% endif %},</p>
<p>This is to confirm that our crew will be at {{ site }} on <strong>{{ date }}</strong>{% if arrival_window %}, arriving between {{ arrival_start }} and {{ arrival_end }}{% endif %}.</p>
{% if site_address %}<p>Address: {{ site_address }}</p>{% endif %}
{% if crew_names %}<p>You will see {{ crew_names }}.</p>{% endif %}
<p>If that day does not work for you, please call us{% if contact_phone %} at {{ contact_phone }}{% endif %} and we will find another.</p>
<p>Thank you,<br>{{ company }}</p>
"""


# ---------------------------------------------------------------------- pure helpers


def as_date(value):
	return engine._as_date(value) if value else None


def task_customer_date(task):
	"""The date a customer-facing Task is firmly booked to start, or None.

	None for a task that is not customer-facing, a pencil, finished, a template or a group task,
	or undated. ``task`` is a Task row or doc.
	"""
	if task is None or not cint(task.get("custom_customer_visit") or 0):
		return None
	if cint(task.get("is_template") or 0) or cint(task.get("is_group") or 0):
		return None
	if task.get("status") in FINISHED_STATUSES or engine.is_tentative(task):
		return None
	span = engine.task_span(task)
	return span[0] if span else None


def visit_customer_date(visit):
	"""A draft visit's scheduled date, or None (submitted, cancelled, started or undated)."""
	if visit is None or cint(visit.get("docstatus") or 0) != 0 or visit.get("visit_date"):
		return None
	return as_date(visit.get("scheduled_visit_date"))


def customer_date(doctype, doc):
	return task_customer_date(doc) if doctype == TASK else visit_customer_date(doc)


def needs_sending(before_day, after_day, stamp, today):
	"""Send when a customer date exists, changed in this save, was not already told, and is ahead."""
	if not after_day or after_day == before_day:
		return False
	if stamp and as_date(stamp) == after_day:
		return False
	return after_day >= today


def first_name(label):
	label = (label or "").strip()
	return label.split()[0] if label else ""


def join_names(names):
	"""``"Austin"``, ``"Austin and Korben"``, ``"Austin, Korben and Jesse"``."""
	names = [n for n in names or [] if n]
	if len(names) < 2:
		return names[0] if names else ""
	return ", ".join(names[:-1]) + " and " + names[-1]


def date_text(day):
	"""``"Thursday, October 15, 2026"``."""
	day = as_date(day)
	return f"{day:%A, %B} {day.day}, {day.year}" if day else ""


def clock_text(moment):
	"""``"8:00 AM"`` from a datetime/time."""
	hour = moment.hour % 12 or 12
	return f"{hour}:{moment.minute:02d} {'AM' if moment.hour < 12 else 'PM'}"


def arrival(slot):
	"""``(window, start, end)`` texts for a time slot, or three blanks."""
	if not slot:
		return "", "", ""
	start, end = clock_text(slot[0]), clock_text(slot[1])
	return f"{start} – {end}", start, end


def escape_context(context):
	"""The body's context with every text value HTML-escaped (Jinja here does not autoescape)."""
	escape = frappe.utils.escape_html
	out = {}
	for key, value in (context or {}).items():
		if isinstance(value, str):
			out[key] = escape(value)
		elif isinstance(value, list | tuple):
			out[key] = [escape(v) if isinstance(v, str) else v for v in value]
		else:
			out[key] = value
	return out


def subject_line(text):
	"""A rendered subject as one plain line."""
	return " ".join(frappe.utils.strip_html_tags(str(text or "")).split())


# ---------------------------------------------------------------------- settings and reads


def enabled():
	"""Project Planner Settings ``customer_date_confirmations``; no row (None) is off."""
	try:
		return bool(cint(frappe.get_cached_doc(SETTINGS).get(TOGGLE)))
	except Exception:
		return False


def _setting(field):
	try:
		return frappe.get_cached_doc(SETTINGS).get(field)
	except Exception:
		return None


def _has_column(doctype, column):
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def _value(doctype, name, field):
	if not name or not _has_column(doctype, field):
		return None
	try:
		return frappe.db.get_value(doctype, name, field)
	except Exception:
		return None


def _default_company():
	company = None
	try:
		company = frappe.defaults.get_global_default("company")
	except Exception:
		company = None
	if not company:
		rows = frappe.get_all("Company", pluck="name", order_by="creation asc", limit_page_length=1)
		company = rows[0] if rows else None
	return company


def _phone(company):
	phone = (_setting(PHONE_SETTING) or "").strip()
	if phone:
		return phone
	return (_value("Company", company, "phone_no") or "").strip() if company else ""


def _valid_email(value):
	value = (value or "").strip()
	if not value:
		return ""
	try:
		return frappe.utils.validate_email_address(value) or ""
	except Exception:
		return ""


def _primary_contact(customer):
	"""The Customer's primary Contact: its ``customer_primary_contact``, else a linked Contact
	marked primary, else None."""
	contact = _value("Customer", customer, "customer_primary_contact")
	if contact:
		return contact
	parents = frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Contact", "link_doctype": "Customer", "link_name": customer},
		pluck="parent",
		limit_page_length=0,
	)
	if not parents:
		return None
	primary = frappe.get_all(
		"Contact",
		filters={"name": ["in", parents], "is_primary_contact": 1},
		pluck="name",
		order_by="modified desc",
		limit_page_length=1,
	)
	return primary[0] if primary else None


def recipient(customer, project=None):
	"""``(email, contact first name)`` for the customer; ``("", "")`` when there is none."""
	name = ""
	if customer:
		contact = _primary_contact(customer)
		if contact:
			name = (_value("Contact", contact, "first_name") or "").strip()
			for field in ("email_id", "custom_email"):
				email = _valid_email(_value("Contact", contact, field))
				if email:
					return email, name
		email = _valid_email(_value("Customer", customer, "email_id"))
		if email:
			return email, name
	if project:
		email = _valid_email(_value("Project", project, "custom_customer_email"))
		if email:
			return email, name
	return "", name


def _full_names(users):
	users = [u for u in dict.fromkeys(users or []) if u]
	if not users:
		return []
	names = {
		row.get("name"): row.get("full_name") or row.get("name")
		for row in frappe.get_all(
			"User", filters={"name": ["in", users]}, fields=["name", "full_name"], limit_page_length=0
		)
	}
	return [names.get(user, user) for user in users]


def _address(kind, name, project):
	from erpnext_enhancements.project_enhancements import routing

	try:
		found = routing.locate([{"kind": kind, "ref": name, "project": project}], detail=True)
		return ((found.get(name) or {}).get("address") or "").strip()
	except Exception:
		return ""


def _task_crew(doc):
	rows = getattr(doc, "custom_crew", None) or []
	names = [first_name(row.get("resource_name")) for row in rows if row.get("resource_name")]
	if not rows:
		users = frappe.get_all(
			"ToDo",
			filters={"reference_type": TASK, "reference_name": doc.name, "status": "Open"},
			pluck="allocated_to",
		)
		names = [first_name(n) for n in _full_names(users)]
	return [n for n in dict.fromkeys(names) if n]


def _visit_crew(doc):
	users = [doc.get("technician")] + [row.get("user") for row in (doc.get("crew") or [])]
	return [n for n in dict.fromkeys(first_name(n) for n in _full_names(users)) if n]


def context_for(doctype, doc):
	"""``(context, recipient email)`` for one Task or visit: what the template is rendered with."""
	project = doc.get("project")
	project_title = (_value("Project", project, "project_name") or project) if project else ""
	company = _default_company() or DEFAULT_COMPANY
	if doctype == TASK:
		customer = _value("Project", project, "customer") if project else None
		day = engine.task_span(doc)[0] if engine.task_span(doc) else None
		window, start, end = arrival(engine.task_slot(doc))
		site = project_title or doc.get("subject") or doc.name
		crew = _task_crew(doc)
		address = _address("task", doc.name, project)
		kind = "project"
	else:
		customer = doc.get("customer") or None
		if not customer and doc.get("maintenance_contract"):
			customer = _value("Sapphire Maintenance Contract", doc.get("maintenance_contract"), "customer")
		if not customer and project:
			customer = _value("Project", project, "customer")
		day = as_date(doc.get("scheduled_visit_date"))
		window, start, end = "", "", ""
		from erpnext_enhancements.api.maintenance_planner import short_site_name

		site = short_site_name(project_title) if project_title else (doc.get("visit_label") or doc.name)
		crew = _visit_crew(doc)
		address = _address("visit", doc.name, project)
		kind = "maintenance"
	email, contact_name = recipient(customer, project)
	customer_name = (_value("Customer", customer, "customer_name") or customer or "") if customer else ""
	context = {
		"customer": customer_name,
		"contact_name": contact_name,
		"site": site or "",
		"site_address": address,
		"date": date_text(day),
		"date_iso": str(day) if day else "",
		"arrival_window": window,
		"arrival_start": start,
		"arrival_end": end,
		"crew": crew,
		"crew_names": join_names(crew),
		"contact_phone": _phone(company),
		"company": company,
		"kind": kind,
	}
	return context, email


def _template():
	"""``(subject, body, found)``: the Desk's Email Template, else the built-in default."""
	try:
		if frappe.db.exists("Email Template", TEMPLATE):
			record = frappe.get_doc("Email Template", TEMPLATE)
			body = record.get("response_html") if cint(record.get("use_html")) else record.get("response")
			return record.get("subject") or DEFAULT_SUBJECT, body or DEFAULT_RESPONSE, True
	except Exception:
		pass
	return DEFAULT_SUBJECT, DEFAULT_RESPONSE, False


def render(doctype, doc):
	"""``{"subject", "html", "recipient", "context", "template_found"}``. Raises on a template error."""
	from erpnext_enhancements import email_style

	context, email = context_for(doctype, doc)
	subject_template, body_template, found = _template()
	subject = subject_line(frappe.render_template(subject_template, dict(context)))
	body = frappe.render_template(body_template, escape_context(context))
	html = email_style.wrap(
		body,
		title=subject,
		eyebrow=_("Visit confirmation"),
		preheader=_("We will see you on {0}.").format(context["date"]) if context["date"] else None,
		tagline=True,
		pillar="service" if doctype == VISIT else "build",
	)
	return {
		"subject": subject,
		"html": html,
		"recipient": email,
		"context": context,
		"template_found": found,
	}


# ---------------------------------------------------------------------- triggers


def _quiet():
	flags = frappe.flags
	return any(getattr(flags, flag, False) for flag in QUIET_FLAGS)


def _queue(doctype, name, day):
	"""Persist the due date (re-drivable), then enqueue the send after commit."""
	due_field = FIELDS[doctype][0]
	if not _has_column(doctype, due_field):
		return False
	frappe.db.set_value(doctype, name, due_field, str(day), update_modified=False)
	frappe.enqueue(JOB, queue="short", enqueue_after_commit=True, doctype=doctype, name=name)
	return True


def consider(doctype, doc, before_day, after_day):
	"""Queue a confirmation when the customer date changed and was not already told."""
	stamp = doc.get(FIELDS[doctype][1])
	today = as_date(frappe.utils.nowdate())
	if needs_sending(before_day, after_day, stamp, today):
		return _queue(doctype, doc.name, after_day)
	return False


def _before(doc):
	try:
		return doc.get_doc_before_save() if hasattr(doc, "get_doc_before_save") else None
	except Exception:
		return None


def on_task_update(doc, method=None):
	"""``Task.on_update``: a firm customer-facing date set or moved → one email. Never raises."""
	try:
		if _quiet() or not enabled():
			return
		before = _before(doc)
		if before is None and getattr(doc, "template_task", None):
			return
		after_day = task_customer_date(doc)
		if not after_day:
			return
		consider(TASK, doc, task_customer_date(before) if before is not None else None, after_day)
	except Exception:
		frappe.log_error(title="Customer date confirmation not queued", message=frappe.get_traceback())


def on_visit_update(doc, method=None):
	"""``Sapphire Maintenance Record.on_update``: a draft visit's date set or moved. Never raises."""
	try:
		if _quiet() or not enabled():
			return
		after_day = visit_customer_date(doc)
		if not after_day:
			return
		before = _before(doc)
		consider(VISIT, doc, visit_customer_date(before) if before is not None else None, after_day)
	except Exception:
		frappe.log_error(title="Customer date confirmation not queued", message=frappe.get_traceback())


def keep_stamps(doc, method=None):
	"""``validate``: the due and stamp fields are the database's, never the form's. Never raises."""
	try:
		fields = FIELDS.get(doc.doctype)
		if not fields:
			return
		present = [f for f in fields if _has_column(doc.doctype, f)]
		if not present:
			return
		if doc.is_new():
			for field in present:
				doc.set(field, None)
			return
		stored = frappe.db.get_value(doc.doctype, doc.name, present, as_dict=True) or {}
		for field in present:
			doc.set(field, stored.get(field))
	except Exception:
		frappe.log_error(title="Customer date confirmation stamps", message=frappe.get_traceback())


# ---------------------------------------------------------------------- sending


def _render_failed(doctype, name):
	key = f"{RENDER_LOG_FLAG}{doctype}:{name}"
	try:
		if frappe.cache.get_value(key):
			return
		frappe.cache.set_value(key, 1, expires_in_sec=RENDER_LOG_SECONDS)
	except Exception:
		pass
	frappe.log_error(
		title="Customer date confirmation not sent",
		message=(
			f"The Email Template {TEMPLATE!r} could not be rendered for {doctype} {name}, so the "
			"customer was not emailed. Fix the template (Preview customer email shows the error); "
			"it is sent on the next sweep.\n\n" + frappe.get_traceback()
		),
	)


def send_one(doctype, name):
	"""Send one due confirmation, at most once per date. ``"sent"``, ``"skipped"`` or ``"failed"``.

	Locks the row, re-reads the due date and the stamp, and re-checks the document still has that
	customer date; anything else clears the due date and sends nothing. The caller commits.
	"""
	due_field, stamp_field = FIELDS[doctype]
	if not _has_column(doctype, due_field) or not _has_column(doctype, stamp_field):
		return "skipped"
	row = frappe.db.get_value(doctype, name, [due_field, stamp_field], as_dict=True, for_update=True)
	if not row or not row.get(due_field):
		return "skipped"
	due, stamp = as_date(row.get(due_field)), as_date(row.get(stamp_field))
	today = as_date(frappe.utils.nowdate())
	doc = frappe.get_doc(doctype, name)
	if due == stamp or due < today or customer_date(doctype, doc) != due:
		frappe.db.set_value(doctype, name, due_field, None, update_modified=False)
		return "skipped"
	try:
		message = render(doctype, doc)
	except Exception:
		_render_failed(doctype, name)
		return "failed"
	if not message["recipient"]:
		frappe.db.set_value(doctype, name, due_field, None, update_modified=False)
		return "skipped"
	frappe.sendmail(
		recipients=[message["recipient"]],
		subject=message["subject"],
		message=message["html"],
		reference_doctype=doctype,
		reference_name=name,
	)
	frappe.db.set_value(doctype, name, {due_field: None, stamp_field: str(due)}, update_modified=False)
	doc.add_comment(
		"Info",
		_("Emailed the customer ({0}) that we are coming on {1}.").format(
			frappe.utils.escape_html(message["recipient"]), date_text(due)
		),
	)
	return "sent"


def send_confirmation(doctype, name):
	"""Background job (enqueued after commit by the triggers). Off switch: sends nothing."""
	if doctype not in FIELDS or not enabled():
		return
	try:
		send_one(doctype, name)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Customer date confirmation failed", message=frappe.get_traceback())


def send_due_confirmations():
	"""Scheduler (every ten minutes): send every confirmation still due, e.g. after a deploy's
	FLUSHDB destroyed its job. Commits after each document so one failure costs only itself."""
	if not enabled():
		return
	for doctype, (due_field, _stamp) in FIELDS.items():
		if not _has_column(doctype, due_field):
			continue
		names = frappe.get_all(
			doctype,
			filters={due_field: ["is", "set"]},
			pluck="name",
			order_by=f"{due_field} asc",
			limit_page_length=SWEEP_LIMIT,
		)
		for name in names:
			try:
				send_one(doctype, name)
				frappe.db.commit()
			except Exception:
				frappe.db.rollback()
				frappe.log_error(title="Customer date confirmation failed", message=frappe.get_traceback())


# ---------------------------------------------------------------------- preview


def preview(doctype, name):
	"""What would be sent for ``doctype``/``name``, and to whom. Sends nothing, records nothing.

	``{"doctype", "name", "enabled", "date", "subject", "html", "recipient", "would_send",
	"already_confirmed", "template", "template_found", "notes": [why it would not send], "error"}``.
	Works with the switch off. A template that fails to render comes back as ``error`` (the Jinja
	message, for whoever is designing it) with no ``html``.
	"""
	if doctype not in FIELDS:
		frappe.throw(_("Only Tasks and maintenance visits have a customer date confirmation."))
	doc = frappe.get_doc(doctype, name)
	doc.check_permission("read")
	is_on = enabled()
	day = customer_date(doctype, doc)
	stamp = as_date(_value(doctype, name, FIELDS[doctype][1]))
	notes = []
	if not is_on:
		notes.append(
			_("Customer date confirmations are off in Project Planner Settings, so nothing is sent.")
		)
	if doctype == TASK:
		if not cint(doc.get("custom_customer_visit") or 0):
			notes.append(_("This task is not marked as a customer-facing visit."))
		elif engine.is_tentative(doc):
			notes.append(_("This task is pencilled in; the customer is told once it is firmed up."))
		elif not engine.task_span(doc):
			notes.append(_("This task has no date yet."))
		elif doc.get("status") in FINISHED_STATUSES:
			notes.append(_("This task is finished."))
	elif not day:
		notes.append(_("Only a draft visit that has not been started, with a date, is confirmed."))
	if day and stamp == day:
		notes.append(_("The customer was already told about {0}.").format(date_text(day)))
	if day and day < as_date(frappe.utils.nowdate()):
		notes.append(_("The date has passed."))
	answer = {
		"doctype": doctype,
		"name": name,
		"enabled": is_on,
		"date": str(day) if day else None,
		"already_confirmed": str(stamp) if stamp else None,
		"template": TEMPLATE,
		"template_found": False,
		"subject": "",
		"html": "",
		"recipient": "",
		"error": None,
	}
	try:
		message = render(doctype, doc)
	except Exception as exc:
		answer["error"] = _("The Email Template could not be rendered: {0}").format(
			frappe.utils.strip_html_tags(str(exc))[:300]
		)
		answer["template_found"] = bool(_template()[2])
		answer["notes"] = notes
		answer["would_send"] = False
		return answer
	answer.update(
		subject=message["subject"],
		html=message["html"],
		recipient=message["recipient"],
		template_found=message["template_found"],
	)
	if not message["recipient"]:
		notes.append(
			_("No email address was found for the customer's primary contact, so nobody would be emailed.")
		)
	if not message["template_found"]:
		notes.append(
			_("The Email Template {0} is missing, so the built-in wording is shown.").format(TEMPLATE)
		)
	answer["notes"] = notes
	answer["would_send"] = bool(
		is_on and day and stamp != day and day >= as_date(frappe.utils.nowdate()) and message["recipient"]
	)
	return answer
