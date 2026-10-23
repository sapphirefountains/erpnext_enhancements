"""Whitelisted endpoints for the Count Desk (v1.737.0): physical counts and audits on a laptop.

The Desk page ``inventory_enhancements/page/count_desk`` calls these. It answers four questions the
phone page (Inventory Scanner Audit, ``api.inventory_scanner``) was never built to answer from a
keyboard: *what should be counted next* (the warehouse tree with each bin's stock, value and last
count), *what is on this shelf* (a sheet of every item the bins in a count hold, so nothing has to be
scanned to appear), *what is wrong* (the variances, by value) and *post it* (Stock Reconciliations
of at most 100 rows). Counts live in the same ``Inventory Count Session`` the phone writes, so a count
taken on the phone opens here too; a Count Desk count carries a ``scope``, the bins it covers.

Every judgement is in ``inventory_enhancements/count_desk_rules.py`` (no Frappe, tested bench-free);
this module reads and writes. The things it is careful about:

* **The sheet lists what the phone lists.** Each bin's expected items are
  ``api.stock_scan._items_at`` (on hand, assigned, its default, on order, not yet decided), called
  with ``limit=None``: the phone caps a location at 150 items, and a count that silently stopped at
  150 would leave the rest of ``Stores - SF`` uncounted.
* **What posts is the variance, applied to what is on hand now** (``count_desk_rules.post_plan``),
  so a take or a receipt after the shelf was counted survives the count. A count posted since by
  somebody else supersedes this one for that item and bin.
* **A paper count is counted against the shelf as it was** (v1.738.0). With the session's Counted At set, a
  line's snapshot is the stock ledger as of then (:func:`_system_at`, ERPNext's ``get_previous_sle``)
  and its ``scan_time`` is then, not the moment it was typed in. Blank is a live count.
* **Every reconciliation submits in the request that posts it** (v1.738.0). ERPNext v16 queues the submit of
  one with more than 100 rows and returns it a draft; a failed job reverts it and a deploy's redis
  flush drops it. v1.737.0 said "Posted" and finalized the count anyway, so a retry answered
  ``repeated`` and the count never posted. A post is split
  (``count_desk_rules.batches``), each one is checked submitted before anything is said, and every
  name is kept on the session (``stock_reconciliations``).
* **Two saves of one count never race.** ``save_counts`` and ``post_count`` lock the session row
  (``select ... for update``) before they load it: the page saves as people type, and a second save
  loading the same ``modified`` would fail ``check_if_latest`` after the first committed.
* **Posting is the framework's call.** With Stock Reconciliation create **and** submit the post is
  submitted as the user, no ``ignore_permissions``, as the Bin Manager's count is. Without them the
  page offers a **draft** for a Stock Manager (inserted with ``ignore_permissions`` after the role
  gate, the phone page's rule since v1.29.0: the clerk counts, the Stock Manager decides), and the
  draft's posting time is fixed at the moment it was drafted (``set_posting_time``), so a draft
  submitted tomorrow does not wipe out today's takes and receipts: ERPNext reposts them on top.
* **A post is idempotent.** A retry after a dropped reply finds the session Finalized and gets the
  first post back with ``repeated``; nothing posts twice.

Access: ``count_desk_rules.COUNT_ROLES`` (the phone page's counting roles). A count belongs to
whoever started it; ``SUPERVISOR_ROLES`` may open and act on anyone's. The session and its lines are
written with ``ignore_permissions`` after that gate, as the phone page does. Reads are GET, writes
POST.
"""

import json

import frappe
from frappe import _
from frappe.utils import (
	add_days,
	cint,
	cstr,
	flt,
	get_datetime,
	get_url_to_form,
	now_datetime,
	nowdate,
	nowtime,
)

from erpnext_enhancements.api import stock_scan as scan
from erpnext_enhancements.inventory_enhancements import count_desk_rules as rules
from erpnext_enhancements.inventory_enhancements import stock_scan_rules as scan_rules
from erpnext_enhancements.inventory_enhancements.doctype.inventory_scanner_settings.inventory_scanner_settings import (
	get_settings,
)

SESSION = "Inventory Count Session"
LINE = "Inventory Count Line"

#: Finalized counts the plan screen lists.
RECENT_COUNTS = 15

#: Rows one save may carry: a pasted column of a long shelf, not an import.
SAVE_LIMIT = 500


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def _check_access():
	if not rules.COUNT_ROLES.intersection(frappe.get_roles()):
		frappe.throw(_("You are not permitted to use the Count Desk."), frappe.PermissionError)


def _is_supervisor():
	return bool(rules.SUPERVISOR_ROLES.intersection(frappe.get_roles()))


def _can_post():
	return bool(
		frappe.has_permission("Stock Reconciliation", "create")
		and frappe.has_permission("Stock Reconciliation", "submit")
	)


def _session(name, editable=False):
	"""The session, if the caller may see it; ``editable`` also requires it to be Open."""
	name = cstr(name).strip()
	if not name or not frappe.db.exists(SESSION, name):
		frappe.throw(
			_("There is no count {0}.").format(name or _("with that name")), frappe.DoesNotExistError
		)
	doc = frappe.get_doc(SESSION, name)
	if doc.counted_by != frappe.session.user and not _is_supervisor():
		frappe.throw(
			_("This count belongs to {0}.").format(scan._full_name(doc.counted_by)), frappe.PermissionError
		)
	if editable and doc.status != "Open":
		frappe.throw(_("Count {0} is {1}; it can no longer be changed.").format(doc.name, _(doc.status)))
	return doc


