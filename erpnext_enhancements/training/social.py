# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The feed: minting achievements, and serving them to the right audience.

Everything here obeys one rule, and it is worth stating before the code rather
than after:

    **The feed never carries a number somebody could be judged by.**

Not scores, not attempt counts, not coverage, not who is behind. An achievement
is "X finished Y" and nothing else. That is not squeamishness — in a
sixteen-person company where everybody knows everybody, a feed that publishes how
many goes at it somebody needed is a feed that makes people wait until they are
sure before they start, which is the opposite of what a training system is for.

Three deliberate constructions
------------------------------

**Minted in the existing fan-out, caught independently.**
``certificates.after_completion`` already fires the certificate, the badges and
the pass email, each inside its own ``try``. The achievement joins that list on
the same terms: a feed row failing to write must never cost somebody their
certificate.

**Audience is a mandatory positional, never a default.** ``_rows`` refuses to run
without a ``learner_type`` and raises on an unknown one, copied verbatim from
``gamification._stat_rows`` including its reason: *"an optional privacy filter is
a privacy filter somebody eventually leaves out."* In v1 the customer feed is
**empty by construction** — customers get their own courses and nothing else.

**Withdrawal propagates.** A revoked or superseded completion cancels its
achievement. The completion stays exactly where it is, because it is evidence; the
celebration of it does not, because a feed that keeps congratulating somebody for
a certification that has since been pulled is worse than no feed.
"""

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from erpnext_enhancements.training.doctype.training_achievement.training_achievement import (
	BADGE_EARNED,
	COURSE_COMPLETED,
	CUSTOMER,
	SIGNED_OFF,
	STAFF,
	WORK_ANNIVERSARY,
	wants_feed,
)

ACHIEVEMENT = "Training Achievement"
KUDOS = "Training Kudos"
PREFERENCE = "Training Profile Preference"

LEARNER_TYPES = (STAFF, CUSTOMER)

#: How many rows one feed page holds. Small on purpose: a feed people scroll to
#: the bottom of is a feed they finish, and this is sixteen people, not a network.
FEED_LIMIT = 30


def _enabled(force=False):
	"""Whether minting should happen at all.

	Two independent halves, and the difference between them is why ``force``
	exists:

	* **the table is there** — a hard precondition, never bypassable;
	* **we are not inside a migrate, install or patch** — the module-wide dormancy
	  convention this app uses everywhere (``assignment.py``, ``gamification.py``,
	  ``notifications.py``, ``signoff.py``), so a schema change never fires
	  business side effects.

	``force`` waives only the second. The backfill patch is the one caller that is
	*supposed* to run during a migrate, and without this it was a guaranteed
	no-op: every helper returned ``None``, ``created`` stayed 0, nothing raised,
	the transaction committed, and ``tabPatch Log`` recorded a successful run — so
	the feed would have opened empty on prod and the patch could never be retried.
	Third instance of that trap in this app; found by the migrate-safety audit.

	Note that relocating the backfill to an ``after_migrate`` hook does **not**
	substitute for this: v16 runs those inside ``post_schema_updates()`` while
	``frappe.flags.in_migrate`` is still True (it is cleared in ``tearDown()``,
	``frappe/migrate.py:116`` called at ``:282``).
	"""
	if not frappe.db.exists("DocType", ACHIEVEMENT):
		return False
	if force:
		return True
	return not (frappe.flags.in_migrate or frappe.flags.in_install or frappe.flags.in_patch)


# ------------------------------------------------------------------- minting


def record(user, kind, title, occurred_on=None, force=False, **sources):
	"""Mint one achievement, unless an identical one is already there.

	Idempotent on ``(user, kind, title, source)`` so a re-run of the backfill, or a
	completion submitted twice, cannot double the feed. Returns the docname or
	``None``; **never raises**, because every caller is a side-effect path hanging
	off something that matters more than this does.

	``force=True`` waives only the migrate/install/patch dormancy check, never the
	"does the table exist" one — see :func:`_enabled`. The backfill patch is the
	only caller that passes it, and it passes it because it runs *inside* a
	migrate by definition.
	"""
	if not _enabled(force=force) or not user or not title:
		return None
	try:
		filters = {"user": user, "kind": kind, "title": title}
		filters.update({key: value for key, value in sources.items() if value})
		existing = frappe.db.exists(ACHIEVEMENT, filters)
		if existing:
			return existing

		doc = frappe.get_doc(
			dict(
				{
					"doctype": ACHIEVEMENT,
					"user": user,
					"kind": kind,
					"title": title,
					"occurred_on": occurred_on or now_datetime(),
				},
				**sources,
			)
		)
		doc.insert(ignore_permissions=True)
		return doc.name
	except Exception:
		frappe.log_error(
			f"Could not record a {kind} achievement for {user}\n{frappe.get_traceback()}",
			"Training feed",
		)
		return None


def on_completion(completion):
	"""Called from ``certificates.after_completion``. One row per passed course."""
	return record(
		completion.user,
		COURSE_COMPLETED,
		completion.get("course_title_snapshot") or completion.get("course"),
		occurred_on=completion.get("completed_on"),
		source_completion=completion.name,
	)


def on_badge(award):
	return record(
		award.user,
		BADGE_EARNED,
		award.badge,
		occurred_on=award.get("awarded_on"),
		source_badge_award=award.name,
	)


def on_signoff(signoff):
	"""A hands-on sign-off is the achievement people are proudest of, and the one
	the old system had no way to show anybody."""
	title = frappe.db.get_value("Training Course", signoff.course, "course_title") or signoff.course
	return record(
		signoff.user,
		SIGNED_OFF,
		title,
		occurred_on=signoff.get("signed_on"),
		source_signoff=signoff.name,
	)


def withdraw_for_completion(completion_name):
	"""Drop the achievement behind a completion that has been revoked or superseded.

	Deleted rather than flagged: an achievement is not evidence and has no audit
	value of its own — the completion it pointed at is still there, still submitted,
	still carrying its ``revoked_reason``. Leaving a tombstone in the feed would be
	the worst of both, publishing that somebody's certification was pulled.
	"""
	if not _enabled():
		return 0
	dropped = 0
	try:
		for name in frappe.get_all(
			ACHIEVEMENT, filters={"source_completion": completion_name}, pluck="name"
		):
			frappe.delete_doc(ACHIEVEMENT, name, ignore_permissions=True, force=True)
			dropped += 1
	except Exception:
		frappe.log_error(
			f"Could not withdraw the achievement for {completion_name}\n{frappe.get_traceback()}",
			"Training feed",
		)
	return dropped


def resweep_visibility(user):
	"""Re-stamp this person's rows after they change their mind about the feed."""
	if not _enabled():
		return 0
	visibility = "Team" if wants_feed(user) else "Private"
	names = frappe.get_all(ACHIEVEMENT, filters={"user": user}, pluck="name")
	for name in names:
		frappe.db.set_value(ACHIEVEMENT, name, "visibility", visibility, update_modified=False)
	return len(names)


