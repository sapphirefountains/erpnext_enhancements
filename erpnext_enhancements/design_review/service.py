"""Design Review: everything the Review Room reads and writes (WI-079 slice 5, ADR 0016 §2).

``api/design_review.py`` is a thin whitelisted layer over these functions; the checks live
here so there is one copy of each.

The checks, in the order every write runs them:

1. **A signed-in System User.** The Desk already refuses Guests and Website Users; the
   endpoint asks again because a whitelisted method is reachable by whatever the framework
   considers authenticated.
2. **A participant.** Votes, verdicts and notes are written only for a session user on the
   review's participant list. No role holds create or write on those doctypes, so the REST API
   cannot go around this. A System Manager who is not a participant moderates but does not vote.
3. **An Open review.** Draft is being set up, Closed is being decided, Decided is history.
4. **The session user, always.** Every vote, verdict and note is stamped with
   ``frappe.session.user`` and never with a value from the client. There is no proxy voting.
   A note may name an Employee who *raised* it in the meeting — that is a fact about the
   meeting, not an author.

Promotion is a human System Manager's (``design_review.authority``): ``triton@``, ``mdm@``
and ``Administrator`` hold the role without being a person deciding anything.

Tabs, per ``CLAUDE.md``.
"""

from __future__ import annotations

import json

import frappe
from frappe import _
from frappe.utils import cint, now_datetime

from erpnext_enhancements.design_review import authority, codes, permissions, tally

REVIEW = "Design Review"
OPEN = "Open"
NOTE_STATUSES = ("Open", "Accepted", "Rejected", "Done")
MAX_NOTE_CHARS = 2000
MAX_SCREENS_PER_CALL = 60

#: Written by the importer and nothing else. ``validate_review`` refuses an edit to them
#: from anywhere that has not set ``flags.design_review_import``.
CONTENT_FIELDS = (
	"stylesheet",
	"flow_rules",
	"revision",
	"source_url",
	"imported_at",
	"imported_by",
	"import_report",
)


# ---------------------------------------------------------------- the review document


def validate_review(doc) -> None:
	"""``Design Review.validate``: participants are distinct System Users, content is import-only."""
	from frappe.permissions import is_system_user

	seen = set()
	for row in doc.get("participants") or []:
		if not row.user:
			continue
		if row.user in seen:
			frappe.throw(_("{0} is on the participant list twice.").format(row.user), frappe.ValidationError)
		seen.add(row.user)
		if not is_system_user(row.user):
			frappe.throw(
				_(
					"{0} is not a System User. Only people who sign in to the Desk can take part in a design review."
				).format(row.user),
				frappe.ValidationError,
			)
	if doc.is_new() or doc.flags.get("design_review_import"):
		return
	before = doc.get_doc_before_save()
	if before is None:
		return
	changed = [f for f in CONTENT_FIELDS if (doc.get(f) or "") != (before.get(f) or "")]
	if _rows(doc.get("tracks")) != _rows(before.get("tracks")):
		changed.append("tracks")
	if changed:
		frappe.throw(
			_(
				"A review's concepts change only by importing a new revision, so the sanitized copy "
				"is the only copy ({0})."
			).format(", ".join(changed)),
			frappe.ValidationError,
		)


def _rows(rows):
	return [(r.track_id, r.label, r.short, cint(r.votable), r.blurb, r.groups_json) for r in rows or []]


# ---------------------------------------------------------------- gates


def require_system_user() -> str:
	user = frappe.session.user
	if user in ("", None, "Guest"):
		frappe.throw(_("Sign in to use design reviews."), frappe.PermissionError)
	from frappe.permissions import is_system_user

	if not is_system_user(user):
		frappe.throw(
			_("Design reviews are for staff accounts that sign in to the Desk."), frappe.PermissionError
		)
	return user


def require_reader(review: str) -> str:
	user = require_system_user()
	if not frappe.db.exists(REVIEW, review) or not permissions.can_read_review(review, user):
		# One message for "missing" and "not yours", so a name cannot be probed.
		frappe.throw(
			_("Design review {0} was not found, or you are not on it.").format(review),
			frappe.DoesNotExistError,
		)
	return user


