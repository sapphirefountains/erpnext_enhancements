# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""The Reorder Board's suggested reorder levels: the reads and the one write (v1.832.0).

The 2026-11-19 operations review: set reorder points from real usage, approved item by item. What
counts as used, the basis and the suggestion are ``reorder_suggest_rules`` (pure, bench-free tested);
this module reads and writes:

* :func:`page_data` is the **Reorder levels** tab's read (``api.reorder_board.get_reorder_suggestions``,
  the board's roles): every Purchase reorder rule ERPNext's job would consider (the board's own
  ``read_rules``), each with its suggestion and basis, and the items used in the window that have no
  rule. Plain SQL with bound parameters behind the board's role gate, as the rest of the board reads.
  The ledger is read per item code, which ERPNext's ``item_code, warehouse, posting_datetime`` index
  serves; the rule items' whole ledger is read, because a count's change is its balance minus the
  balance before it, which can sit before the window.
* :func:`apply` writes one rule's Reorder at and Buy (``api.reorder_board.apply_reorder_suggestion``,
  POST): the person holds one of ``APPLY_ROLES`` (Purchase Manager, Stock Manager, System Manager), the
  Item is checked for their own write permission, and the change is one ``Item.save()`` as them, so
  ERPNext validates it and the Item's version history records who changed it. Only an existing rule
  is changed: a suggestion is never a new rule. A page older than the Item writes nothing.

  **Who that is, in practice.** ERPNext gives Item write to Item Manager alone, and production's
  Custom DocPerms keep it so (Stock Manager has write 0). So Apply needs Item Manager *and* one of the
  apply roles: 14 people on 2026-11-19, not counting Administrator and the service account. The page
  says so to anyone else, and no permission is changed here.
* **A variant's rule lives on its template.** ERPNext's reorder job gives a variant with no rule of its
  own its template's rules (``get_reorder_levels_for_variants``), and the board lists them under the
  variant. Changing one changes it for every variant of that template, which is not an item-by-item
  approval, so such a line is marked with its template and has no Apply, and :func:`apply` refuses it
  in those words.

Tabs, per ``CLAUDE.md`` (new file).
"""

import frappe
from frappe import _
from frappe.utils import cstr, getdate, nowdate

from erpnext_enhancements.inventory_enhancements import reorder_board as board
from erpnext_enhancements.inventory_enhancements import reorder_rules
from erpnext_enhancements.inventory_enhancements import reorder_suggest_rules as rules

PURCHASE = "Purchase"


# --- who may ---------------------------------------------------------------------------


def has_apply_role(user=None):
	return bool(set(frappe.get_roles(user or frappe.session.user)).intersection(rules.APPLY_ROLES))


def can_apply():
	"""Whether the page offers Apply: an apply role and Item write. Each save checks the Item again."""
	return has_apply_role() and bool(frappe.has_permission("Item", "write"))


# --- reads -----------------------------------------------------------------------------


def _not_shop():
	"""``(sites, not_shop)``: the job-site warehouses, and those plus the Project Shelf. Never raises:
	a site without either feature has none of them, and everything is the shop."""
	from erpnext_enhancements.inventory_enhancements import job_sites, project_shelf

	try:
		sites = set(job_sites.site_warehouses())
	except Exception:
		sites = set()
	try:
		not_shop = set(project_shelf.not_shop_warehouses())
	except Exception:
		not_shop = set(sites)
	return sites, not_shop | sites


def tracking_since():
	"""The first day this site recorded a take (Material Issue), or ``None`` if it never has."""
	rows = frappe.db.sql(
		"""select min(sle.posting_date)
		from `tabStock Ledger Entry` sle
		join `tabStock Entry` se on se.name = sle.voucher_no
		where sle.voucher_type = 'Stock Entry' and se.purpose = 'Material Issue' and sle.is_cancelled = 0"""
	)
	return rules.as_date(rows[0][0]) if rows and rows[0] else None


def used_codes(start, today):
	"""Items with a line in the window that could be a use (a take, a delivery, a move out)."""
	rows = frappe.db.sql(
		"""select distinct sle.item_code
		from `tabStock Ledger Entry` sle
		left join `tabStock Entry` se on se.name = sle.voucher_no and sle.voucher_type = 'Stock Entry'
		where sle.is_cancelled = 0 and sle.actual_qty < 0
			and sle.posting_date between %(start)s and %(today)s
			and (se.purpose in ('Material Issue', 'Material Transfer', 'Material Transfer for Manufacture')
				or sle.voucher_type in ('Delivery Note', 'Sales Invoice', 'POS Invoice'))""",
		{"start": start, "today": today},
	)
	return {r[0] for r in rows or () if r and r[0]}


def read_ledger(item_codes, today):
	"""``{item_code: [rows in ledger order]}``: every live ledger row of these items up to today, with
	the Stock Entry's purpose beside it."""
	if not item_codes:
		return {}
	rows = frappe.db.sql(
		"""select sle.item_code, sle.warehouse, sle.actual_qty, sle.qty_after_transaction,
			sle.posting_date, sle.voucher_type, sle.voucher_no, sle.project, se.purpose
		from `tabStock Ledger Entry` sle
		left join `tabStock Entry` se on se.name = sle.voucher_no and sle.voucher_type = 'Stock Entry'
		where sle.is_cancelled = 0 and sle.item_code in %(codes)s and sle.posting_date <= %(today)s
		order by sle.posting_datetime, sle.creation""",
		{"codes": board._in(sorted(item_codes)), "today": today},
		as_dict=True,
	)
	out = {}
	for row in rows or ():
		out.setdefault(row.get("item_code"), []).append(row)
	return out