# -------------------------------------------------------------------- reading


def _learner_type(user):
	return STAFF if frappe.db.exists("Employee", {"user_id": user, "status": "Active"}) else CUSTOMER


def _rows(learner_type, limit=FEED_LIMIT, before=None):
	"""**The only read of the achievement table in this module.**

	``learner_type`` is positional and mandatory and an unrecognised one raises,
	rather than degrading to "everyone" — the failure being designed against is a
	future caller that omits it and silently publishes a mixed feed. Verbatim from
	``gamification._stat_rows``, which says the same thing about the leaderboard.

	The customer feed is empty by construction. Customers get their own courses and
	nothing else; there is no second cohort to be a peer group for them, and one
	client seeing another client's progress is a different product with different
	consequences.
	"""
	if learner_type not in LEARNER_TYPES:
		raise ValueError(f"learner_type must be one of {LEARNER_TYPES}, got {learner_type!r}")
	if learner_type == CUSTOMER:
		return []

	filters = {"learner_type": STAFF, "visibility": "Team"}
	if before:
		filters["occurred_on"] = ["<", before]
	return frappe.get_all(
		ACHIEVEMENT,
		filters=filters,
		fields=["name", "user", "kind", "title", "occurred_on"],
		order_by="occurred_on desc",
		limit=cint(limit) or FEED_LIMIT,
	)