def require_participant(review: str, *, while_open: bool = True) -> str:
	user = require_reader(review)
	if not permissions.is_participant(review, user):
		frappe.throw(
			_("Only the people on this review's participant list can vote, give verdicts or write notes."),
			frappe.PermissionError,
		)
	if while_open and frappe.db.get_value(REVIEW, review, "status") != OPEN:
		frappe.throw(
			_("This review is not open, so votes, verdicts and notes are closed."), frappe.ValidationError
		)
	return user


def require_moderator() -> str:
	user = require_system_user()
	if "System Manager" not in frappe.get_roles(user):
		frappe.throw(_("Only a System Manager can do that."), frappe.PermissionError)
	return user


def require_human_moderator() -> str:
	user = require_system_user()
	if not authority.is_human_system_manager(user, frappe.get_roles(user)):
		frappe.throw(
			_("Only a person with the System Manager role can promote a decision. Service accounts cannot."),
			frappe.PermissionError,
		)
	return user


# ---------------------------------------------------------------- reads


def list_reviews() -> list[dict]:
	user = require_system_user()
	filters = (
		{}
		if "System Manager" in frappe.get_roles(user)
		else {"name": ["in", permissions.reviews_for(user) or [""]]}
	)
	rows = frappe.get_all(
		REVIEW,
		filters=filters,
		fields=["name", "title", "status", "description", "revision", "modified"],
		order_by="modified desc",
		limit_page_length=200,
	)
	mine = set(permissions.reviews_for(user))
	for row in rows:
		row["participant"] = row["name"] in mine
	return rows


def get_review(review: str) -> dict:
	"""Everything the Review Room draws, except screen markup, which is fetched per option."""
	user = require_reader(review)
	doc = frappe.get_doc(REVIEW, review)
	moderator = "System Manager" in frappe.get_roles(user)
	participant = permissions.is_participant(review, user)

	tracks = [
		{
			"id": t.track_id,
			"label": t.label,
			"short": t.short,
			"votable": cint(t.votable),
			"blurb": t.blurb,
			"groups": _json(t.groups_json, []),
		}
		for t in doc.tracks
	]
	options = frappe.get_all(
		"Design Option",
		filters={"review": review},
		fields=["option_code", "option_name", "track", "what", "tradeoff", "sort_order"],
		order_by="sort_order asc",
		limit_page_length=0,
	)
	screens = frappe.get_all(
		"Design Screen",
		filters={"review": review},
		fields=[
			"name",
			"track",
			"option_code",
			"screen_code",
			"screen_name",
			"group_name",
			"frame",
			"width",
			"height",
			"sort_order",
			"content_hash",
		],
		order_by="sort_order asc",
		limit_page_length=0,
	)
	parts: dict[str, list] = {}
	for row in frappe.get_all(
		"Design Part",
		filters={"review": review},
		fields=["track", "screen_code", "number", "part_name"],
		order_by="number asc",
		limit_page_length=0,
	):
		parts.setdefault(f"{row.track}:{row.screen_code}", []).append([row.number, row.part_name])

	return {
		"name": doc.name,
		"title": doc.title,
		"status": doc.status,
		"description": doc.description,
		"revision": doc.revision,
		"source_url": doc.source_url,
		"stylesheet": doc.stylesheet or "",
		"flow": _json(doc.flow_rules, {}),
		"tracks": tracks,
		"options": options,
		"screens": screens,
		"parts": parts,
		"me": {
			"user": user,
			"participant": participant,
			"moderator": moderator,
			"promoter": authority.is_human_system_manager(user, frappe.get_roles(user)),
		},
		"participants": [{"user": p.user, "full_name": p.full_name or p.user} for p in doc.participants],
		**_activity(review, user),
	}


