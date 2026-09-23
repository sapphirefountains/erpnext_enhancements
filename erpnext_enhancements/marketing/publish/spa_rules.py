# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The rules behind the /marketing app's endpoints (TASK-2026-01487). Pure: no frappe.

``spa.py`` is the thin frappe layer over these, the same split as ``workflow.py`` and
``approval.py``, so the bench-free CI tier can check every rule without a site.

What lives here:

* **Who may open the app at all** (``can_use``): Marketing Team, Marketing Manager or System
  Manager. The DocPerms decide what they then see; this only keeps everyone else out.
* **What the composer may write** (``clean_post_input``). An allowlist of fields, so a payload
  cannot carry ``status``, ``approver`` or ``owner`` -- the controller refuses those too
  (``workflow.status_edit_problem``), and two locks are better than one on the field that
  decides what goes public.
* **Times are the site's, never the browser's.** ``scheduled_at`` is a naive site-local
  datetime (Frappe stores it so), and the app is used from phones in whatever time zone their
  owner is in. So every time here is a ``YYYY-MM-DD HH:MM[:SS]`` string or a naive
  ``datetime``, parsed by hand, never converted.
* **A new time must be in the future** (``schedule_problem``). A post scheduled in the past is
  published the moment it is approved, which a calendar drag onto yesterday almost never
  means. Leaving the time empty is how "as soon as it is approved" is said.
