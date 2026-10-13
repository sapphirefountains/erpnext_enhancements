# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Keep Google in step with ERPNext: reconcile one item, repair a window, snap back Google edits.

**One way, ERPNext is the truth** (Nik, 2026-10-12, settled). Nothing here ever writes a Google
change back into ERPNext. Every path ends in :func:`reconcile`, which compares what the item says
now with the ``Calendar Sync Link`` row of what was last written, and makes at most one or two
Google calls: none when the content hash matches.

Three ways work reaches Google, all safe to run twice:

* **Per item, on change** (``triggers.py``): a save, submit, cancel or delete enqueues
  :func:`run_item_job` after commit, once per record per request.
* **Nightly full repair** (:func:`nightly_repair`) and the Settings page's *Re-sync everything*
  (:func:`run_full_repair`): every item in the window (Settings, 14 days back to 180 ahead) plus
  every link in it, then the projected visits, whose stale events are removed. This is what makes the
  deploy's ``FLUSHDB`` harmless: a job it destroyed is redone by the next repair at the latest.
* **Snap-back** (:func:`snapback`, every 10 minutes): Google's incremental ``events.list`` per
  calendar with a stored ``syncToken``. An event with this site's private properties that somebody
  changed is rewritten, one deleted in Google is recreated, one whose source is gone is deleted. An
  event without our properties is somebody else's and is never touched. A 410 drops the token and
  lists in full.

**The master switch.** With Calendar Sync Settings ``enabled`` off (its default) or no Google
account authorized, every job returns before building a client, so there is no Google call at all.
:func:`plan` (the preview) works either way and never writes: neither Google nor the database.

