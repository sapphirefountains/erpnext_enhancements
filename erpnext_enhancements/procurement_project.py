"""Project attribution on purchasing lines (WI-014 follow-through).

``Purchase Order Item.project`` is mandatory here — a Property Setter fixture
(`Purchase Order Item-project-reqd`) turned it on so material *and* subcontract
cost lands on the job it was bought for. Two things made that requirement
painful in day-to-day purchasing, and this module fixes the first of them:

* **ERPNext does not push the Purchase Order's header ``project`` down to its
  item rows.** So a PO that plainly names its job on the header still refused to
  save until someone re-picked the same project on every line — on prod, 44 of
  the 204 lines sitting under a PO *with* a header project were blank.
  :func:`cascade_project_to_items` fills the blanks from the header.
* **Non-job spend had nowhere to land** (office supplies, shop consumables,
  fuel). That is solved by the five standing overhead projects seeded in
  ``patches.seed_overhead_projects`` — the purchaser picks one on the PO header
  and, thanks to the cascade above, every line inherits it.

Blank rows only, never an overwrite: a PO that legitimately spans two jobs keeps
whatever each line already says. The client mirrors this in
``public/js/purchase_order_project.js`` — that is the copy that matters in the
Desk, because the mandatory check runs in the browser before the request is ever
sent. This server copy covers the paths no form script sees: the REST API, data
import, and documents mapped in from a Material Request.

**Where a line is received** (v1.817.0, the owner's decision of 2026-11-12) is decided
from the same project. A line for a job defaults to the Project Shelf; a line for one of
the overhead buckets defaults to the item's own bin, and to the shop's general receiving
location when the item has no bin yet (:func:`default_line_warehouse`; the rule itself is
``inventory_enhancements/project_shelf_rules.line_warehouse``, tested bench-free). It is a
default, applied when a line first has both its item and its project: the purchaser can
pick another place on the order, and the person receiving can pick another place per line
(:func:`receiving_defaults`).

**One thing is held on every save, not just offered: an Overhead line never sits on the
Project Shelf.** ERPNext itself puts it there (the Company's default warehouse is the shelf
since the rename, and so are hundreds of Item Defaults), on every path this app's form
script never sees: a duplicated order, one mapped from a Supplier Quotation or a Material
Request, a REST save that sends a warehouse, a row added with Update Items, a receipt made
with Create > Purchase Receipt. Shop supplies received there would leave the shop's stock
under a project that is not a job. So the order's save (:func:`default_line_warehouses`), a
row added after submit (:func:`default_new_order_line`) and a new receipt
(:func:`default_receipt_warehouses`) each move such a line to the item's own bin. The form
does it sooner so the purchaser sees it; correctness does not depend on the form.
"""

import frappe
from frappe.utils import flt

#: Project Type that marks a project as a non-job overhead bucket. Deliberately
#: its own type rather than reusing ``Internal`` — the 13 Internal projects are
#: specific R&D jobs (IDP000-IDP016) and overhead must stay separable from R&D in
#: reporting. It also keeps these out of the Projects Dashboard, which lists only
#: Build/Design/Events/Service/Delivery.
OVERHEAD_PROJECT_TYPE = "Overhead"


def cascade_project_to_items(doc, method=None):
	"""``before_validate`` hook: fill blank item-row projects from the header.

	Runs before Frappe's mandatory-field check (``run_before_save_methods`` fires
	``before_validate`` ahead of ``_validate``), so a row left blank by an API
	caller is filled rather than rejected.
	"""
	project = (doc.get("project") or "").strip()
	if not project:
		return

	for row in doc.get("items") or []:
		if not (row.get("project") or "").strip():
			row.project = project
			# The line had no project until now, so nothing chose its location with one in
			# mind: it gets the project's default place (v1.817.0), unless a person picked one.
			_default_row_warehouse(doc, row, filled_by_cascade=True)


def is_overhead(project):
	"""Whether ``project`` is one of the standing overhead buckets (Project Type
	:data:`OVERHEAD_PROJECT_TYPE`). A blank or unknown project is not."""
	project = (project or "").strip()
	if not project:
		return False
	return frappe.db.get_value("Project", project, "project_type") == OVERHEAD_PROJECT_TYPE