def _item_stamps(item_codes):
	"""``{item_code: {modified, lead_time_days, variant_of}}``: the page's stale check, the lead time, and
	the template a variant's rule may come from."""
	if not item_codes:
		return {}
	return {
		r["name"]: r
		for r in frappe.db.sql(
			"select name, modified, lead_time_days, variant_of from `tabItem` where name in %(codes)s",
			{"codes": board._in(sorted(item_codes))},
			as_dict=True,
		)
	}


def _own_rules(item_codes):
	"""``{(item_code, warehouse)}``: the rules each item holds itself. A rule the board lists that is not
	here is a variant's, inherited from its template."""
	if not item_codes:
		return set()
	return {
		(r[0], r[1])
		for r in frappe.db.sql(
			"""select parent, warehouse from `tabItem Reorder`
			where parenttype = 'Item' and parent in %(codes)s""",
			{"codes": board._in(sorted(item_codes))},
		)
		or ()
	}


def _line(item_code, details, stamp, usage, start, days, rule=None, inherited_from=None):
	whole = bool(details.get("stock_uom_whole"))
	lead = (rule or {}).get("lead_time_days")
	if lead is None:
		lead = stamp.get("lead_time_days")
	suggestion = rules.suggest(usage["used"], usage["uses"], days, lead, whole)
	line = {
		"item_code": item_code,
		"item_name": details.get("item_name") or "",
		"stock_uom": details.get("stock_uom") or "",
		"whole": whole,
		"modified": cstr(stamp.get("modified")),
		"basis_start": start,
		"basis_days": days,
		"used": usage["used"],
		"uses": usage["uses"],
		"returned": usage["returned"],
		"counted_down": usage["counted_down"],
		"counted_up": usage["counted_up"],
		"suggestion": suggestion,
	}
	if rule is not None:
		line.update(
			{
				"key": reorder_rules.line_key(item_code, rule.get("warehouse")),
				# A variant's rule set on its template: shown, never applied from here.
				"inherited_from": inherited_from,
				"warehouse": rule.get("warehouse"),
				"warehouse_group": rule.get("warehouse_group"),
				"reorder_level": rule.get("reorder_level"),
				"reorder_qty": rule.get("reorder_qty"),
				"direction": rules.direction(rule.get("reorder_level"), suggestion.get("level")),
				"matches": bool(
					suggestion.get("enough")
					and rules.direction(rule.get("reorder_level"), suggestion.get("level"))
					== rules.DIRECTION_SAME
					and rules.direction(rule.get("reorder_qty"), suggestion.get("qty"))
					== rules.DIRECTION_SAME
				),
			}
		)
	return line


def build(days=None, today=None):
	"""Every rule with its suggestion, and the used items with no rule."""
	window = rules.clean_window(days)
	today = today or getdate(nowdate())
	first_day = rules.window_start(today, window)
	rule_rows, _projected = board.read_rules()
	ancestors = board.read_ancestors()
	sites, not_shop = _not_shop()
	ruled = {r["item_code"] for r in rule_rows}
	candidates = used_codes(first_day, today) - ruled
	codes = sorted(ruled | candidates)
	details, _suppliers = board._item_details(codes, False)
	stamps = _item_stamps(codes)
	own = _own_rules(sorted(ruled))
	ledger = read_ledger(codes, today)
	since = tracking_since()

	def in_rule_shop(rule):
		return lambda w: bool(w) and w not in not_shop and reorder_rules.in_scope(w, rule, ancestors)

	lines = []
	for rule in rule_rows:
		code = rule["item_code"]
		stamp = stamps.get(code) or {}
		usage = rules.measure(ledger.get(code), in_rule_shop(rule), sites, not_shop, first_day, since, today)
		inherited = None
		if (code, rule.get("warehouse")) not in own:
			inherited = stamp.get("variant_of") or None
		lines.append(
			_line(
				code,
				details.get(code) or {},
				stamp,
				usage,
				usage["basis_start"],
				usage["basis_days"],
				rule,
				inherited,
			)
		)

	def in_any_shop(w):
		return bool(w) and w not in not_shop

	unruled = []
	for code in sorted(candidates):
		usage = rules.measure(ledger.get(code), in_any_shop, sites, not_shop, first_day, since, today)
		if usage["used"] <= 0:
			continue
		unruled.append(
			_line(
				code,
				details.get(code) or {},
				stamps.get(code) or {},
				usage,
				usage["basis_start"],
				usage["basis_days"],
			)
		)

	lines.sort(key=lambda l: (not l["suggestion"]["enough"], l["item_code"], l.get("warehouse") or ""))
	unruled.sort(key=lambda l: (-l["used"], l["item_code"]))
	return {
		"window": window,
		"window_start": first_day,
		"today": today,
		"tracking_since": since,
		"lines": lines,
		"unruled": unruled,
	}