"""

import datetime
import re

from erpnext_enhancements.marketing.publish import constants as P
from erpnext_enhancements.marketing.publish import outbox, workflow

#: May open /marketing. The DocPerms decide the rest.
ACCESS_ROLES = frozenset({workflow.TEAM_ROLE, workflow.APPROVER_ROLE, "System Manager"})

#: The Social Post fields the composer writes. Nothing else is taken from a payload.
POST_FIELDS = (
	"title",
	"body",
	"link",
	"link_title",
	"link_description",
	"scheduled_at",
	"video_title",
	"video_tags",
	"video_thumbnail",
	"youtube_playlist_id",
)
TARGET_FIELDS = ("social_account", "variant_text", "first_comment")
MAX_TARGETS = 20
MAX_MEDIA = 20
TITLE_MAX = 140
#: The widest calendar a request may ask for: a six-week month grid, plus a margin.
MAX_WINDOW_DAYS = 45
#: A reschedule this far in the past is still "now" (the form was open a moment).
PAST_GRACE = datetime.timedelta(minutes=5)

ASSET_TYPES = ("Image", "Video", "Document")
USAGE_RIGHTS = ("Needs client approval", "Cleared for social", "Not for social")
#: What a browser may say an uploaded file is, by asset type.
MIME_PREFIX = {"Image": ("image/",), "Video": ("video/",), "Document": ("application/pdf",)}

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^(\d{4}-\d{2}-\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?$")


def can_use(roles):
	return bool(ACCESS_ROLES & set(roles or ()))


def _text(value):
	return (value if isinstance(value, str) else "" if value is None else str(value)).strip()


def parse_date(value):
	"""A ``YYYY-MM-DD`` string as a ``date``; ValueError otherwise."""
	text = _text(value)
	if not _DATE.match(text):
		raise ValueError(f"not a date: {value!r}")
	return datetime.date.fromisoformat(text)


def parse_local_datetime(value):
	"""A site-local ``YYYY-MM-DD HH:MM[:SS]`` as a naive ``datetime``; None for blank.

	A ``T`` separator is accepted (an ``<input type=datetime-local>`` sends one). An offset or a
	``Z`` is refused rather than converted: this app never moves a time between zones.
	"""
	if isinstance(value, datetime.datetime):
		return value.replace(microsecond=0)
	text = _text(value)
	if not text:
		return None
	match = _DATETIME.match(text)
	if not match:
		raise ValueError(f"not a site-local date and time: {value!r}")
	day, hour, minute, second = match.groups()
	return datetime.datetime.combine(
		datetime.date.fromisoformat(day), datetime.time(int(hour), int(minute), int(second or 0))
	)


def calendar_window(start, end):
	"""``(first_moment, last_moment)`` for the days ``start`` .. ``end`` inclusive.

	Refuses a reversed window, and one wider than ``MAX_WINDOW_DAYS``: the widest view is a
	six-week month grid, and an unbounded window is an unbounded query.
	"""
	first, last = parse_date(start), parse_date(end)
	if last < first:
		raise ValueError("the calendar window ends before it starts")
	if (last - first).days > MAX_WINDOW_DAYS:
		raise ValueError(f"the calendar window is wider than {MAX_WINDOW_DAYS} days")
	return (
		datetime.datetime.combine(first, datetime.time.min),
		datetime.datetime.combine(last, datetime.time(23, 59, 59)),
	)


def schedule_problem(scheduled_at, now):
	"""Why ``scheduled_at`` may not be set as a new publish time, or None."""
	if scheduled_at is None:
		return None
	if scheduled_at < now - PAST_GRACE:
		return (
			"That time has already passed. Pick a time in the future, or leave it empty to publish "
			"as soon as the post is approved."
		)
	return None


def default_title(values):
	"""A title for a post that was given none: the first line of what it says."""
	for key in ("title", "video_title", "body"):
		line = _text(values.get(key)).split("\n")[0].strip()
		if line:
			return line[:TITLE_MAX]
	return "Untitled post"


def clean_post_input(payload):
	"""``(values, targets, media)`` from a composer payload, or ValueError.

	Only ``POST_FIELDS``, ``TARGET_FIELDS`` and a list of asset names are taken; anything else in
	the payload is ignored. ``scheduled_at`` comes back as a naive datetime or None.
	"""
	if not isinstance(payload, dict):
		raise ValueError("the post must be an object")
	values = {key: _text(payload.get(key)) for key in POST_FIELDS}
	values["scheduled_at"] = parse_local_datetime(payload.get("scheduled_at"))
	values["title"] = default_title(values)[:TITLE_MAX]
	values["video_thumbnail"] = values["video_thumbnail"] or None

	raw_targets = payload.get("targets") or []
	if not isinstance(raw_targets, list):
		raise ValueError("targets must be a list")
	if len(raw_targets) > MAX_TARGETS:
		raise ValueError(f"a post goes to at most {MAX_TARGETS} accounts")
	targets = []
	seen = set()
	for row in raw_targets:
		if not isinstance(row, dict) or not _text(row.get("social_account")):
			raise ValueError("every account row needs an account")
		target = {key: _text(row.get(key)) for key in TARGET_FIELDS}
		if target["social_account"] in seen:
			raise ValueError(f"{target['social_account']} is listed twice")
		seen.add(target["social_account"])
		targets.append(target)

	raw_media = payload.get("media") or []
	if not isinstance(raw_media, list):
		raise ValueError("media must be a list")
	if len(raw_media) > MAX_MEDIA:
		raise ValueError(f"a post carries at most {MAX_MEDIA} media items")
	media = []
	for item in raw_media:
		name = _text(item.get("asset") if isinstance(item, dict) else item)
		if not name:
			raise ValueError("every media item needs an asset")
		media.append(name)
	return values, targets, media


def plan_targets(existing, wanted):
	"""How to turn the saved target rows into ``wanted`` without renaming the ones that stay.

	``existing`` is ``[{"name", "social_account", ...}]``; ``wanted`` is ``clean_post_input``'s
	targets. Returns ``(update, add, remove)``: ``[(row_name, values)]``, ``[values]``,
	``[row_name]``. Matching by account keeps each row's name, so the change history reads as
	"LinkedIn's text changed" rather than "every row deleted and re-added".
	"""
	by_account = {row.get("social_account"): row for row in existing}
	update, add = [], []
	kept = set()
	for target in wanted:
		row = by_account.get(target["social_account"])
		if row is None:
			add.append(target)
		else:
			update.append((row.get("name"), target))
			kept.add(row.get("name"))
	remove = [row.get("name") for row in existing if row.get("name") not in kept]
	return update, add, remove


def post_actions(post, user, roles, may_write, browser):
	"""What the current user may do to ``post``, for the buttons. The endpoints decide again.

	``approve_problems`` says why Approve is unavailable, so the app can show the reason
	instead of hiding the button without one.
	"""
	status = workflow.status_of(post)
	editable = status in outbox.EDITABLE_POST_STATUSES
	# `unchanged=True`: the app sends the post's `modified` with the click, and "it changed
	# after you opened it" is decided then, by the approve endpoint, not now.
	approve_problems = workflow.approval_problems(post, user, roles, browser, unchanged=True)
	return {
		"can_edit": bool(may_write and editable),
		"can_submit": bool(may_write and status == outbox.POST_DRAFT),
		"can_approve": bool(may_write and not approve_problems),
		"approve_problems": approve_problems if status == outbox.POST_PENDING_APPROVAL else [],
		"can_send_back": bool(may_write and not workflow.send_back_problems(post)),
		"can_cancel": bool(may_write and not workflow.cancel_problems(post)),
		"can_delete": bool(may_write and not workflow.delete_problems(post)),
		"locked": not editable,
	}


def quota_view(accounts, now):
	"""What the calendar shows for each Instagram and YouTube account's remaining quota.

	``remaining`` is None until the limiter has looked (``ratelimit`` writes it when it admits
	or refuses a job). A reading older than a day is shown as its time, not as current.
	"""
	rows = []
	for account in accounts:
		network = account.get("network")
		if network not in (P.NETWORK_INSTAGRAM, P.NETWORK_YOUTUBE):
			continue
		checked = account.get("quota_checked_at")
		remaining = account.get("quota_remaining")
		stale = checked is None or (now - checked) > datetime.timedelta(hours=24)
		rows.append(
			{
				"account": account.get("name"),
				"network": network,
				"label": account.get("account_name") or account.get("handle") or network,
				"remaining": None if checked is None else remaining,
				"checked_at": checked,
				"stale": stale,
				"unit": "posts in 24 hours" if network == P.NETWORK_INSTAGRAM else "uploads today",
			}
		)
	return rows


def limits():
	"""The per-network numbers the composer counts against. The server's checks stay authoritative."""
	return {
		P.NETWORK_INSTAGRAM: {
			"text": P.INSTAGRAM_CAPTION_MAX,
			"hashtags": P.INSTAGRAM_HASHTAGS_MAX,
			"mentions": P.INSTAGRAM_MENTIONS_MAX,
			"media": P.INSTAGRAM_CAROUSEL_MAX,
		},
		P.NETWORK_LINKEDIN: {
			"text": P.LINKEDIN_COMMENTARY_MAX,
			"media": P.LINKEDIN_IMAGES_MAX,
			"link_title": P.LINKEDIN_ARTICLE_TITLE_MAX,
		},
		P.NETWORK_YOUTUBE: {
			"text_bytes": P.YOUTUBE_DESCRIPTION_MAX_BYTES,
			"title": P.YOUTUBE_TITLE_MAX,
			"tags": P.YOUTUBE_TAGS_MAX,
		},
		P.NETWORK_FACEBOOK: {},
	}