def _log(title):
	"""Record a failure without ever becoming one: these run inside a Purchase Order's save."""
	try:
		frappe.log_error(title=title)
	except Exception:
		pass


def _scanner_settings():
	from erpnext_enhancements.inventory_enhancements.doctype.inventory_scanner_settings.inventory_scanner_settings import (
		get_settings,
	)

	return get_settings()


def _usable_warehouses(names, company=None):
	"""The names among ``names`` that are enabled leaf warehouses (of ``company`` when given),
	in the order given."""
	names = [name for name in names if name]
	if not names:
		return []
	filters = {"name": ["in", names], "is_group": 0, "disabled": 0}
	if company:
		filters["company"] = company
	found = {row.name for row in frappe.get_all("Warehouse", filters=filters, fields=["name"])}
	return [name for name in names if name in found]


def item_own_bin(item_code, company=None):
	"""The item's own bin, or ``None``: the bin it is assigned to (an enabled Putaway Rule;
	with several, the lowest Putaway priority, then the one holding the most, then by name;
	every rule this app writes has priority 1), else its Item Default warehouse,
	else the warehouse a reorder rule asks for. The Project Shelf, a job site and the
	boneyard are never an item's bin and are skipped wherever they appear
	(``project_shelf_rules.own_bin``).
	"""
	from erpnext_enhancements.inventory_enhancements import project_shelf, project_shelf_rules

	item_code = (item_code or "").strip()
	if not item_code:
		return None
	putaway = [
		row[0]
		for row in frappe.db.sql(
			"""
			select pr.warehouse
			from `tabPutaway Rule` pr
			left join `tabBin` b on b.item_code = pr.item_code and b.warehouse = pr.warehouse
			where pr.item_code = %(item)s and pr.disable = 0
			order by pr.priority asc, coalesce(b.actual_qty, 0) desc, pr.warehouse asc
			""",
			{"item": item_code},
		)
	]
	default_filters = {"parent": item_code, "parenttype": "Item"}
	if company:
		default_filters["company"] = company
	defaults = frappe.get_all(
		"Item Default", filters=default_filters, fields=["default_warehouse"], order_by="idx asc"
	)
	reorder = frappe.get_all(
		"Item Reorder",
		filters={"parent": item_code, "parenttype": "Item"},
		fields=["warehouse"],
		order_by="idx asc",
	)
	not_bins = set(project_shelf.not_shop_warehouses())
	boneyard = (_scanner_settings().get("boneyard_warehouse") or "").strip()
	if boneyard:
		not_bins.add(boneyard)
	candidates = _usable_warehouses(
		[*putaway, *(row.default_warehouse for row in defaults), *(row.warehouse for row in reorder)],
		company,
	)
	return project_shelf_rules.own_bin(candidates, not_bins=not_bins)


def default_line_warehouse(item_code, project, company=None):
	"""Where a purchase line for ``item_code`` on ``project`` is received by default:
	``{"warehouse": name or None, "reason": "shelf" | "own_bin" | "fallback" | "none"}``.

	A project that is not an overhead bucket: the Project Shelf. An overhead bucket: the
	item's own bin (:func:`item_own_bin`), else the shop's general receiving location
	(Inventory Scanner Settings). No project, or no Project Shelf set up: nothing
	(``None``, ``"none"``), and the line keeps what it has. **Never raises**: it runs inside
	a Purchase Order's save, and a failure there is logged and answered with "nothing".
	"""
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf, project_shelf_rules

		project = (project or "").strip()
		shelf = project_shelf.warehouse()
		if not project or not shelf:
			return {"warehouse": None, "reason": project_shelf_rules.REASON_NONE}
		overhead = is_overhead(project)
		own = item_own_bin(item_code, company) if overhead else None
		fallback = project_shelf.fallback_warehouse() if overhead and not own else None
		warehouse, reason = project_shelf_rules.line_warehouse(True, overhead, shelf, own, fallback)
		return {"warehouse": warehouse, "reason": reason}
	except Exception:
		_log("Purchase line location: the default could not be worked out")
		return {"warehouse": None, "reason": "none"}


def _item_default_warehouse(item_code, company):
	if not item_code:
		return None
	filters = {"parent": item_code, "parenttype": "Item"}
	if company:
		filters["company"] = company
	return frappe.db.get_value("Item Default", filters, "default_warehouse", order_by="idx asc")


