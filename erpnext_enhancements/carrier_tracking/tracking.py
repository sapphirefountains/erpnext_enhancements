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
* **Never raises into the scheduler.** An unexpected error is logged once per run, masked.

**Refresh (``refresh_order``)** checks every FedEx and UPS package on one order now, stopped ones
included (a person asked), at most once a minute per order.
"""

import time

import frappe

from erpnext_enhancements.carrier_tracking import carriers as C
from erpnext_enhancements.carrier_tracking import client, config
from erpnext_enhancements.carrier_tracking import poll_rules as R

DOCTYPE = "Tracked Package"

#: Purchase Order statuses whose packages are no longer polled. "Delivered" is ERPNext's status for
#: an order delivered by the supplier straight to the customer (drop-ship).
CLOSED_ORDER_STATUSES = ("Closed", "Completed", "Cancelled", "Delivered")

#: The row fields the rules read.
ROW_FIELDS = (
	"name",
	"creation",
	"reference_name",
	"tracking_number",
	"resolved_carrier",
	"status",
	"status_description",
	"estimated_delivery",
	"delivered_on",
	"last_location",
	"last_checked",
	"last_success",
	"last_change",
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
	"tracking_url",
	"status",
	"status_description",
	"estimated_delivery",
	"delivered_on",
	"last_location",
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


def apply(row, result, now, manual=False):
	"""Write the carrier's answer for one row. Returns the updates written."""
	updates = R.apply_result(row, result, now, manual=manual)
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
	for row in rows:
		if clock() - started > R.RUN_BUDGET_SECONDS:
			break
		carrier = row.get("resolved_carrier")
		if carrier in blocked or carrier not in configs:
			continue
		result = client.track(configs[carrier], row.get("tracking_number"), cache, now=now)
		apply(row, result, now)
		frappe.db.commit()
		checked += 1
		if result.get("outcome") in R.CARRIER_LEVEL:
			blocked.add(carrier)
	return {"checked": checked, "skipped_carriers": sorted(blocked)}


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


def check_rows(rows, manual=True):
	"""Ask the carriers about ``rows`` now. Rows of a carrier that is not live are left alone."""
	settings = config._settings()
	configs = {carrier: config.carrier_config(carrier, settings) for carrier in live_carriers(settings)}
	if not configs:
		return 0
	now = frappe.utils.now_datetime()
	cache = config.cache()
	blocked = set()
	checked = 0
	for row in rows:
		carrier = row.get("resolved_carrier")
		if carrier not in configs or carrier in blocked:
			continue
		result = client.track(configs[carrier], row.get("tracking_number"), cache, now=now)
		apply(row, result, now, manual=manual)
		checked += 1
		if result.get("outcome") in R.CARRIER_LEVEL:
			blocked.add(carrier)
	return checked


def refresh_cooldown_key(purchase_order):
	return f"{client.CACHE_PREFIX}:refresh:{purchase_order}"


def refresh_order(purchase_order):
	"""Check every FedEx and UPS package on one order now. Returns how many were checked, or
	``None`` when the order was refreshed less than ``REFRESH_COOLDOWN_SECONDS`` ago."""
	key = refresh_cooldown_key(purchase_order)
	try:
		if frappe.cache.get_value(key):
			return None
		frappe.cache.set_value(key, 1, expires_in_sec=R.REFRESH_COOLDOWN_SECONDS)
	except Exception:
		pass
	rows = [row for row in rows_for_order(purchase_order) if row.get("resolved_carrier") in C.TRACKABLE]
	return check_rows(rows, manual=True)
