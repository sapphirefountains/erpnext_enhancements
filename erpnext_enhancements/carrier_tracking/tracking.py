# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Check tracked packages with their carriers: the hourly poller and a person's Refresh.

The decisions (which rows, what an answer changes, when to stop) are ``poll_rules``; the carrier
calls are ``config.track`` / ``client.track``. This module reads rows, makes the calls and writes
back exactly what ``poll_rules.apply_result`` returns, with ``db.set_value`` (no Document save, no
``modified`` bump, no Version), so the carrier's answer never collides with a person's edit.

**The poller (``poll``, hourly).**

* **Does nothing, and logs nothing, while it cannot work**: the master switch off, or no carrier
  switched on with both credentials. A dormant feature must not fill the Error Log.
* **Which packages**: on a submitted Purchase Order that is not Closed, Completed, Canceled or
  Delivered (drop-ship) and not fully received; FedEx or UPS; not delivered; not stopped; not
  checked in the last ``RECHECK_AFTER_HOURS``. Never-checked first, then oldest-checked first.
* **Bounded**: at most ``BATCH_SIZE`` packages and ``RUN_BUDGET_SECONDS`` a run. A carrier that
  refused our login, said "too many requests" or was down is not asked again in the same run.
* **Re-drivable**: the next run picks up whatever this one did not reach, so a deploy that flushes
  the queue (CLAUDE.md: every merge to main ``FLUSHDB``s Redis) costs one hour, nothing more. Each
  package's answer is committed on its own, so a run cut short keeps what it learned.
* **Never raises into the scheduler, and one bad row cannot stop it.** Each package is checked in
  its own ``try``: a row whose check or write fails is rolled back, stamped as checked (so it is not
  picked first again next hour), and the run goes on. Problems are logged once per run, masked.