def _default_row_warehouse(doc, row, filled_by_cascade=False):
	"""Give one draft Purchase Order line its default location, when nobody chose one.

	A line with no location always gets it. **An Overhead line that names the Project
	Shelf always gets it** (``project_shelf_rules.overhead_on_shelf``): that is never a
	person's choice, whatever put it there. A line whose project the cascade has just
	filled gets it too when its location is one nobody chose (the Project Shelf, or the
	item's Item Default: what ERPNext or a mapper put there). Otherwise a line that came
	from a Material Request keeps the request's location, and a location that is already
	there is left alone. Never raises.
	"""
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf, project_shelf_rules

		if doc.get("docstatus") or not (row.get("item_code") or "").strip():
			return
		current = (row.get("warehouse") or "").strip()
		company = doc.get("company")
		shelf = project_shelf.warehouse()
		project = (row.get("project") or "").strip()
		stranded = bool(
			current
			and shelf
			and current == shelf
			and project_shelf_rules.overhead_on_shelf(current, bool(project), is_overhead(project), shelf)
		)
		if current and not stranded:
			if not filled_by_cascade or (row.get("material_request_item") or "").strip():
				return
			automatic = (shelf, _item_default_warehouse(row.get("item_code"), company))
			if not project_shelf_rules.may_default(current, automatic):
				return
		answer = default_line_warehouse(row.get("item_code"), row.get("project"), company)
		if answer.get("warehouse") and answer["warehouse"] != current:
			row.warehouse = answer["warehouse"]
	except Exception:
		_log("Purchase line location: the default could not be applied")


def default_line_warehouses(doc, method=None):
	"""``before_validate`` hook, after :func:`cascade_project_to_items`: a draft line that
	has a project and no location gets the project's default place.

	The Desk does this itself as the line is filled in (``purchase_order_project.js``),
	where the purchaser can see it and change it. This is the copy for the paths with no
	form: the REST API, data import, a mapped document. It runs before ERPNext's own
	``set_missing_values``, which would otherwise fill the blank from the Item Default or
	the Company's default warehouse.

	A location that is already there is left alone, **except the one nobody chooses: an
	Overhead line on the Project Shelf** goes to the item's own bin on every save of a draft.
	That is the server's own guarantee. Without it the rule held only where the form's script
	ran in time: a duplicated order, one mapped from a Supplier Quotation or a Material
	Request, and a REST save that sends a warehouse all arrive with the shelf already filled
	in by ERPNext, and would save it. When the item has no bin and no fallback is set, there
	is nowhere better to send it and the line stays as it is.
	"""
	if doc.get("docstatus"):
		return
	for row in doc.get("items") or []:
		_default_row_warehouse(doc, row)


def _is_new(doc):
	try:
		return bool(doc.get("__islocal") or doc.is_new())
	except Exception:
		return False


def _erpnext_fills(item_code, company):
	"""The location ERPNext puts on a new order line for ``item_code`` by itself, as a tuple of
	at most a few names: what a row holds when nobody chose its location.

	**ERPNext's own function is asked** (``erpnext.stock.get_item_details.get_item_warehouse_``),
	never a copy of its order. It is the function Update Items itself calls for a row the dialog
	sent no warehouse for (``accounts_controller.get_new_child_item_warehouse``), and the one
	behind the value the dialog pre-fills when an item is picked. Read at erpnext v16.54.0, its
	order is the item's Item Default, the item group's default, the brand's default, then the
	Company's default warehouse, each only if the user's Warehouse permissions allow it. It is
	asked here with no order header, so the order's own target warehouse is not part of the
	answer: that one is a person's choice (:func:`_order_target_warehouse`).

	When ERPNext's function answers nothing, ERPNext filled nothing, and nothing is automatic.
	Only when the function cannot be called at all (a version where it has moved) does this
	fall back to what this module can read by itself: the Item Default and the Company's
	default. An item group's or a brand's default is not covered on that fallback. Never raises.
	"""
	if not (item_code or "").strip():
		return ()
	try:
		from erpnext.stock.get_item_details import get_item_warehouse_

		answer = get_item_warehouse_(
			frappe._dict(company=company, warehouse=None, set_warehouse=None),
			frappe._dict(name=item_code),
			True,
		)
		return ((answer or "").strip(),) if (answer or "").strip() else ()
	except Exception:
		pass
	found = []
	try:
		found.append(_item_default_warehouse(item_code, company))
	except Exception:
		pass
	found.append(_company_default_warehouse(company))
	return tuple(name.strip() for name in found if (name or "").strip())