def asset_input(payload):
	"""Validated fields for a new Marketing Media Asset from an upload, or ValueError."""
	title = _text(payload.get("title"))
	if not title:
		raise ValueError("the asset needs a title")
	asset_type = _text(payload.get("asset_type"))
	if asset_type not in ASSET_TYPES:
		raise ValueError(f"asset type must be one of {', '.join(ASSET_TYPES)}")
	rights = _text(payload.get("usage_rights")) or USAGE_RIGHTS[0]
	if rights not in USAGE_RIGHTS:
		raise ValueError(f"usage rights must be one of {', '.join(USAGE_RIGHTS)}")
	mime = _text(payload.get("mime_type")).lower()
	if mime and not mime.startswith(MIME_PREFIX[asset_type]):
		raise ValueError(f"a {mime} file is not a {asset_type.lower()}")

	def number(key, kind, top):
		raw = payload.get(key)
		if raw in (None, ""):
			return None
		try:
			value = kind(raw)
		except (TypeError, ValueError):
			raise ValueError(f"{key} must be a number") from None
		if value < 0 or value > top:
			raise ValueError(f"{key} is out of range")
		return value

	return {
		"title": title[:TITLE_MAX],
		"asset_type": asset_type,
		"usage_rights": rights,
		"alt_text": _text(payload.get("alt_text")) or None,
		"mime_type": mime or None,
		"width": number("width", int, 100_000),
		"height": number("height", int, 100_000),
		"duration_seconds": number("duration_seconds", float, 86_400.0),
	}


def metric_totals(rows):
	"""Sum each engagement figure over a job's days; None where no day reported it."""
	keys = ("impressions", "reach", "engagements", "clicks", "video_views")
	totals = dict.fromkeys(keys)
	for row in rows:
		for key in keys:
			value = row.get(key)
			if value is not None:
				totals[key] = (totals[key] or 0) + value
	return totals