def _lock(name):
	"""Hold the session row until this request commits, so two saves of one count run one after
	the other instead of both loading the same ``modified``."""
	frappe.db.sql(f"select name from `tab{SESSION}` where name = %s for update", (cstr(name).strip(),))


def _json_list(value, limit=None):
	if isinstance(value, str):
		try:
			value = json.loads(value)
		except ValueError:
			frappe.throw(_("The request could not be read."))
	if not isinstance(value, list):
		frappe.throw(_("The request could not be read."))
	if limit and len(value) > limit:
		frappe.throw(_("One save can carry {0} rows. Save fewer at a time.").format(limit))
	return value


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _warehouses():
	return frappe.get_all(
		"Warehouse",
		fields=[
			"name",
			"warehouse_name",
			"parent_warehouse",
			"company",
			"is_group",
			"disabled",
			"warehouse_type",
			"lft",
			"rgt",
		],
		order_by="lft asc",
	)


def _trail(row, groups):
	if row.get("lft") is None:
		return ""
	names = [
		g.warehouse_name or g.name
		for g in groups
		if g.lft is not None and cint(g.lft) < cint(row.lft) and cint(g.rgt) > cint(row.rgt)
	]
	return scan_rules.location_trail(names)


def _bin_stats():
	"""Per warehouse: how many items are on hand and what the stock is worth. One grouped query
	(Frappe 16 refuses ``count(...)`` as a string field in ``get_all``)."""
	rows = frappe.db.sql(
		"""
		select warehouse,
			sum(case when actual_qty > 0 then 1 else 0 end) as on_hand_items,
			coalesce(sum(stock_value), 0) as stock_value
		from `tabBin`
		group by warehouse
		""",
		as_dict=True,
	)
	return {row.warehouse: row for row in rows}


def _last_counted():
	"""Per warehouse, the last day a count was recorded there: a submitted Stock Reconciliation in
	the stock ledger (the Operations KPI's definition, so the two agree), or a finalized count's
	line. The second matters because a count where everything matched posts nothing to the ledger,
	and was still a count."""
	ledger = frappe.db.sql(
		"""
		select warehouse, max(posting_date) as last
		from `tabStock Ledger Entry`
		where voucher_type = 'Stock Reconciliation' and is_cancelled = 0
		group by warehouse
		""",
		as_dict=True,
	)
	counts = frappe.db.sql(
		f"""
		select l.warehouse, max(l.scan_time) as last
		from `tab{LINE}` l
		inner join `tab{SESSION}` s on s.name = l.parent
		where l.parenttype = %(parenttype)s and s.status = 'Finalized'
		group by l.warehouse
		""",
		{"parenttype": SESSION},
		as_dict=True,
	)
	out = {}
	for row in list(ledger) + list(counts):
		if row.warehouse:
			out[row.warehouse] = rules.latest(out.get(row.warehouse), row.last)
	return out


def _full_names(users):
	users = [u for u in set(users or []) if u]
	if not users:
		return {}
	rows = frappe.get_all("User", filters={"name": ["in", users]}, fields=["name", "full_name"])
	return {row.name: row.full_name or row.name for row in rows}


def _line_counts(names):
	"""``{session: lines}`` for the open counts the plan screen lists."""
	if not names:
		return {}
	rows = frappe.db.sql(
		f"select parent, count(*) as n from `tab{LINE}` where parenttype = %(pt)s and parent in %(names)s group by parent",
		{"pt": SESSION, "names": tuple(names)},
		as_dict=True,
	)
	return {row.parent: cint(row.n) for row in rows}


def _lines_of(names):
	"""``{session: [line, ...]}``, the fields the history needs."""
	if not names:
		return {}
	rows = frappe.get_all(
		LINE,
		filters={"parenttype": SESSION, "parent": ["in", list(names)]},
		fields=["parent", "item_code", "warehouse", "counted_qty", "system_qty", "scan_time"],
	)
	out = {}
	for row in rows:
		out.setdefault(row.parent, []).append(row)
	return out


def _open_counts(supervisor):
	filters = {"status": "Open"}
	if not supervisor:
		filters["counted_by"] = frappe.session.user
	rows = frappe.get_all(
		SESSION,
		filters=filters,
		fields=["name", "count_title", "counted_by", "start_time", "modified", "scope", "blind_count"],
		order_by="modified desc",
		limit=100,
	)
	lines = _line_counts([row.name for row in rows])
	who = _full_names([row.counted_by for row in rows])
	return [
		{
			"name": row.name,
			"title": row.count_title or "",
			"counted_by": row.counted_by,
			"counted_by_name": who.get(row.counted_by) or row.counted_by,
			"mine": row.counted_by == frappe.session.user,
			"start_time": cstr(row.start_time) or None,
			"modified": cstr(row.modified) or None,
			"scope": rules.parse_scope(row.scope),
			"blind": bool(cint(row.blind_count)),
			"lines": lines.get(row.name, 0),
			"from_phone": not rules.parse_scope(row.scope),
		}
		for row in rows
	]


