# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Dev-only preview harness for the learner training player at ``/training_preview``.

Boots the *real* ``TR.Player`` — the same four scripts and ``player.css`` the live
``/training`` page loads — against a **canned in-memory transport** defined in the
template: no bench data, no server round trips, no progress written. It is the
design workbench and screenshot-diff target for the states that only appear after
a server reply and are otherwise a nuisance to reach — the completion celebration,
the quiz review, the empty and dormant catalogs, every block type in one lesson.

It exists to make the ``TR.core`` refactor (and every later redesign step) checkable:
render all states, before and after, and compare.

Two things carry over from ``training.py`` and are load-bearing:

* **The filename must stay ``training_preview.py``, underscored.** Frappe imports a
  web page's controller by hyphen-to-underscore-ing the *template* basename, so a
  hyphenated controller is never imported and ``get_context`` silently never runs
  (``scripts/check_www_controllers.py`` guards it).
* **No ``frappe.*`` in the browser.** The player runs for Website Users with
  ``desk_access = 0``; the canned transport is plain objects and the player never
  learns it is not talking to the server.

Not a learner surface and never linked from one: restricted to the authoring and
admin roles, plus anyone on a developer-mode site. A plain learner gets a 404 —
reported as missing rather than forbidden, because a 403 would confirm the route.
"""

import frappe
from frappe.utils import cint, flt

from erpnext_enhancements.utils.deploy import get_deploy_version

no_cache = 1

# Who may open the workbench. The authoring/admin roles, never a plain learner.
ALLOWED_ROLES = {"System Manager", "Training Manager", "Training Author"}


def get_context(context):
	"""Route: ``/training_preview`` (rendered by ``training_preview.html``)."""
	if frappe.session.user == "Guest":
		frappe.local.flags.redirect_location = "/login?redirect-to=/training_preview"
		raise frappe.Redirect

	if not (frappe.conf.get("developer_mode") or (ALLOWED_ROLES & set(frappe.get_roles()))):
		# Reported as missing, not forbidden — a 403 would confirm the route exists.
		raise frappe.DoesNotExistError

	context.no_cache = 1
	context.deploy_version = get_deploy_version()
	context.draft_json = _draft_payload()
	return context


def _draft_payload():
	"""The SECOND mode: a real draft lesson, rendered by the real player.

	With no request args this returns None and the page is exactly what it has always
	been -- the canned design workbench. With ``?course=…`` it resolves that course's
	open draft and returns the payload **publish would write**, so an author previews
	the bytes a learner will get rather than a parallel reconstruction of them.

	That distinction is the whole point, and it is why every field below is copied
	from a real producer rather than approximated. The classic builder rebuilt the
	learner payload in JavaScript -- roughly 640 lines -- and the two drifted: a shim
	made a broken runtime look fine to the author and broken to every learner. So the
	lesson payloads come from ``training_author._split_lesson``, which its own
	docstring names as the single place a learner-facing payload may be built; the
	table of contents is assembled exactly as ``_materialize_lessons`` assembles it at
	publish; and the envelope around them mirrors ``api.training.get_course`` key for
	key. An approximation here would be a preview of something that does not exist.

	Gated HARDER than the workbench around it, deliberately. The page-level check
	admits anyone on a developer-mode site; draft mode does not inherit that, because
	developer mode is a deployment setting and not a permission, and this returns the
	answer key. Draft mode requires an authoring role AND write permission on the
	specific course -- the same gate ``get_builder_bootstrap`` uses, which is what
	makes it safe to hand an author content a learner must never see.

	Returns None on anything it cannot serve. A preview that silently falls back to
	the canned lesson is a preview of the wrong thing, so the template says which mode
	it is in rather than leaving the author to infer it from the content.
	"""
	course = (frappe.form_dict.get("course") or "").strip()
	if not course:
		return None

	from erpnext_enhancements.api.training_author import _split_lesson

	if not (ALLOWED_ROLES & set(frappe.get_roles())):
		return None
	if not frappe.db.exists("Training Course", course):
		return None
	if not frappe.has_permission("Training Course", "write", doc=course):
		return None

	draft = frappe.db.exists("Training Course Version", {"course": course, "docstatus": 0})
	if not draft:
		return None

	version = frappe.get_doc("Training Course Version", draft)
	doc = frappe.get_doc("Training Course", course)

	# The reading order every other caller uses. NOT `idx` -- Training Lesson is not a
	# child table, so `idx` is 0 on every row and ordering by it is ordering by nothing.
	names = frappe.get_all(
		"Training Lesson",
		filters={"course_version": version.name},
		pluck="name",
		order_by="chapter_key asc, idx_in_chapter asc, creation asc",
	)

	lessons, keys, toc, minutes = [], {}, [], 0
	for name in names:
		lesson = frappe.get_doc("Training Lesson", name)
		public, key = _split_lesson(lesson)
		lessons.append(public)
		keys[public["lesson_key"]] = key
		minutes += cint(lesson.estimated_minutes)
		# Row-for-row what `_materialize_lessons` writes into `toc_json`, minus the
		# `lesson` docname -- which `_public_toc` strips before a learner sees it, and
		# which an author has no more use for here than a learner does.
		toc.append(
			{
				"lesson_key": lesson.lesson_key,
				"chapter_key": lesson.chapter_key or "",
				"title": lesson.lesson_title,
				"minutes": cint(lesson.estimated_minutes),
				"has_quiz": cint(lesson.has_quiz),
				"blocks": len(lesson.blocks or []),
			}
		)

	return frappe.as_json(
		{
			"course": {
				"course": doc.name,
				"title": doc.course_title,
				"slug": doc.slug or "",
				"summary": doc.summary or "",
				"cover_image": doc.cover_image or "",
				"weight": doc.weight,
				"minutes": cint(doc.estimated_minutes),
				"self_enrol": bool(cint(doc.allow_self_enrollment)),
			},
			# Read off the course, never hardcoded. The gates are what the player grades
			# and paces against, so a preview holding different ones is a preview of a
			# different course -- and these are exactly the settings an author is most
			# likely to be checking.
			"gates": {
				"passing_score": flt(doc.passing_score),
				"min_video_coverage": flt(doc.min_video_coverage),
				"require_checkpoints_answered": bool(cint(doc.require_checkpoints_answered)),
				"max_attempts": cint(doc.max_attempts),
			},
			"version": {
				"course_version": version.name,
				"version_number": cint(version.version_number),
				# Counted here rather than read off the version, because
				# `total_lessons` and `estimated_minutes` are computed at publish and a
				# draft's copies are stale by definition. The player's course counter
				# reads `version.lessons`.
				"lessons": len(lessons),
				"minutes": minutes,
				"release_notes": version.release_notes or "",
			},
			"chapters": frappe.get_all(
				"Training Chapter",
				filters={"parent": version.name, "parenttype": "Training Course Version"},
				fields=["chapter_key", "chapter_title", "description"],
				order_by="idx asc",
			),
			"toc": toc,
			"lessons": lessons,
			# The answer key rides along because an AUTHOR is entitled to it --
			# `get_builder_bootstrap` already serves `is_correct` to exactly these
			# people -- and without it the preview cannot grade, which is what the
			# classic builder's own note means by "test this checkpoint tests nothing".
			# It is the reason the gate above is stricter than the page's.
			"keys": keys,
		}
	)
