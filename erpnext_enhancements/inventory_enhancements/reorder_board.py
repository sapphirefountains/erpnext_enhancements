# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Reorder Board's reads and writes (v1.730.0, ER-2026-1323810).

Parker Bailey asked, and Nik approved on 2026-10-22, for "more control over requests being sent
and made instead of just automatically clogging the material request table". So:

* ``patches/disable_auto_material_requests`` switches ERPNext's own auto-indent off
  (``Stock Settings.auto_indent`` = 0). With it off, ``erpnext.stock.reorder_item.reorder_item``
  (``daily_long``) returns before it reads anything, and so never creates a request and never
  sends ``reorder_email_notify``'s email (checked against ERPNext v16.50.0, which production runs,
  and the ``version-16`` branch).
* :func:`build_board` is the page's one read: every reorder rule, whether it is below its point
  by ERPNext's own rule and how much ERPNext would have asked for, and every open request for a
  rule's item and warehouse, from Material Request through Purchase Order to receipt.
* :func:`create_requests` makes the Draft Material Requests the buyer picked (one per company).
* :func:`snooze` / :func:`unsnooze` park a line without forgetting it.
* :func:`sync_watch` (hourly) and :func:`send_daily_digest` (07:20 site time) tell the buyers
  what newly fell below, by email and by the Desk bell.

