# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The visit review digest: every answer on a visit, emailed when it goes for review.

When a technician finishes a visit in the Visit Wizard, or presses Request Review on the
desk form, the record moves from Draft to Pending Review. It then waits for a Maintenance
Reviewer to approve it before it is billed. Nik, 2026-10-12: send that review to
``service_repair@`` for Lisa to check, and to the technician who filled the form out, "so
they have a copy of all their answers and such, like a digest".

**Two emails, one body.** The review list gets it as "ready for review": ERPNext
Enhancements Settings → Visit Review Emails, seeded with ``service_repair@`` by
``patches/seed_visit_review_emails.py`` (Nik asked for it to be a setting). An empty list
sends the review copy to nobody. The technician gets the same answers as their copy. The technician is the record's
``technician`` field, which is the person filling the visit in (see the record controller):
the Visit Wizard moves that field to whoever actually does the visit.

This is not the "Maintenance Review Needed" Notification. That one tells ``operations@``
that a review is waiting, with four facts. This one carries the form.

**When.** On the save that moves ``workflow_state`` into Pending Review. A visit that is
Sent Back and resubmitted therefore sends a fresh copy, with the corrected answers. A site
with no active workflow submits a visit directly and it never passes through review, so
there it is sent on submit instead.

**Inline, not enqueued.** ``frappe.sendmail`` only writes Email Queue rows, in the same
transaction as the save. So the digest commits with the state change or not at all, and an
Email Queue row survives the deploy's Redis flush where a queued job does not (CLAUDE.md).
Building it is a handful of reads.

**It never raises.** A digest that fails to render must not stop a technician sending a
visit for review. The failure is logged and the visit goes through.

**Photos are links, not images.** Visit photos are private files, which a mail client
cannot load without signing in, so an ``<img>`` would arrive broken. A link opens the photo
for anyone who is signed in.
"""

from urllib.parse import quote

import frappe
from frappe import _
from frappe.utils import cint, flt, formatdate, get_datetime, get_url, get_url_to_form, nowdate

from erpnext_enhancements import email_style
from erpnext_enhancements.utils.url_safety import is_safe_url

SETTINGS = "ERPNext Enhancements Settings"
RECIPIENTS_FIELD = "visit_review_emails"
REVIEW_STATE = "Pending Review"
TEMPLATE = "erpnext_enhancements/templates/emails/sapphire_maintenance/visit_review_digest.html"
QUIET_FLAGS = ("in_import", "in_patch", "in_install", "in_migrate")

# The tables a visit's answers live in, in the order the digest shows them. Water
# chemistry comes first and chemicals used last, as they do in the wizard.
ANSWER_TABLES = ("chemistry_readings", "cleaning_tasks", "maintenance_results")
ALL_TABLES = (*ANSWER_TABLES, "consumables")


# ------------------------------------------------------------------ entry points


def on_visit_update(doc, method=None):
	"""``Sapphire Maintenance Record.on_update``: the visit just went for review. Never raises."""
	try:
		if _quiet() or cint(doc.get("docstatus")) != 0:
			return
		if (doc.get("workflow_state") or "") != REVIEW_STATE:
			return
		if not doc.has_value_changed("workflow_state"):
			return
		send(doc)
	except Exception:
		_log()


def on_visit_submit(doc, method=None):
	"""``Sapphire Maintenance Record.on_submit``: only where no workflow is active. Never raises.

	With the workflow, the digest went out when the visit entered Pending Review, and
	submitting is the reviewer's Approve & Submit, which needs no second copy.
	"""
	try:
		if _quiet():
			return
		from frappe.model.workflow import get_workflow_name

		if get_workflow_name(doc.doctype):
			return
		send(doc)
	except Exception:
		_log()


def send(doc):
	"""Send the digest to the review list and to the technician. Returns who it went to."""
	context = build_context(doc)
	sent = []
	reviewers = review_recipients()
	if reviewers and _send(reviewers, doc, context, "review"):
		sent.extend(reviewers)
	technician = _technician_email(doc)
	if technician and technician.lower() not in {r.lower() for r in reviewers}:
		if _send([technician], doc, context, "technician"):
			sent.append(technician)
	return sent


def review_recipients():
	"""ERPNext Enhancements Settings → Visit Review Emails, deduped, in the order listed.

	Empty means nobody, by choice: the patch that seeds it runs once. A settings read that
	fails is logged and also reads as nobody, so the technician's copy still goes out.
	"""
	try:
		rows = frappe.get_cached_doc(SETTINGS).get(RECIPIENTS_FIELD) or []
	except Exception:
		_log()
		return []
	seen = set()
	recipients = []
	for row in rows:
		email = (row.get("email") or "").strip()
		if email and email.lower() not in seen:
			seen.add(email.lower())
			recipients.append(email)
	return recipients


# ---------------------------------------------------------------------- sending


def _quiet():
	flags = frappe.flags
	return any(getattr(flags, flag, False) for flag in QUIET_FLAGS)


def _log():
	"""Log a swallowed failure, and swallow a failure to log."""
	try:
		frappe.log_error(title="Visit review digest not sent", message=frappe.get_traceback())
	except Exception:
		pass


def _send(recipients, doc, context, audience):
	subject = _subject(context, audience)
	try:
		message = email_style.render(
			TEMPLATE,
			dict(context, **_audience_context(context, audience)),
			title=subject,
			eyebrow=_("Maintenance"),
			preheader=context["preheader"],
			pillar="service",
		)
		frappe.sendmail(
			recipients=recipients,
			subject=subject,
			message=message,
			reference_doctype=doc.doctype,
			reference_name=doc.name,
		)
		return True
	except Exception:
		_log()
		return False


def _subject(context, audience):
	if audience == "review":
		return _("Ready for review: {0} visit, {1}").format(context["site"], context["visit_date"])
	return _("Your visit form: {0}, {1}").format(context["site"], context["visit_date"])


def _audience_context(context, audience):
	"""The few lines that differ between the reviewer's email and the technician's copy."""
	if audience == "review":
		return {
			"intro": _(
				"{0} sent this visit for review. Every answer on the form is below. Open the visit "
				"to approve it or send it back."
			).format(context["technician_name"]),
			"button_label": _("Review the visit"),
			"closing": None,
		}
	return {
		"intro": _(
			"This is your copy of the visit form you sent for review. Every answer you recorded is below."
		),
		"button_label": _("Open the visit"),
		"closing": _("If anything here is wrong, tell the office before the visit is approved."),
	}


def _technician_email(doc):
	"""The technician's address, or None for no technician, a system user or a disabled one."""
	user = doc.get("technician")
	if not user or user in ("Guest", "Administrator"):
		return None
	row = frappe.db.get_value("User", user, ["email", "enabled"], as_dict=True)
	if not row or not cint(row.get("enabled")):
		return None
	return row.get("email") or None