def _recent_counts(supervisor):
	filters = {"status": "Finalized"}
	if not supervisor:
		filters["counted_by"] = frappe.session.user
	rows = frappe.get_all(
		SESSION,
		filters=filters,
		fields=[
			"name",
			"count_title",
			"counted_by",
			"start_time",
			"end_time",
			"stock_reconciliation",
			"stock_reconciliations",
			"scope",
		],
		order_by="end_time desc",
		limit=RECENT_COUNTS,
	)
	lines = _lines_of([row.name for row in rows])
	who = _full_names([row.counted_by for row in rows])
	made = {row.name: _made(row) for row in rows}
	sr = _reconciliation_rows([name for names in made.values() for name in names])
	out = []
	for row in rows:
		matched, counted = rules.accuracy(lines.get(row.name, []))
		recs = [sr[name] for name in made[row.name] if name in sr]
		out.append(
			{
				"name": row.name,
				"title": row.count_title or "",
				"counted_by_name": who.get(row.counted_by) or row.counted_by,
				"end_time": cstr(row.end_time) or None,
				"items": counted,
				"matched": matched,
				"accuracy": round(matched * 100.0 / counted, 1) if counted else None,
				"stock_reconciliation": made[row.name][0] if made[row.name] else None,
				"stock_reconciliations": made[row.name],
				"sr_status": rules.combined_status(_sr_status(rec) for rec in recs),
				"difference": sum(flt(rec.difference_amount) for rec in recs),
				"bins": len(rules.parse_scope(row.scope)),
			}
		)
	return out


def _made(doc):
	"""Every reconciliation a session made: ``stock_reconciliations``, with the older single link."""
	return rules.reconciliation_names(doc.get("stock_reconciliations"), doc.get("stock_reconciliation"))


def _reconciliation_rows(names):
	"""``{name: {name, docstatus, difference_amount}}`` for these Stock Reconciliations."""
	names = sorted({name for name in names or [] if name})
	if not names:
		return {}
	return {
		r.name: r
		for r in frappe.get_all(
			"Stock Reconciliation",
			filters={"name": ["in", names]},
			fields=["name", "docstatus", "difference_amount"],
		)
	}


def _sr_status(rec):
	if not rec:
		return None
	return {0: "Draft", 1: "Submitted", 2: "Cancelled"}.get(cint(rec.docstatus), "Draft")


def _stats(bins):
	"""The plan screen's four numbers, over the last ``COVERAGE_DAYS``."""
	today = nowdate()
	since = add_days(today, -rules.COVERAGE_DAYS)
	cover = rules.coverage(bins, today)
	sessions = frappe.get_all(
		SESSION,
		filters={"status": "Finalized", "end_time": [">=", since]},
		fields=["name", "stock_reconciliation", "stock_reconciliations"],
	)
	names = [row.name for row in sessions]
	all_lines = []
	for lines in _lines_of(names).values():
		all_lines.extend(lines)
	matched, counted = rules.accuracy(all_lines)
	recs = sorted({name for row in sessions for name in _made(row)})
	value = None
	if recs:
		value = frappe.db.sql(
			"""
			select coalesce(sum(difference_amount), 0)
			from `tabStock Reconciliation`
			where docstatus = 1 and name in %(names)s
			""",
			{"names": tuple(recs)},
		)
	return {
		"coverage": cover,
		"accuracy": round(matched * 100.0 / counted, 1) if counted else None,
		"items_counted": counted,
		"counts": len(names),
		"adjusted_value": flt(value[0][0]) if value else 0.0,
		"days": rules.COVERAGE_DAYS,
	}


@frappe.whitelist(methods=["GET"])
def boot():
	"""What the Count Desk needs before it draws anything.

	* ``groups`` / ``bins`` -- the warehouse tree: every group (``name``, ``label``, ``parent``) and
	  every countable bin (a leaf, enabled, not Transit) with its parent, trail, items on hand, stock
	  value and ``last_counted`` (:func:`_last_counted`). The page builds the tree and its roll-ups.
	* ``open_counts`` -- the caller's open counts (everyone's, for a supervisor), phone ones included.
	* ``recent_counts`` -- the last finalized ones, with accuracy and the reconciliation.
	* ``stats`` -- coverage, accuracy and adjustments over the last 90 days.
	* ``can_post`` -- Stock Reconciliation create and submit; without it the page drafts.
	* ``reasons`` / ``require_reason`` -- the reason codes, and whether a variance needs one to post.
	"""
	_check_access()
	settings = get_settings()
	supervisor = _is_supervisor()
	warehouses = _warehouses()
	group_rows = [w for w in warehouses if cint(w.is_group)]
	stats = _bin_stats()
	last = _last_counted()
	today = nowdate()
	groups = [
		{"name": w.name, "label": w.warehouse_name or w.name, "parent": w.parent_warehouse or None}
		for w in group_rows
	]
	bins = []
	for w in warehouses:
		if not rules.countable(w):
			continue
		stat = stats.get(w.name) or {}
		bins.append(
			{
				"warehouse": w.name,
				"warehouse_name": w.warehouse_name or w.name,
				"parent": w.parent_warehouse or None,
				"company": w.company,
				"trail": _trail(w, group_rows),
				"on_hand_items": cint(stat.get("on_hand_items")),
				"stock_value": flt(stat.get("stock_value")),
				"last_counted": last.get(w.name),
				"days_since": rules.days_since(last.get(w.name), today),
			}
		)
	bins.sort(key=lambda b: scan_rules.natural_key(b["warehouse_name"]))
	return {
		"user": frappe.session.user,
		"today": today,
		"supervisor": supervisor,
		"can_post": _can_post(),
		"groups": groups,
		"bins": bins,
		"open_counts": _open_counts(supervisor),
		"recent_counts": _recent_counts(supervisor),
		"stats": _stats(bins),
		"reasons": list(rules.REASON_CODES),
		"require_reason": bool(cint(settings.get("require_variance_reason"))),
		"coverage_days": rules.COVERAGE_DAYS,
		"max_scope": rules.MAX_SCOPE,
	}


