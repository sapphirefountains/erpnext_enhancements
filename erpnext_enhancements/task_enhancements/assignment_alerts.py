"""My Work's alerts (WI-081 Phase 2): the weekday morning job, and the text for a High assignment.

Nik chose, on 2026-10-20: a daily digest instead of an email per assignment, individual emails only
for High priority, a reminder on the due date, the assigner told after N days overdue, and a text
for urgent work. What production showed decided the shape:

* **The Morning Briefing reaches two people** (Briefing Recipients: Nik emailed, James not), while
  19 people held open assignments. So the summary is folded into the briefing for whoever is
  emailed the briefing (``api/briefing._user_todos`` now reads My Work's rules), and everyone else
  with something to say gets the same summary as a short "My Work" email. Nobody gets both.
* **Two emails per assignment.** In the 30 days to 2026-10-20 the "New ToDo Created" Notification
  sent 231 emails and Frappe's own Assignment notification 78 more, to largely the same people. The
  Notification now fires for High priority only (``fixtures/notification.json``), and
  ``hooks.notification_skip_email_types`` stops Frappe emailing Assignment bell entries at all;
  the bell entries themselves are unchanged.

Wiring (``hooks.py``):

* ``scheduler_events.cron["40 6 * * 1-5"]`` -> :func:`daily_run`, ten minutes after the briefing.
  Three independent steps, one failing never stops the next: due-today bells
  (:func:`nudge_due_today`), escalations (:func:`escalate`), the summary emails
  (:func:`send_digests`).
* ``doc_events["ToDo"]["after_insert"]`` -> :func:`on_todo_insert`: a High-priority assignment
  that someone else made texts the assignee, after the commit.

Kill switches, live and without a deploy (``bench --site <site> set-config <key> 1``):
``my_work_digest_disabled``, ``my_work_escalation_disabled``, ``my_work_nudge_disabled``,
``my_work_sms_disabled``.

Things it is careful about:

* **Only a real due date counts.** A ToDo dated its own creation day has no due date
  (``assignments.due_date``); nudging or escalating on that stamp would fire on every
  assignment made without a date.
* **An escalation is said once.** ``ToDo.custom_escalated_on`` is stamped when it is sent, in the
  database, not in redis: every deploy FLUSHDBs redis, and the reminders in this app that survived
  that (training, e-sign, the planner digest) all keep their marker on a row.
* **The text goes after the commit** (``enqueue_after_commit``), so a slow gateway never holds up
  whoever is assigning, and a rolled-back assignment texts nobody. It goes to the Employee's
  ``cell_number``, the rule every other text in this app uses (``planner_notices.cell_number``).
* Record titles are user-typed. The summary hands them to ``email_style.item``, whose macro
  escapes them; the briefing's Markdown lines escape them before they become link labels, and
  ``email_style.markdown`` sanitizes that email again.
* **Every link opens the assignment** (v1.739.0, :func:`link`): a Task or a maintenance visit opens
  the record, anything else its ToDo, checked against what the reader may open. None opens My
  Work's homepage any more.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_url, get_url_to_form, getdate, now_datetime, nowdate

from erpnext_enhancements.task_enhancements import assignments

FIELD_ESCALATED = "custom_escalated_on"

#: Rows per section in the summary email; the rest are a "and N more" line to My Work.
DIGEST_ROWS = 8


def _off(key):
	return bool(cint(frappe.conf.get(key)))


def _open_rows():
	fields = [
		"name",
		"allocated_to",
		"assigned_by",
		"owner",
		"date",
		"creation",
		"priority",
		"description",
		"reference_type",
		"reference_name",
	]
	if frappe.db.has_column("ToDo", FIELD_ESCALATED):
		fields.append(FIELD_ESCALATED)
	return frappe.get_all(
		"ToDo",
		filters={"status": "Open", "allocated_to": ["is", "set"]},
		fields=fields,
		limit_page_length=0,
	)


def _enabled_users(users):
	users = [u for u in set(users) if u and u not in assignments.NOBODY]
	if not users:
		return {}
	rows = frappe.get_all(
		"User",
		filters={"name": ["in", users], "enabled": 1, "user_type": "System User"},
		fields=["name", "email", "full_name", "first_name"],
	)
	return {r.name: r for r in rows}


def _titles(rows):
	"""{(doctype, name): title} for the records the rows name."""
	from frappe.desk.doctype.notification_log.notification_log import get_title

	out = {}
	for row in rows:
		key = (row.reference_type, row.reference_name)
		if row.reference_type and row.reference_name and key not in out:
			try:
				out[key] = get_title(row.reference_type, row.reference_name) or row.reference_name
			except Exception:
				out[key] = row.reference_name
	return out


def _what(row, titles):
	"""(title, ask) for an assignment, as plain text."""
	ask = assignments.the_ask(row.description, row.reference_type, row.reference_name)
	title = titles.get((row.reference_type, row.reference_name)) if row.reference_type else None
	return (title or assignments.truncate(ask, 80) or row.name), ask


# ---------------------------------------------------------------------------
# Links (v1.739.0): every email, bell and text about an assignment opens that assignment -- its Task
# or visit, else its ToDo -- never My Work's homepage. The rule is assignments.link_kind.


def _may_read(doctype, name, user):
	try:
		return bool(frappe.has_permission(doctype, "read", doc=name, user=user))
	except Exception:
		return False


def _may_read_todo(row, user):
	"""v16 ``todo.has_permission``, its cheap half first: the assignee, whoever assigned it and
	whoever made it always may; anyone else (a System Manager) only through the check itself."""
	if user and user in (row.get("allocated_to"), row.get("assigned_by"), row.get("owner")):
		return True
	return bool(row.get("name")) and _may_read("ToDo", row.get("name"), user)


def link(row, user=None):
	"""Where a link to the assignment ``row`` goes for ``user``, who defaults to its assignee.

	``{"kind", "doctype", "name", "url", "path", "label"}``: ``doctype``/``name`` are what a bell
	entry points at, ``url`` is absolute (an email, a text), ``path`` is the site-relative link a
	bell carries when it opens My Work, ``label`` the button's words. The record is read-checked
	only when the answer depends on it, because the morning job asks once per row.
	"""
	user = user or row.get("allocated_to") or row.get("assigned_by") or row.get("owner")
	ref_type, ref_name, name = row.get("reference_type"), row.get("reference_name"), row.get("name")
	todo_ok = _may_read_todo(row, user)
	record_ok = bool(
		ref_type
		and ref_name
		and (ref_type in assignments.RECORD_LINK_DOCTYPES or not todo_ok)
		and _may_read(ref_type, ref_name, user)
	)
	kind = assignments.link_kind(ref_type, ref_name, record_ok, todo_ok)
	if kind == "record":
		doctype, docname, path = ref_type, ref_name, None
		url = get_url_to_form(ref_type, ref_name)
	elif kind == "todo":
		doctype, docname, path = "ToDo", name, None
		url = get_url_to_form("ToDo", name)
	else:
		from urllib.parse import quote

		tab = "/unclaimed" if not row.get("allocated_to") else ""
		doctype, docname = "ToDo", name
		path = f"/desk/my-work{tab}?todo={quote(str(name or ''))}"
		url = get_url(path)
	return {
		"kind": kind,
		"doctype": doctype,
		"name": docname,
		"url": url,
		"path": path,
		"label": _(assignments.link_label(doctype if kind != "my_work" else None)),
	}


def bell_fields(row, user):
	"""What a bell entry (Notification Log) about ``row`` points at, for ``user``."""
	target = link(row, user)
	fields = {"document_type": target["doctype"], "document_name": target["name"]}
	if target["path"]:
		fields["link"] = target["path"]
	return fields


def _url(row, user=None):
	"""The absolute link an email or a text about ``row`` carries (:func:`link`)."""
	return link(row, user)["url"]


def _bell(user, row, subject):
	from frappe.desk.doctype.notification_log.notification_log import enqueue_create_notification

	notification = {"type": "Assignment", "subject": subject, "from_user": "Administrator"}
	notification.update(bell_fields(row, user))
	enqueue_create_notification(user, notification)


# ---------------------------------------------------------------------------
# The morning job


def daily_run():
	"""Weekdays 06:40. Each step is on its own: one failing is logged and the next still runs."""
	today = getdate(nowdate())
	rows = _open_rows()
	for step, key in (
		(nudge_due_today, "my_work_nudge_disabled"),
		(escalate, "my_work_escalation_disabled"),
		(send_digests, "my_work_digest_disabled"),
	):
		if _off(key):
			continue
		try:
			step(rows, today)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(title=f"My Work: {step.__name__} failed", message=frappe.get_traceback())


def nudge_due_today(rows, today):
	"""A bell entry for every open assignment whose real due date is today. Returns how many."""
	due = [r for r in rows if assignments.due_today(r, today)]
	if not due:
		return 0
	people = _enabled_users(r.allocated_to for r in due)
	titles = _titles(due)
	sent = 0
	for row in due:
		if row.allocated_to not in people:
			continue
		title, ask = _what(row, titles)
		subject = _("Due today: {0}").format(frappe.bold(frappe.utils.escape_html(title)))
		if ask and ask != title:
			subject += ": " + frappe.utils.escape_html(assignments.truncate(ask, 100))
		_bell(row.allocated_to, row, subject)
		sent += 1
	return sent


def escalate(rows, today):
	"""Tell whoever assigned it, once, when an assignment is ESCALATE_AFTER_DAYS past due."""
	if not frappe.db.has_column("ToDo", FIELD_ESCALATED):
		return 0
	late = [r for r in rows if assignments.escalation_due(r, today)]
	if not late:
		return 0
	people = _enabled_users([r.assigned_by for r in late] + [r.allocated_to for r in late])
	titles = _titles(late)
	sent = 0
	for row in late:
		if row.assigned_by not in people:
			continue
		title, ask = _what(row, titles)
		days = (today - assignments.due_date(row.date, row.creation)).days
		who = people.get(row.allocated_to)
		name = (who.full_name if who else None) or row.allocated_to
		subject = _("{0} is {1} days late on {2}").format(
			frappe.bold(frappe.utils.escape_html(name)), days, frappe.bold(frappe.utils.escape_html(title))
		)
		if ask and ask != title:
			subject += ": " + frappe.utils.escape_html(assignments.truncate(ask, 100))
		_bell(row.assigned_by, row, subject)
		frappe.db.set_value("ToDo", row.name, FIELD_ESCALATED, str(today), update_modified=False)
		sent += 1
	return sent


def _briefing_emailed_users():
	"""Users emailed the Morning Briefing, whose briefing already carries this summary."""
	try:
		if not cint(frappe.db.get_single_value("ERPNext Enhancements Settings", "briefing_enabled")):
			return set()
		return set(
			frappe.get_all(
				"Briefing Recipient",
				filters={"parenttype": "ERPNext Enhancements Settings", "send_email": 1},
				pluck="user",
			)
		)
	except Exception:
		return set()


def _since(today):
	"""When "new" starts: the previous weekday morning. Monday's summary covers the weekend."""
	days = 3 if today.weekday() == 0 else 1
	return add_to_date(now_datetime(), days=-days)