The judgements are ``reorder_rules`` (pure, bench-free tested). This module reads with ERPNext's
own helpers where there is one -- ``get_items_for_reorder`` for the rules (its filters: Item
enabled, a stock item, not past end of life; variants inherit their template's rules) and
``get_item_warehouse_projected_qty`` for the projected qty rolled up the warehouse tree -- so the
board and auto-indent cannot disagree about what is below. The rest are plain reads with bound
parameters. Every write goes through a document: a Material Request inserted as the person, a
Reorder Watch row through its controller. No SQL writes.

**State lives in the database.** ``Reorder Watch`` holds one row per item and warehouse that is
below its point: since when, whether the digest has named it, and its snooze. The digest is a
cron job whose rows are the memory, so a deploy's redis FLUSHDB that drops the queued job costs
nothing: Frappe re-enqueues a due job that is not in the queue (``Scheduled Job Type.enqueue``
checks ``is_job_in_queue`` and only stamps ``last_execution`` when the job starts), and anything
not yet named is still un-named tomorrow.

Tabs, per ``CLAUDE.md`` (new file).
"""

import frappe
from frappe import _
from frappe.utils import add_days, cint, get_url, getdate, now_datetime, nowdate

from erpnext_enhancements.inventory_enhancements import reorder_rules as rules
from erpnext_enhancements.inventory_enhancements.doctype.reorder_watch.reorder_watch import watch_name

#: Who may open the board, snooze a line and ask for a request. The buyers (Purchase Manager,
#: Purchase User), the stock manager who owns the reorder rules, and System Manager. Not Stock
#: User: a technician or clerk takes and counts stock, and choosing what to buy is not theirs.
#: Parker holds all four of Purchase Manager, Purchase User, Stock Manager and Stock User.
BOARD_ROLES = ("Purchase Manager", "Purchase User", "Stock Manager", "System Manager")

WATCH = "Reorder Watch"
SETTINGS = "Inventory Scanner Settings"
ROLE_FIELD = "reorder_alert_role"
DEFAULT_ROLE = "Purchase Manager"
PAGE = "reorder-board"
PAGE_PATH = "/desk/reorder-board"

#: A MariaDB named lock around Material Request creation, so a double click cannot make two.
LOCK_NAME = "erpnext_enhancements.reorder_board.create_requests"
LOCK_TIMEOUT_SECONDS = 10

#: Rows in the digest email before it says "and n more" and points at the board.
DIGEST_ROW_LIMIT = 60

DIGEST_TITLE = "Reorder Board: daily digest"


class BoardError(Exception):
	"""A refusal with a sentence for the person, raised before anything is written."""


# --- who may ---------------------------------------------------------------------------


def has_board_role(user=None):
	roles = set(frappe.get_roles(user or frappe.session.user))
	return bool(roles.intersection(BOARD_ROLES))


def require_board_role():
	if not has_board_role():
		frappe.throw(_("The Reorder Board is for Purchasing and Stock Managers."), frappe.PermissionError)


def permissions():
	return {
		"can_create": bool(frappe.has_permission("Material Request", "create")),
		"can_submit": bool(frappe.has_permission("Material Request", "submit")),
		"can_snooze": bool(frappe.has_permission(WATCH, "write"))
		and bool(frappe.has_permission(WATCH, "create")),
		"can_see_orders": bool(frappe.has_permission("Purchase Order", "read")),
	}


# --- reads -----------------------------------------------------------------------------


def _reorder_item():
	from erpnext.stock import reorder_item

	return reorder_item


def _in(values):
	"""A tuple for ``IN %(x)s`` that is never empty (``IN ()`` is a syntax error)."""
	values = tuple(v for v in values if v)
	return values or ("",)


def read_rules():
	"""``(rules, projected_map)``: every Purchase reorder rule ERPNext's job would consider."""
	reorder_item = _reorder_item()
	items = reorder_item.get_items_for_reorder() or {}
	if not items:
		return [], {}
	warehouse_company = dict(frappe.db.sql("select name, company from `tabWarehouse` where disabled = 0"))
	projected_map = reorder_item.get_item_warehouse_projected_qty(items) or {}

	rule_rows = []
	for item_code, rows in items.items():
		for d in rows:
			if cint(d.get("has_variants")):
				continue
			# A disabled warehouse is skipped, as _reorder_item skips it.
			if d.get("warehouse") not in warehouse_company:
				continue
			# The board asks for purchases. Every rule on production is Purchase (2026-10-22);
			# a Transfer or Manufacture rule would want another kind of request.
			if (d.get("material_request_type") or "Purchase") != "Purchase":
				continue
			rule_rows.append(
				{
					"item_code": item_code,
					"warehouse": d.get("warehouse"),
					"warehouse_group": d.get("warehouse_group") or None,
					"company": warehouse_company.get(d.get("warehouse")),
					"reorder_level": rules.flt(d.get("warehouse_reorder_level")),
					"reorder_qty": rules.flt(d.get("warehouse_reorder_qty")),
					"lead_time_days": cint(d.get("lead_time_days")),
				}
			)
	return rule_rows, projected_map


def _item_details(item_codes, include_rates):
	"""Name, units, default supplier and last purchase rate for each rule's item."""
	codes = _in(item_codes)
	items = {
		r["name"]: r
		for r in frappe.db.sql(
			"""select name, item_name, stock_uom, purchase_uom, last_purchase_rate
			from `tabItem` where name in %(codes)s""",
			{"codes": codes},
			as_dict=True,
		)
	}
	suppliers = {}
	for r in frappe.db.sql(
		"""select parent, company, default_supplier from `tabItem Default`
		where parenttype = 'Item' and parent in %(codes)s and ifnull(default_supplier, '') != ''""",
		{"codes": codes},
		as_dict=True,
	):
		suppliers[(r["parent"], r["company"])] = r["default_supplier"]
	factors = {
		(r["parent"], r["uom"]): rules.flt(r["conversion_factor"])
		for r in frappe.db.sql(
			"""select parent, uom, conversion_factor from `tabUOM Conversion Detail`
			where parenttype = 'Item' and parent in %(codes)s""",
			{"codes": codes},
			as_dict=True,
		)
	}
	uoms = {u for r in items.values() for u in (r.get("stock_uom"), r.get("purchase_uom")) if u}
	whole = {
		r["name"]
		for r in frappe.db.sql(
			"select name from `tabUOM` where must_be_whole_number = 1 and name in %(uoms)s",
			{"uoms": _in(uoms)},
			as_dict=True,
		)
	}
	out = {}
	for code, r in items.items():
		stock_uom = r.get("stock_uom")
		purchase_uom = r.get("purchase_uom") or stock_uom
		factor = 1.0
		if purchase_uom != stock_uom:
			factor = factors.get((code, purchase_uom)) or 1.0
		out[code] = {
			"item_name": r.get("item_name"),
			"stock_uom": stock_uom,
			"purchase_uom": purchase_uom,
			"conversion_factor": factor,
			"stock_uom_whole": stock_uom in whole,
			"purchase_uom_whole": purchase_uom in whole,
			"last_purchase_rate": rules.flt(r.get("last_purchase_rate")) if include_rates else None,
		}
	return out, suppliers


def _on_hand(item_codes):
	return {
		rules.line_key(r["item_code"], r["warehouse"]): rules.flt(r["actual_qty"])
		for r in frappe.db.sql(
			"select item_code, warehouse, actual_qty from `tabBin` where item_code in %(codes)s",
			{"codes": _in(item_codes)},
			as_dict=True,
		)
	}


def read_tracking(item_codes, rule_keys, today):
	"""The open requests and orders for the rules' items, as :func:`reorder_rules.track_lines`."""
	codes = _in(item_codes)
	since = add_days(today, -rules.RECEIVED_VISIBLE_DAYS - 1)
	requests = frappe.db.sql(
		"""select mri.name, mri.parent, mri.item_code, mri.warehouse, mri.stock_qty, mri.schedule_date,
			mr.docstatus, mr.status, mr.transaction_date, mr.company
		from `tabMaterial Request Item` mri
		inner join `tabMaterial Request` mr on mr.name = mri.parent
		where mr.docstatus < 2 and mr.material_request_type = 'Purchase'
			and mr.status not in %(closed)s
			and mri.item_code in %(codes)s
			and (mr.status != 'Received' or mr.modified >= %(since)s)""",
		{"codes": codes, "closed": tuple(rules.CLOSED_REQUEST_STATUSES), "since": since},
		as_dict=True,
	)
	requests = [r for r in requests if rules.line_key(r["item_code"], r["warehouse"]) in rule_keys]

	stage_column = ""
	if frappe.db.has_column("Purchase Order", "custom_order_stage"):
		stage_column = ", po.custom_order_stage as order_stage"
	orders = frappe.db.sql(
		f"""select poi.name, poi.parent, poi.item_code, poi.warehouse, poi.stock_qty, poi.schedule_date,
			poi.material_request_item, po.docstatus, po.status, po.supplier, po.supplier_name{stage_column}
		from `tabPurchase Order Item` poi
		inner join `tabPurchase Order` po on po.name = poi.parent
		where po.docstatus < 2 and (
			poi.material_request_item in %(mr_items)s
			or (ifnull(poi.material_request_item, '') = '' and poi.item_code in %(codes)s
				and po.status not in %(closed)s
				and (po.per_received < 100 or po.modified >= %(since)s))
		)""",
		{
			"mr_items": _in(r["name"] for r in requests),
			"codes": codes,
			"closed": tuple(rules.CLOSED_ORDER_STATUSES),
			"since": since,
		},
		as_dict=True,
	)
	request_names = {r["name"] for r in requests}
	orders = [
		o
		for o in orders
		if (o.get("material_request_item") in request_names)
		or (
			not o.get("material_request_item") and rules.line_key(o["item_code"], o["warehouse"]) in rule_keys
		)
	]
	receipts = frappe.db.sql(
		"""select pri.parent, pri.purchase_order_item, pri.stock_qty, pr.docstatus, pr.posting_date
		from `tabPurchase Receipt Item` pri
		inner join `tabPurchase Receipt` pr on pr.name = pri.parent
		where pr.docstatus < 2 and pri.purchase_order_item in %(po_items)s""",
		{"po_items": _in(o["name"] for o in orders)},
		as_dict=True,
	)
	return rules.track_lines(requests, orders, receipts, today)


def read_watches():
	rows = frappe.get_all(
		WATCH,
		fields=[
			"name",
			"item_code",
			"warehouse",
			"below_since",
			"alerted_on",
			"snooze_kind",
			"snoozed_until",
			"snooze_projected_qty",
			"snoozed_by",
			"snoozed_on",
			"snooze_reason",
			"snooze_ended_on",
			"last_request",
			"last_request_ref",
		],
		limit_page_length=0,
	)
	return {rules.line_key(r["item_code"], r["warehouse"]): r for r in rows}


def build_board(include_rates=False, now=None):
	"""Everything the page shows, and the lists the digest and the request endpoint use."""
	now = now or now_datetime()
	today = getdate(nowdate())
	rule_rows, projected_map = read_rules()
	codes = sorted({r["item_code"] for r in rule_rows})
	details, suppliers = _item_details(codes, include_rates)
	on_hand = _on_hand(codes)
	for r in rule_rows:
		d = details.get(r["item_code"]) or {}
		r.update(d)
		r["on_hand"] = on_hand.get(rules.line_key(r["item_code"], r["warehouse"]), 0.0)
		r["default_supplier"] = suppliers.get((r["item_code"], r.get("company")))
	keys = {rules.line_key(r["item_code"], r["warehouse"]) for r in rule_rows}
	tracked = read_tracking(codes, keys, today)
	watches = read_watches()
	board = rules.assemble(rule_rows, projected_map, tracked, watches, now)
	names = {r["item_code"]: r.get("item_name") for r in rule_rows}
	uoms = {r["item_code"]: r.get("stock_uom") for r in rule_rows}
	for t in tracked:
		t["item_name"] = names.get(t["item_code"])
		t["stock_uom"] = uoms.get(t["item_code"])
	board["on_order"] = tracked
	board["watches"] = watches
	return board


def auto_indent_on():
	try:
		return bool(cint(frappe.db.get_single_value("Stock Settings", "auto_indent")))
	except Exception:
		return False


# --- the page's read -------------------------------------------------------------------

_LINE_FIELDS = (
	"key",
	"item_code",
	"item_name",
	"warehouse",
	"warehouse_group",
	"company",
	"on_hand",
	"projected_qty",
	"reorder_level",
	"reorder_qty",
	"suggested_qty",
	"stock_uom",
	"purchase_uom",
	"conversion_factor",
	"stock_uom_whole",
	"purchase_uom_whole",
	"default_supplier",
	"last_purchase_rate",
	"lead_time_days",
	"below_since",
	"snooze",
	"snooze_kind",
	"snoozed_until",
	"snooze_projected_qty",
	"snoozed_by",
	"snoozed_on",
	"snooze_reason",
)


def page_data():
	"""The page's whole read (``api.reorder_board.get_board``)."""
	perms = permissions()
	board = build_board(include_rates=perms["can_see_orders"])
	on_order = board["on_order"]
	if not perms["can_see_orders"]:
		# The stage still shows; which order, from whom, does not.
		for t in on_order:
			t["orders"] = [
				{
					"name": None,
					"supplier_name": None,
					"expected": o.get("expected"),
					"arrival": o.get("arrival"),
					"docstatus": o.get("docstatus"),
				}
				for o in t["orders"]
			]

	def slim(line):
		return {k: line.get(k) for k in _LINE_FIELDS}

	return {
		"needs": [slim(l) for l in board["needs"]],
		"snoozed": [slim(l) for l in board["snoozed"]],
		"on_order": on_order,
		"rules": len(board["lines"]),
		"permissions": perms,
		"auto_indent_on": auto_indent_on(),
		"snooze_kinds": list(rules.SNOOZE_KINDS),
		"max_qty": rules.MAX_QTY,
		"stage_labels": rules.STAGE_LABELS,
		"generated_at": now_datetime(),
	}


# --- Material Requests -----------------------------------------------------------------


def _parse_lines(lines):
	if isinstance(lines, str):
		lines = frappe.parse_json(lines)
	if not isinstance(lines, list) or not lines:
		raise BoardError(_("Choose at least one item."))
	if len(lines) > rules.MAX_LINES:
		raise BoardError(_("Ask for at most {0} items at a time.").format(rules.MAX_LINES))
	out, seen = [], set()
	for row in lines:
		if not isinstance(row, dict):
			raise BoardError(_("The list of items could not be read. Reload the page."))
		key = rules.line_key(row.get("item_code"), row.get("warehouse"))
		if key in seen:
			continue
		seen.add(key)
		out.append(
			{
				"key": key,
				"item_code": row.get("item_code"),
				"warehouse": row.get("warehouse"),
				"qty": row.get("qty"),
			}
		)
	return out


def _lock():
	got = frappe.db.sql("select get_lock(%s, %s)", (LOCK_NAME, LOCK_TIMEOUT_SECONDS))
	return bool(got and got[0] and cint(got[0][0]) == 1)


def _unlock():
	try:
		frappe.db.sql("select release_lock(%s)", (LOCK_NAME,))
	except Exception:
		# The lock is the connection's: it goes with it at the end of the request anyway.
		pass


def _replay(client_ref, watches):
	names = sorted(
		{
			w.get("last_request")
			for w in watches.values()
			if w.get("last_request_ref") == client_ref and w.get("last_request")
		}
	)
	return [n for n in names if frappe.db.exists("Material Request", n)]


def create_requests(lines, client_ref):
	"""Draft Material Requests (type Purchase) for the picked lines, one per company.

	Returns ``{ok, requests: [{name, company, lines}], replayed}`` or ``{ok: False, message,
	stale: [...]}``. Re-checks everything on the server: the person may create Material Requests,
	each line still has a rule, is still at or below its point, and no open request or order (a
	Draft included) covers it. Idempotent: the call is serialised by a named lock, and a retry
	with the same ``client_ref`` (a double click, or a retry after a lost reply) returns the
	request it already made. Each request is inserted as the person, so ERPNext's own validation
	and permissions apply; nothing is submitted (TASK-2026-02925: the buyer reviews and submits
	it on the form).
	"""
	require_board_role()
	if not frappe.has_permission("Material Request", "create"):
		frappe.throw(_("You may not create Material Requests."), frappe.PermissionError)
	if not rules.valid_client_ref(client_ref):
		raise BoardError(_("This request could not be identified. Reload the page and try again."))
	picked = _parse_lines(lines)

	if not _lock():
		raise BoardError(_("Another request is being made right now. Try again in a moment."))
	try:
		# A fresh snapshot: whatever the call before this one committed (its Material Request)
		# must be visible here, and REPEATABLE READ would otherwise keep this transaction's view.
		frappe.db.commit()
		board = build_board()
		replayed = _replay(client_ref, board["watches"])
		if replayed:
			return {"ok": True, "replayed": True, "requests": [{"name": n} for n in replayed]}

		by_key = {l["key"]: l for l in board["lines"]}
		stale, built = [], []
		for row in picked:
			line = by_key.get(row["key"])
			label = f"{row.get('item_code')} ({row.get('warehouse')})"
			if not line:
				stale.append({"key": row["key"], "reason": _("{0} has no reorder rule now.").format(label)})
				continue
			if line.get("covered"):
				stale.append(
					{
						"key": row["key"],
						"reason": _("{0} is already on {1}.").format(
							label, ", ".join(n for n in line["covered_by"] if n)
						),
					}
				)
				continue
			if not line.get("below"):
				stale.append(
					{
						"key": row["key"],
						"reason": _("{0} is no longer at or below its reorder point.").format(label),
					}
				)
				continue
			qty, error = rules.clean_qty(row.get("qty"), line.get("stock_uom_whole"))
			if error:
				stale.append({"key": row["key"], "reason": f"{label}: {_(error)}"})
				continue
			built.append((line, qty))
		if stale:
			return {
				"ok": False,
				"message": _("Nothing was requested. The board has changed since it was loaded:"),
				"stale": stale,
			}

		today = getdate(nowdate())
		by_company = {}
		for line, qty in built:
			by_company.setdefault(line.get("company"), []).append((line, qty))
		made = []
		for company, entries in sorted(by_company.items(), key=lambda kv: kv[0] or ""):
			mr = frappe.new_doc("Material Request")
			mr.update(
				{
					"company": company,
					"material_request_type": "Purchase",
					"transaction_date": today,
				}
			)
			for line, qty in entries:
				uom = line.get("purchase_uom") or line.get("stock_uom")
				factor = line.get("conversion_factor") or 1.0
				mr.append(
					"items",
					{
						"item_code": line["item_code"],
						"item_name": line.get("item_name"),
						"schedule_date": rules.schedule_date(today, line.get("lead_time_days")),
						"qty": rules.order_qty(qty, factor, line.get("purchase_uom_whole")),
						"uom": uom,
						"stock_uom": line.get("stock_uom"),
						"conversion_factor": factor,
						"warehouse": line["warehouse"],
						"reorder_qty": line.get("reorder_qty"),
						"reorder_level": line.get("reorder_level"),
						"projected_on_hand": line.get("projected_qty"),
					},
				)
			mr.schedule_date = max(d.schedule_date for d in mr.get("items"))
			mr.insert()
			made.append({"name": mr.name, "company": company, "lines": len(entries)})
			for line, _qty in entries:
				_remember_request(board["watches"].get(line["key"]), line, mr.name, client_ref)
		frappe.db.commit()
		return {"ok": True, "replayed": False, "requests": made}
	finally:
		_unlock()


def _ensure_watch(item_code, warehouse, fields):
	"""Write ``fields`` on the line's watch row, making the row if there is none.

	The row's name is the item and warehouse, so the hourly sync inserting the same row a moment
	earlier is a DuplicateEntryError here, and the write lands on its row instead.
	"""
	name = watch_name(item_code, warehouse)
	if not frappe.db.exists(WATCH, name):
		try:
			frappe.get_doc(
				{
					"doctype": WATCH,
					"item_code": item_code,
					"warehouse": warehouse,
					"below_since": now_datetime(),
					**fields,
				}
			).insert(ignore_permissions=True)
			return
		except frappe.DuplicateEntryError:
			pass
	frappe.db.set_value(WATCH, name, fields, update_modified=False)


def _remember_request(watch, line, request, client_ref):
	"""Note the request on the line's watch row, so a retry with this ``client_ref`` replays it."""
	_ensure_watch(
		line["item_code"], line["warehouse"], {"last_request": request, "last_request_ref": client_ref}
	)


# --- snoozes ---------------------------------------------------------------------------


def _line_for(item_code, warehouse):
	board = build_board()
	key = rules.line_key(item_code, warehouse)
	line = next((l for l in board["lines"] if l["key"] == key), None)
	return line, board["watches"].get(key)


def snooze(item_code, warehouse, kind, reason=None):
	"""Park a line that is below its point: it moves to Snoozed and the digest skips it."""
	require_board_role()
	line, watch = _line_for(item_code, warehouse)
	if not line or not line.get("below"):
		raise BoardError(_("This item is not below its reorder point now. Reload the board."))
	now = now_datetime()
	fields, error = rules.snooze_fields(kind, line.get("projected_qty"), now, reason, frappe.session.user)
	if error:
		raise BoardError(_(error))
	# As the person: Reorder Watch write (and create, for a line the hourly sync has not seen yet).
	if watch and watch.get("name"):
		doc = frappe.get_doc(WATCH, watch["name"])
		doc.update(fields)
		doc.save()
	else:
		doc = frappe.get_doc(
			{"doctype": WATCH, "item_code": item_code, "warehouse": warehouse, "below_since": now, **fields}
		)
		doc.insert()
	return {"ok": True, "key": line["key"], "snooze_kind": kind}


def unsnooze(item_code, warehouse):
	"""Bring a snoozed line back to Needs reorder. Not named again by the digest: you just saw it."""
	require_board_role()
	key = rules.line_key(item_code, warehouse)
	name = frappe.db.get_value(WATCH, {"item_code": item_code, "warehouse": warehouse}, "name", order_by=None)
	if not name:
		return {"ok": True, "key": key}
	doc = frappe.get_doc(WATCH, name)
	doc.update(rules.cleared_snooze())
	doc.save()
	return {"ok": True, "key": key}


# --- the hourly sync and the morning digest -------------------------------------------


def _apply_sync(board, now):
	inserts, updates, deletes = rules.sync_plan(board["lines"], board["watches"], now)
	for row in inserts:
		if frappe.db.exists(WATCH, watch_name(row["item_code"], row["warehouse"])):
			continue
		frappe.get_doc(
			{
				"doctype": WATCH,
				"item_code": row["item_code"],
				"warehouse": row["warehouse"],
				"below_since": row["below_since"],
			}
		).insert(ignore_permissions=True)
	for key, fields in updates:
		frappe.db.set_value(WATCH, board["watches"][key]["name"], fields, update_modified=False)
	for key in deletes:
		# No Deleted Document copy: the row is alert state, and it goes every time an item recovers.
		frappe.delete_doc(
			WATCH,
			board["watches"][key]["name"],
			ignore_permissions=True,
			ignore_missing=True,
			delete_permanently=True,
		)
	return len(inserts), len(updates), len(deletes)


def sync_watch():
	"""Hourly: record what is below since when, end snoozes that have run out, forget what recovered.

	Never raises (a scheduler job that raises is only a Scheduled Job Log row nobody reads).
	"""
	try:
		now = now_datetime()
		_apply_sync(build_board(now=now), now)
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Reorder Board: hourly sync failed", message=frappe.get_traceback())


def alert_role():
	"""The role whose people get the digest: Inventory Scanner Settings, else Purchase Manager."""
	try:
		role = frappe.db.get_single_value(SETTINGS, ROLE_FIELD)
	except Exception:
		role = None
	return (role or "").strip() or DEFAULT_ROLE


def recipients():
	"""Enabled System Users holding the alert role, minus Administrator, Guest and Triton."""
	role = alert_role()
	users = frappe.db.sql(
		"""select distinct u.name, u.email, u.enabled, u.user_type
		from `tabUser` u
		inner join `tabHas Role` hr on hr.parent = u.name and hr.parenttype = 'User'
		where hr.role = %(role)s and u.enabled = 1
		order by u.name""",
		{"role": role},
		as_dict=True,
	)
	return rules.filter_recipients(users)


def send_daily_digest():
	"""07:20 site time: what newly fell below since the last digest, to the alert role's people.

	One email (the app's design system) to all of them and one bell notification each, both
	pointing at the board. Nothing new, nothing sent. A line is stamped ``alerted_on`` in the
	same transaction that queues the email (Email Queue is a table), so it is named exactly once.
	Never raises.
	"""
	try:
		now = now_datetime()
		board = build_board(now=now)
		_apply_sync(board, now)
		watches = read_watches()
		newly, ended = rules.digest_pick(board["needs"], watches)
		if not newly and not ended:
			return
		people = recipients()
		if not people:
			return
		_send(newly, ended, len(board["needs"]), people)
		for line in newly + ended:
			frappe.db.set_value(
				WATCH,
				watches[line["key"]]["name"],
				{"alerted_on": now, "snooze_ended_on": None},
				update_modified=False,
			)
		frappe.db.commit()
	except Exception:
		frappe.db.rollback()
		frappe.log_error(title="Reorder Board: daily digest failed", message=frappe.get_traceback())


def _qty(value):
	number = rules.flt(value)
	return f"{number:,.0f}" if abs(number - round(number)) < 1e-9 else f"{number:,.2f}"


def digest_rows(lines, url_for):
	return [
		[
			(url_for(l["item_code"]), l["item_code"]),
			l.get("item_name") or "",
			l.get("warehouse") or "",
			_qty(l.get("projected_qty")),
			_qty(l.get("reorder_level")),
			f"{_qty(l.get('suggested_qty'))} {l.get('stock_uom') or ''}".strip(),
		]
		for l in lines
	]


def _send(newly, ended, total_needing, people):
	from frappe.utils import get_url_to_form

	from erpnext_enhancements import email_style

	board_url = get_url(PAGE_PATH)
	count = len(newly) + len(ended)
	headers = [_("Item"), _("Item Name"), _("Warehouse"), _("Projected"), _("Reorder at"), _("Suggested")]
	url_for = lambda code: get_url_to_form("Item", code)  # noqa: E731

	body = email_style.kpis(
		[
			{"label": _("Newly below"), "value": str(len(newly)), "tone": "warning"},
			{"label": _("Snooze ended"), "value": str(len(ended))},
			{"label": _("Need reordering"), "value": str(total_needing)},
		]
	)
	body += email_style.p(
		_(
			"These items are at or below their reorder point and no open Material Request or Purchase "
			"Order covers them. Nothing has been requested automatically: choose what to request on the "
			"Reorder Board, or snooze what can wait."
		)
	)
	shown = 0
	for heading, lines in ((_("Newly below their reorder point"), newly), (_("Snooze ended"), ended)):
		if not lines:
			continue
		room = max(DIGEST_ROW_LIMIT - shown, 0)
		if not room:
			break
		body += email_style.h(heading)
		body += email_style.table(headers, digest_rows(lines[:room], url_for))
		shown += min(len(lines), room)
	if count > shown:
		body += email_style.note(_("And {0} more on the board.").format(count - shown))
	body += email_style.button(board_url, _("Open the Reorder Board"))

	subject = _("{0} item(s) fell below their reorder point").format(count)
	frappe.sendmail(
		recipients=people,
		subject=subject,
		message=email_style.wrap(body, title=_("Items to reorder"), eyebrow=format_today()),
	)
	for user in people:
		frappe.get_doc(
			{
				"doctype": "Notification Log",
				"for_user": user,
				"type": "Alert",
				"subject": subject,
				"email_content": _("Open the Reorder Board to request them or snooze them."),
				"link": PAGE_PATH,
			}
		).insert(ignore_permissions=True)


def format_today():
	from frappe.utils import format_date

	return format_date(nowdate())