def _items(codes):
	"""``{item_code: {...}}`` with what a sheet row needs, ``whole_number`` from the stock UOM."""
	codes = [c for c in set(codes or []) if c]
	if not codes:
		return {}
	rows = frappe.get_all(
		"Item",
		filters={"name": ["in", codes]},
		fields=[
			"name",
			"item_name",
			"stock_uom",
			"disabled",
			"is_stock_item",
			"has_serial_no",
			"has_batch_no",
			"has_variants",
			"valuation_rate",
		],
	)
	uoms = {row.stock_uom for row in rows if row.stock_uom}
	whole = set()
	if uoms:
		whole = set(
			frappe.get_all(
				"UOM", filters={"name": ["in", list(uoms)], "must_be_whole_number": 1}, pluck="name"
			)
		)
	return {row.name: dict(row, whole_number=row.stock_uom in whole) for row in rows}


def _balances(pairs):
	"""``{(item_code, warehouse): {actual_qty, reserved, valuation_rate}}`` from ``tabBin``."""
	pairs = {p for p in pairs or [] if p[0] and p[1]}
	if not pairs:
		return {}
	rows = frappe.get_all(
		"Bin",
		filters={
			"item_code": ["in", list({p[0] for p in pairs})],
			"warehouse": ["in", list({p[1] for p in pairs})],
		},
		fields=["item_code", "warehouse", "actual_qty", "reserved_stock", "valuation_rate"],
	)
	return {
		(row.item_code, row.warehouse): {
			"actual_qty": flt(row.actual_qty),
			"reserved": flt(row.reserved_stock),
			"valuation_rate": flt(row.valuation_rate),
		}
		for row in rows
		if (row.item_code, row.warehouse) in pairs
	}


def _system_at(pairs, when):
	"""``{(item_code, warehouse): qty}``: what the system showed at each pair at ``when``.

	``when`` is a session's Counted At: the stock ledger as of then, through ERPNext's own
	``get_previous_sle`` (the last entry at or before it; nothing yet is 0). Not
	``erpnext.stock.utils.get_stock_balance``, whose body is this same call: it is whitelisted and
	asks for Item read, which Inventory Clerk does not hold on production (checked 2026-10-23), and
	this page's role gate has already run. ``None`` is a live count: ``tabBin`` now."""
	pairs = {p for p in pairs or [] if p[0] and p[1]}
	if not pairs:
		return {}
	if not when:
		return {pair: balance["actual_qty"] for pair, balance in _balances(pairs).items()}
	from erpnext.stock.stock_ledger import get_previous_sle

	when = get_datetime(when)
	out = {}
	for item_code, warehouse in sorted(pairs):
		sle = get_previous_sle(
			{
				"item_code": item_code,
				"warehouse": warehouse,
				"posting_date": when.strftime("%Y-%m-%d"),
				"posting_time": when.strftime("%H:%M:%S"),
			}
		)
		out[(item_code, warehouse)] = flt((sle or {}).get("qty_after_transaction"))
	return out


def _counted_at(doc):
	"""The session's Counted At as a datetime, or ``None`` for a live count."""
	return rules.to_datetime(doc.get("counted_at"))


def _line_dicts(doc):
	return [
		{
			"idx": line.idx,
			"storage_location": line.storage_location,
			"warehouse": line.warehouse,
			"item_code": line.item_code,
			"item_name": line.item_name,
			"uom": line.uom,
			"system_qty": flt(line.system_qty),
			"counted_qty": flt(line.counted_qty),
			"reason_code": line.get("reason_code") or "",
			"reason": line.reason or "",
			"recount": cint(line.get("recount")),
			"scan_time": line.scan_time,
			"counted_by": line.get("counted_by") or None,
		}
		for line in doc.lines
	]


def _expected(scope, by_name):
	"""``{warehouse: listing}`` for each bin in scope: ``api.stock_scan._items_at``, uncapped."""
	out = {}
	for name in scope:
		row = by_name.get(name)
		if not row or not rules.countable(row):
			continue
		out[name] = scan._items_at(row, limit=None)[0]
	return out