def digest_sections(user, rows, today, since):
	"""One person's summary: {"overdue", "today", "new", "waiting": [rows], "undated": count}."""
	mine = [r for r in rows if r.allocated_to == user]
	sections = {"overdue": [], "today": [], "new": [], "waiting": [], "undated": 0}
	for row in mine:
		which = assignments.bucket(assignments.due_date(row.date, row.creation), today)
		if which in ("overdue", "today"):
			sections[which].append(row)
		elif row.creation and row.creation >= since:
			sections["new"].append(row)
		elif which == "undated":
			sections["undated"] += 1
	for row in rows:
		if row.assigned_by == user and row.allocated_to != user:
			if assignments.bucket(assignments.due_date(row.date, row.creation), today) == "overdue":
				sections["waiting"].append(row)
	return sections


def send_digests(rows, today):
	"""The morning summary for everyone with something to say who is not emailed the briefing."""
	skip = _briefing_emailed_users()
	since = _since(today)
	candidates = {r.allocated_to for r in rows} | {r.assigned_by for r in rows if r.assigned_by}
	people = _enabled_users(candidates - skip)
	if not people:
		return 0
	titles = _titles(rows)
	sent = 0
	for user, person in people.items():
		sections = digest_sections(user, rows, today, since)
		if not assignments.digest_worth_sending(sections):
			continue
		try:
			_send_digest(person, sections, titles, today, people)
			sent += 1
		except Exception:
			frappe.log_error(
				title="My Work: summary email failed", message=f"{user}\n{frappe.get_traceback()}"
			)
	return sent