def _activity(review: str, user: str) -> dict:
	"""Votes, verdicts, notes and decisions, with live and imported kept apart."""
	options_by_track: dict[str, list[str]] = {}
	for row in frappe.get_all(
		"Design Option",
		filters={"review": review},
		fields=["track", "option_code"],
		order_by="sort_order asc",
		limit_page_length=0,
	):
		options_by_track.setdefault(row.track, []).append(row.option_code)

	votes = frappe.get_all(
		"Design Vote",
		filters={"review": review},
		fields=["track", "voter", "ranking", "cast_at", "is_imported", "imported_by_name"],
		limit_page_length=0,
	)
	tallies = {}
	my_votes = {}
	for track, options in options_by_track.items():
		live = [v for v in votes if v.track == track and not v.is_imported]
		imported = [v for v in votes if v.track == track and v.is_imported]
		tallies[track] = {
			"live": tally.borda([_json(v.ranking, []) for v in live], options),
			"imported": tally.borda([_json(v.ranking, []) for v in imported], options),
			"live_first": _first_choices(live),
			"imported_first": _first_choices(imported, imported=True),
		}
		mine = [v for v in live if v.voter == user]
		if mine:
			my_votes[track] = {"ranking": _json(mine[0].ranking, []), "cast_at": str(mine[0].cast_at or "")}

	verdicts = frappe.get_all(
		"Design Verdict",
		filters={"review": review},
		fields=["option_code", "screen_code", "voter", "verdict", "is_imported"],
		limit_page_length=0,
	)
	notes = frappe.get_all(
		"Design Note",
		filters={"review": review},
		fields=[
			"name",
			"code",
			"track",
			"option_code",
			"screen_code",
			"part_number",
			"part_name",
			"text",
			"status",
			"author",
			"raised_by",
			"creation",
			"promoted_request",
			"is_imported",
			"imported_by_name",
		],
		order_by="creation asc",
		limit_page_length=0,
	)
	people = _names({n.author for n in notes if n.author} | {v.voter for v in votes if v.voter})
	employees = _employee_names({n.raised_by for n in notes if n.raised_by})
	for n in notes:
		n["author_name"] = n.imported_by_name if n.is_imported else people.get(n.author, n.author)
		n["raised_by_name"] = employees.get(n.raised_by, "")
		n["mine"] = (n.author == user) and not n.is_imported
	decisions = frappe.get_all(
		"Design Decision",
		filters={"review": review},
		fields=[
			"name",
			"track",
			"option_code",
			"title",
			"decision",
			"status",
			"promoted_request",
			"notes_json",
			"creation",
		],
		order_by="creation asc",
		limit_page_length=0,
	)
	for d in decisions:
		d["notes"] = _json(d.pop("notes_json"), [])
	return {
		"tallies": tallies,
		"my_votes": my_votes,
		"verdicts": {
			"live": tally.verdict_counts([v for v in verdicts if not v.is_imported]),
			"imported": tally.verdict_counts([v for v in verdicts if v.is_imported]),
			"mine": {
				f"{v.option_code}:{v.screen_code}": v.verdict
				for v in verdicts
				if v.voter == user and not v.is_imported
			},
		},
		"notes": notes,
		"decisions": decisions,
		"voter_names": people,
	}


def _first_choices(rows, imported=False):
	"""``{"L3": ["Ana", "Ben"]}``: who put each option first. Names, because these ballots are named."""
	people = {} if imported else _names({r.voter for r in rows if r.voter})
	out: dict[str, list[str]] = {}
	for r in rows:
		ranking = _json(r.ranking, [])
		if ranking:
			who = r.imported_by_name if imported else people.get(r.voter, r.voter)
			out.setdefault(ranking[0], []).append(who)
	return out


def get_screens(review: str, names: list[str]) -> dict[str, str]:
	"""Sanitized markup for up to ``MAX_SCREENS_PER_CALL`` screens of one review."""
	require_reader(review)
	names = [n for n in (names or [])[:MAX_SCREENS_PER_CALL] if isinstance(n, str)]
	if not names:
		return {}
	rows = frappe.get_all(
		"Design Screen",
		filters={"review": review, "name": ["in", names]},
		fields=["name", "html"],
		limit_page_length=0,
	)
	return {r.name: r.html or "" for r in rows}