def _order_target_warehouse(order):
	"""The order's own target warehouse (``Purchase Order.set_warehouse``, "Set Target
	Warehouse"), or ``None``. ERPNext puts it on a row the dialog sent no warehouse for, ahead
	of every default of the item. Never raises."""
	try:
		return (frappe.db.get_value("Purchase Order", order, "set_warehouse") or "").strip() or None
	except Exception:
		return None


def default_new_order_line(doc, method=None):
	"""``before_insert`` on **Purchase Order Item**: a row added to a submitted order with
	Update Items.

	ERPNext inserts that row by itself (``accounts_controller.update_child_qty_rate`` at
	v16.54.0), so the order's ``before_validate`` hooks above never see it. Two things it
	needs, both of which the order's own save would have given it:

	* **its project**, from the order's header, when it has none. ERPNext's Update Items
	  sets none, and the field is mandatory here;
	* **its location by the rule** when it has none, when it is an Overhead line on the
	  Project Shelf (ERPNext fills the Company's default warehouse, which is the shelf), **or
	  when it is a job's line at a location nobody chose** (below).

	**A job's line at the item's own default goes to the shelf.** The row never arrives blank
	in practice: the dialog pre-fills its warehouse column when the item is picked, and for a
	row sent without one ERPNext fills the same value itself
	(``accounts_controller.get_new_child_item_warehouse`` at v16.54.0). For a stock item with a
	bin of its own that value is the bin, so a part bought for a job was ordered into shop
	stock (found on the production mirror, 2026-11-13). A job's row whose location is exactly
	what ERPNext fills for the item (:func:`_erpnext_fills`: the item's, its group's or its
	brand's default, or the Company's) is therefore placed by the rule, which for a job is the
	shelf. A location that is anything else was typed in the dialog and stays, and so does
	the order's own target warehouse (:func:`_order_target_warehouse`). The rule is
	``project_shelf_rules.added_row_may_default``.

	**Known limit.** A person who picks, in the dialog, the very warehouse ERPNext would have
	filled (the item's own default bin, for a job's line) cannot be told apart from ERPNext's
	default: the same value arrives either way. That row goes to the shelf. The location can
	still be changed when the line is received.

	**An Overhead line is not touched by this**: off the shelf it keeps the location it
	arrives with, as before.

	**It runs second on that hook.** ``procurement_line_project.fill_new_line_project``
	(v1.816.3) is listed before it in ``hooks.py`` and owns the project: the header's, else the
	one project the order's lines share, and a refusal in words when the lines name several
	jobs and the header none. So the project is normally there when this picks the location,
	and a row whose project came from the lines is placed by that project. The header fill
	below is kept: it does nothing after that rule, and keeps the row whole without it. When
	that rule refuses the row, this never runs; when it leaves the project blank (nothing to
	go on, or the insert ignores mandatory fields), the row keeps the location it has.

	Rows saved with their order are not inserted on their own and never reach this. Never
	raises: a row it cannot place is left as ERPNext made it.
	"""
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf, project_shelf_rules

		if doc.get("parenttype") != "Purchase Order" or not (doc.get("parent") or "").strip():
			return
		order = frappe.db.get_value(
			"Purchase Order", doc.get("parent"), ["project", "company", "docstatus"], as_dict=True
		)
		if not order:
			return
		if not (doc.get("project") or "").strip() and (order.get("project") or "").strip():
			doc.project = order.get("project").strip()
		project = (doc.get("project") or "").strip()
		current = (doc.get("warehouse") or "").strip()
		shelf = project_shelf.warehouse()
		overhead = is_overhead(project)
		# Only a job's line at some other location than the shelf needs the two reads: every
		# other row is decided without them, exactly as before.
		asks = bool(current and shelf and project and not overhead and current != shelf)
		header = _order_target_warehouse(doc.get("parent")) if asks else None
		automatic = _erpnext_fills(doc.get("item_code"), order.get("company")) if asks and current != header else ()
		if not project_shelf_rules.added_row_may_default(
			current, bool(project), overhead, shelf, header=header, automatic=automatic
		):
			return
		answer = default_line_warehouse(doc.get("item_code"), project, order.get("company"))
		if answer.get("warehouse") and answer["warehouse"] != current:
			doc.warehouse = answer["warehouse"]
	except Exception:
		_log("Purchase line location: a row added with Update Items could not be placed")