# ---------------------------------------------------------------------- content


def build_context(doc):
	"""Everything the template shows, as display-ready strings.

	The template only lays this out. Every value is worded here, so the tests can read
	the digest's content without rendering it, and so the template needs no logic that
	could drift from the record's own rules (what counts as answered, what is out of range).
	"""
	labels = _feature_labels(doc)
	technician_name = _full_name(doc.get("technician")) or _("The technician")
	site = _site(doc)
	visit_date = formatdate(doc.get("visit_date") or doc.get("scheduled_visit_date") or nowdate())
	completion = f"{cint(flt(doc.get('completion_percent')))}%"

	out_of_range = [
		_tagged(doc, row, labels, row.get("reading"))
		for row in doc.get("chemistry_readings") or []
		if cint(row.get("out_of_range")) and flt(row.get("reading_value"))
	]
	unanswered = _unanswered(doc, labels)

	warnings = []
	if out_of_range:
		warnings.append(
			_("Warning: {0} out of range: {1}.").format(
				_count(len(out_of_range), _("reading"), _("readings")), ", ".join(out_of_range)
			)
		)
	if unanswered:
		warnings.append(
			_("Warning: {0} not answered yet: {1}.").format(
				_count(len(unanswered), _("required item"), _("required items")), ", ".join(unanswered)
			)
		)

	preheader = [_("{0} complete").format(completion)]
	if out_of_range:
		preheader.append(_count(len(out_of_range), _("reading out of range"), _("readings out of range")))

	return {
		"name": doc.name,
		"site": site,
		"visit_date": visit_date,
		"technician_name": technician_name,
		"url": get_url_to_form(doc.doctype, doc.name),
		"preheader": " · ".join(preheader),
		"warnings": warnings,
		"summary": _summary(doc, labels, site, visit_date, technician_name, completion),
		"blocks": _blocks(doc, labels),
		"notes_heading": _("Visit notes"),
		"notes": (doc.get("visit_notes") or "").strip() or None,
	}


