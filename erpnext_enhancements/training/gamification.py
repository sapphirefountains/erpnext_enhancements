# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Points, badges, streaks and the two leaderboards.

**Staff and customers are never ranked together.** A client contact who watched a
handover video does not belong in a list of technicians, and the technicians
certainly do not belong in a list a client can read. Two mechanisms, because one
would eventually be forgotten:

  * ``Training Learner Stat.learner_type`` is derived by that doctype's own
	controller and never accepted from a caller, so the column cannot be wrong;
  * every read of the table in this module goes through ``_stat_rows``, whose
	first argument is a **mandatory positional** learner type and which raises on
	one it does not recognise. There is no "all" scope to pass by accident, and no
	code path here that reads the table without naming a board. An optional privacy
	filter is a privacy filter somebody eventually leaves out.

The customer board is narrowed once more, to the caller's own Customer, so a
leaderboard never becomes a client directory. ``Training Learner Stat`` carries no
Customer column, so that narrowing is a ``user in (...)`` set resolved through
Contact → Dynamic Link rather than a column filter — same guarantee, one extra
indexed query, and it stays correct if a contact moves between customers.

**Everything is denormalised into ``Training Learner Stat``, one row per learner**,
because aggregating ``Training Completion`` per page load is a table scan of the
one Training table that only ever grows. The row is rebuilt on submit of a
completion (indexed by user, cheap) and swept nightly. Nothing in it is evidence
of anything — it can be thrown away and rebuilt.

**Streaks decay at midnight.** ``refresh_learner_stats`` zeroes any streak whose
last completion is older than yesterday, and ``_decayed_streak`` repeats the test
at read time: a scheduler that missed a night must never show somebody a streak
they no longer have. The stored value is a cache, the date is the truth.

**Badges are data, not code.** ``Training Badge`` holds the criteria and this
module only evaluates them, so adding an award is a form, not a deploy. Awarding
is idempotent by re-running — it computes what a learner should hold and inserts
what is missing — which is why ``Training Badge Award`` refuses duplicates and why
points are copied onto the award rather than read through the link.