def default_receipt_warehouses(doc, method=None):
	"""``before_validate`` on **Purchase Receipt**: a receipt saved for the first time puts
	each order line in its default receiving place (:func:`apply_receiving_defaults`).

	This is the Desk's Create > Purchase Receipt, and any other mapper or API call: the
	receipt copies the order line's warehouse, and an Overhead line that names the Project
	Shelf would be received onto it. Only on the first save, so a place the receiver picks
	afterwards stays. Skipped for a return (stock goes back out of where it is), and for a
	receipt whose places a person has just chosen: the order's Receive Items dialog and a
	scan at a bin in Stock Scan say so with ``flags.receiving_places_chosen``. Never raises.
	"""
	try:
		if doc.get("docstatus") or doc.get("is_return") or not _is_new(doc):
			return
		if getattr(doc, "flags", None) and doc.flags.get("receiving_places_chosen"):
			return
		apply_receiving_defaults(doc)
	except Exception:
		_log("Receipt: the default locations could not be applied")


#: The most lines one :func:`line_warehouses` call answers. A purchase order has a handful.
MAX_LINES = 200


def _company_default_warehouse(company):
	try:
		return frappe.db.get_value("Company", company, "default_warehouse") if company else None
	except Exception:
		return None


@frappe.whitelist()
def line_warehouses(rows, company=None):
	"""The default location for each of a Purchase Order form's lines, and whether the form
	may apply it.

	``rows`` is ``[{"key": <row name>, "item_code", "project", "warehouse", "was_item_code",
	"was_project", "applied"}]``: the line as it is now, the item and project it had before
	this change (blank for a line that has just got both), and the location the form's rule
	last set on it, if any. The answer is ``{key: {"item_code", "project", "warehouse",
	"warehouse_name", "reason", "keep"}}``. The item and project are echoed so the form can
	drop an answer for a line that changed while the request was in flight.

	**``keep`` true means the line's location is a person's pick and must not be changed**
	(``project_shelf_rules.form_may_default``): the test is made here, not in the browser,
	because it needs the item's default bin and the rule's answer for the line's previous
	project. An Overhead line on the Project Shelf is never kept: the save would move it
	anyway (:func:`default_line_warehouses`).

	Reads only. Needs read on Purchase Order: the answer says where an item is kept and
	whether a project is an overhead bucket.
	"""
	from erpnext_enhancements.inventory_enhancements import project_shelf, project_shelf_rules

	if not frappe.has_permission("Purchase Order", "read"):
		frappe.throw(frappe._("You need permission to read Purchase Orders."), frappe.PermissionError)
	if isinstance(rows, str):
		rows = frappe.parse_json(rows)
	out = {}
	names = {}
	shelf = project_shelf.warehouse()
	company_default = _company_default_warehouse(company)
	for raw in (rows or [])[:MAX_LINES]:
		if not isinstance(raw, dict):
			continue
		key = str(raw.get("key") or "").strip()
		item_code = str(raw.get("item_code") or "").strip()
		project = str(raw.get("project") or "").strip()
		if not key or not item_code or not project:
			continue
		answer = default_line_warehouse(item_code, project, company)
		warehouse = answer.get("warehouse")
		if warehouse and warehouse not in names:
			names[warehouse] = frappe.db.get_value("Warehouse", warehouse, "warehouse_name") or warehouse

		current = str(raw.get("warehouse") or "").strip()
		was_item = str(raw.get("was_item_code") or "").strip()
		was_project = str(raw.get("was_project") or "").strip()
		first_or_item_changed = not was_item or not was_project or was_item != item_code
		previous = None
		if current and not first_or_item_changed:
			previous = default_line_warehouse(was_item, was_project, company).get("warehouse")
		automatic = (shelf, _item_default_warehouse(item_code, company), company_default)
		may = project_shelf_rules.form_may_default(
			current, first_or_item_changed, automatic, previous, str(raw.get("applied") or "").strip()
		)
		stranded = project_shelf_rules.overhead_on_shelf(
			current, True, answer.get("reason") in ("own_bin", "fallback"), shelf
		)
		out[key] = {
			"item_code": item_code,
			"project": project,
			"warehouse": warehouse,
			"warehouse_name": names.get(warehouse),
			"reason": answer.get("reason"),
			"keep": not (may or stranded),
		}
	return out