def _header(doc, warehouses=None):
	warehouses = warehouses if warehouses is not None else _warehouses()
	by_name = {w.name: w for w in warehouses}
	group_rows = [w for w in warehouses if cint(w.is_group)]
	scope = rules.parse_scope(doc.get("scope"))
	made = _made(doc)
	sr = _reconciliation_rows(made)
	recs = [sr[name] for name in made if name in sr]
	counted_at = _counted_at(doc)
	mine = doc.counted_by == frappe.session.user
	return {
		"name": doc.name,
		"title": doc.get("count_title") or "",
		"status": doc.status,
		"counted_by": doc.counted_by,
		"counted_by_name": scan._full_name(doc.counted_by),
		"mine": mine,
		"start_time": cstr(doc.start_time) or None,
		"end_time": cstr(doc.end_time) or None,
		"counted_at": counted_at.strftime("%Y-%m-%d %H:%M:%S") if counted_at else None,
		"blind": bool(cint(doc.get("blind_count"))),
		"notes": doc.notes or "",
		"scoped": bool(scope),
		"scope": [
			{
				"warehouse": name,
				"warehouse_name": (by_name.get(name) or {}).get("warehouse_name") or name,
				"trail": _trail(by_name[name], group_rows) if name in by_name else "",
			}
			for name in scope
		],
		"stock_reconciliation": made[0] if made else None,
		"stock_reconciliations": made,
		"sr_status": rules.combined_status(_sr_status(rec) for rec in recs),
		"difference": sum(flt(rec.difference_amount) for rec in recs),
		"editable": doc.status == "Open" and (mine or _is_supervisor()),
	}


def _sheet_payload(doc):
	warehouses = _warehouses()
	by_name = {w.name: w for w in warehouses}
	scope = rules.parse_scope(doc.get("scope"))
	lines = _line_dicts(doc)
	expected = _expected(scope, by_name) if doc.status == "Open" else {}
	codes = {line["item_code"] for line in lines}
	pairs = {(line["item_code"], line["warehouse"]) for line in lines}
	counted_pairs = set(pairs)
	for warehouse, listing in expected.items():
		for row in listing:
			codes.add(row["item_code"])
			pairs.add((row["item_code"], warehouse))
	names = {w.name: w.warehouse_name or w.name for w in warehouses}
	# Under a Counted At an uncounted row shows, and is counted against, the ledger as of then; a
	# counted row's snapshot already is that.
	counted_at = _counted_at(doc) if doc.status == "Open" else None
	as_of = _system_at(pairs - counted_pairs, counted_at) if counted_at else None
	rows = rules.sheet_rows(expected, lines, _balances(pairs), _items(codes), names, as_of=as_of)
	settings = get_settings()
	return {
		"count": _header(doc, warehouses),
		"rows": rows,
		"can_post": _can_post(),
		"require_reason": bool(cint(settings.get("require_variance_reason"))),
		"reasons": list(rules.REASON_CODES),
	}


@frappe.whitelist(methods=["GET"])
def get_sheet(session):
	"""One count's sheet: its header and every row (:func:`count_desk_rules.sheet_rows`). An open
	count lists every item its bins hold, counted or not; a finalized one lists what was counted."""
	_check_access()
	return _sheet_payload(_session(session))


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------


def _open_overlaps(bins, exclude=None):
	"""Other open counts covering any of ``bins``, as ``[{name, title, counted_by_name, bins}]``."""
	rows = frappe.get_all(
		SESSION,
		filters={"status": "Open", "scope": ["is", "set"]},
		fields=["name", "count_title", "counted_by", "scope"],
	)
	wanted = set(bins)
	who = _full_names([row.counted_by for row in rows])
	out = []
	for row in rows:
		if row.name == exclude:
			continue
		shared = [b for b in rules.parse_scope(row.scope) if b in wanted]
		if shared:
			out.append(
				{
					"name": row.name,
					"title": row.count_title or "",
					"counted_by_name": who.get(row.counted_by) or row.counted_by,
					"bins": len(shared),
				}
			)
	return out


@frappe.whitelist(methods=["POST"])
def start_count(warehouses, title=None, blind=0, counted_at=None):
	"""Start a count of ``warehouses`` (a JSON list of bins and groups; a group counts every bin
	under it). ``counted_at`` is when a paper count was walked (blank for a live count;
	:func:`count_desk_rules.check_counted_at`). Returns the new count's name, any other open count
	that covers the same bins (said, not refused: two people may split a big shelf), and any part of
	the choice that was left out."""
	_check_access()
	selected = _json_list(warehouses, limit=rules.MAX_SCOPE * 2)
	warehouse_rows = _warehouses()
	bins, problems = rules.expand_scope(selected, warehouse_rows)
	problem = rules.check_scope(bins, problems)
	if problem:
		frappe.throw(problem)
	now = now_datetime()
	when, problem = rules.check_counted_at(counted_at, now, now)
	if problem:
		frappe.throw(problem)
	by_name = {w.name: w for w in warehouse_rows}
	labels = [
		(by_name.get(name) or {}).get("warehouse_name") or name
		for name in rules.parse_scope(selected)
		if name in by_name
	]
	doc = frappe.get_doc(
		{
			"doctype": SESSION,
			"counted_by": frappe.session.user,
			"company": by_name[bins[0]].company,
			"status": "Open",
			"start_time": now,
			"count_title": rules.check_title(title) or rules.default_title(labels),
			"blind_count": 1 if cint(blind) else 0,
			"scope": rules.dump_scope(bins),
			"counted_at": when,
		}
	)
	doc.insert(ignore_permissions=True)
	return {
		"name": doc.name,
		"bins": len(bins),
		"problems": problems,
		"overlaps": _open_overlaps(bins, doc.name),
	}