Gated on ``Training Settings → Gamification``, off by default, and every entry
point is wrapped. ``award_badges`` runs inside ``Training Completion``'s submit
transaction (via ``certificates.after_completion``): a badge is a garnish, and a
garnish must never roll back the audit record it decorates.
"""

import functools
import itertools

import frappe
from frappe.utils import add_days, cint, flt, getdate, nowdate

from erpnext_enhancements.training.doctype.training_settings.training_settings import is_enabled

STAT_DOCTYPE = "Training Learner Stat"
BADGE_DOCTYPE = "Training Badge"
AWARD_DOCTYPE = "Training Badge Award"

# The two boards. Same vocabulary as Training Assignment.learner_type and
# Training Learner Stat.learner_type — one word for "which kind of learner is
# this" across the whole module.
STAFF = "Employee"
CUSTOMER = "Website User"
LEARNER_TYPES = (STAFF, CUSTOMER)

# Course scoring. Badge points come from the badge record; these three are the
# baseline everybody earns by finishing things. Arbitrary, but they have to stay
# put: re-weighting silently reorders every historical board, which reads to the
# people on it as a bug rather than as a policy change.
POINTS_PER_COURSE = 10
POINTS_PER_PERFECT = 5
POINTS_PER_STREAK_DAY = 2

# One day of slack before a streak breaks. A pass at 23:58 followed by one at
# 00:02 is the same evening's work to everybody except a calendar.
STREAK_GRACE_DAYS = 1

LEADERBOARD_LIMIT = 20

MANAGER_ROLES = {"System Manager", "Training Manager", "HR Manager"}

# The manager view of the customer board, said out loud. A missing customer fails
# closed to an empty board — which is the right reading of a privacy filter nobody
# set — so "every customer at once" needs its own value rather than being what you
# get by forgetting one.
ALL_CUSTOMERS = "__all_customers__"

# Training Badge.criteria_type, mirrored from that doctype. An unrecognised type
# awards nothing rather than everything — a badge whose rule this module cannot
# answer must not become a badge everybody has.
FIRST_COMPLETION = "First Completion"
PERFECT_SCORE = "Perfect Score"
COUNT_BASED = "Courses Completed Count"
STREAK_DAYS = "Streak Days"
COURSE_COMPLETED = "Course Completed"
CATEGORY_COMPLETED = "Category Completed"


# ----------------------------------------------------------------------- guards


def _log_quietly(where):
	try:
		frappe.log_error(
			title="Training gamification failed",
			message=f"{where}\n{frappe.get_traceback()}",
		)
	except Exception:
		pass


def _active():
	"""Enabled, not mid-maintenance, and the stat table actually exists."""
	flags = frappe.flags
	if flags.in_migrate or flags.in_install or flags.in_patch or flags.in_import:
		return False
	if not is_enabled("gamification_enabled"):
		return False
	return bool(frappe.db.exists("DocType", STAT_DOCTYPE))


def _safe(default=None):
	"""Gate + swallow, the same shape as ``compliance._advisory``.

	``award_badges`` runs inside the submit transaction of a Training Completion.
	An exception there rolls back the completion itself — a lost certification
	because a badge could not be counted. Never worth it.
	"""

	def decorate(fn):
		@functools.wraps(fn)
		def wrapper(*args, **kwargs):
			try:
				if not _active():
					return default() if callable(default) else default
				return fn(*args, **kwargs)
			except Exception:
				_log_quietly(fn.__name__)
			return default() if callable(default) else default

		return wrapper

	return decorate


# ------------------------------------------------------------------ entry points


@_safe(default=list)
def award_badges(completion):
	"""Rebuild one learner's stats and return the badge names they just earned.

	Reached from ``Training Completion.on_submit`` through
	``certificates.after_completion``, so a completion recorded by hand earns
	exactly what one earned through the player does. Accepts the document or its
	name: ``on_submit`` has the doc, a retry has the name.
	"""
	user = getattr(completion, "user", None)
	name = getattr(completion, "name", None) or (completion if isinstance(completion, str) else None)
	if not user and name:
		user = frappe.db.get_value("Training Completion", name, "user")
	if not user:
		return []
	return rebuild_learner_stat(user, source_completion=name).get("new_badges", [])


# The name ``certificates.after_completion`` calls. Kept as an alias rather than a
# rename so both halves of the seam can be read for what they are: one module
# awards badges, the other reacts to a completion.
award_for_completion = award_badges


@_safe()
def refresh_learner_stats():
	"""Nightly sweep: rebuild every learner with a completion, then expire streaks.

	Rebuilds everyone rather than only yesterday's movers, because the cheap thing
	to get wrong here is a row that quietly stopped being updated — an Employee
	record created late, a badge added last week — and then disagreed with the
	truth for a year. It is a nightly pass over one row per person; the scan it
	buys off is the one that would otherwise happen on every page load.
	"""
	for user in frappe.get_all("Training Completion", filters={"docstatus": 1}, pluck="user", distinct=True):
		if not user:
			continue
		try:
			rebuild_learner_stat(user)
		except Exception:
			# One unbuildable learner must not cost everybody else their refresh.
			_log_quietly(f"rebuild_learner_stat({user})")

	_expire_stale_streaks()
	frappe.db.commit()


@frappe.whitelist()
def get_leaderboard(scope=None):
	"""The board for one kind of learner. Never both.

	``scope`` is a convenience, never an access decision: a manager may ask for
	either board, anybody else gets their own whatever they send.
	"""
	if not _active():
		return {"enabled": False, "scope": None, "rows": []}

	me = frappe.session.user
	learner_type = _resolve_scope(scope, me)
	rows = _stat_rows(learner_type, customer=_customer_bound(learner_type, me))

	names = _full_names([row.get("user") for row in rows])
	out = []
	for index, row in enumerate(rows, start=1):
		user = row.get("user")
		out.append(
			{
				"rank": index,
				"full_name": names.get(user) or user,
				"points": cint(row.get("total_points")),
				"courses_completed": cint(row.get("courses_completed")),
				"badges_earned": cint(row.get("badges_earned")),
				"current_streak_days": _decayed_streak(row),
				"longest_streak_days": cint(row.get("longest_streak_days")),
				"is_me": user == me,
			}
		)
	return {"enabled": True, "scope": learner_type, "rows": out}


# ------------------------------------------------------------- building the row


def rebuild_learner_stat(user, source_completion=None):
	"""Upsert one learner's denormalised row, awarding any badge now earned.

	Badges are evaluated *before* the row is written so the awards they add are
	counted in the same pass; a second pass would leave the board briefly
	disagreeing with the badge list.
	"""
	stats = _measure(user)
	stats["new_badges"] = _award_missing_badges(user, stats, source_completion)

	awards = frappe.get_all(AWARD_DOCTYPE, filters={"user": user}, fields=["points"])
	badge_points = sum(cint(a.get("points")) for a in awards)

	name = frappe.db.get_value(STAT_DOCTYPE, {"user": user}, "name")
	doc = frappe.get_doc(STAT_DOCTYPE, name) if name else frappe.new_doc(STAT_DOCTYPE)
	doc.user = user
	# learner_type and employee are deliberately *not* set here. Training Learner
	# Stat derives both on every save, and that derivation is what keeps the two
	# boards apart — writing them from here would make the guarantee depend on
	# this caller getting it right.
	doc.total_points = stats["course_points"] + badge_points
	doc.courses_completed = stats["courses_completed"]
	doc.badges_earned = len(awards)
	doc.current_streak_days = stats["current_streak"]
	doc.longest_streak_days = max(cint(getattr(doc, "longest_streak_days", 0)), stats["longest_streak"])
	doc.last_completion_date = stats["last_completion_date"]
	# Only ever advanced. A lesson watched or a quiz attempted is activity too, and
	# those are stamped by other parts of the module — a completion sweep must not
	# wind the date back to the last thing *it* knows about.
	doc.last_activity_date = _latest(doc.last_activity_date, stats["last_completion_date"])
	doc.save(ignore_permissions=True)

	return stats


def _measure(user):
	"""Everything the badges and the board need, from this learner's own rows.

	One indexed query over this learner's completions — affordable per submit, and
	exactly the scan the denormalised row exists to keep off the page load.
	"""
	rows = frappe.get_all(
		"Training Completion",
		filters={"user": user, "docstatus": 1},
		fields=["course", "score_percent", "completed_on"],
	)

	courses = {r.get("course") for r in rows if r.get("course")}
	perfect = sum(1 for r in rows if flt(r.get("score_percent")) >= 100)
	days = sorted({getdate(r.get("completed_on")) for r in rows if r.get("completed_on")})
	current_streak, longest_streak = _streaks(days)

	return {
		"user": user,
		"courses": courses,
		"courses_completed": len(courses),
		"perfect_scores": perfect,
		"current_streak": current_streak,
		"longest_streak": longest_streak,
		"last_completion_date": days[-1] if days else None,
		"course_points": (
			len(courses) * POINTS_PER_COURSE
			+ perfect * POINTS_PER_PERFECT
			+ longest_streak * POINTS_PER_STREAK_DAY
		),
	}


def _latest(*dates):
	known = [getdate(d) for d in dates if d]
	return max(known) if known else None


def _streaks(days):
	"""``(current, longest)`` runs of consecutive days with a completion.

	``current`` is the run ending today or within ``STREAK_GRACE_DAYS`` of today;
	anything older has already decayed and reads as zero here even if the nightly
	sweep has not written that back yet.
	"""
	if not days:
		return 0, 0

	longest = run = 1
	for previous, day in itertools.pairwise(days):
		run = run + 1 if (day - previous).days == 1 else 1
		longest = max(longest, run)

	current = run if (getdate(nowdate()) - days[-1]).days <= STREAK_GRACE_DAYS else 0
	return current, longest


def _decayed_streak(row):
	"""Read-time decay, so a missed scheduler run cannot inflate the board."""
	last = row.get("last_completion_date")
	if not last:
		return 0
	if (getdate(nowdate()) - getdate(last)).days > STREAK_GRACE_DAYS:
		return 0
	return cint(row.get("current_streak_days"))


def _expire_stale_streaks():
	"""Midnight decay, written back so exports and reports agree with the board."""
	cutoff = add_days(nowdate(), -STREAK_GRACE_DAYS)
	for name in frappe.get_all(
		STAT_DOCTYPE,
		filters={"current_streak_days": [">", 0], "last_completion_date": ["<", cutoff]},
		pluck="name",
	):
		frappe.db.set_value(STAT_DOCTYPE, name, "current_streak_days", 0, update_modified=False)


# -------------------------------------------------------------------- badges


def _award_missing_badges(user, stats, source_completion=None):
	"""Insert an award for every enabled badge this learner has earned and lacks.

	Idempotent by re-running rather than by remembering: it compares what they
	should hold against what they do. ``Training Badge Award`` refuses duplicates
	on its own, so a race between the nightly sweep and a submit costs one caught
	exception rather than a double score — which is why each insert is caught
	individually instead of the loop being wrapped once.
	"""
	held = set(frappe.get_all(AWARD_DOCTYPE, filters={"user": user}, pluck="badge"))
	awarded = []

	for badge in frappe.get_all(
		BADGE_DOCTYPE,
		filters={"enabled": 1},
		fields=["name", "criteria_type", "criteria_value", "criteria_course", "criteria_category"],
	):
		if badge.get("name") in held or not _badge_is_earned(badge, stats):
			continue
		try:
			frappe.get_doc(
				{
					"doctype": AWARD_DOCTYPE,
					"badge": badge.get("name"),
					"user": user,
					"source_completion": source_completion,
				}
			).insert(ignore_permissions=True)
			awarded.append(badge.get("name"))
		except Exception:
			_log_quietly(f"award {badge.get('name')} to {user}")

	return awarded


def _badge_is_earned(badge, stats):
	kind = badge.get("criteria_type")
	if kind == FIRST_COMPLETION:
		return stats["courses_completed"] >= 1
	if kind == PERFECT_SCORE:
		return stats["perfect_scores"] >= 1
	if kind == COUNT_BASED:
		return stats["courses_completed"] >= cint(badge.get("criteria_value"))
	if kind == STREAK_DAYS:
		return stats["longest_streak"] >= cint(badge.get("criteria_value"))
	if kind == COURSE_COMPLETED:
		return badge.get("criteria_course") in stats["courses"]
	if kind == CATEGORY_COMPLETED:
		return _category_is_complete(badge.get("criteria_category"), stats["courses"])
	# Unknown criterion: award nothing. A rule this module cannot answer must not
	# turn into a badge everybody has.
	return False


def _category_is_complete(category, completed):
	"""Every published course in the category, and there has to be at least one.

	An empty category would otherwise satisfy "all of them are done" vacuously and
	hand the badge to the entire company.
	"""
	if not category:
		return False
	courses = set(
		frappe.get_all(
			"Training Course",
			filters={"category": category, "status": "Published"},
			pluck="name",
		)
	)
	return bool(courses) and courses.issubset(completed)


# -------------------------------------------------------------- board separation


def _stat_rows(learner_type, customer=None, limit=LEADERBOARD_LIMIT):
	"""**The only read of the stat table in this module.**

	``learner_type`` is positional and mandatory and an unrecognised one raises,
	rather than degrading to "everyone" — the failure being designed against is a
	future caller that omits it and silently publishes a mixed board. A customer
	board additionally requires a Customer and returns nothing without one.
	"""
	if learner_type not in LEARNER_TYPES:
		raise ValueError(f"learner_type must be one of {LEARNER_TYPES}, got {learner_type!r}")

	filters = {"learner_type": learner_type}
	if learner_type == CUSTOMER and customer != ALL_CUSTOMERS:
		users = _customer_users(customer)
		if not users:
			return []
		filters["user"] = ["in", users]

	return frappe.get_all(
		STAT_DOCTYPE,
		filters=filters,
		fields=[
			"user",
			"total_points",
			"courses_completed",
			"badges_earned",
			"current_streak_days",
			"longest_streak_days",
			"last_completion_date",
		],
		order_by="total_points desc, courses_completed desc",
		limit=limit,
	)


def _resolve_scope(scope, user):
	"""Which board this caller gets. Managers may choose; nobody else can."""
	own = _learner_type(user)
	if not _is_manager(user):
		return own

	requested = (scope or "").strip()
	if requested in LEARNER_TYPES:
		return requested
	if requested.lower() in ("staff", "internal", "employee"):
		return STAFF
	if requested.lower() in ("customer", "customers", "client", "website user"):
		return CUSTOMER
	return own


def _customer_bound(learner_type, user):
	"""The Customer a customer board is confined to.

	A Training Manager sees the whole customer board; a customer contact sees only
	their own organisation's, and a contact we cannot place gets an empty board
	rather than everybody else's.
	"""
	if learner_type != CUSTOMER:
		return None
	if _is_manager(user):
		return ALL_CUSTOMERS
	return _customer_for_user(user) or ""


def _customer_users(customer):
	"""Logins belonging to one Customer, via Contact → Dynamic Link.

	``Training Learner Stat`` has no Customer column, so the customer board is
	narrowed by user set instead. Blank customer means "we could not place this
	caller", which returns nothing — never everybody.
	"""
	if not customer:
		return []
	contacts = frappe.get_all(
		"Dynamic Link",
		filters={"link_doctype": "Customer", "link_name": customer, "parenttype": "Contact"},
		pluck="parent",
	)
	if not contacts:
		return []
	return [u for u in frappe.get_all("Contact", filters={"name": ["in", contacts]}, pluck="user") if u]


def _learner_type(user):
	"""Mirrors ``Training Learner Stat._resolve_learner`` exactly.

	It has to: that controller decides which board a row lands on, and this decides
	which board the caller is shown. Two different rules would let somebody be
	ranked on one board while being scoped to the other.
	"""
	return STAFF if frappe.db.get_value("Employee", {"user_id": user}, "name") else CUSTOMER


def _customer_for_user(user):
	from erpnext_enhancements.training.doctype.training_assignment.training_assignment import (
		_customer_for_user as resolve,
	)

	return resolve(user)


def _is_manager(user):
	return bool(MANAGER_ROLES & set(frappe.get_roles(user)))


def _full_names(users):
	"""One query for the ≤20 names on screen, rather than one per row."""
	users = [u for u in dict.fromkeys(u for u in users if u)]
	if not users:
		return {}
	return {
		row.get("name"): row.get("full_name")
		for row in frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"])
	}