Errors on one item are recorded on its link (``status`` Error, ``last_error``) and logged once when
an item starts failing, never per retry; the run moves on. A refused refresh token
(:class:`google_api.GoogleAuthError`) stops the run, because every other call would fail the same way.
"""

import json
import time

import frappe

from erpnext_enhancements.project_enhancements.calendar_sync import google_api, payload, sources

SETTINGS = "Calendar Sync Settings"
LINK = "Calendar Sync Link"
ROLES = ("projects", "maintenance", "holidays")
ROLE_FIELDS = {
	"projects": "projects_calendar_id",
	"maintenance": "maintenance_calendar_id",
	"holidays": "holidays_calendar_id",
}

REPAIR_SECONDS = 20 * 60
SNAP_PAGES = 40
SNAP_ACTIONS = 300
PREVIEW_MAX_DAYS = 400
ERRORS_SHOWN = 10


# ---------------------------------------------------------------------- settings


def settings():
	return frappe.get_cached_doc(SETTINGS)


def calendar_ids(doc=None):
	doc = doc or settings()
	return {role: (doc.get(field) or "").strip() for role, field in ROLE_FIELDS.items()}


def color_overrides(doc=None):
	doc = doc or settings()
	return {
		row.get("applies_to"): str(row.get("color_id") or "").strip()
		for row in (doc.get("colors") or [])
		if row.get("applies_to") and row.get("color_id")
	}


def is_on(doc=None):
	"""Switch on, an account authorized and Google Settings' OAuth client usable (without it no token
	can be refreshed, and every job would log the same failure). Never raises; False on any doubt."""
	try:
		doc = doc or settings()
		return (
			bool(int(doc.get("enabled") or 0))
			and bool(google_api.stored_refresh_token())
			and google_api.google_settings_ready()
		)
	except Exception:
		return False


def site():
	return getattr(frappe.local, "site", None) or ""


def write_state(**values):
	"""Write sync-state fields of the Single without touching ``modified`` (an open Settings form
	must not hit a timestamp mismatch) and only after every declared default has its row, or the
	first write would blank the rest (CLAUDE.md, Singles)."""
	from erpnext_enhancements.project_enhancements.doctype.calendar_sync_settings.calendar_sync_settings import (
		materialize_defaults,
	)

	materialize_defaults()
	frappe.db.set_single_value(SETTINGS, values, update_modified=False)
	frappe.clear_document_cache(SETTINGS, SETTINGS)


def repair_window(doc=None):
	doc = doc or settings()
	back = doc.get("days_back")
	ahead = doc.get("days_ahead")
	return sources.window(14 if back in (None, "") else back, 180 if ahead in (None, "") else ahead)


# ---------------------------------------------------------------------- links


def get_link(ref):
	name = frappe.db.get_value(LINK, {"source_ref": ref}, "name")
	return frappe.get_doc(LINK, name) if name else None


def _save_link(link, item, ref, **values):
	if link is None:
		link = frappe.new_doc(LINK)
		link.source_ref = ref
	link.source_doctype = item.get("doctype")
	link.source_name = item.get("name")
	link.source_key = item.get("key") or ""
	if item.get("kind") and item.get("state") != payload.STATE_GONE:
		link.kind = payload.link_kind(item)
	for field, value in values.items():
		link.set(field, value)
	link.flags.ignore_permissions = True
	try:
		link.save() if not link.is_new() else link.insert()
	except Exception as exc:
		# Two jobs for one item raced to insert the row: take the other one's and write over it.
		if link.is_new() and type(exc).__name__ in ("DuplicateEntryError", "UniqueValidationError"):
			existing = get_link(ref)
			if existing is None:
				raise
			for field, value in values.items():
				existing.set(field, value)
			existing.flags.ignore_permissions = True
			existing.save()
			return existing
		raise
	return link


def _now():
	from frappe.utils import now_datetime

	return now_datetime()


def _record_error(link, item, ref, exc):
	count = int((link.get("error_count") if link else 0) or 0) + 1
	text = exc.describe() if isinstance(exc, google_api.GoogleCalendarError) else type(exc).__name__
	try:
		_save_link(link, item, ref, status="Error", last_error=text, error_count=count)
	except Exception:
		pass
	if count == 1:
		# Once when an item starts failing, not per retry. Keyword arguments: v16 picks the title by
		# content otherwise. No traceback: it would carry the request.
		frappe.log_error(
			title="Calendar sync: an event could not be written",
			message=f"{ref}: {text}. It is retried by the next change, the snap-back check and the nightly repair.",
		)


# ---------------------------------------------------------------------- reconcile


def _upsert(client, calendar_id, event_id, body, existing):
	"""Write ``body`` under ``event_id``; returns ``(event, event_id)``.

	An item with a live link updates first (one call), falling back to insert when Google has no
	such event. A new item inserts first and on 409 (the id exists: a re-run, or an event deleted in
	Google, which Google keeps as ``cancelled``) updates, which also restores a deleted one. If Google
	refuses both (an id purged long ago), the next generation of the id is inserted.
	"""
	if existing:
		try:
			return client.update_event(calendar_id, event_id, body), event_id
		except google_api.GoogleCalendarError as exc:
			if exc.status not in (404, 410):
				raise
	try:
		return client.insert_event(calendar_id, {**body, "id": event_id}), event_id
	except google_api.GoogleCalendarError as exc:
		if exc.status != 409:
			raise
	try:
		return client.update_event(calendar_id, event_id, body), event_id
	except google_api.GoogleCalendarError as exc:
		if exc.status not in (404, 410):
			raise
	base = event_id.split("g")[0]  # "erp" + hex never contains a "g": anything after one is a generation
	for generation in range(1, 6):
		fresh = f"{base}g{generation}"
		if fresh == event_id:
			continue
		try:
			return client.insert_event(calendar_id, {**body, "id": fresh}), fresh
		except google_api.GoogleCalendarError as exc:
			if exc.status != 409:
				raise
	raise google_api.GoogleCalendarError(409, "duplicate", "every event id generation is taken")


def reconcile(item, doc=None, client=None, dry_run=False, force=False):
	"""Bring one item's event in line. Returns ``(action, title)``.

	``action`` is ``create``, ``update``, ``delete``, ``unchanged``, ``none`` (gone and never
	synced), ``no_calendar`` (its calendar ID is blank) or ``error``. ``dry_run`` decides the same
	way and writes nothing anywhere. ``force`` writes even when the hash matches: snap-back's
	rewrite of an event somebody edited in Google, which our hash cannot see.
	"""
	doc = doc or settings()
	ref = payload.source_ref(item.get("doctype"), item.get("name"), item.get("key"))
	link = get_link(ref)
	live = link is not None and link.get("status") != "Deleted" and link.get("event_id")
	if item.get("state") == payload.STATE_GONE:
		if not live:
			if link is not None and link.get("status") == "Error" and not dry_run:
				# It failed before any event existed, and now there is nothing to write.
				_save_link(link, item, ref, status="Deleted", last_error="", error_count=0)
			return "none", link.get("title") if link else ""
		if dry_run:
			return "delete", link.get("title")
		try:
			client.delete_event(link.calendar_id, link.event_id)
		except google_api.GoogleAuthError:
			raise
		except Exception as exc:
			_record_error(link, item, ref, exc)
			return "error", link.get("title")
		_save_link(link, item, ref, status="Deleted", last_synced=_now(), last_error="", error_count=0)
		return "delete", link.get("title")

	calendar_id = calendar_ids(doc).get(item.get("calendar"))
	body = payload.build_event(item, site(), color_overrides(doc))
	if not calendar_id:
		return "no_calendar", body["summary"]
	digest = payload.content_hash(body, calendar_id)
	if (
		not force
		and live
		and link.get("status") == "Synced"
		and link.content_hash == digest
		and link.calendar_id == calendar_id
	):
		return "unchanged", body["summary"]
	action = "update" if live else "create"
	if dry_run:
		return action, body["summary"]
	event_id = (link.get("event_id") if link else None) or payload.event_id(
		site(), item.get("doctype"), item.get("name"), item.get("key")
	)
	try:
		moved = live and link.calendar_id and link.calendar_id != calendar_id
		if moved:
			client.delete_event(link.calendar_id, link.event_id)
		event, event_id = _upsert(client, calendar_id, event_id, body, live and not moved)
	except google_api.GoogleAuthError:
		raise
	except Exception as exc:
		_record_error(link, item, ref, exc)
		return "error", body["summary"]
	_save_link(
		link,
		item,
		ref,
		title=body["summary"],
		status="Synced",
		calendar_id=calendar_id,
		event_id=event_id,
		event_date=item.get("start"),
		content_hash=digest,
		etag=(event or {}).get("etag") or "",
		last_synced=_now(),
		last_error="",
		error_count=0,
	)
	return action, body["summary"]


# ---------------------------------------------------------------------- jobs


def _log_failure(title):
	try:
		frappe.log_error(title=title, message=frappe.get_traceback())
	except Exception:
		pass


def _auth_failed(exc):
	try:
		google_api.forget_access_token()
		if not frappe.cache.get_value("ee_calendar_sync_auth_logged"):
			frappe.cache.set_value("ee_calendar_sync_auth_logged", 1, expires_in_sec=3600)
			frappe.log_error(
				title="Calendar sync: Google refused the sign-in",
				message=f"{exc.describe()}. Open Calendar Sync Settings and click Authorize with Google again.",
			)
	except Exception:
		pass


def sync_ref(doctype, name, key=None, doc=None, client=None):
	"""Reconcile one source record. Returns the action. Never raises except on a refused sign-in."""
	doc = doc or settings()
	try:
		item = sources.item_for(doctype, name, key)
	except Exception:
		_log_failure("Calendar sync: could not read a record")
		return "error"
	try:
		action, _title = reconcile(item, doc, client or google_api.Client())
	except google_api.GoogleAuthError:
		raise
	except Exception:
		_log_failure("Calendar sync: could not reconcile a record")
		return "error"
	return action


def run_item_job(doctype, name, key=None):
	"""Queued after commit by ``triggers``: one record. Safe to run twice."""
	doc = settings()
	if not is_on(doc):
		return
	try:
		sync_ref(doctype, name, key, doc)
	except google_api.GoogleAuthError as exc:
		_auth_failed(exc)
	except Exception:
		_log_failure("Calendar sync: item job failed")


def run_project_job(project):
	"""A project's name, status, type or address changed: every task and visit of it that is in the
	window or already has an event. Hash skips make the unchanged ones free."""
	doc = settings()
	if not is_on(doc):
		return
	start, end = repair_window(doc)
	refs = [
		(sources.TASK, n)
		for n in frappe.get_all(
			sources.TASK, filters={"project": project}, pluck="name", limit_page_length=500
		)
	]
	refs += [
		(sources.VISIT, n)
		for n in frappe.get_all(
			sources.VISIT, filters={"project": project}, pluck="name", limit_page_length=500
		)
	]
	linked = set(
		frappe.get_all(
			LINK,
			filters={"source_name": ["in", [n for _d, n in refs] or [""]], "status": ["!=", "Deleted"]},
			pluck="source_name",
			limit_page_length=0,
		)
	)
	in_window = set(sources.refs_in_window(start, end))
	client = google_api.Client()
	try:
		for ref in refs:
			if ref in in_window or ref[1] in linked:
				sync_ref(ref[0], ref[1], doc=doc, client=client)
				frappe.db.commit()
	except google_api.GoogleAuthError as exc:
		_auth_failed(exc)


def sync_projections(doc=None, client=None, dry_run=False, start=None, end=None):
	"""Every projected visit in the window, and the removal of projected events that are no longer
	projected (drafted, moved, paused, ended). Returns ``[(action, title, date)]``."""
	doc = doc or settings()
	if start is None or end is None:
		start, end = repair_window(doc)
	results = []
	items = sources.projected_items(start, end)
	wanted = set()
	for item in items:
		wanted.add(payload.source_ref(item["doctype"], item["name"], item["key"]))
		action, title = reconcile(item, doc, client, dry_run=dry_run)
		results.append((action, title, str(item.get("start")), "Projected Visit"))
		if not dry_run:
			frappe.db.commit()
	for row in frappe.get_all(
		LINK,
		filters={
			"kind": "Projected Visit",
			"status": ["!=", "Deleted"],
			"event_date": ["between", [start, end]],
		},
		fields=["name", "source_ref", "source_name", "source_key", "title", "event_date"],
		limit_page_length=0,
	):
		if row.get("source_ref") in wanted:
			continue
		stale = sources.gone(
			sources.CONTRACT, row.get("source_name"), row.get("source_key"), payload.KIND_PROJECTED
		)
		action, title = reconcile(stale, doc, client, dry_run=dry_run)
		results.append(
			(action, title or row.get("title"), str(row.get("event_date") or ""), "Projected Visit")
		)
		if not dry_run:
			frappe.db.commit()
	return results


def run_projections_job():
	doc = settings()
	if not is_on(doc):
		return
	try:
		sync_projections(doc, google_api.Client())
	except google_api.GoogleAuthError as exc:
		_auth_failed(exc)
	except Exception:
		_log_failure("Calendar sync: projected visits failed")


def _window_refs(start, end):
	"""Every record in the window, plus every live link in it (a record moved or canceled out of
	the window still has an event there to fix). Projected visits are handled separately."""
	refs = list(dict.fromkeys(sources.refs_in_window(start, end)))
	seen = set(refs)
	for row in frappe.get_all(
		LINK,
		filters={
			"status": ["!=", "Deleted"],
			"event_date": ["between", [start, end]],
			"kind": ["!=", "Projected Visit"],
		},
		fields=["source_doctype", "source_name"],
		limit_page_length=0,
	):
		ref = (row.get("source_doctype"), row.get("source_name"))
		if ref[0] and ref[1] and ref not in seen:
			seen.add(ref)
			refs.append(ref)
	return refs


def full_repair(doc=None, budget=REPAIR_SECONDS):
	"""Reconcile the whole window. Returns the counts by action."""
	doc = doc or settings()
	start, end = repair_window(doc)
	client = google_api.Client()
	counts = {}
	began = time.monotonic()
	finished = True
	for doctype, name in _window_refs(start, end):
		if time.monotonic() - began > budget:
			finished = False
			break
		action = sync_ref(doctype, name, doc=doc, client=client)
		counts[action] = counts.get(action, 0) + 1
		frappe.db.commit()
	if finished:
		for action, _title, _day, _kind in sync_projections(doc, client):
			counts[action] = counts.get(action, 0) + 1
	summary = ", ".join(f"{n} {a}" for a, n in sorted(counts.items())) or "nothing in the window"
	if not finished:
		summary += " (stopped at the time limit; the rest waits for the next run)"
	write_state(last_full_repair=_now(), last_full_repair_summary=f"{start} to {end}: {summary}")
	return counts


def run_full_repair():
	"""*Re-sync everything* and the nightly run. A no-op while the switch is off."""
	doc = settings()
	if not is_on(doc):
		return None
	try:
		return full_repair(doc)
	except google_api.GoogleAuthError as exc:
		_auth_failed(exc)
	except Exception:
		_log_failure("Calendar sync: full repair failed")
	return None


def nightly_repair():
	"""``scheduler_events.daily_long``: the safety net under every trigger (see the module docstring)."""
	run_full_repair()


# ---------------------------------------------------------------------- snap-back


def _expected_id(item, link):
	if link is not None and link.get("event_id"):
		return link.get("event_id")
	return payload.event_id(site(), item.get("doctype"), item.get("name"), item.get("key"))


def handle_event(event, calendar_id, doc, client, cache):
	"""Apply snap-back to one listed event. Returns the decision."""
	if not payload.is_ours(event, site()):
		return payload.IGNORE
	props = payload.event_props(event)
	doctype, name, key = (
		props.get(payload.PROP_DOCTYPE),
		props.get(payload.PROP_NAME),
		props.get(payload.PROP_KEY),
	)
	if doctype not in sources.SOURCE_DOCTYPES or not name:
		return payload.IGNORE
	ref = payload.source_ref(doctype, name, key)
	if ref not in cache:
		cache[ref] = sources.item_for(doctype, name, key)
	item = cache[ref]
	link = get_link(ref)
	ids = calendar_ids(doc)
	item_calendar = ids.get(item.get("calendar")) if item.get("state") != payload.STATE_GONE else None
	etag = link.get("etag") if link is not None and link.get("calendar_id") == calendar_id else None
	decision = payload.snapback_action(
		event, site(), item.get("state"), item_calendar, calendar_id, etag, _expected_id(item, link)
	)
	if decision == payload.IGNORE:
		return decision
	if decision == payload.DELETE:
		client.delete_event(calendar_id, event.get("id"))
		if (
			link is not None
			and link.get("event_id") == event.get("id")
			and link.get("calendar_id") == calendar_id
		):
			_save_link(link, item, ref, status="Deleted", last_synced=_now())
	elif decision == payload.FORGET:
		if link is not None and link.get("event_id") == event.get("id") and link.get("status") != "Deleted":
			_save_link(link, item, ref, status="Deleted", last_synced=_now())
	else:
		# Rewrite or recreate: forced, because the hash describes what WE sent, which has not changed.
		reconcile(item, doc, client, force=True)
	frappe.db.commit()
	return decision


def _snap_calendar(client, calendar_id, token, doc, cache, budget):
	"""List one calendar from ``token`` (or in full) and apply snap-back. Returns the next token, or
	None when the run stopped before Google gave one (the old token is kept and the rest is redone)."""
	page = None
	for _ in range(SNAP_PAGES):
		answer = client.list_events(calendar_id, sync_token=token, page_token=page)
		for event in answer.get("items") or []:
			if budget["left"] <= 0:
				return None
			try:
				decision = handle_event(event, calendar_id, doc, client, cache)
			except google_api.GoogleAuthError:
				raise
			except Exception:
				_log_failure("Calendar sync: snap-back could not fix an event")
				continue
			if decision != payload.IGNORE:
				budget["left"] -= 1
		page = answer.get("nextPageToken")
		if not page:
			return answer.get("nextSyncToken")
	return None


def snapback():
	"""``scheduler_events`` every 10 minutes. A no-op while the switch is off."""
	doc = settings()
	if not is_on(doc):
		return
	try:
		tokens = json.loads(doc.get("sync_tokens") or "{}")
		if not isinstance(tokens, dict):
			tokens = {}
	except Exception:
		tokens = {}
	client = google_api.Client()
	cache, budget = {}, {"left": SNAP_ACTIONS}
	configured = [c for c in dict.fromkeys(calendar_ids(doc).values()) if c]
	try:
		for calendar_id in configured:
			try:
				fresh = _snap_calendar(client, calendar_id, tokens.get(calendar_id), doc, cache, budget)
			except google_api.GoogleAuthError:
				raise
			except google_api.GoogleCalendarError as exc:
				if exc.status != 410:
					raise
				# The token expired: list in full and start a new one.
				tokens.pop(calendar_id, None)
				fresh = _snap_calendar(client, calendar_id, None, doc, cache, budget)
			if fresh:
				tokens[calendar_id] = fresh
	except google_api.GoogleAuthError as exc:
		_auth_failed(exc)
		return
	except Exception:
		_log_failure("Calendar sync: snap-back failed")
	tokens = {c: t for c, t in tokens.items() if c in configured}
	write_state(sync_tokens=json.dumps(tokens), last_incremental_sync=_now())


# ---------------------------------------------------------------------- preview and health


def plan(start, end):
	"""The dry run: what a full repair of ``start``..``end`` would create, update or delete.

	No Google call and no database write, with the switch on or off. ``[{action, kind, title,
	date, source}]`` without the unchanged ones, plus counts.
	"""
	from frappe.utils import date_diff, getdate

	start, end = getdate(start), getdate(end)
	if end < start:
		frappe.throw(frappe._("The end date is before the start date."))
	if date_diff(end, start) > PREVIEW_MAX_DAYS:
		frappe.throw(frappe._("Pick a range of {0} days or less.").format(PREVIEW_MAX_DAYS))
	doc = settings()
	rows, counts = [], {}
	for doctype, name in _window_refs(start, end):
		item = sources.item_for(doctype, name)
		action, title = reconcile(item, doc, None, dry_run=True)
		counts[action] = counts.get(action, 0) + 1
		if action in ("create", "update", "delete", "no_calendar"):
			rows.append(
				{
					"action": action,
					"kind": payload.link_kind(item) or doctype,
					"title": title,
					"date": str(item.get("start") or ""),
					"source": f"{doctype} {name}",
				}
			)
	for action, title, day, kind in sync_projections(doc, None, dry_run=True, start=start, end=end):
		counts[action] = counts.get(action, 0) + 1
		if action in ("create", "update", "delete", "no_calendar"):
			rows.append({"action": action, "kind": kind, "title": title, "date": day, "source": "Projected"})
	rows.sort(key=lambda r: (r["date"], r["action"], r["title"] or ""))
	return {
		"start": str(start),
		"end": str(end),
		"counts": counts,
		"rows": rows[:1000],
		"enabled": is_on(doc),
	}


def health(test=False):
	"""Everything the Settings page's health panel shows. ``test`` makes one token refresh."""
	doc = settings()
	connected = bool(google_api.stored_refresh_token())
	out = {
		"enabled": bool(int(doc.get("enabled") or 0)),
		"active": is_on(doc),
		"google_settings_ready": google_api.google_settings_ready(),
		"connected": connected,
		"account": doc.get("google_account") or "",
		"authorized_on": str(doc.get("authorized_on") or ""),
		"authorized_by": doc.get("authorized_by") or "",
		"redirect_uri": google_api.redirect_uri(""),
		"calendars": calendar_ids(doc),
		"window": [str(d) for d in repair_window(doc)],
		"last_incremental_sync": str(doc.get("last_incremental_sync") or ""),
		"last_full_repair": str(doc.get("last_full_repair") or ""),
		"last_full_repair_summary": doc.get("last_full_repair_summary") or "",
		"counts": {},
		"errors": [],
		"token_ok": None,
		"token_error": "",
	}
	if frappe.db.table_exists(LINK):
		for status, kind, n in (
			frappe.db.sql("SELECT status, kind, COUNT(*) FROM `tabCalendar Sync Link` GROUP BY status, kind")
			or []
		):
			out["counts"].setdefault(status or "", {})[kind or ""] = int(n)
		out["errors"] = frappe.get_all(
			LINK,
			filters={"status": "Error"},
			fields=["source_doctype", "source_name", "title", "last_error", "modified"],
			order_by="modified desc",
			limit_page_length=ERRORS_SHOWN,
		)
	if test and connected:
		try:
			google_api.access_token(force=True)
			out["token_ok"] = True
		except google_api.GoogleCalendarError as exc:
			out["token_ok"] = False
			out["token_error"] = exc.describe()
	return out