@frappe.whitelist(methods=["POST"])
def update_count(session, title=None, blind=None, notes=None, add_warehouses=None, counted_at=None):
	"""Rename a count, switch blind counting, keep notes, set when it was counted, or add bins to
	it. Only the arguments sent change. A phone count has no bins to add to: it covers whatever is
	scanned.

	``counted_at`` sets the count's Counted At (``""`` clears it, back to a live count). A change
	re-reads the snapshot of every line already counted (:func:`_resnapshot`): the lines are a count
	of the shelf at that moment, so each is counted against what the system showed then. A phone
	count takes none: the phone keeps scanning into it, each scan snapshotted as it is made."""
	_check_access()
	_lock(session)
	doc = _session(session, editable=True)
	resnapshot = False
	if counted_at is not None:
		when, problem = rules.check_counted_at(
			counted_at, doc.get("creation") or doc.start_time, now_datetime()
		)
		if problem:
			frappe.throw(problem)
		if when and not rules.parse_scope(doc.get("scope")):
			frappe.throw(
				_("A count taken on the phone is counted as each item is scanned, so it has no Counted At.")
			)
		if when != _counted_at(doc):
			doc.counted_at = when
			resnapshot = True
	if title is not None:
		doc.count_title = rules.check_title(title) or doc.count_title
	if blind is not None:
		doc.blind_count = 1 if cint(blind) else 0
	if notes is not None:
		doc.notes = cstr(notes)[:5000]
	if add_warehouses:
		scope = rules.parse_scope(doc.scope)
		if not scope:
			frappe.throw(
				_(
					"This count was started on the phone and covers whatever is scanned. Start a new count to choose locations."
				)
			)
		bins, problems = rules.expand_scope(
			_json_list(add_warehouses, limit=rules.MAX_SCOPE * 2), _warehouses()
		)
		if not bins:
			frappe.throw(" ".join(problems) or _("Choose a location to add."))
		merged = rules.parse_scope(scope + bins)
		problem = rules.check_scope(merged, [])
		if problem:
			frappe.throw(problem)
		doc.scope = rules.dump_scope(merged)
	if resnapshot:
		_resnapshot(doc)
	doc.save(ignore_permissions=True)
	return _sheet_payload(doc) if add_warehouses or resnapshot else {"count": _header(doc)}


def _resnapshot(doc):
	"""Every counted line against the count's baseline: the ledger at its Counted At, or, cleared
	back to a live count, what the system shows now. ``scan_time`` follows, because a later
	reconciliation of the item supersedes a count by that time (:func:`_superseded`)."""
	when = _counted_at(doc)
	stamp = when or now_datetime()
	system = _system_at({(line.item_code, line.warehouse) for line in doc.lines}, when)
	for line in doc.lines:
		line.system_qty = flt(system.get((line.item_code, line.warehouse)))
		line.scan_time = stamp


def _find_line(doc, item_code, warehouse, storage_location):
	for line in doc.lines:
		if (
			line.item_code == item_code
			and line.warehouse == warehouse
			and (line.storage_location or None) == (storage_location or None)
		):
			return line
	return None


def _check_location(values, scope, cache):
	"""A sentence when a row's place cannot be counted, else ``None``. A scoped count's bins were
	checked when they were chosen; a phone count's row is checked here."""
	warehouse = values["warehouse"]
	if warehouse not in cache:
		row = scan._warehouse(warehouse)
		cache[warehouse] = None if scope and warehouse in scope else scan._location_problem(row, warehouse)
	if cache[warehouse]:
		return cache[warehouse]
	location = values.get("storage_location")
	if location:
		if frappe.db.get_value("Storage Location", location, "warehouse") != warehouse:
			return _("{0} is not a storage location in {1}.").format(location, warehouse)
	return None


@frappe.whitelist(methods=["POST"])
def save_counts(session, rows):
	"""Save rows of a count as they are typed. ``rows`` is a JSON list of
	``{key, counted_qty, reason_code, reason, recount}`` (:func:`count_desk_rules.check_entry`); a
	``counted_qty`` of ``null`` clears that count.

	A row whose count changes takes a fresh snapshot of what the system shows there and the time,
	as a scan does on the phone; a reason or a flag alone changes neither. Under a Counted At the
	snapshot is the ledger as of then and the time is then (a paper count typed in later). Each row
	is judged on its own and a refused one does not stop the rest (a pasted column with one typo
	saves the others).
	Returns each row's ``ok`` / ``message`` and, per (item, warehouse) touched, its rows as they now
	stand: a variance belongs to the item at the warehouse, so every line of it can change."""
	_check_access()
	entries = _json_list(rows, limit=SAVE_LIMIT)
	_lock(session)
	doc = _session(session, editable=True)
	scope = rules.parse_scope(doc.get("scope"))
	keys = [rules.split_key(entry.get("key")) if isinstance(entry, dict) else None for entry in entries]
	items = _items({k["item_code"] for k in keys if k})
	# One read of what the system shows, for every row this save carries a count for: the snapshot a
	# changed count takes is "now" (or the count's Counted At), the same moment for each of them.
	counted_at = _counted_at(doc)
	system = _system_at(
		{
			(k["item_code"], k["warehouse"])
			for entry, k in zip(entries, keys, strict=True)
			if k and isinstance(entry, dict) and entry.get("counted_qty") not in (None, "")
		},
		counted_at,
	)
	locations = {}
	results = []
	touched = set()
	now = counted_at or now_datetime()
	for entry, where in zip(entries, keys, strict=True):
		key = entry.get("key") if isinstance(entry, dict) else None
		item = items.get(where["item_code"]) if where else None
		values, problem = rules.check_entry(
			entry,
			scope,
			rules.item_refusal(item) if where else None,
			whole_number=bool(item and item.get("whole_number")),
			uom=(item or {}).get("stock_uom") or "",
		)
		if not problem:
			problem = _check_location(values, scope, locations)
		if problem:
			results.append({"key": key, "ok": False, "message": problem})
			continue
		line = _find_line(doc, values["item_code"], values["warehouse"], values.get("storage_location"))
		if values.get("clear"):
			if line:
				doc.lines = [row for row in doc.lines if row is not line]
				touched.add((values["item_code"], values["warehouse"]))
			results.append({"key": key, "ok": True, "cleared": True})
			continue
		if "counted_qty" in values:
			if not line:
				line = doc.append(
					"lines",
					{
						"item_code": values["item_code"],
						"warehouse": values["warehouse"],
						"storage_location": values.get("storage_location"),
						"item_name": item.get("item_name"),
						"uom": item.get("stock_uom"),
					},
				)
				line.counted_qty = None
			if (
				line.counted_qty is None
				or abs(flt(line.counted_qty) - values["counted_qty"]) > rules.TOLERANCE
			):
				line.system_qty = flt(system.get((values["item_code"], values["warehouse"])))
				line.scan_time = now
				line.counted_by = frappe.session.user
			line.counted_qty = values["counted_qty"]
		elif not line:
			results.append(
				{"key": key, "ok": False, "message": _("Count it first, then add a reason or a flag.")}
			)
			continue
		for field in ("reason_code", "reason", "recount"):
			if field in values:
				line.set(field, values[field])
		touched.add((values["item_code"], values["warehouse"]))
		results.append({"key": key, "ok": True})
	if touched:
		doc.save(ignore_permissions=True)
	return {"results": results, "groups": _group_rows(doc, touched), "modified": cstr(doc.modified)}