**Calls made inside a web request** (a person's Refresh, or the first check after Add) are capped:
at most ``WEB_MAX_CHECKS`` packages and ``WEB_BUDGET_SECONDS`` per request, the rest left to the
poller; at most one such round per order per ``REFRESH_COOLDOWN_SECONDS``, shared by Add and
Refresh so adding and removing a number cannot get round it; and at most ``USER_CALLS_PER_WINDOW``
carrier calls per person per ``USER_WINDOW_SECONDS`` across all orders.
"""

import time

import frappe

from erpnext_enhancements.carrier_tracking import carriers as C
from erpnext_enhancements.carrier_tracking import client, config, package_rules
from erpnext_enhancements.carrier_tracking import poll_rules as R

DOCTYPE = "Tracked Package"

#: Purchase Order statuses whose packages are no longer polled (``package_rules``, one list).
CLOSED_ORDER_STATUSES = package_rules.CLOSED_ORDER_STATUSES

#: A person's request waits for the carrier at most this long and for at most this many packages;
#: whatever is left is the poller's. One slow carrier call can take TIMEOUT (5 + 15 s) for the
#: token and again for the track call, so the budget is checked before every call.
WEB_MAX_CHECKS = 5
WEB_BUDGET_SECONDS = 25
#: Carrier calls one person may cause from the Desk, across every order, per window.
USER_CALLS_PER_WINDOW = 30
USER_WINDOW_SECONDS = 600

#: The row fields the rules read.
ROW_FIELDS = (
	"name",
	"creation",
	"reference_name",
	"tracking_number",
	"resolved_carrier",
	"carrier_guessed",
	"status",
	"status_description",
	"estimated_delivery",
	"delivered_on",
	"last_location",
	"destination",
	"last_checked",
	"last_success",
	"last_change",
	"first_refused_on",
	"failure_count",
	"polling_stopped",
	"stop_reason",
)

#: The fields a banner shows. Never a secret; last_error is masked when it is written.
DISPLAY_FIELDS = (
	"name",
	"tracking_number",
	"carrier",
	"resolved_carrier",
	"carrier_guessed",
	"tracking_url",
	"status",
	"status_description",
	"estimated_delivery",
	"delivered_on",
	"last_location",
	"destination",
	"last_checked",
	"last_success",
	"last_error",
	"polling_stopped",
	"stop_reason",
	"creation",
	"owner",
)


def _installed():
	try:
		return bool(frappe.db.exists("DocType", DOCTYPE))
	except Exception:
		return False


# ---------------------------------------------------------------------------- writing


def apply(row, result, now, manual=False, carrier_answered_at=None):
	"""Write the carrier's answer for one row. Returns the updates written."""
	updates = R.apply_result(row, result, now, manual=manual, carrier_answered_at=carrier_answered_at)
	frappe.db.set_value(DOCTYPE, row["name"], updates, update_modified=False)
	return updates


# ---------------------------------------------------------------------------- the poller


def due_rows(carriers, now, limit):
	"""Candidate rows for the poller, oldest-checked first. ``poll_rules.pick`` makes the final cut."""
	if not carriers:
		return []
	due_before = frappe.utils.add_to_date(now, hours=-R.RECHECK_AFTER_HOURS, as_datetime=True)
	columns = ", ".join(f"tp.`{field}`" for field in ROW_FIELDS)
	return frappe.db.sql(
		f"""
		select {columns}
		from `tab{DOCTYPE}` tp
		join `tabPurchase Order` po on po.name = tp.reference_name
		where tp.reference_doctype = 'Purchase Order'
			and tp.polling_stopped = 0
			and tp.resolved_carrier in %(carriers)s
			and coalesce(tp.status, '') != %(delivered)s
			and (tp.last_checked is null or tp.last_checked <= %(due_before)s)
			and po.docstatus = 1
			and coalesce(po.status, '') not in %(closed)s
			and coalesce(po.per_received, 0) < 100
		order by (tp.last_checked is null) desc, tp.last_checked asc, tp.name asc
		limit %(limit)s
		""",
		{
			"carriers": tuple(sorted(carriers)),
			"delivered": C.STATUS_LABELS[C.DELIVERED],
			"due_before": due_before,
			"closed": CLOSED_ORDER_STATUSES,
			"limit": int(limit),
		},
		as_dict=True,
	)


def poll():
	"""Hourly scheduler entry. Never raises."""
	try:
		return run_poll()
	except Exception:
		try:
			frappe.db.rollback()
			frappe.log_error(
				title="Package tracking: the hourly check stopped",
				message=C.mask(frappe.get_traceback(), limit=4000),
			)
		except Exception:
			pass
		return None


def run_poll(clock=time.monotonic):
	"""One bounded run. Returns ``{"checked": n, "skipped_carriers": [...]}``, or ``None`` when off."""
	if not _installed():
		return None
	settings = config._settings()
	if not config.feature_enabled(settings):
		return None
	configs = {carrier: config.carrier_config(carrier, settings) for carrier in C.TRACKABLE}
	configs = {carrier: cfg for carrier, cfg in configs.items() if cfg}
	if not configs:
		return None

	now = frappe.utils.now_datetime()
	rows = R.pick(due_rows(set(configs), now, R.BATCH_SIZE * 2), now, R.BATCH_SIZE)
	cache = config.cache()
	started = clock()
	blocked = set()
	checked = 0
	broken = []

	answered = last_answers(configs)

	def write(row, result):
		# One row must not stop the run, nor be picked first for ever: undo it, stamp it checked
		# with a plain note, and go on. The traceback is logged once, after the loop.
		carrier = row.get("resolved_carrier")
		try:
			apply(row, result, now, carrier_answered_at=answered.get(carrier))
			frappe.db.commit()
			if result.get("outcome") == R.OK:
				answered[carrier] = now
		except Exception:
			broken.append((row.get("name"), C.mask(frappe.get_traceback(), limit=1500)))
			_mark_broken(row, now)

	round_ = Round(write)
	for row in rows:
		if clock() - started > R.RUN_BUDGET_SECONDS:
			break
		carrier = row.get("resolved_carrier")
		if carrier in blocked or carrier not in configs:
			continue
		try:
			result = client.track(configs[carrier], row.get("tracking_number"), cache, now=now)
		except Exception:
			broken.append((row.get("name"), C.mask(frappe.get_traceback(), limit=1500)))
			_mark_broken(row, now)
			continue
		checked += 1
		if round_.handle(carrier, row, result):
			blocked.add(carrier)
	round_.flush()
	if broken:
		_log_broken(broken)
	return {"checked": checked, "skipped_carriers": sorted(blocked), "broken": [name for name, _ in broken]}


class Round:
	"""Tells a refusal about one number from a refusal about every number, within one round of checks.

	An ambiguous refusal (``poll_rules.is_number_rejection``: a 400 or 404 with a code we cannot place)
	is held, not written, until another package of the same carrier is answered in the same round:

	* **another package gets the same kind of refusal**: the carrier is refusing our requests, not
	  these numbers. Both are written as carrier-level (nothing counted, nothing stopped) and the
	  carrier is skipped for the rest of the round;
	* **another package gets a real answer** (found, not found, invalid, unreadable): the refusal was
	  about its own number, and is written so (``poll_rules`` may stop it after
	  ``REJECTED_GIVE_UP_DAYS``);
	* **nothing else is learned** (no other package of that carrier, or only carrier-level failures):
	  the ambiguous case. At the end of the round the refusal is written as about its number. If the
	  carrier was at fault after all, a person's Refresh restarts the package once it answers.

	``write(row, result)`` saves one row. ``handle`` returns whether the carrier should be skipped for
	the rest of the round.
	"""

	def __init__(self, write):
		self.write = write
		self.pending = {}

	def handle(self, carrier, row, result):
		if R.is_number_rejection(result):
			first = self.pending.pop(carrier, None)
			if first is None:
				self.pending[carrier] = (row, result)
				return False
			for held_row, held_result in (first, (row, result)):
				self.write(held_row, dict(held_result, scope=client.SCOPE_CARRIER))
			return True
		held = None
		if carrier in self.pending and result.get("outcome") not in R.CARRIER_LEVEL:
			held = self.pending.pop(carrier)
		# This answer is written first, so a held refusal is judged knowing the carrier just answered.
		self.write(row, result)
		if held:
			self.write(*held)
		return R.blocks_carrier(result)

	def flush(self):
		for held_row, held_result in list(self.pending.values()):
			self.write(held_row, held_result)
		self.pending.clear()


def last_answers(carriers):
	"""``{carrier: newest last_success}`` over all of each carrier's packages: when the carrier last
	gave a real answer about anything. One query per round. ``{}`` if it cannot be read, which makes
	the ambiguous-refusal rule stop nothing (``poll_rules.REJECTED_GIVE_UP_DAYS``)."""
	carriers = sorted(c for c in carriers if c in C.TRACKABLE)
	if not carriers:
		return {}
	try:
		rows = frappe.db.sql(
			f"select resolved_carrier, max(last_success) from `tab{DOCTYPE}` "
			"where resolved_carrier in %(carriers)s and last_success is not null group by resolved_carrier",
			{"carriers": tuple(carriers)},
		)
		return {carrier: moment for carrier, moment in rows or [] if moment}
	except Exception:
		return {}


def _mark_broken(row, now):
	"""After a failed check: roll back, then stamp ``last_checked`` so the row goes to the back."""
	try:
		frappe.db.rollback()
		frappe.db.set_value(
			DOCTYPE,
			row.get("name"),
			{"last_checked": now, "last_error": "Something went wrong saving this package's status."},
			update_modified=False,
		)
		frappe.db.commit()
	except Exception:
		try:
			frappe.db.rollback()
		except Exception:
			pass


def _log_broken(broken):
	try:
		message = "\n\n".join(f"{name}:\n{trace}" for name, trace in broken[:5])
		frappe.log_error(
			title=f"Package tracking: {len(broken)} package(s) could not be saved", message=message
		)
	except Exception:
		pass


# ---------------------------------------------------------------------------- one order


def rows_for_order(purchase_order, fields=ROW_FIELDS):
	return frappe.get_all(
		DOCTYPE,
		filters={"reference_doctype": "Purchase Order", "reference_name": purchase_order},
		fields=list(fields),
		order_by="creation asc",
		limit_page_length=0,
	)


def live_carriers(settings=None):
	"""The carriers that can be asked right now."""
	settings = settings or config._settings()
	if not config.feature_enabled(settings):
		return []
	return [carrier for carrier in C.TRACKABLE if config.carrier_config(carrier, settings)]


def check_rows(rows, manual=True, clock=time.monotonic):
	"""Ask the carriers about ``rows`` now, inside a web request. Rows of a carrier that is not live
	are left alone. At most ``WEB_MAX_CHECKS`` calls and ``WEB_BUDGET_SECONDS``; the rest wait for
	the poller. Each call also counts against the person's window (``USER_CALLS_PER_WINDOW``).

	Returns ``{"checked": n, "user_limited": bool, "retry_minutes": m}``; ``user_limited`` says the
	person's window was full, so the banner can say why nothing was checked and when to try again.
	"""
	outcome = {"checked": 0, "user_limited": False, "retry_minutes": 0}
	settings = config._settings()
	configs = {carrier: config.carrier_config(carrier, settings) for carrier in live_carriers(settings)}
	if not configs:
		return outcome
	now = frappe.utils.now_datetime()
	cache = config.cache()
	started = clock()
	blocked = set()
	answered = last_answers(configs)

	def write(row, result):
		carrier = row.get("resolved_carrier")
		apply(row, result, now, manual=manual, carrier_answered_at=answered.get(carrier))
		if result.get("outcome") == R.OK:
			answered[carrier] = now

	round_ = Round(write)
	for row in rows:
		if outcome["checked"] >= WEB_MAX_CHECKS or clock() - started > WEB_BUDGET_SECONDS:
			break
		carrier = row.get("resolved_carrier")
		if carrier not in configs or carrier in blocked:
			continue
		if not _take_user_call():
			outcome["user_limited"] = True
			outcome["retry_minutes"] = _user_wait_minutes()
			break
		result = client.track(configs[carrier], row.get("tracking_number"), cache, now=now)
		outcome["checked"] += 1
		if round_.handle(carrier, row, result):
			blocked.add(carrier)
	round_.flush()
	return outcome


def _user_key():
	return f"{client.CACHE_PREFIX}:user_calls:{frappe.session.user}"


def _take_user_call():
	"""Count one carrier call against the person's window; ``False`` once the window is full.

	Atomic: Redis ``INCR`` on the site-namespaced key (``frappe.cache.make_key``, the same prefix
	Frappe's own ``set_value`` uses), so parallel requests cannot go over the limit. The window's
	expiry is set by the call that opens it **and by any call that finds the key without one**
	(``ttl`` negative): if the opening call's ``EXPIRE`` was lost (``expire_key`` swallows a dropped
	connection, or the worker died between the two commands), the next call repairs it instead of
	locking the person out until Redis is flushed (review round 3). If Redis cannot be reached the
	call is allowed: the order's cooldown and the request's own cap still bound it.
	"""
	try:
		key = _user_key()
		redis_key = frappe.cache.make_key(key)
		used = int(frappe.cache.incrby(redis_key, 1))
		if used == 1 or int(frappe.cache.ttl(redis_key)) < 0:
			frappe.cache.expire_key(key, USER_WINDOW_SECONDS)
		return used <= USER_CALLS_PER_WINDOW
	except Exception:
		return True


def _user_wait_minutes():
	"""Whole minutes until the person's window opens again (at least 1)."""
	try:
		seconds = int(frappe.cache.ttl(frappe.cache.make_key(_user_key())))
	except Exception:
		seconds = USER_WINDOW_SECONDS
	if seconds <= 0:
		seconds = USER_WINDOW_SECONDS
	return max(1, -(-seconds // 60))


def refresh_cooldown_key(purchase_order):
	return f"{client.CACHE_PREFIX}:refresh:{purchase_order}"


def claim_order_cooldown(purchase_order):
	"""``True`` if this request may ask the carriers about this order now, and starts its cooldown.
	Shared by Refresh and the first check after Add."""
	key = refresh_cooldown_key(purchase_order)
	try:
		if frappe.cache.get_value(key):
			return False
		frappe.cache.set_value(key, 1, expires_in_sec=R.REFRESH_COOLDOWN_SECONDS)
	except Exception:
		pass
	return True


def refresh_order(purchase_order):
	"""Check the FedEx and UPS packages on one order now (capped, see ``check_rows``). Returns
	``check_rows``' answer, or ``None`` when the order was checked less than
	``REFRESH_COOLDOWN_SECONDS`` ago. Packages not reached are the poller's."""
	if not claim_order_cooldown(purchase_order):
		return None
	rows = [row for row in rows_for_order(purchase_order) if row.get("resolved_carrier") in C.TRACKABLE]
	# Oldest-checked first, so a second Refresh after the cap reaches the ones the first did not.
	rows.sort(key=lambda row: (row.get("last_checked") is not None, str(row.get("last_checked") or "")))
	return check_rows(rows, manual=True)