def _md(text):
	"""Text that is safe inside a Markdown link label or list item."""
	return "".join("\\" + ch if ch in "\\`*_[]()#<>|!" else ch for ch in str(text or ""))


def _item(row, titles, today, user, people=None, waiting=False):
	"""One assignment as the summary lists it: ``{"title", "detail", "url", "label"}``."""
	title, ask = _what(row, titles)
	detail = []
	if ask and ask != title:
		detail.append(assignments.truncate(ask, 120))
	due = assignments.due_date(row.date, row.creation)
	if due and due < today:
		detail.append(_("{0} days late").format((today - due).days))
	if waiting:
		who = (people or {}).get(row.allocated_to)
		detail.append((who.full_name if who else None) or row.allocated_to)
	target = link(row, user)
	return {
		"title": title,
		"detail": " · ".join(str(d) for d in detail),
		"url": target["url"],
		# The design system's buttons carry their capitals in the words (docs/email-design-system.md).
		"label": target["label"].upper(),
	}


def digest_blocks(sections, titles, today, user, people=None):
	"""The summary's sections for ``user``: ``[{"heading", "items", "more"}]``, empty ones left out.

	Until v1.739.0 this was Markdown with each title a small text link, under one big "Open My
	Work" button that opened My Work's homepage -- the button people pressed. Each assignment now
	carries its own button to its Task, visit or ToDo, and My Work is a small link at the foot.
	"""
	blocks = []
	for key, heading in (
		("overdue", _("Overdue")),
		("today", _("Due today")),
		("new", _("New since yesterday")),
		("waiting", _("Late with people you assigned")),
	):
		rows = sections.get(key) or []
		if not rows:
			continue
		blocks.append(
			{
				"heading": f"{heading} ({len(rows)})",
				"items": [
					_item(r, titles, today, user, people, waiting=key == "waiting") for r in rows[:DIGEST_ROWS]
				],
				"more": max(len(rows) - DIGEST_ROWS, 0),
			}
		)
	return blocks