def _summary(doc, labels, site, visit_date, technician_name, completion):
	rows = [
		(_("Visit"), doc.name),
		(_("Site"), site),
		(_("Account"), _customer_name(doc.get("customer"))),
		(_("Visit type"), doc.get("visit_label") or _("Regular visit")),
	]
	if doc.get("serial_no"):
		rows.append((_("Water feature"), labels.get(doc.serial_no) or doc.serial_no))
	rows.append((_("Technician"), technician_name))
	crew = [
		row.get("full_name") or _full_name(row.get("user")) or row.get("user")
		for row in doc.get("crew") or []
	]
	crew = [name for name in crew if name]
	if crew:
		rows.append((_("Crew"), ", ".join(crew)))
	rows.extend(
		[
			(_("Visit date"), visit_date),
			(_("On site"), _on_site(doc)),
			(_("Completed"), completion),
			(_("Safety and PPE acknowledged"), _("Yes") if cint(doc.get("safety_acknowledged")) else _("No")),
			(_("Client sign-off"), _("Signed") if doc.get("client_sign_off") else _("Not signed")),
		]
	)
	return rows


def _blocks(doc, labels):
	"""One block per (water feature, form section), then the chemicals used.

	A Per Site Visit record covers several features and tags each row with one, so its
	blocks are headed with the feature's name. A single-feature record is not: naming the
	only feature on every heading is noise.
	"""
	order = []
	for table in ALL_TABLES:
		for row in doc.get(table) or []:
			key = _feature_key(doc, row)
			if key not in order:
				order.append(key)
	named = len(order) > 1

	blocks = []
	for key in order:
		feature = (labels.get(key) or key or _("Whole site")) if named else None
		for table, default_title in (
			("chemistry_readings", _("Water chemistry")),
			("cleaning_tasks", _("Cleaning")),
			("maintenance_results", _("Inspection")),
		):
			rows = [row for row in doc.get(table) or [] if _feature_key(doc, row) == key]
			for title, section_rows in _by_section(rows, default_title):
				blocks.append(_block(table, _heading(feature, title), section_rows))

	consumables = doc.get("consumables") or []
	if consumables:
		used = [row for row in consumables if flt(row.get("qty")) > 0]
		headers = [_("Item"), _("Qty")] + ([_("Water feature")] if named else [])
		rows = []
		for row in used:
			cells = [row.get("item_name") or row.get("item") or "", _amount(row.get("qty"), row.get("uom"))]
			if named:
				key = _feature_key(doc, row)
				cells.append(labels.get(key) or key or _("Whole site"))
			rows.append(cells)
		blocks.append(
			{
				"heading": _("Chemicals and parts used"),
				"headers": headers,
				"rows": rows,
				"photos": [],
				"empty": _("None used."),
			}
		)
	return blocks


def _block(table, heading, rows):
	photos = []
	if table == "chemistry_readings":
		headers = [_("Reading"), _("Value"), _("Target"), _("Notes")]
		cells = [_reading_cells(row) for row in rows]
		labels = [row.get("reading") for row in rows]
	elif table == "cleaning_tasks":
		headers = [_("Task"), _("Done"), _("Notes")]
		cells = [
			[
				row.get("task") or "",
				_("Done") if cint(row.get("is_done")) else _("Not done"),
				row.get("notes") or "",
			]
			for row in rows
		]
		labels = [row.get("task") for row in rows]
	else:
		headers = [_("Question"), _("Answer"), _("Details")]
		cells = [[row.get("question") or "", _answer(row), row.get("other_details") or ""] for row in rows]
		labels = [row.get("question") for row in rows]
	for row, label in zip(rows, labels, strict=True):
		link = _photo_link(row.get("photo"), label)
		if link:
			photos.append(link)
	return {"heading": heading, "headers": headers, "rows": cells, "photos": photos, "empty": None}


def _reading_cells(row):
	value = flt(row.get("reading_value"))
	unit = (row.get("uom") or "").strip()
	if not value:
		# Blank and zero are the same thing on this form: "not measured"
		# (evaluate_reading_ranges says why, and the completion count agrees).
		shown = _("Not recorded")
	else:
		shown = _amount(value, unit)
		if cint(row.get("out_of_range")):
			shown = _("{0} (OUT OF RANGE)").format(shown)
	return [row.get("reading") or "", shown, _target(row, unit), row.get("notes") or ""]


def _target(row, unit):
	"""The range the reading was judged against: only the bounds that are set, as the
	range check itself reads them."""
	low, high = flt(row.get("min_value")), flt(row.get("max_value"))
	suffix = f" {unit}" if unit else ""
	if low and high:
		return f"{_number(low)}–{_number(high)}{suffix}"
	if low:
		return f"≥ {_number(low)}{suffix}"
	if high:
		return f"≤ {_number(high)}{suffix}"
	return ""


