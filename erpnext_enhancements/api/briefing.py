"""Per-user Morning Briefing (ported from Triton's briefing scheduler).

Weekday mornings (cron ``30 6 * * 1-5``, evaluated in the site's System
Settings timezone — must be America/Denver for Triton parity), a batch job
pre-generates one briefing per enabled recipient and caches it durably in the
**Daily Briefing** doctype: today's and overdue tasks assigned to the user,
today's calendar, their open pipeline, and due ToDos — narrated by Gemini via
the existing ``api.gemini`` wrapper, with a deterministic markdown fallback
composed from the same data when Gemini is unavailable or disabled (never a
dead "sorry" message).

Surfaces: the "Morning Briefing" Custom HTML Block (desk), an optional
per-recipient email, and (read-only, structured) the Wall Display.

Settings: the "Morning Briefing" section of ERPNext Enhancements Settings —
``briefing_enabled`` master switch (default off, the app's staged-rollout
convention), ``briefing_use_gemini`` cost switch, and the
``briefing_recipients`` child table (per-row email opt-in).
"""

import frappe
from frappe.utils import add_days, cint, now_datetime, nowdate

from erpnext_enhancements.api.task_dashboard import CLOSED_TASK_STATUSES, STAFF_ROLES

RETENTION_DAYS = 60
TASK_LIMIT = 15
EVENT_LIMIT = 10
PIPELINE_LIMIT = 5
TODO_LIMIT = 5

# Won/dead opportunities don't belong in a "what needs my attention" briefing.
CLOSED_OPPORTUNITY_STATUSES = ("Lost", "Closed", "Converted")

SYSTEM_INSTRUCTION = (
	"You are the executive AI assistant for Sapphire Fountains, a water-feature "
	"design/build/service company. You write crisp, energizing morning briefings "
	"for one employee. Only reference the live data provided — never invent "
	"tasks, meetings or deals. Omit any section with no data. Use Markdown."
)


def _get_settings():
	return frappe.get_cached_doc("ERPNext Enhancements Settings")


def briefing_enabled(settings=None):
	settings = settings or _get_settings()
	return bool(cint(settings.get("briefing_enabled")))


# --------------------------------------------------------------------------- data


def _user_tasks(user, today):
	"""Open tasks assigned to ``user``: (overdue, today's) card lists."""
	like = f'%"{user}"%'  # _assign is a JSON array of user emails
	task_fields = [
		"name",
		"subject",
		"priority",
		"status",
		"project",
		"_assign",
		"exp_start_date",
		"exp_end_date",
	]

	overdue = frappe.get_all(
		"Task",
		filters={
			"status": ("not in", CLOSED_TASK_STATUSES),
			"exp_end_date": ("<", today),
			"_assign": ("like", like),
		},
		fields=task_fields,
		order_by="exp_end_date asc",
		limit_page_length=TASK_LIMIT,
	)

	spanning = frappe.get_all(
		"Task",
		filters={
			"status": ("not in", CLOSED_TASK_STATUSES),
			"exp_start_date": ("<=", today),
			"exp_end_date": (">=", today),
			"_assign": ("like", like),
		},
		fields=task_fields,
		order_by="priority desc, exp_end_date asc",
		limit_page_length=TASK_LIMIT,
	)
	edges = frappe.get_all(
		"Task",
		filters={
			"status": ("not in", CLOSED_TASK_STATUSES),
			"exp_start_date": today,
			"exp_end_date": ("is", "not set"),
			"_assign": ("like", like),
		},
		fields=task_fields,
	) + frappe.get_all(
		"Task",
		filters={
			"status": ("not in", CLOSED_TASK_STATUSES),
			"exp_end_date": today,
			"exp_start_date": ("is", "not set"),
			"_assign": ("like", like),
		},
		fields=task_fields,
	)
	seen = {t.name for t in spanning}
	today_tasks = (spanning + [t for t in edges if t.name not in seen])[:TASK_LIMIT]

	projects = {}
	project_ids = {t.project for t in overdue + today_tasks if t.project}
	if project_ids:
		projects = dict(
			frappe.get_all(
				"Project",
				filters={"name": ("in", list(project_ids))},
				fields=["name", "project_name"],
				as_list=True,
			)
		)

	def card(task):
		return {
			"subject": task.subject,
			"priority": task.priority or "Low",
			"project": projects.get(task.project) or task.project or "",
			"due": str(task.exp_end_date) if task.exp_end_date else "",
		}

	return [card(t) for t in overdue], [card(t) for t in today_tasks]


def _user_events(user, today):
	"""Today's schedule: public open Events overlapping today, plus the user's own."""
	events = frappe.db.sql(
		"""
		SELECT subject, starts_on, ends_on, all_day
		FROM `tabEvent`
		WHERE status = 'Open'
		  AND (event_type = 'Public' OR owner = %(user)s)
		  AND starts_on <= %(day_end)s
		  AND COALESCE(ends_on, starts_on) >= %(day_start)s
		ORDER BY all_day DESC, starts_on ASC
		LIMIT %(limit)s
		""",
		{
			"user": user,
			"day_start": f"{today} 00:00:00",
			"day_end": f"{today} 23:59:59",
			"limit": EVENT_LIMIT,
		},
		as_dict=True,
	)
	return [
		{
			"subject": e.subject,
			"starts_on": str(e.starts_on) if e.starts_on else "",
			"all_day": cint(e.all_day),
		}
		for e in events
	]