def _group_rows(doc, touched):
	"""The rows of each (item, warehouse) in ``touched`` as they stand after a save. Expected rows
	are the page's to keep: an uncounted, expected item comes back as no rows, and the page puts its
	uncounted row back."""
	if not touched:
		return []
	lines = [line for line in _line_dicts(doc) if (line["item_code"], line["warehouse"]) in touched]
	codes = {pair[0] for pair in touched}
	names = {}
	for pair in touched:
		names[pair[1]] = frappe.db.get_value("Warehouse", pair[1], "warehouse_name") or pair[1]
	as_of = {} if _counted_at(doc) else None
	rows = rules.sheet_rows({}, lines, _balances(touched), _items(codes), names, as_of=as_of)
	out = []
	for item_code, warehouse in sorted(touched):
		out.append(
			{
				"item_code": item_code,
				"warehouse": warehouse,
				"rows": [r for r in rows if r["item_code"] == item_code and r["warehouse"] == warehouse],
			}
		)
	return out


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------


def _superseded(group_map):
	"""The (item, warehouse) pairs with a submitted Stock Reconciliation posted after the group's
	last count: somebody else's count, or a Bin Manager correction, came later."""
	if not group_map:
		return set()
	items = sorted({key[0] for key in group_map})
	warehouses = sorted({key[1] for key in group_map})
	rows = frappe.db.sql(
		"""
		select item_code, warehouse, max(timestamp(posting_date, posting_time)) as last
		from `tabStock Ledger Entry`
		where voucher_type = 'Stock Reconciliation' and is_cancelled = 0
			and item_code in %(items)s and warehouse in %(warehouses)s
		group by item_code, warehouse
		""",
		{"items": tuple(items), "warehouses": tuple(warehouses)},
		as_dict=True,
	)
	out = set()
	for row in rows:
		group = group_map.get((row.item_code, row.warehouse))
		if not group or not row.last or not group.get("latest"):
			continue
		if get_datetime(row.last) > get_datetime(group["latest"]):
			out.add((row.item_code, row.warehouse))
	return out


def _reconciliations(doc, plan, draft):
	"""The unsaved Stock Reconciliations for ``plan["adjust"]``, as ``[(sr, rows)]``: one per
	(company, difference account), because a reconciliation has one of each, and each of those in
	runs of ``count_desk_rules.RECONCILIATION_ROWS`` so it submits in this request
	(:func:`post_count`). ``expense_account`` from the resolver every stock voucher here uses
	(``stock_accounts.difference_account``: production's Company has no Stock Adjustment Account,
	and ERPNext refuses a reconciliation without one)."""
	settings = get_settings()
	companies = {}
	for row in plan["adjust"]:
		company = frappe.get_cached_value("Warehouse", row["warehouse"], "company") or doc.company
		account = scan._difference_account(company, row["item_code"])
		companies.setdefault((company, account), []).append(row)
	out = []
	for (company, account), all_rows in companies.items():
		for rows in rules.batches(all_rows):
			sr = frappe.new_doc("Stock Reconciliation")
			sr.purpose = "Stock Reconciliation"
			sr.company = company
			sr.expense_account = account
			cost_center = scan._cost_center(settings.get("scan_cost_center"), company)
			if cost_center:
				sr.cost_center = cost_center
			if draft:
				# Posted as of now even if a Stock Manager submits it next week: ERPNext then reposts
				# whatever moved in between on top of it, instead of the draft overwriting it.
				sr.set_posting_time = 1
				sr.posting_date = nowdate()
				sr.posting_time = nowtime()
			for row in rows:
				item = {"item_code": row["item_code"], "warehouse": row["warehouse"], "qty": row["qty"]}
				if row.get("valuation_rate"):
					item["valuation_rate"] = row["valuation_rate"]
				sr.append("items", item)
			out.append((sr, rows))
	return out