# ---------------------------------------------------------------- participant writes


def cast_vote(review: str, track: str, ranking) -> dict:
	user = require_participant(review)
	if not _votable(review, track):
		frappe.throw(_("Track {0} is not ranked in this review.").format(track), frappe.ValidationError)
	options = frappe.get_all(
		"Design Option",
		filters={"review": review, "track": track},
		pluck="option_code",
		order_by="sort_order asc",
		limit_page_length=0,
	)
	try:
		ranking = tally.clean_ranking(
			_json(ranking, ranking) if isinstance(ranking, str) else ranking, options
		)
	except ValueError as exc:
		frappe.throw(_(str(exc)), frappe.ValidationError)
	existing = frappe.get_all(
		"Design Vote",
		filters={"review": review, "track": track, "voter": user, "is_imported": 0},
		pluck="name",
	)
	doc = frappe.get_doc("Design Vote", existing[0]) if existing else frappe.new_doc("Design Vote")
	doc.update(
		{
			"review": review,
			"track": track,
			"voter": user,
			"ranking": json.dumps(ranking),
			"cast_at": now_datetime(),
			"is_imported": 0,
		}
	)
	_write(doc)
	for extra in existing[1:]:
		frappe.delete_doc("Design Vote", extra, ignore_permissions=True)
	return {"track": track, "ranking": ranking}


def cast_verdict(review: str, option_code: str, screen_code: str, verdict: str | None) -> dict:
	user = require_participant(review)
	screen = frappe.get_all(
		"Design Screen",
		filters={"review": review, "option_code": option_code, "screen_code": screen_code},
		fields=["track"],
		limit=1,
	)
	if not screen:
		frappe.throw(_("That screen is not part of this review."), frappe.ValidationError)
	existing = frappe.get_all(
		"Design Verdict",
		filters={
			"review": review,
			"option_code": option_code,
			"screen_code": screen_code,
			"voter": user,
			"is_imported": 0,
		},
		pluck="name",
	)
	if not verdict:
		for name in existing:
			frappe.delete_doc("Design Verdict", name, ignore_permissions=True)
		return {"verdict": None}
	if verdict not in tally.VERDICTS:
		frappe.throw(_("A verdict is Yes, Maybe or No."), frappe.ValidationError)
	doc = frappe.get_doc("Design Verdict", existing[0]) if existing else frappe.new_doc("Design Verdict")
	doc.update(
		{
			"review": review,
			"track": screen[0].track,
			"option_code": option_code,
			"screen_code": screen_code,
			"voter": user,
			"verdict": verdict,
			"is_imported": 0,
		}
	)
	_write(doc)
	return {"verdict": verdict}


def add_note(review: str, code: str, text: str, raised_by: str | None = None) -> dict:
	user = require_participant(review)
	text = (text or "").strip()
	if len(text) < 3:
		frappe.throw(_("Write the note first."), frappe.ValidationError)
	if len(text) > MAX_NOTE_CHARS:
		frappe.throw(_("Keep a note under {0} characters.").format(MAX_NOTE_CHARS), frappe.ValidationError)
	parsed = codes.parse_code(code)
	if not parsed:
		frappe.throw(_("{0} is not an element code like L3-S04-E05.").format(code), frappe.ValidationError)
	option_code, screen_code, number = parsed
	track = _track_of(review, option_code, screen_code)
	part_name = frappe.db.get_value(
		"Design Part",
		{"review": review, "track": track, "screen_code": screen_code, "number": number},
		"part_name",
	)
	if not part_name:
		frappe.throw(_("{0} is not a part of this review.").format(code), frappe.ValidationError)
	if raised_by and not frappe.db.exists("Employee", raised_by):
		frappe.throw(
			_("Pick the person who raised it from the employee list, or leave it empty."),
			frappe.ValidationError,
		)
	doc = frappe.new_doc("Design Note")
	doc.update(
		{
			"review": review,
			"track": track,
			"code": codes.format_code(option_code, screen_code, number),
			"option_code": option_code,
			"screen_code": screen_code,
			"part_number": number,
			"part_name": part_name,
			"text": text,
			"status": "Open",
			"author": user,
			"raised_by": raised_by or None,
			"is_imported": 0,
		}
	)
	doc.insert(ignore_permissions=True)
	return {"name": doc.name}