def _answer(row):
	selection = (row.get("selection") or "").strip()
	answer = (row.get("answer") or "").strip()
	if selection and answer:
		return f"{selection}: {answer}"
	return selection or answer or _("Not answered")


def _unanswered(doc, labels):
	"""Required rows still empty, by the rule the record's own submit check applies."""
	missing = []
	for row in doc.get("maintenance_results") or []:
		if cint(row.get("is_mandatory")) and not (row.get("selection") or row.get("answer")):
			missing.append(_tagged(doc, row, labels, row.get("question")))
	for row in doc.get("chemistry_readings") or []:
		if cint(row.get("is_mandatory")) and not flt(row.get("reading_value")):
			missing.append(_tagged(doc, row, labels, row.get("reading")))
	for row in doc.get("cleaning_tasks") or []:
		if cint(row.get("is_mandatory")) and not (row.get("is_done") or row.get("notes")):
			missing.append(_tagged(doc, row, labels, row.get("task")))
	return missing


# ---------------------------------------------------------------------- helpers


def _feature_key(doc, row):
	return row.get("serial_no") or doc.get("serial_no") or ""


def _tagged(doc, row, labels, label):
	"""``label``, naming its water feature when the record covers more than one."""
	serial = row.get("serial_no")
	if serial and not doc.get("serial_no"):
		return _("{0} ({1})").format(label or "", labels.get(serial) or serial)
	return label or ""


def _by_section(rows, default_title):
	"""``rows`` split by the form section they came from, in the order they appear."""
	sections = {}
	for row in rows:
		sections.setdefault(row.get("section_title") or default_title, []).append(row)
	return list(sections.items())


def _heading(feature, title):
	return f"{feature} — {title}" if feature else title


def _feature_labels(doc):
	"""``{serial_no: item_name}`` for every water feature the record mentions."""
	serials = {
		row.get("serial_no") for table in ALL_TABLES for row in doc.get(table) or [] if row.get("serial_no")
	}
	if doc.get("serial_no"):
		serials.add(doc.serial_no)
	if not serials:
		return {}
	return {
		row.get("name"): row.get("item_name")
		for row in frappe.get_all(
			"Serial No", filters={"name": ["in", sorted(serials)]}, fields=["name", "item_name"]
		)
		if row.get("item_name")
	}


def _site(doc):
	if doc.get("project"):
		return frappe.db.get_value("Project", doc.project, "project_name") or doc.project
	return _customer_name(doc.get("customer")) or doc.name


def _customer_name(customer):
	if not customer:
		return None
	return frappe.db.get_value("Customer", customer, "customer_name") or customer


def _full_name(user):
	if not user:
		return None
	return frappe.db.get_value("User", user, "full_name") or user


def _on_site(doc):
	"""``8:05 AM – 10:20 AM (2 h 15 min)``, less any paused time. None when unrecorded."""
	if not (doc.get("clock_in_time") and doc.get("clock_out_time")):
		return _clock(doc.get("clock_in_time")) if doc.get("clock_in_time") else None
	start, end = get_datetime(doc.clock_in_time), get_datetime(doc.clock_out_time)
	span = f"{_clock(start)} – {_clock(end)}"
	seconds = (end - start).total_seconds() - flt(doc.get("paused_duration"))
	if seconds <= 0:
		return span
	hours, minutes = divmod(int(seconds // 60), 60)
	length = _("{0} h {1} min").format(hours, f"{minutes:02d}") if hours else _("{0} min").format(minutes)
	return f"{span} ({length})"


def _clock(value):
	return get_datetime(value).strftime("%I:%M %p").lstrip("0")


def _photo_link(path, label):
	"""``(absolute url, label)`` for a row's photo, or None for no photo or an unsafe URL."""
	if not path:
		return None
	path = str(path)
	url = path if path.startswith(("http://", "https://")) else get_url(quote(path, safe="/:?=&%#"))
	if not is_safe_url(url):
		return None
	return (url, _("Photo: {0}").format(label or _("untitled")))


def _number(value):
	"""7.4, not 7.400000000000001 or 7.40."""
	return f"{flt(value):g}"


def _amount(value, unit):
	unit = (unit or "").strip()
	return f"{_number(value)} {unit}" if unit else _number(value)


def _count(n, one, many):
	return f"{n} {one if n == 1 else many}"