def feed(user, before=None):
	"""The team feed as *user* may see it, with kudos attached."""
	rows = _rows(_learner_type(user), before=before)
	if not rows:
		return {"items": []}

	names = [row.name for row in rows]
	kudos = {}
	if frappe.db.exists("DocType", KUDOS):
		for row in frappe.get_all(
			KUDOS,
			filters={"achievement": ["in", names]},
			fields=["achievement", "from_user", "reaction", "note", "creation"],
			order_by="creation asc",
		):
			kudos.setdefault(row.achievement, []).append(row)

	full_names = _full_names(
		{row.user for row in rows}
		| {k.from_user for group in kudos.values() for k in group}
	)
	for row in rows:
		row["full_name"] = full_names.get(row.user, row.user)
		row["kudos"] = [
			{
				"from_user": k.from_user,
				"from_name": full_names.get(k.from_user, k.from_user),
				"reaction": k.reaction,
				"note": k.note or "",
			}
			for k in kudos.get(row.name, [])
		]
		row["mine"] = any(k.from_user == user for k in kudos.get(row.name, []))
		row["is_own"] = row.user == user
	return {"items": rows}


def add_kudos(user, achievement, reaction, note=None):
	"""Congratulate somebody. One per person per achievement; sending again edits.

	Editing rather than stacking: a second kudos from the same person is them
	changing their mind or adding a sentence, not a second cheer, and a feed where
	one enthusiastic colleague can appear five times under the same item reads as
	noise.
	"""
	if not _enabled():
		frappe.throw(_("The team feed is not available."))
	if _learner_type(user) != STAFF:
		frappe.throw(_("Only staff can post to the team feed."), frappe.PermissionError)

	target = frappe.db.get_value(
		ACHIEVEMENT, achievement, ["name", "learner_type", "visibility"], as_dict=True
	)
	if not target or target.learner_type != STAFF or target.visibility != "Team":
		# Same message for missing, private and wrong-audience. Telling them apart
		# tells somebody which achievements exist.
		frappe.throw(_("That is not something you can react to."))

	existing = frappe.db.exists(KUDOS, {"achievement": achievement, "from_user": user})
	doc = frappe.get_doc(KUDOS, existing) if existing else frappe.new_doc(KUDOS)
	doc.achievement = achievement
	doc.from_user = user
	doc.reaction = reaction
	doc.note = note
	doc.save(ignore_permissions=True)
	return {"kudos": doc.name, "reaction": doc.reaction}


def get_preferences(user):
	row = (
		frappe.db.get_value(
			PREFERENCE, {"user": user}, ["show_on_feed", "show_on_leaderboard"], as_dict=True
		)
		if frappe.db.exists("DocType", PREFERENCE)
		else None
	)
	# Absent means the default, and the default is on. An opt-in feed in a
	# sixteen-person company is an empty feed, and empty reads as broken.
	return {
		"show_on_feed": True if row is None else bool(row.show_on_feed),
		"show_on_leaderboard": True if row is None else bool(row.show_on_leaderboard),
	}


def set_preferences(user, show_on_feed=None, show_on_leaderboard=None):
	if not frappe.db.exists("DocType", PREFERENCE):
		frappe.throw(_("Preferences are not available on this site."))
	existing = frappe.db.exists(PREFERENCE, {"user": user})
	doc = frappe.get_doc(PREFERENCE, existing) if existing else frappe.new_doc(PREFERENCE)
	doc.user = user
	if show_on_feed is not None:
		doc.show_on_feed = cint(show_on_feed)
	if show_on_leaderboard is not None:
		doc.show_on_leaderboard = cint(show_on_leaderboard)
	doc.save(ignore_permissions=True)
	# Existing rows have to follow, or an opt-out only applies to the future and
	# everything already posted stays up — which is not what anybody means by it.
	resweep_visibility(user)
	return get_preferences(user)


def _full_names(users):
	users = [u for u in users if u]
	if not users:
		return {}
	return {
		row.name: row.full_name or row.name
		for row in frappe.get_all(
			"User", filters={"name": ["in", users]}, fields=["name", "full_name"]
		)
	}