def _user_pipeline(user):
	opportunities = frappe.get_all(
		"Opportunity",
		filters={
			"opportunity_owner": user,
			"status": ("not in", CLOSED_OPPORTUNITY_STATUSES),
		},
		fields=["name", "title", "party_name", "status", "opportunity_amount"],
		order_by="modified desc",
		limit_page_length=PIPELINE_LIMIT,
	)
	return [
		{
			"title": o.title or o.name,
			"party": o.party_name or "",
			"status": o.status or "",
			"amount": float(o.opportunity_amount or 0),
		}
		for o in opportunities
	]


def _user_todos(user, today):
	todos = frappe.get_all(
		"ToDo",
		filters={"allocated_to": user, "status": "Open", "date": ("<=", today)},
		fields=["description", "date", "priority"],
		order_by="date asc",
		limit_page_length=TODO_LIMIT,
	)
	return [
		{
			"description": frappe.utils.strip_html(t.description or "")[:140],
			"date": str(t.date) if t.date else "",
		}
		for t in todos
	]


def gather_briefing_data(user):
	"""Everything one user's briefing is built from — also consumed raw by the
	Wall Display band (structured, no LLM dependency)."""
	today = nowdate()
	overdue, today_tasks = _user_tasks(user, today)
	return {
		"date": today,
		"user": user,
		"overdue_tasks": overdue,
		"today_tasks": today_tasks,
		"events": _user_events(user, today),
		"pipeline": _user_pipeline(user),
		"todos": _user_todos(user, today),
	}


# ----------------------------------------------------------------------- compose


def _first_name(user):
	full_name = frappe.db.get_value("User", user, "full_name") or user
	return full_name.split(" ")[0]


def _format_data_blocks(data):
	lines = []

	def block(title, rows, fmt):
		lines.append(f"### {title}")
		if rows:
			lines.extend(fmt(r) for r in rows)
		else:
			lines.append("(none)")
		lines.append("")

	block(
		"TODAY'S SCHEDULE",
		data["events"],
		lambda e: f"- {e['subject']}" + ("" if e["all_day"] else f" at {e['starts_on']}"),
	)
	block(
		"TASKS DUE TODAY",
		data["today_tasks"],
		lambda t: f"- [{t['priority']}] {t['subject']}" + (f" ({t['project']})" if t["project"] else ""),
	)
	block(
		"OVERDUE TASKS",
		data["overdue_tasks"],
		lambda t: f"- {t['subject']}" + (f" ({t['project']})" if t["project"] else "") + f" — due {t['due']}",
	)
	block(
		"OPEN PIPELINE (yours)",
		data["pipeline"],
		lambda o: f"- {o['title']} — {o['party']} [{o['status']}]"
		+ (f" ${o['amount']:,.0f}" if o["amount"] else ""),
	)
	block("OPEN TODOS DUE", data["todos"], lambda t: f"- {t['description']} (due {t['date']})")
	return "\n".join(lines)


def compose_prompt(data, user):
	first_name = _first_name(user)
	return (
		f"Write the morning briefing for {first_name} for {data['date']}.\n\n"
		"LIVE DATA STREAMS (only reference these — do not invent items):\n\n"
		f"{_format_data_blocks(data)}\n"
		"Output format (Markdown, omit empty sections):\n"
		f"Start with a one-line personal greeting to {first_name}.\n"
		"Then sections, each only if it has data:\n"
		"## 📅 Today's Schedule\n"
		"## 📋 Tasks Today\n"
		"## ⚠️ Overdue\n"
		"## 💼 Pipeline Pulse\n"
		"## 🎯 Top 3 Priorities (your judgement, drawn strictly from the data)\n"
		"Keep it under 350 words. Plain, direct, no fluff."
	)


def compose_fallback(data, user):
	"""Deterministic markdown briefing from the same data — used when Gemini is
	disabled or fails. Never a dead apology: counts + the actual lists."""
	first_name = _first_name(user)
	parts = [f"Good morning, {first_name} — here's {data['date']} at a glance."]

	def section(title, rows, fmt):
		if not rows:
			return
		parts.append(f"\n## {title}\n")
		parts.extend(fmt(r) for r in rows)

	section(
		"📅 Today's Schedule",
		data["events"],
		lambda e: f"- {e['subject']}" + ("" if e["all_day"] else f" — {e['starts_on']}"),
	)
	section(
		"📋 Tasks Today",
		data["today_tasks"],
		lambda t: f"- **[{t['priority']}]** {t['subject']}" + (f" — {t['project']}" if t["project"] else ""),
	)
	section(
		"⚠️ Overdue",
		data["overdue_tasks"],
		lambda t: f"- {t['subject']}" + (f" — {t['project']}" if t["project"] else "") + f" (due {t['due']})",
	)
	section(
		"💼 Pipeline Pulse",
		data["pipeline"],
		lambda o: f"- {o['title']} — {o['party']} [{o['status']}]",
	)
	section("✅ ToDos Due", data["todos"], lambda t: f"- {t['description']}")

	if len(parts) == 1:
		parts.append("\nNothing scheduled, due, or overdue on your plate. Enjoy the clear runway!")
	return "\n".join(parts)