def page_data(days=None):
	"""The Reorder levels tab's whole read."""
	data = build(days)
	data.update(
		{
			"window_choices": list(rules.WINDOW_CHOICES),
			"min_uses": rules.MIN_USES,
			"min_days": rules.MIN_DAYS,
			"safety_days": rules.SAFETY_DAYS,
			"cover_days": rules.COVER_DAYS,
			"default_lead_days": rules.DEFAULT_LEAD_DAYS,
			"max_qty": rules.MAX_QTY,
			"can_apply": can_apply(),
		}
	)
	return data


# --- the write -------------------------------------------------------------------------


def _whole(stock_uom):
	if not stock_uom:
		return False
	rows = frappe.db.sql(
		"select must_be_whole_number from `tabUOM` where name = %(uom)s", {"uom": stock_uom}, as_dict=True
	)
	return bool(rows and int(rules.flt(rows[0].get("must_be_whole_number"))))


def apply(item_code, warehouse, modified, level, qty):
	"""Set the reorder rule of ``item_code`` on ``warehouse`` to ``level`` and ``qty``.

	A refusal the person can act on is a ``board.BoardError`` (the endpoint answers ``{ok: False,
	message}``); a missing role or Item permission raises ``frappe.PermissionError``. Returns
	``{ok, saved, message, item_code, warehouse, reorder_level, reorder_qty, modified}``; ``saved`` is
	False when the rule already had those numbers.
	"""
	board.require_board_role()
	if not has_apply_role():
		frappe.throw(
			_("Only a Purchase Manager, a Stock Manager or a System Manager may change reorder rules here."),
			frappe.PermissionError,
		)
	code = cstr(item_code).strip()
	warehouse = cstr(warehouse).strip()
	if not code or not warehouse:
		raise board.BoardError(_("Choose an item and its reorder rule."))
	if not frappe.db.exists("Item", code):
		raise board.BoardError(_("There is no Item {0}.").format(code))
	item = frappe.get_doc("Item", code)
	item.check_permission("write")
	new_level, new_qty, error = rules.clean_rule(level, qty, _whole(item.get("stock_uom")))
	if error:
		raise board.BoardError(_(error))
	if not rules.same_stamp(item.get("modified"), modified):
		raise board.BoardError(
			_(
				"{0} was changed by someone else since this page read it. Nothing was saved; reload the "
				"page to see its current rule."
			).format(code)
		)
	target = next(
		(
			row
			for row in item.get("reorder_levels") or []
			if row.get("warehouse") == warehouse
			and (row.get("material_request_type") or PURCHASE) == PURCHASE
		),
		None,
	)
	if target is None:
		template = cstr(item.get("variant_of")).strip()
		if template:
			# ERPNext gives a variant with no rule of its own its template's rules; the board lists them
			# under the variant. Changing the template's rule changes it for every variant.
			raise board.BoardError(
				_(
					"{0} takes its reorder rule from its template, {1}. Change the rule on {1}; it applies to "
					"every variant of it."
				).format(code, template)
			)
		raise board.BoardError(
			_("{0} has no reorder rule on {1} any more. Reload the page.").format(code, warehouse)
		)
	changed = False
	for field, value in (("warehouse_reorder_level", new_level), ("warehouse_reorder_qty", new_qty)):
		if abs(rules.flt(target.get(field)) - value) > 1e-9:
			target.set(field, value)
			changed = True
	if changed:
		item.save()
	return {
		"ok": True,
		"saved": changed,
		"message": _("Reorder rule saved.") if changed else _("The rule already had those numbers."),
		"item_code": code,
		"warehouse": warehouse,
		"reorder_level": new_level,
		"reorder_qty": new_qty,
		"modified": cstr(item.get("modified")),
	}
