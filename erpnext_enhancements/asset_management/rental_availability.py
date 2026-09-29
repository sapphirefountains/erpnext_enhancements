# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Reads and writes behind event-rental availability.

The judgements live in :mod:`rental_rules` (bench-free). This module is the part that
touches the database:

* **what is in the way** of a fountain (:func:`asset_conflicts`) — Asset Bookings on the
  same fountain from any other booking, of any type, and out-of-service records;
* **how many** of an accessory pool are already out (:func:`pool_peak`);
* **keeping each fountain's calendar in step** with a Rental Booking
  (:func:`sync_calendar`), which creates, moves and removes the booking's ``Asset Booking``
  legs;
* the endpoints the form uses: :func:`get_availability`, :func:`plan_package` and the
  :func:`rentable_asset_query` link search.

**Two people booking the last fountain at once.** Check-then-insert is a race: both read
"free", both insert. :func:`lock_rows` takes a row lock (``SELECT ... FOR UPDATE``) on every
Asset and accessory pool a booking touches before anything is read, so the second save waits
for the first to commit and then sees its legs. Names are locked in sorted order, so two
bookings sharing fountains cannot deadlock each other.
"""

import frappe
from frappe import _
from frappe.utils import cint, flt, get_datetime

from erpnext_enhancements.asset_management import rental_rules as rules

#: Asset custom fields this feature reads. All guarded with ``has_column``: doc events fire
#: during ERPNext's own test bootstrap, before this app's custom fields exist.
RENTABLE_FIELD = "custom_rentable"
PREP_FIELD = "custom_rental_prep_hours"
TURNAROUND_FIELD = "custom_rental_turnaround_hours"

#: An Asset in either of these states cannot go out on a rental.
GONE_ASSET_STATUSES = ("Scrapped", "Sold")

#: Project fields the booking's schedule is mirrored into (the Events section of a Project).
PROJECT_SCHEDULE_FIELDS = {
	"delivery_datetime": "custom_delivery_date_time",
	"setup_datetime": "custom_setup_date_time",
	"event_start_datetime": "custom_event_date_time",
	"takedown_datetime": "custom_take_down_date_time",
}

#: ``frappe.flags`` key naming the Rental Booking currently rewriting its own legs. Asset
#: Booking refuses a hand edit, cancel or delete of a managed leg unless this names its owner.
SYNC_FLAG = "rental_booking_sync"


def lock_rows(doctype, names):
	"""Row-lock ``names`` of ``doctype`` until the transaction ends. Sorted, to avoid deadlocks."""
	names = sorted({n for n in names if n})
	if names:
		frappe.db.sql(
			f"select name from `tab{doctype}` where name in %(names)s order by name for update",
			{"names": tuple(names)},
		)


def _asset_columns():
	return [f for f in (RENTABLE_FIELD, PREP_FIELD, TURNAROUND_FIELD) if frappe.db.has_column("Asset", f)]


def fountain_profiles(assets=None, rentable_only=False):
	"""``{asset: profile}`` with name, model, rentable flag and prep/turnaround hours.

	Reads with ``get_all`` (no permission check): someone who may book a rental may not be
	allowed to open the Asset register, and the booking still has to know the fountain's
	turnaround.
	"""
	extra = _asset_columns()
	filters = {"docstatus": ["<", 2]}
	if assets is not None:
		assets = [a for a in assets if a]
		if not assets:
			return {}
		filters["name"] = ["in", assets]
	if rentable_only:
		if RENTABLE_FIELD not in extra:
			return {}
		filters[RENTABLE_FIELD] = 1
		filters["status"] = ["not in", GONE_ASSET_STATUSES]
	rows = frappe.get_all(
		"Asset",
		filters=filters,
		fields=["name", "asset_name", "item_code", "status", *extra],
		order_by="asset_name asc",
	)
	profiles = {}
	for row in rows:
		profiles[row.name] = frappe._dict(
			name=row.name,
			asset_name=row.asset_name,
			item_code=row.item_code,
			status=row.status,
			rentable=cint(row.get(RENTABLE_FIELD)),
			prep_hours=rules.whole(row.get(PREP_FIELD), 0),
			turnaround_hours=rules.whole(row.get(TURNAROUND_FIELD), rules.DEFAULT_TURNAROUND_HOURS),
		)
	return profiles


def asset_conflicts(asset, legs, exclude_booking=None):
	"""What stands in the way of ``legs`` on ``asset``, as a list of dicts; empty when free.

	Each conflict carries ``kind`` (``booking`` or ``out_of_service``), ``name``, a
	human ``label`` and its window. Legs owned by ``exclude_booking`` are the booking's own
	and never conflict with it.
	"""
	if not legs:
		return []
	span_from, span_to = rules.block_span(legs)
	conflicts = []

	bookings = frappe.db.sql(
		"""
		select ab.name, ab.booking_type, ab.from_datetime, ab.to_datetime, ab.rental_booking,
			rb.customer_name
		from `tabAsset Booking` ab
		left join `tabRental Booking` rb on rb.name = ab.rental_booking
		where ab.asset = %(asset)s
			and ab.docstatus < 2
			and ab.from_datetime < %(to)s
			and ab.to_datetime > %(from)s
			and coalesce(ab.rental_booking, '') != %(exclude)s
		order by ab.from_datetime
		""",
		{"asset": asset, "from": span_from, "to": span_to, "exclude": exclude_booking or ""},
		as_dict=True,
	)
	seen_rentals = set()
	for row in bookings:
		start, end = get_datetime(row.from_datetime), get_datetime(row.to_datetime)
		if not any(rules.overlaps(leg_from, leg_to, start, end) for _leg, leg_from, leg_to in legs):
			continue
		if row.rental_booking:
			# One rental owns up to three legs; report the rental once, not each leg.
			if row.rental_booking in seen_rentals:
				continue
			seen_rentals.add(row.rental_booking)
			label = _("rental {0} for {1}").format(row.rental_booking, row.customer_name or _("a customer"))
			conflicts.append(
				{"kind": "booking", "doctype": "Rental Booking", "name": row.rental_booking,
				 "label": label, "from": start, "to": end}
			)
		else:
			label = _("{0} booking {1}").format(_(row.booking_type), row.name)
			conflicts.append(
				{"kind": "booking", "doctype": "Asset Booking", "name": row.name,
				 "label": label, "from": start, "to": end}
			)

	outages = frappe.db.sql(
		"""
		select name, reason, out_from, blocked_until
		from `tabAsset Out of Service`
		where asset = %(asset)s
			and out_from < %(to)s
			and (blocked_until is null or blocked_until > %(from)s)
		""",
		{"asset": asset, "from": span_from, "to": span_to},
		as_dict=True,
	)
	for row in outages:
		start = get_datetime(row.out_from)
		end = get_datetime(row.blocked_until) if row.blocked_until else None
		if not any(rules.overlaps(leg_from, leg_to, start, end) for _leg, leg_from, leg_to in legs):
			continue
		label = _("out of service ({0}) — {1}").format(_(row.reason), row.name)
		conflicts.append(
			{"kind": "out_of_service", "doctype": "Asset Out of Service", "name": row.name,
			 "label": label, "from": start, "to": end}
		)
	return conflicts


def pool_capacity(pool):
	"""``(label, bookable units, turnaround hours, disabled)`` for an accessory pool."""
	row = frappe.db.get_value(
		"Rental Accessory Pool",
		pool,
		["pool_name", "total_qty", "out_of_service_qty", "turnaround_hours", "disabled"],
		as_dict=True,
	)
	if not row:
		return pool, 0, 0, True
	capacity = max(cint(row.total_qty) - cint(row.out_of_service_qty), 0)
	return row.pool_name or pool, capacity, rules.whole(row.turnaround_hours), bool(cint(row.disabled))


def pool_peak(pool, window_from, window_to, exclude_booking=None):
	"""Most units of ``pool`` out at once inside the window, across other holding bookings."""
	rows = frappe.db.sql(
		"""
		select a.block_from, a.block_to, a.qty
		from `tabRental Booking Accessory` a
		inner join `tabRental Booking` b on b.name = a.parent
		where a.parenttype = 'Rental Booking'
			and a.pool = %(pool)s
			and b.status in %(holding)s
			and b.name != %(exclude)s
			and a.block_from < %(to)s
			and a.block_to > %(from)s
		""",
		{
			"pool": pool,
			"holding": tuple(rules.HOLDING_STATUSES),
			"exclude": exclude_booking or "",
			"from": window_from,
			"to": window_to,
		},
		as_dict=True,
	)
	return rules.peak_usage(
		[(get_datetime(r.block_from), get_datetime(r.block_to), cint(r.qty)) for r in rows],
		window_from,
		window_to,
	)


# ---------------------------------------------------------------- calendar sync


def _existing_legs(booking_name):
	return frappe.get_all(
		"Asset Booking",
		filters={"rental_booking": booking_name, "docstatus": ["<", 2]},
		fields=["name", "asset", "rental_leg", "from_datetime", "to_datetime", "docstatus",
				"customer", "project", "location"],
	)


def sync_calendar(doc):
	"""Make every fountain's calendar match the Rental Booking ``doc``.

	Creates the legs it lacks, moves the ones whose window changed (in place, so a submitted
	leg keeps its name and its inspections), removes the ones it no longer needs, submits
	them once the booking is firm and releases them when it is expired or canceled.

	Runs inside the booking's own transaction: if any leg is refused — Asset Booking checks
	overlap itself, as a second line behind the booking's validate — the whole save rolls
	back and nothing is half-booked.
	"""
	released = doc.status in rules.RELEASED_STATUSES
	firm = doc.status in rules.FIRM_STATUSES

	desired = []
	if not released:
		delivery, takedown = get_datetime(doc.delivery_datetime), get_datetime(doc.takedown_datetime)
		for row in doc.fountains or []:
			for leg, start, end in rules.leg_windows(delivery, takedown, row.prep_hours, row.turnaround_hours):
				desired.append((row.asset, leg, start, end))

	existing = _existing_legs(doc.name)
	create, move, remove = rules.plan_leg_changes(
		desired,
		[
			{"name": r.name, "asset": r.asset, "leg": r.rental_leg,
			 "from": get_datetime(r.from_datetime), "to": get_datetime(r.to_datetime)}
			for r in existing
		],
	)

	shared = {"customer": doc.customer, "project": doc.project, "location": doc.venue_address}
	previous = frappe.flags.get(SYNC_FLAG)
	frappe.flags[SYNC_FLAG] = doc.name
	try:
		for name in remove:
			_remove_leg(name)
		for name, start, end in move:
			leg = frappe.get_doc("Asset Booking", name)
			leg.update({"from_datetime": start, "to_datetime": end, **shared})
			leg.flags.ignore_permissions = True
			leg.save()
		for asset, leg_kind, start, end in create:
			leg = frappe.get_doc(
				{
					"doctype": "Asset Booking",
					"asset": asset,
					"booking_type": rules.LEG_BOOKING_TYPE[leg_kind],
					"rental_booking": doc.name,
					"rental_leg": leg_kind,
					"from_datetime": start,
					"to_datetime": end,
					**shared,
				}
			)
			leg.insert(ignore_permissions=True)

		moved = {name for name, _s, _e in move}
		for row in _existing_legs(doc.name):
			if row.name not in moved and any((row.get(k) or None) != (v or None) for k, v in shared.items()):
				leg = frappe.get_doc("Asset Booking", row.name)
				leg.update(shared)
				leg.flags.ignore_permissions = True
				leg.save()
			if firm and row.docstatus == 0:
				leg = frappe.get_doc("Asset Booking", row.name)
				leg.flags.ignore_permissions = True
				leg.submit()
	finally:
		frappe.flags[SYNC_FLAG] = previous

	# Point each fountain line at its Rental leg, for the click-through to that calendar.
	rental_legs = {
		r.asset: r.name for r in _existing_legs(doc.name) if r.rental_leg == rules.LEG_RENTAL
	}
	for row in doc.fountains or []:
		wanted = rental_legs.get(row.asset)
		if row.asset_booking != wanted:
			row.db_set("asset_booking", wanted, update_modified=False)


def _remove_leg(name):
	"""Release one leg: a draft is deleted outright, a submitted one is canceled."""
	docstatus = frappe.db.get_value("Asset Booking", name, "docstatus")
	if docstatus == 0:
		frappe.delete_doc("Asset Booking", name, ignore_permissions=True)
	elif docstatus == 1:
		leg = frappe.get_doc("Asset Booking", name)
		leg.flags.ignore_permissions = True
		leg.cancel()


def release_all(doc):
	"""Remove every live leg of ``doc`` (used when a booking is deleted)."""
	previous = frappe.flags.get(SYNC_FLAG)
	frappe.flags[SYNC_FLAG] = doc.name
	try:
		for row in _existing_legs(doc.name):
			_remove_leg(row.name)
	finally:
		frappe.flags[SYNC_FLAG] = previous


# ---------------------------------------------------------------- endpoints


def _parse_schedule(delivery_datetime, takedown_datetime):
	delivery = get_datetime(delivery_datetime) if delivery_datetime else None
	takedown = get_datetime(takedown_datetime) if takedown_datetime else None
	problems = rules.schedule_problems(delivery, takedown)
	if problems:
		frappe.throw(" ".join(_(p) for p in problems), title=_("Check the schedule"))
	return delivery, takedown


def _serialize_conflicts(conflicts):
	return [dict(c, **{"from": str(c["from"]), "to": str(c["to"]) if c["to"] else None}) for c in conflicts]


@frappe.whitelist()
def get_availability(delivery_datetime, takedown_datetime, exclude_booking=None):
	"""Every rentable fountain and accessory pool, and whether it is free for the dates.

	Returns ``{"fountains": [...], "pools": [...]}``. A fountain carries ``available``, its
	blocked window (with its own prep and turnaround) and the ``conflicts`` in the way; a
	pool carries ``capacity``, ``booked`` (the peak already out) and ``available``.
	``exclude_booking`` leaves a booking's own lines out, so editing a booking does not see
	itself as the obstacle.
	"""
	frappe.has_permission("Rental Booking", "read", throw=True)
	delivery, takedown = _parse_schedule(delivery_datetime, takedown_datetime)

	fountains = []
	for profile in fountain_profiles(rentable_only=True).values():
		legs = rules.leg_windows(delivery, takedown, profile.prep_hours, profile.turnaround_hours)
		conflicts = asset_conflicts(profile.name, legs, exclude_booking)
		block_from, block_to = rules.block_span(legs)
		fountains.append(
			{
				"asset": profile.name,
				"asset_name": profile.asset_name,
				"item_code": profile.item_code,
				"available": not conflicts,
				"block_from": str(block_from),
				"block_to": str(block_to),
				"conflicts": _serialize_conflicts(conflicts),
			}
		)

	pools = []
	for name in frappe.get_all("Rental Accessory Pool", filters={"disabled": 0}, pluck="name", order_by="name"):
		label, capacity, turnaround, _disabled = pool_capacity(name)
		start, end = rules.pool_window(delivery, takedown, turnaround)
		booked = pool_peak(name, start, end, exclude_booking)
		pools.append(
			{"pool": name, "label": label, "capacity": capacity, "booked": booked,
			 "available": max(capacity - booked, 0)}
		)
	return {"fountains": fountains, "pools": pools}


@frappe.whitelist()
def plan_package(package, delivery_datetime, takedown_datetime, current_assets=None, exclude_booking=None):
	"""What applying Rental Package ``package`` would add to a booking, without changing it.

	Picks free fountains of each model the package names (skipping ones already on the
	booking), and lists its accessories and fees. Returns ``shortfalls`` in words for any
	model with too few free fountains — the form shows them rather than failing, because a
	package half-filled is still a useful start.
	"""
	frappe.has_permission("Rental Booking", "read", throw=True)
	delivery, takedown = _parse_schedule(delivery_datetime, takedown_datetime)
	pkg = frappe.get_doc("Rental Package", package)
	if cint(pkg.disabled):
		frappe.throw(_("Rental Package {0} is disabled.").format(package))

	current = set(frappe.parse_json(current_assets) or [])
	profiles = fountain_profiles(rentable_only=True)
	on_booking = [p for name, p in profiles.items() if name in current]

	fountains, shortfalls = [], []
	for line in pkg.fountains or []:
		wanted = rules.whole(line.qty) - sum(1 for p in on_booking if p.item_code == line.item)
		if wanted <= 0:
			continue
		picked = []
		for profile in profiles.values():
			if len(picked) >= wanted:
				break
			if profile.item_code != line.item or profile.name in current:
				continue
			legs = rules.leg_windows(delivery, takedown, profile.prep_hours, profile.turnaround_hours)
			if asset_conflicts(profile.name, legs, exclude_booking):
				continue
			picked.append(profile)
			current.add(profile.name)
		fountains.extend({"asset": p.name, "asset_name": p.asset_name, "rate": flt(line.rate)} for p in picked)
		if len(picked) < wanted:
			shortfalls.append(
				_("{0} of {1} {2} fountains are free for those dates.").format(len(picked), wanted, line.item)
			)

	return {
		"fountains": fountains,
		"accessories": [{"pool": a.pool, "qty": cint(a.qty), "rate": flt(a.rate)} for a in pkg.accessories or []],
		"fees": {
			"delivery_setup_fee": flt(pkg.delivery_setup_fee),
			"pickup_removal_fee": flt(pkg.pickup_removal_fee),
			"security_deposit": flt(pkg.security_deposit),
		},
		"shortfalls": shortfalls,
	}


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def rentable_asset_query(doctype, txt, searchfield, start, page_len, filters):
	"""Link search for a fountain line: rentable Assets only.

	A custom query rather than a filter because v16 validates the chosen link through the
	same search, and the people who book rentals are not necessarily allowed to search the
	whole Asset register.
	"""
	frappe.has_permission("Rental Booking", "read", throw=True)
	if not frappe.db.has_column("Asset", RENTABLE_FIELD):
		return []
	return frappe.db.sql(
		f"""
		select name, asset_name, item_code
		from `tabAsset`
		where `{RENTABLE_FIELD}` = 1
			and docstatus < 2
			and status not in %(gone)s
			and (name like %(txt)s or asset_name like %(txt)s or item_code like %(txt)s)
		order by asset_name
		limit %(start)s, %(page_len)s
		""",
		{
			"gone": GONE_ASSET_STATUSES,
			"txt": f"%{txt}%",
			"start": cint(start),
			"page_len": cint(page_len) or 20,
		},
	)


@frappe.whitelist()
def get_project_rental(project):
	"""The live Rental Booking for a Project, for the banner on the Project form, or None."""
	if not project or not frappe.has_permission("Rental Booking", "read"):
		return None
	return frappe.db.get_value(
		"Rental Booking",
		{"project": project, "status": ["in", rules.HOLDING_STATUSES]},
		["name", "status"],
		as_dict=True,
		order_by="creation desc",
	)


def push_schedule_to_project(doc):
	"""Copy the booking's schedule onto its Project's Events fields.

	The booking owns the schedule; the Project form shows those fields read-only while a
	live booking exists (``public/js/asset_management/project_rental.js``). Written with
	``db.set_value`` and without touching ``modified``, so a Project form someone has open
	is not told it was changed underneath them for a mirror write.
	"""
	if not doc.project:
		return
	values = {}
	for source, target in PROJECT_SCHEDULE_FIELDS.items():
		if frappe.db.has_column("Project", target):
			values[target] = doc.get(source)
	if not values:
		return
	current = frappe.db.get_value("Project", doc.project, list(values), as_dict=True) or {}
	changed = {
		k: v for k, v in values.items()
		if (get_datetime(current.get(k)) if current.get(k) else None) != (get_datetime(v) if v else None)
	}
	if changed:
		frappe.db.set_value("Project", doc.project, changed, update_modified=False)


def project_schedule(project):
	"""The Project's Events schedule, keyed by the booking's field names (blanks omitted)."""
	fields = {s: t for s, t in PROJECT_SCHEDULE_FIELDS.items() if frappe.db.has_column("Project", t)}
	if not project or not fields:
		return {}
	row = frappe.db.get_value("Project", project, list(fields.values()), as_dict=True) or {}
	return {source: row.get(target) for source, target in fields.items() if row.get(target)}

