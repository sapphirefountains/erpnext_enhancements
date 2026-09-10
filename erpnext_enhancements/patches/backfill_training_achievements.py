"""Give the feed something to show on day one (v1.386.0).

**A feed with three items in it reads as broken, not as new.** Prod holds three
submitted `Training Completion` rows, four `Training Badge Award` rows and one
sign-off, so a feed built and left to fill itself would open empty for everybody
and be judged on that.

So this mints an achievement for what has already happened. Nothing here is
invented: every row points at a real completion, award or sign-off that was
already on the site, with its own date. The feed is a different *view* of history,
not a new claim about it.

Work anniversaries are seeded too, and they are the only entries with no source
document. That is deliberate: sixteen people who have mostly been here for years
give the feed a spine on the day it opens, and an anniversary is the one thing
worth celebrating that nobody has to have *done* anything to earn — which matters
in a feed whose whole risk is that it becomes a scoreboard.

Idempotent: `social.record` refuses a duplicate on `(user, kind, title, source)`,
so a second run adds nothing. Never raises — an achievement that fails to backfill
is a missing line in a feed, and that is not worth a failed migrate.
"""

import frappe
from frappe.utils import getdate, today


def execute():
	if not frappe.db.exists("DocType", "Training Achievement"):
		frappe.log_error(
			"Training Achievement is not on this site, so the feed was not backfilled.",
			"Training feed",
		)
		return

	from erpnext_enhancements.training import social

	created = 0
	created += _completions(social)
	created += _badges(social)
	created += _signoffs(social)
	created += _anniversaries(social)

	if created:
		print(f"[erpnext_enhancements] backfilled {created} achievement(s) into the team feed")


def _completions(social):
	"""Only **Valid** ones.

	A `Superseded` completion belongs to content that has since been materially
	changed, and an `Expired` or `Revoked` one is exactly what
	`withdraw_for_completion` exists to take back out. Seeding them would mean the
	feed opening with entries it would immediately have had to delete.
	"""
	made = 0
	for row in frappe.get_all(
		"Training Completion",
		filters={"docstatus": 1, "status": "Valid"},
		fields=["name", "user", "course", "course_title_snapshot", "completed_on"],
		order_by="completed_on asc",
	):
		if social.record(
			row.user,
			"Course Completed",
			row.course_title_snapshot or row.course,
			occurred_on=row.completed_on,
			source_completion=row.name,
		):
			made += 1
	return made


def _badges(social):
	made = 0
	if not frappe.db.exists("DocType", "Training Badge Award"):
		return 0
	for row in frappe.get_all(
		"Training Badge Award",
		fields=["name", "user", "badge", "awarded_on"],
		order_by="awarded_on asc",
	):
		if social.record(
			row.user,
			"Badge Earned",
			row.badge,
			occurred_on=row.awarded_on,
			source_badge_award=row.name,
		):
			made += 1
	return made


def _signoffs(social):
	made = 0
	if not frappe.db.exists("DocType", "Training Signoff"):
		return 0
	for row in frappe.get_all(
		"Training Signoff",
		filters={"docstatus": 1, "outcome": "Competent"},
		fields=["name", "user", "course", "signed_on"],
		order_by="signed_on asc",
	):
		title = frappe.db.get_value("Training Course", row.course, "course_title") or row.course
		if social.record(
			row.user, "Signed Off", title, occurred_on=row.signed_on, source_signoff=row.name
		):
			made += 1
	return made


def _anniversaries(social):
	"""The most recent completed year of service for each active employee.

	One entry each, not one per year: a feed that opens with sixty rows of history
	is a feed nobody scrolls to the bottom of. Somebody in their first year gets
	nothing, which is correct — "zero years" is not an anniversary.
	"""
	made = 0
	now = getdate(today())
	for row in frappe.get_all(
		"Employee",
		filters={"status": "Active", "user_id": ["is", "set"], "date_of_joining": ["is", "set"]},
		fields=["user_id", "employee_name", "date_of_joining"],
	):
		joined = getdate(row.date_of_joining)
		years = now.year - joined.year
		if (now.month, now.day) < (joined.month, joined.day):
			years -= 1
		if years < 1:
			continue
		try:
			occurred = joined.replace(year=joined.year + years)
		except ValueError:
			# 29 February. The 28th rather than skipping three years in four.
			occurred = joined.replace(year=joined.year + years, day=28)
		label = "1 year at Sapphire Fountains" if years == 1 else f"{years} years at Sapphire Fountains"
		if social.record(row.user_id, "Work Anniversary", label, occurred_on=occurred):
			made += 1
	return made