#: The Morning Briefing's word for each section a My Work line came from.
BRIEFING_TAGS = {"overdue": "overdue", "today": "due today", "new": "new", "waiting": "waiting"}


def briefing_lines(items):
	"""The Morning Briefing's My Work list as Markdown lines, each a link to its assignment.

	``items`` are ``api/briefing._user_todos``'s (``description``, ``bucket``, ``url``). The text
	is user-typed, so it is escaped before it becomes a link label; the email sanitizes again.
	"""
	lines = []
	for item in items or []:
		text = _md(item.get("description"))
		url = item.get("url")
		line = f"[{text}]({url})" if url else text
		tag = BRIEFING_TAGS.get(item.get("bucket"))
		lines.append(f"- {line}" + (f" ({_(tag)})" if tag else ""))
	return lines


def _send_digest(person, sections, titles, today, people):
	from erpnext_enhancements import email_style

	count = len(sections["overdue"]) + len(sections["today"])
	subject = _("My Work: {0} due or overdue today").format(count) if count else _("My Work: new assignments")
	parts = []
	for block in digest_blocks(sections, titles, today, person.name, people):
		parts.append(email_style.h(block["heading"]))
		parts.extend(email_style.item(i["title"], i["detail"], i["url"], i["label"]) for i in block["items"])
		if block["more"]:
			parts.append(email_style.note(_("And {0} more in My Work.").format(block["more"])))
	if sections.get("undated"):
		parts.append(email_style.note(_("{0} more with no due date.").format(sections["undated"])))
	parts.append(email_style.links([(get_url("/desk/my-work"), _("See all your work in My Work"))]))
	body = "".join(parts)
	frappe.sendmail(
		recipients=[person.email or person.name],
		subject=subject,
		message=email_style.wrap(body, title=_("My Work"), eyebrow=frappe.utils.formatdate(today)),
		reference_doctype="User",
		reference_name=person.name,
	)


# ---------------------------------------------------------------------------
# The High-priority text


def on_todo_insert(doc, method=None):
	"""doc_events ToDo ``after_insert``: text the assignee of a High assignment someone else made."""
	try:
		if _off("my_work_sms_disabled") or (doc.get("priority") or "") != "High":
			return
		# The next occurrence of a repeating assignment is not news (recurrence.py).
		if getattr(doc, "flags", None) is not None and doc.flags.get("ee_repeat_of"):
			return
		assignee, assigner = doc.get("allocated_to"), doc.get("assigned_by")
		if not assignee or assignee in assignments.NOBODY or assignee == (assigner or doc.get("owner")):
			return
		flags = frappe.flags
		if (
			getattr(flags, "in_import", False)
			or getattr(flags, "in_install", False)
			or getattr(flags, "in_migrate", False)
		):
			return
		frappe.enqueue(
			"erpnext_enhancements.task_enhancements.assignment_alerts.send_high_priority_text",
			todo=doc.name,
			enqueue_after_commit=True,
		)
	except Exception:
		frappe.log_error(title="My Work: could not queue a text", message=frappe.get_traceback())


def send_high_priority_text(todo):
	"""Background job: the text itself. Returns True when one went."""
	if not frappe.db.exists("ToDo", todo):
		return False
	row = frappe.db.get_value(
		"ToDo",
		todo,
		[
			"name",
			"status",
			"allocated_to",
			"assigned_by",
			"owner",
			"description",
			"reference_type",
			"reference_name",
			"date",
			"creation",
		],
		as_dict=True,
	)
	if not row or row.status != "Open":
		return False
	from erpnext_enhancements.project_enhancements.planner_notices import cell_number

	number = cell_number(row.allocated_to)
	if not number:
		return False
	title, ask = _what(row, _titles([row]))
	assigner = row.assigned_by or row.owner
	name = frappe.db.get_value("User", assigner, "full_name") if assigner else None
	message = assignments.sms_text(name, title, ask, _url(row))
	from erpnext_enhancements.api.telephony import send_system_sms

	try:
		send_system_sms(number, message)
	except Exception:
		frappe.log_error(
			title="My Work: High-priority text failed", message=f"{todo}\n{frappe.get_traceback()}"
		)
		return False
	return True