def _post_result(doc, plan=None, posted=None, drafts=None, repeated=False):
	posted = posted or []
	drafts = drafts or []
	if repeated:
		message = _("This count was already posted.")
		made = _made(doc)
		if len(made) == 1:
			message += " " + _("Its reconciliation is {0}.").format(made[0])
		elif made:
			message += " " + _("Its reconciliations are {0}.").format(", ".join(made))
	else:
		message = rules.result_message(plan, posted, drafts)
	return {
		"message": message,
		"repeated": repeated,
		"posted": posted,
		"drafts": drafts,
		"adjusted": (plan or {}).get("adjust", []),
		"matched": len((plan or {}).get("matched", [])),
		"skipped": (plan or {}).get("skipped", []),
		"count": _header(doc),
	}


@frappe.whitelist(methods=["POST"])
def post_count(session, mode="post"):
	"""Finish a count and post its variances (:func:`count_desk_rules.post_plan`).

	``mode="post"`` submits the reconciliations as the user (Stock Reconciliation create and submit);
	``mode="draft"`` inserts them as drafts for a Stock Manager. There is one per company and
	difference account and per ``RECONCILIATION_ROWS`` rows, so a Stock Manager's submit of a draft
	is not sent to a background job either. Each gets a comment saying which count it came from, who
	counted it and why. Items that matched post nothing; items that cannot be posted are listed with
	the reason and kept in the count's notes. A count where nothing needed adjusting is finalized
	with no reconciliation at all, which ERPNext would otherwise refuse. Refused, posting nothing,
	while any variance lacks a reason the settings require.

	All of it is one request and one transaction: the count is finalized only once every
	reconciliation is submitted (or drafted), and a reconciliation that is not submitted when
	``submit`` returns refuses the whole post, so nothing is half posted and a retry posts afresh."""
	_check_access()
	mode = cstr(mode).strip() or "post"
	if mode not in ("post", "draft"):
		frappe.throw(_("Choose to post the count or to draft it."))
	_lock(session)
	doc = _session(session)
	if doc.status == "Finalized":
		return _post_result(doc, repeated=True)
	doc = _session(session, editable=True)
	if not doc.lines:
		frappe.throw(_("Nothing has been counted yet."))
	if mode == "post" and not _can_post():
		frappe.throw(
			_(
				"Posting a count needs Stock Reconciliation create and submit. Send it for review instead, and a Stock Manager submits the draft."
			),
			frappe.PermissionError,
		)
	group_map = rules.groups(_line_dicts(doc))
	items = _items({key[0] for key in group_map})
	refusals = {code: rules.item_refusal(items.get(code)) for code in {key[0] for key in group_map}}
	refusals = {code: why for code, why in refusals.items() if why}
	companies = {}

	def rate_for(item_code, warehouse):
		if warehouse not in companies:
			companies[warehouse] = frappe.get_cached_value("Warehouse", warehouse, "company") or doc.company
		return scan._receipt_rate(item_code, warehouse, companies[warehouse])

	plan = rules.post_plan(
		group_map,
		_balances(set(group_map)),
		refusals,
		_superseded(group_map),
		rate_for,
		require_reason=bool(cint(get_settings().get("require_variance_reason"))),
	)
	if plan["needs_reason"]:
		names = sorted({row["item_code"] for row in plan["needs_reason"]})
		frappe.throw(_("A variance reason is required before posting: {0}.").format(", ".join(names)))

	posted, drafts = [], []
	batches = _reconciliations(doc, plan, draft=mode == "draft")
	who = scan._full_name(doc.counted_by)
	link = get_url_to_form(SESSION, doc.name)
	for part, (sr, rows) in enumerate(batches, 1):
		if mode == "post":
			sr.insert()
		else:
			sr.insert(ignore_permissions=True)
		sr.add_comment(
			"Comment",
			rules.reconciliation_comment(
				doc.name, doc.get("count_title"), who, rows, link=link, part=part, parts=len(batches)
			),
		)
		if mode == "post":
			sr.submit()
			# A reconciliation of more than 100 rows is submitted by a background job, still a draft
			# here; none is that long (batches), and this refuses the post, rolling back every one,
			# rather than say "Posted" over a draft.
			if cint(sr.docstatus) != 1:
				frappe.throw(
					_(
						"{0} was not submitted when it was posted, so nothing was posted. Post the count again, or send it for review."
					).format(sr.name)
				)
			posted.append(sr.name)
		else:
			drafts.append(sr.name)

	made = posted or drafts
	notes = [doc.notes] if doc.notes else []
	if plan["skipped"]:
		notes.append(
			_("Not posted:")
			+ "\n"
			+ "\n".join(
				f"{row['item_code']} at {row['warehouse']}: {row['reason']}" for row in plan["skipped"]
			)
		)
	doc.db_set(
		{
			"status": "Finalized",
			"end_time": now_datetime(),
			"stock_reconciliation": made[0] if made else None,
			"stock_reconciliations": rules.dump_names(made) or None,
			"notes": "\n\n".join(notes)[:10000] if notes else doc.notes,
		}
	)
	return _post_result(doc, plan, posted, drafts)