def _receiving_line(line, company, shelf, fallback, overhead_cache):
	"""One order line's default receiving place (``project_shelf_rules.receiving_warehouse``)."""
	from erpnext_enhancements.inventory_enhancements import project_shelf_rules

	project = (line.get("project") or "").strip()
	if project not in overhead_cache:
		overhead_cache[project] = is_overhead(project)
	overhead = overhead_cache[project]
	ordered = (line.get("warehouse") or "").strip()
	own = None
	if shelf and project and overhead and (not ordered or ordered == shelf):
		own = item_own_bin(line.get("item_code"), company)
	warehouse, reason, changed = project_shelf_rules.receiving_warehouse(
		ordered, bool(project), overhead, shelf, own, fallback
	)
	return {
		"warehouse": warehouse,
		"ordered_warehouse": ordered or None,
		"reason": reason,
		"changed": bool(changed),
		"project": project or None,
		"overhead": bool(overhead),
	}


@frappe.whitelist()
def receiving_defaults(purchase_order):
	"""Where each line of one Purchase Order is received by default, for the order's Receive
	Items dialog: ``{"shelf", "shelf_name", "lines": {<row name>: {"warehouse",
	"warehouse_name", "ordered_warehouse", "reason", "changed", "project", "overhead"}}}``.

	It is the order line's own location, except that a line with none gets the project's
	default, and an Overhead line that names the Project Shelf is offered the item's own bin
	instead (``changed`` true): every order line that was open when ``Stores`` became the
	shelf named Stores, Overhead ones included, and none of them was rewritten. The person
	receiving can pick another place per line. Reads only; needs read on the order.
	"""
	from erpnext_enhancements.inventory_enhancements import project_shelf

	po = frappe.get_doc("Purchase Order", purchase_order)
	po.check_permission("read")
	shelf = project_shelf.warehouse()
	fallback = project_shelf.fallback_warehouse() if shelf else None
	cache, lines, names = {}, {}, {}
	for row in po.items:
		answer = _receiving_line(row, po.company, shelf, fallback, cache)
		warehouse = answer["warehouse"]
		if warehouse and warehouse not in names:
			names[warehouse] = frappe.db.get_value("Warehouse", warehouse, "warehouse_name") or warehouse
		answer["warehouse_name"] = names.get(warehouse)
		lines[row.name] = answer
	return {"shelf": shelf, "shelf_name": project_shelf.label(), "lines": lines}


def apply_receiving_defaults(receipt):
	"""Put each line of an unsaved Purchase Receipt mapped from an order in its default
	receiving place (:func:`receiving_defaults`' rule), for the receipts nobody fills in by
	hand: the packing-slip and vendor-bill drafts of the document intake. Only a line with no
	location, or an Overhead line that names the Project Shelf, changes. Returns how many
	lines changed. Never raises: a draft that keeps the order's locations is still a draft a
	person reviews before submitting.
	"""
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf

		shelf = project_shelf.warehouse()
		if not shelf:
			return 0
		fallback = project_shelf.fallback_warehouse()
		cache, changed = {}, 0
		for row in receipt.get("items") or []:
			answer = _receiving_line(row, receipt.get("company"), shelf, fallback, cache)
			if answer["warehouse"] and answer["warehouse"] != (row.get("warehouse") or "").strip():
				row.warehouse = answer["warehouse"]
				changed += 1
		if changed and (receipt.get("set_warehouse") or "").strip():
			# ERPNext clears a header location the rows disagree with; cleared here so the
			# draft does not open saying one place while its lines say another.
			receipt.set_warehouse = None
		return changed
	except Exception:
		_log("Receipt draft: the default locations could not be applied")
		return 0