def _generate_narrative(data, user, settings):
	"""Returns (content, source, error)."""
	if not cint(settings.get("briefing_use_gemini")):
		return compose_fallback(data, user), "Fallback", None

	try:
		from erpnext_enhancements.api.gemini import generate_content_with_vertex_ai

		triton_settings = frappe.get_doc("Triton Settings")
		text, _thoughts = generate_content_with_vertex_ai(
			compose_prompt(data, user), SYSTEM_INSTRUCTION, triton_settings, feature="morning_briefing"
		)
		if text and text.strip():
			return text.strip(), "Gemini", None
		return compose_fallback(data, user), "Fallback", "Gemini returned empty text"
	except Exception as e:
		frappe.log_error(f"Briefing narrative failed for {user}: {e}", "Morning Briefing")
		return compose_fallback(data, user), "Fallback", str(e)[:500]


# ---------------------------------------------------------------------- generate


def generate_briefing_for_user(user, force=False, settings=None):
	"""Generate (or return the cached) Daily Briefing for ``user`` today."""
	settings = settings or _get_settings()
	today = nowdate()

	existing = frappe.db.get_value("Daily Briefing", {"user": user, "date": today})
	if existing:
		if not force:
			return frappe.get_doc("Daily Briefing", existing)
		frappe.delete_doc("Daily Briefing", existing, force=True, ignore_permissions=True)

	data = gather_briefing_data(user)
	content, source, error = _generate_narrative(data, user, settings)

	doc = frappe.get_doc(
		{
			"doctype": "Daily Briefing",
			"user": user,
			"date": today,
			"content": content,
			"narrative_source": source,
			"generated_at": now_datetime(),
			"generation_error": error,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def scheduled_briefing_run():
	"""Cron entry (weekdays 06:30 site-TZ): hand the batch to a long worker —
	N users x up-to-120s Gemini calls must not block a short worker."""
	if not briefing_enabled():
		return
	frappe.enqueue(
		"erpnext_enhancements.api.briefing.generate_briefings_for_all_users",
		queue="long",
		timeout=3600,
	)


def generate_briefings_for_all_users():
	"""Generate every enabled recipient's briefing; one failure never kills the
	batch (commit per user, error logged)."""
	settings = _get_settings()
	if not briefing_enabled(settings):
		return

	for row in settings.get("briefing_recipients") or []:
		user = row.user
		if not user or not cint(frappe.db.get_value("User", user, "enabled") or 0):
			continue
		try:
			doc = generate_briefing_for_user(user, settings=settings)
			if cint(row.get("send_email")) and doc:
				_send_briefing_email(doc)
			frappe.db.commit()
		except Exception:
			frappe.db.rollback()
			frappe.log_error(
				f"Briefing generation failed for {user}\n{frappe.get_traceback()}",
				"Morning Briefing",
			)


def _send_briefing_email(doc):
	# one row per (user, date): a same-day regeneration via force doesn't
	# re-email — only the scheduled batch sends.
	frappe.sendmail(
		recipients=[doc.user],
		subject=f"Morning Briefing — {doc.date}",
		message=frappe.utils.md_to_html(doc.content or ""),
	)


def purge_old_briefings():
	"""Daily scheduler: drop briefings older than RETENTION_DAYS."""
	cutoff = add_days(nowdate(), -RETENTION_DAYS)
	for name in frappe.get_all(
		"Daily Briefing", filters={"date": ("<", cutoff)}, pluck="name"
	):
		frappe.delete_doc("Daily Briefing", name, force=True, ignore_permissions=True)


# --------------------------------------------------------------------- endpoint


@frappe.whitelist()
def get_morning_briefing(force=0):
	"""The session user's briefing for today (desk block endpoint).

	Recipients govern the scheduled batch + email; any staff role may pull a
	briefing on demand here. ``force=1`` regenerates synchronously (can take up
	to ~2 min when Gemini is slow — the block shows a spinner).
	"""
	settings = _get_settings()
	if not briefing_enabled(settings):
		return {"available": False, "reason": "Morning Briefing is disabled in ERPNext Enhancements Settings."}

	if not STAFF_ROLES.intersection(set(frappe.get_roles())):
		frappe.throw(
			frappe._("You do not have permission to view the Morning Briefing."),
			frappe.PermissionError,
		)

	user = frappe.session.user
	doc = generate_briefing_for_user(user, force=cint(force), settings=settings)
	return {
		"available": True,
		"briefing": doc.content,
		"date": str(doc.date),
		"source": doc.narrative_source,
		"generated_at": str(doc.generated_at),
	}