def delete_note(note: str) -> dict:
	row = frappe.db.get_value(
		"Design Note", note, ["review", "author", "is_imported", "status", "promoted_request"], as_dict=True
	)
	if not row:
		frappe.throw(_("That note was not found."), frappe.DoesNotExistError)
	user = require_participant(row.review)
	if row.author != user or row.is_imported:
		frappe.throw(_("You can delete only notes you wrote."), frappe.PermissionError)
	if row.status != "Open" or row.promoted_request:
		frappe.throw(
			_("A note that has been decided on stays, so the decision still has its reason."),
			frappe.ValidationError,
		)
	frappe.delete_doc("Design Note", note, ignore_permissions=True)
	return {"deleted": note}


# ---------------------------------------------------------------- moderator writes


def set_note_status(note: str, status: str) -> dict:
	user = require_moderator()
	review = frappe.db.get_value("Design Note", note, "review")
	if not review:
		frappe.throw(_("That note was not found."), frappe.DoesNotExistError)
	if status not in NOTE_STATUSES:
		frappe.throw(_("A note's status is Open, Accepted, Rejected or Done."), frappe.ValidationError)
	doc = frappe.get_doc("Design Note", note)
	doc.status = status
	doc.decided_by = user
	doc.save(ignore_permissions=True)
	return {"name": note, "status": status}


def record_decision(
	review: str,
	title: str,
	decision: str,
	track: str | None = None,
	option_code: str | None = None,
	notes=None,
) -> dict:
	user = require_moderator()
	require_reader(review)
	title = (title or "").strip()[:140]
	decision = (decision or "").strip()
	if not title or len(decision) < 20:
		frappe.throw(
			_("Give the decision a title and say what was decided in at least 20 characters."),
			frappe.ValidationError,
		)
	if option_code and not frappe.db.exists("Design Option", {"review": review, "option_code": option_code}):
		frappe.throw(_("{0} is not an option in this review.").format(option_code), frappe.ValidationError)
	note_names = _json(notes, []) if isinstance(notes, str) else list(notes or [])
	if note_names:
		found = set(
			frappe.get_all(
				"Design Note", filters={"review": review, "name": ["in", note_names]}, pluck="name"
			)
		)
		missing = [n for n in note_names if n not in found]
		if missing:
			frappe.throw(
				_("These notes are not in this review: {0}").format(", ".join(missing)),
				frappe.ValidationError,
			)
	doc = frappe.new_doc("Design Decision")
	doc.update(
		{
			"review": review,
			"track": track or None,
			"option_code": option_code or None,
			"title": title,
			"decision": decision,
			"status": "Recorded",
			"decided_by": user,
			"notes_json": json.dumps(note_names),
		}
	)
	doc.insert(ignore_permissions=True)
	return {"name": doc.name}