def purchase_order_projects(doc):
	"""Every distinct project on one Purchase Order: header first, then row order.

	The header ``Purchase Order.project`` and the row ``Purchase Order Item.project`` can
	disagree, which is the reason :func:`cascade_project_to_items` exists and the reason every
	report here matches on the union rather than on either alone. Two things that print the
	job on the same document must not arrive at it two different ways, so the PDF filename and
	the print format both call this.

	**A list, not a string, and that is the load-bearing part.** On production today every
	submitted order resolves to exactly one project, and all 61 orders with a blank header
	have blank rows too — so a function returning a single value would look correct on every
	document that exists. It would be wrong the first time somebody buys for two jobs on one
	order, silently, by naming one of them. Returning the set makes the caller decide what to
	do about it where a reader can see the decision.

	Takes a document rather than a name: both callers already hold one, and re-reading the
	rows would be a second query for data in front of us.
	"""
	projects = []
	for value in [doc.get("project"), *(row.get("project") for row in doc.get("items") or [])]:
		value = (value or "").strip()
		if value and value not in projects:
			projects.append(value)
	return projects


#: A Purchase Order with nothing left to receive. Same rule as
#: ``api/pickup_routing.py::_is_outstanding`` — the *number*, not the label: ``Closed``
#: can hide an order whose goods never turned up, and ``To Bill`` means they are already
#: here. Kept identical on purpose; two different answers to "is there anything still to
#: collect" on the same Project form would be worse than either answer alone.
#:
#: Defined in ``procurement_quantities`` and re-exported here under the name the Supplier
#: Pickup List already imports: the Procurement Tracker's Print → Open applies the same rule
#: from that frappe-free module, and one definition cannot drift from itself.
from erpnext_enhancements.procurement_quantities import SETTLED_PO_STATUSES


@frappe.whitelist()
def get_receivable_purchase_orders(project):
	"""Purchase Orders on this project that still have goods to receive.

	Backs the Project form's "+ Purchase Receipt" button, which until now opened a
	blank Purchase Receipt carrying nothing but the project — no supplier, no order, no
	lines — leaving the receiver to retype a delivery that ERPNext already knew about.

	Matches on the **union** of the header ``Purchase Order.project`` and the item-row
	``Purchase Order Item.project``, the same rule the pick-up routing map uses. The two
	agree on every order on this site today (70 of 70) — but only because
	:func:`cascade_project_to_items` fills blank item rows on save, and it fills *blanks
	only*, on *save only*. An order written before that hook existed, or re-pointed by a
	path that bypasses it, can still carry one and not the other; that is exactly the
	state the pick-up routing map found on 44 of 204 lines (CHANGELOG v1.190.0). The
	union costs one extra query and cannot be wrong.
	"""
	project = (project or "").strip()
	if not project:
		return []

	# Whitelisted and login-only, so gate on Project read — this returns every PO's
	# supplier and grand_total for the job, and without the check any authenticated user
	# could read them by project name. Same gate as the sibling pickup_routing endpoints.
	frappe.get_doc("Project", project).check_permission("read")

	names = set(
		frappe.get_all("Purchase Order", filters={"project": project}, pluck="name")
	)
	for row in frappe.get_all(
		"Purchase Order Item", filters={"project": project}, fields=["parent"], distinct=True
	):
		if row.get("parent"):
			names.add(row["parent"])

	if not names:
		return []

	orders = frappe.get_all(
		"Purchase Order",
		filters={"name": ["in", list(names)], "docstatus": 1},
		fields=["name", "supplier", "transaction_date", "per_received", "grand_total", "currency", "status"],
		order_by="transaction_date desc, name desc",
	)
	return [
		po
		for po in orders
		if po.status not in SETTLED_PO_STATUSES and flt(po.per_received) < 100
	]


@frappe.whitelist()
def get_overhead_projects():
	"""Return the active overhead buckets, alphabetically.

	The client script uses this to point a purchaser at the non-job buckets when
	a PO has no project at all. Returns ``[{name, project_name}, ...]`` — empty
	on a site where ``patches.seed_overhead_projects`` has not run.
	"""
	return frappe.get_all(
		"Project",
		filters={"project_type": OVERHEAD_PROJECT_TYPE, "status": "Active"},
		fields=["name", "project_name"],
		order_by="project_name asc",
	)