def promote_decision(
	decision: str, target_erpnext=0, target_triton=0, request_type="Feature", impact="Nice to have"
) -> dict:
	"""File the decision as an Enhancement Request, already ``Approved``, and queue its breakdown."""
	user = require_human_moderator()
	doc = frappe.get_doc("Design Decision", decision)
	if doc.status == "Promoted" or doc.promoted_request:
		frappe.throw(
			_("This decision was already promoted to {0}.").format(doc.promoted_request),
			frappe.ValidationError,
		)
	review = frappe.get_doc(REVIEW, doc.review)
	notes = _decision_notes(doc)
	lines = [doc.decision.strip(), "", f"From design review {review.title} ({review.name})."]
	if doc.option_code:
		option = frappe.db.get_value(
			"Design Option", {"review": review.name, "option_code": doc.option_code}, "option_name"
		)
		lines.append(f"Option chosen: {doc.option_code} {option or ''}".rstrip())
	if notes:
		lines += ["", "Notes carried forward, by element code:"]
		lines += [f"- {n['code']} ({n['part_name']}): {n['text']}" for n in notes]

	from erpnext_enhancements.api import feedback

	values = {
		"title": doc.title,
		"request_type": request_type,
		"impact": impact,
		"description": "\n".join(lines),
	}
	request = feedback.file_request(
		values,
		requested_by=user,
		source=feedback.SOURCE_DESIGN_REVIEW,
		source_doctype="Design Decision",
		source_ref=doc.name,
		approve=True,
		target_erpnext=target_erpnext,
		target_triton=target_triton,
		decision_reason=f"Promoted from design decision {doc.name}.",
	)
	doc.status = "Promoted"
	doc.promoted_request = request.name
	doc.promoted_by = user
	doc.promoted_at = now_datetime()
	doc.save(ignore_permissions=True)
	for n in notes:
		frappe.db.set_value("Design Note", n["name"], "promoted_request", request.name, update_modified=False)
	return {"decision": doc.name, "request": request.name}


def _decision_notes(decision_doc) -> list[dict]:
	names = _json(decision_doc.notes_json, [])
	if not names:
		return []
	rows = frappe.get_all(
		"Design Note",
		filters={"name": ["in", names]},
		fields=["name", "code", "part_name", "text", "status"],
		limit_page_length=0,
	)
	order = {n: i for i, n in enumerate(names)}
	return sorted([dict(r) for r in rows], key=lambda r: order.get(r["name"], 0))


def notes_for_request(request: dict) -> list[dict]:
	"""The design notes a promoted request carries, for the Claude Code brief.

	Element code, part and text only: no names of the people who wrote or raised them.
	``[]`` for a request that did not come from a decision, and never raises, because the brief
	must render whatever happens here.
	"""
	try:
		if (request or {}).get("source_doctype") != "Design Decision" or not request.get("source_ref"):
			return []
		decision = frappe.get_doc("Design Decision", request["source_ref"])
		return [
			{"code": n["code"], "part": n["part_name"], "text": n["text"], "status": n["status"]}
			for n in _decision_notes(decision)
		]
	except Exception:
		return []


# ---------------------------------------------------------------- helpers


def _write(doc) -> None:
	if doc.is_new():
		doc.insert(ignore_permissions=True)
	else:
		doc.save(ignore_permissions=True)


def _votable(review: str, track: str) -> bool:
	return bool(
		frappe.db.exists(
			"Design Review Track", {"parent": review, "parenttype": REVIEW, "track_id": track, "votable": 1}
		)
	)


def _track_of(review: str, option_code: str, screen_code: str) -> str:
	track = frappe.db.get_value(
		"Design Screen", {"review": review, "option_code": option_code, "screen_code": screen_code}, "track"
	)
	if not track:
		frappe.throw(
			_("{0}-{1} is not a screen in this review.").format(option_code, screen_code),
			frappe.ValidationError,
		)
	return track


def _names(users) -> dict[str, str]:
	users = [u for u in users if u]
	if not users:
		return {}
	rows = frappe.get_all(
		"User", filters={"name": ["in", users]}, fields=["name", "full_name"], limit_page_length=0
	)
	return {r.name: r.full_name or r.name for r in rows}


def _employee_names(employees) -> dict[str, str]:
	employees = [e for e in employees if e]
	if not employees:
		return {}
	rows = frappe.get_all(
		"Employee", filters={"name": ["in", employees]}, fields=["name", "employee_name"], limit_page_length=0
	)
	return {r.name: r.employee_name or r.name for r in rows}


def _json(value, default):
	if value in (None, ""):
		return default
	if isinstance(value, (list, dict)):
		return value
	try:
		return json.loads(value)
	except (TypeError, ValueError):
		return default
