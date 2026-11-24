# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every judgement about the Project Shelf (v1.817.0). No Frappe: tested bench-free.

**What the Project Shelf is.** One shared warehouse where parts bought for a job wait until
the job uses them. The owner's decision of 2026-11-12: everything ordered for a project is
received there by default, the person receiving can still put it somewhere else, and stock on
the shelf does not count as stock the shop has. It is the warehouse ERPNext installed as
``Stores``, renamed (``patches/rename_stores_to_project_shelf``), and it is named by one
setting, ``Inventory Scanner Settings.project_shelf_warehouse``, never by its name or by its
place in the tree.

**It is one warehouse, not one per job.** A job site has a warehouse of its own because
material sits there for weeks and somebody has to find it. The shelf is one physical place in
the shop. So which job a part on it belongs to is not something ERPNext's on-hand table can
say (it keeps quantity by item and warehouse only): it is **derived from the project on each
Stock Ledger row** at the shelf. A receipt carries its order line's project, a take carries
the job it was taken for, and a move onto or off the shelf carries the job it names
(:func:`move_project`). :func:`shelf_summary` adds those rows up.

**That derivation can drift, and it says so.** A movement with no project on it (a count, a
transfer made by hand in the Desk) changes what is on the shelf without changing any job's
number. Nothing here hides that: the difference between what is on hand and what the tags
add up to is returned per item (``untagged``), each such movement is listed
(``untagged_movements``), and ``exact`` is true only when none of that is there.

**A count names its jobs with a tag that posts nothing** (v1.843.0, the owner's yes of
2026-11-24). A count is a Stock Reconciliation, and ERPNext 16.60.0 has no project on one: a
custom field would be copied onto the Stock Adjustment GL line (``stock_controller``), so the
loss would land in the job's P&L, and a count has one row per item and place, so it could not
split between two jobs. So the count posts exactly as it did (the difference to the stock
adjustment account, no project), and a **Project Shelf Tag** beside it says whose units the
change was: document, item, job, signed quantity. :func:`shelf_summary` reads a tag as sharing
out that movement's own change among the jobs it names, **never more than the change and only
of its sign**, so a tag cannot invent a claim; whatever the tags do not cover stays untagged.
A tag counts only while its document's ledger rows are live, and a canceled document's tags
are deleted too (``project_shelf.drop_tags``). :func:`count_split` is who a count's change belongs to when nobody
says (the default) and whether a person's own split adds up; :func:`settle_untagged` is the
same default applied to the movements off the shelf that were never named (the cleanup patch).

**A job's figure is never more than is physically on the shelf.** A tag is a claim, and a
claim on stock that has left is not stock. So each job's ``qty`` is its tag balance capped by
what is on hand (:func:`allocate`), the uncapped balance stays beside it as ``tagged``, and
what the cap took off is ``over_tagged``. Production had this on the day the shelf was named:
13 units received for a job in September, moved to bins by a transfer with no project, and
since used up. The ledger still tags them to that job at the shelf; nothing is there.

**The default location of a purchase line** (:func:`line_warehouse`): a line for a project
that is not an Overhead project goes to the shelf; a line for an Overhead project (shop
supplies, replenishment) goes to the item's own bin (:func:`own_bin`), and to the shop's
general receiving location when the item has no bin yet. It is a default at the moment the
line gets its project and item, with one thing held on every save of a draft: **an Overhead
line never sits on the shelf** (:func:`overhead_on_shelf`). ERPNext itself puts it there,
because the Company's default warehouse is the shelf, and stock received there would leave
the shop's count under a project that is not a job. Everything else a person picks, sticks.

Indentation is tabs (new file).
"""

from erpnext_enhancements.inventory_enhancements import job_site_rules as site_rules

#: The shelf's ``warehouse_name`` after the rename.
SHELF_NAME = "Project Shelf"

#: What the same warehouse was called before: ERPNext's stock ``Stores``. A QR label printed
#: under the old name still opens the shelf (``project_shelf.former_name``).
FORMER_NAME = "Stores"

#: The Project Type of the standing non-job buckets (``procurement_project``). Repeated here
#: because that module imports Frappe; ``tests/test_project_shelf`` pins the two together.
OVERHEAD_PROJECT_TYPE = "Overhead"

#: Why a line got the location it did.
REASON_SHELF = "shelf"
REASON_OWN_BIN = "own_bin"
REASON_FALLBACK = "fallback"
REASON_NONE = "none"

#: Kinds of movement in ``untagged_movements``.
KIND_COUNT = "count"
KIND_MOVE = "move"
KIND_OTHER = "other"

RECONCILIATION = "Stock Reconciliation"
STOCK_ENTRY = "Stock Entry"

#: Quantities closer to zero than this are zero.
TOLERANCE = 1e-6

#: How many untagged movements :func:`shelf_summary` returns, newest first.
MOVEMENT_LIMIT = 200

#: Where a Project Shelf Tag came from (its ``source``, v1.843.0).
TAG_COUNT = "Count"  # named by the person in the Bin Manager's count dialog
TAG_DEFAULT = "Default"  # the rule's split when nobody named it: Count Desk, the phone audit, the Desk
TAG_CLEANUP = "Cleanup"  # the one-off patch that settled the movements nobody had named
TAG_CHANGED = "Changed"  # re-split afterwards with the Bin Manager's "Change whose"
TAG_SOURCES = (TAG_COUNT, TAG_DEFAULT, TAG_CLEANUP, TAG_CHANGED)

#: In a person's split of a count, the part that belongs to no job.
NO_JOB = ""


def _name(value):
	return str(value or "").strip()


def _qty(value):
	try:
		return float(value or 0)
	except (TypeError, ValueError):
		return 0.0


# ---------------------------------------------------------------------------
# Where a purchase line goes
# ---------------------------------------------------------------------------


def own_bin(putaway_bins, default_warehouse=None, reorder_warehouses=(), not_bins=()):
	"""The item's own bin, or ``None`` when it has none yet.

	In order: the first bin it is assigned to (``putaway_bins``: its enabled Putaway Rules, in
	the order the caller ranked them), then its Item Default warehouse, then the warehouse a
	reorder rule asks for. ``not_bins`` are places that are never an item's bin and are skipped
	wherever they appear: the Project Shelf, every job site, the boneyard. On the day the shelf
	was renamed 774 Item Defaults pointed at it, left there by ERPNext's company default, and
	none of them means "this item is kept on the Project Shelf". (The patch blanks the ones on
	items that do not maintain stock; the rest are skipped here.)
	"""
	skip = {_name(name) for name in not_bins or ()}
	candidates = [*(putaway_bins or ()), default_warehouse, *(reorder_warehouses or ())]
	for candidate in candidates:
		name = _name(candidate)
		if name and name not in skip:
			return name
	return None


def line_warehouse(has_project, overhead, shelf, own=None, fallback=None):
	"""``(warehouse, reason)``: where a purchase line is received by default.

	* no project on the line yet, or no Project Shelf set up: nothing is decided
	  (``None``, :data:`REASON_NONE`) and the line keeps whatever it has;
	* a project that is not Overhead: the shelf;
	* an Overhead project: the item's own bin, else the shop's general receiving location
	  (``fallback``), else nothing.
	"""
	shelf = _name(shelf)
	if not has_project or not shelf:
		return None, REASON_NONE
	if not overhead:
		return shelf, REASON_SHELF
	if _name(own):
		return _name(own), REASON_OWN_BIN
	if _name(fallback):
		return _name(fallback), REASON_FALLBACK
	return None, REASON_NONE


def first_count_warehouse(has_shelf, default=None, own=None, fallback=None, not_bins=()):
	"""Where an item's first count is suggested, right after Maintain Stock is turned on for it
	(``maintain_stock.first_count_warehouse``, the *Open a Stock Reconciliation* prefill).

	The switch is for items that were bought and never kept in stock, and those are the items
	whose Item Default is the warehouse ERPNext handed out by itself: ``Stores``, which is the
	Project Shelf since v1.817.0 (over 700 of the 774 such defaults were on non-stock items). A
	count entered there would put a shop consumable on the shelf: the shop would read zero of
	it, the units would belong to no job, and a take there would ask for one.

	* **With a Project Shelf**: the item's own bin (``own``, which never is the shelf, a job
	  site or the boneyard), else the shop's general receiving location (``fallback``), else
	  nothing, so the person has to say where the item really is.
	* **With none set up**: the item's default as before, unless it is one of ``not_bins``
	  (a job site): nothing is ever counted in as shop stock at a place that is not the shop's.
	"""
	if has_shelf:
		return _name(own) or _name(fallback) or None
	default = _name(default)
	if not default or default in {_name(name) for name in not_bins or ()}:
		return None
	return default


def may_default(current, automatic=()):
	"""Whether a line's location may still be set by the rule: it is blank, or it is one of
	the values nobody chose (``automatic``: the shelf, the item's Item Default). A location a
	person picked is neither, and is left alone."""
	current = _name(current)
	return not current or current in {_name(value) for value in automatic or () if _name(value)}


def overhead_on_shelf(current, has_project, overhead, shelf):
	"""Whether a line is an Overhead line that names the Project Shelf: the one location that
	is never anybody's choice. ERPNext fills it in by itself (the Company's default warehouse
	is the shelf, and so are hundreds of Item Defaults), and shop supplies received there
	would drop out of the shop's stock under a project that is not a job. The order's save,
	a new receipt and the Receive Items dialog all move such a line to the item's own bin."""
	shelf = _name(shelf)
	return bool(shelf and has_project and overhead and _name(current) == shelf)


def added_row_may_default(current, has_project, overhead, shelf, header=None, automatic=()):
	"""Whether a row that is inserted on its own into a submitted order (ERPNext's Update
	Items) may be placed by the rule. It arrives with a location already on it: the one typed
	in the dialog, else the order's own target warehouse, else ERPNext's default for the item.

	* No location: yes.
	* **An Overhead line on the Project Shelf**: yes (:func:`overhead_on_shelf`).
	* Any other Overhead line, a line with no project, a site with no Project Shelf: no. The
	  row keeps what it has.
	* **A job's line** is the rest. It may be placed, which for a job means the shelf, only
	  when its location is one nobody chose: ``automatic``, what ERPNext fills in for the item
	  by itself. The order's own target warehouse (``header``) is a person's choice for the
	  whole order and stays, even when it is also the item's default. Any location that is
	  neither was typed in the dialog and stays.

	One thing this cannot tell apart: a person who picks, in the dialog, the very warehouse
	ERPNext would have filled in. That row goes to the shelf like any other; the location can
	still be changed when the line is received.
	"""
	current = _name(current)
	if not current or overhead_on_shelf(current, has_project, overhead, shelf):
		return True
	if not _name(shelf) or not has_project or overhead:
		return False
	if current == _name(header):
		return False
	return may_default(current, automatic)


def form_may_default(current, first_or_item_changed, automatic=(), previous=None, applied=None):
	"""Whether the Purchase Order form may set a line's location by the rule, when the line's
	item or project has just changed. **A location a person picked by hand stays.**

	* A line with no location: yes.
	* **The line has its item and project for the first time, or its item changed**
	  (``first_or_item_changed``): ERPNext has just filled the location itself, from the Item
	  Default or the Company's default. Yes when it is one of those values (``automatic``:
	  the shelf, the item's Item Default, the Company's default); a location that is none of
	  them was typed by a person, and stays.
	* **Only the project changed**: ERPNext does not touch the location then, so whatever is
	  there was put there on purpose or by this rule. Yes only when it is the rule's own
	  value: what the form last set on this line (``applied``), or what the rule gives for the
	  project the line had before (``previous``, for a line that was already there when the
	  form opened). Anything else is a hand pick, and stays, even when it happens to equal
	  the item's default bin.
	"""
	current = _name(current)
	if not current:
		return True
	if first_or_item_changed:
		return current in {_name(value) for value in automatic or () if _name(value)}
	return current in {_name(value) for value in (applied, previous) if _name(value)}


def receiving_warehouse(ordered, has_project, overhead, shelf, own=None, fallback=None):
	"""``(warehouse, reason, changed)``: where a receipt of one order line goes by default.

	The order line's own location, with two exceptions: a line with none gets the rule's
	(:func:`line_warehouse`), and **an Overhead line that names the Project Shelf** goes to the
	item's bin instead. The second is what handles the order lines that were open when
	``Stores`` became the shelf: every one of them named Stores, Overhead ones included, and
	nothing rewrites them. ``changed`` says the answer differs from the order line, so the
	screen can say why. The person receiving can always pick another place.
	"""
	ordered = _name(ordered)
	shelf = _name(shelf)
	ruled, reason = line_warehouse(has_project, overhead, shelf, own, fallback)
	if not ordered:
		return ruled, reason, False
	if shelf and ordered == shelf and has_project and overhead and ruled and ruled != shelf:
		return ruled, reason, True
	return ordered, (REASON_SHELF if shelf and ordered == shelf else REASON_NONE), False


# ---------------------------------------------------------------------------
# Whose stock a movement is
# ---------------------------------------------------------------------------


def take_needs_job(at_shelf, setting_requires):
	"""Whether a take must name a job: always at the shelf (every unit there belongs to one),
	elsewhere when the setting says so."""
	return bool(at_shelf or setting_requires)


def add_offset_setting(has_job, at_shelf):
	"""Which Inventory Scanner Settings account a no-order add offsets to, as the setting's
	fieldname.

	Away from the shelf a job on an add means the parts came **back from** that job, so the
	credit goes to the account a take charges (``take_expense_account``) and the job's cost
	nets out; with no job it is found stock (``add_offset_account``, the account a stock
	manager reviews). **At the shelf the job is the one the parts are set aside for**, and the
	shelf requires one: crediting that job there would cancel the charge its later take makes,
	so the job would bear nothing for parts it used. At the shelf it is always found stock.
	"""
	if has_job and not at_shelf:
		return "take_expense_account"
	return "add_offset_account"


def sole_holder(holders):
	"""The one job that holds this item on the shelf, or ``None`` when none or several do.
	``holders`` is ``[(project, qty), ...]``; only a positive quantity holds anything, and the
	quantity is the capped one (:func:`allocate`), so a job whose tagged stock has all left
	is never "the only job that holds it"."""
	held = [project for project, qty in holders or () if _name(project) and _qty(qty) > TOLERANCE]
	return held[0] if len(held) == 1 else None


def move_project(
	source_is_shelf,
	destination_is_shelf,
	source_site_project=None,
	destination_site_project=None,
	given=None,
	holders=(),
):
	"""``(project, problem)``: the job a transfer carries on its stock entry, or the sentence
	that refuses it. Both ledger rows of one transfer carry the same project.

	* **Neither end is the shelf**: as before (``job_site_rules.transfer_project``): the
	  destination site's job, else the source site's, else none.
	* **Shelf to a job site**: that site's job. The parts bought for a job go to its site.
	* **Shelf to anywhere else** (a bin, the boneyard: the stock is released to the shop): the
	  job the page names (``given``); with none named, the one job that holds this item on the
	  shelf (:func:`sole_holder`); with several or none, it is refused and asks. The caller
	  passes ``holders`` only when the item's tags are exact (:func:`item_exact`): where they
	  are not, some of what is there has no job on record, and nothing is guessed.
	* **A job site back to the shelf**: the site's job. It is still that job's.
	* **Anywhere else onto the shelf**: the job named. Stock set aside on the shelf belongs to
	  a job from the moment it lands, so with none named it is refused and asks.
	"""
	given = _name(given) or None
	if not source_is_shelf and not destination_is_shelf:
		return site_rules.transfer_project(source_site_project, destination_site_project), None
	if source_is_shelf:
		if destination_site_project:
			return destination_site_project, None
		project = given or sole_holder(holders)
		if project:
			return project, None
		return None, (
			"Say which job these were bought for. Everything on the Project Shelf belongs to a job, "
			"so a move off it names the job it is leaving."
		)
	if source_site_project:
		return source_site_project, None
	if given:
		return given, None
	return None, (
		"Say which job these are being set aside for. Everything on the Project Shelf belongs to a job."
	)


# ---------------------------------------------------------------------------
# What is shop stock
# ---------------------------------------------------------------------------


def split_totals(bins, site_warehouses=(), shelf=None):
	"""``(shop, sites, shelf)``: an item's on-hand split three ways. ``bins`` is
	``[(warehouse, qty), ...]``. The shop's is what a search shows as the total; the other two
	are named beside it, each as its own place."""
	shelf = _name(shelf)
	sites = set(site_warehouses or ())
	shop = on_sites = on_shelf = 0.0
	for warehouse, qty in bins or ():
		amount = _qty(qty)
		if shelf and warehouse == shelf:
			on_shelf += amount
		elif warehouse in sites:
			on_sites += amount
		else:
			shop += amount
	return shop, on_sites, on_shelf


def not_shop(site_warehouses=(), shelf=None):
	"""The set of warehouses whose stock is not shop stock: every job site and the shelf."""
	out = set(site_warehouses or ())
	if _name(shelf):
		out.add(_name(shelf))
	return out


def split_places(places):
	"""``(shop, shelf, sites)``: the places an item is on hand, each a dict the server marked
	with ``project_shelf`` or ``job_site``. Order within each is kept."""
	shop, shelf, sites = [], [], []
	for place in places or ():
		if place.get("project_shelf"):
			shelf.append(place)
		elif place.get("job_site"):
			sites.append(place)
		else:
			shop.append(place)
	return shop, shelf, sites


# ---------------------------------------------------------------------------
# What is on the shelf, by job
# ---------------------------------------------------------------------------


def allocate(on_hand, tagged):
	"""Share what is physically on the shelf among the jobs whose tags claim it.

	``tagged`` is ``[(project, balance, last_in), ...]`` for one item: each job's tag balance
	(in minus out) and the date it last received any. Returns ``{project: quantity}``, each
	between 0 and that job's balance, adding up to no more than ``on_hand``.

	When the tags claim no more than is there, every job gets its balance. When they claim
	more, somebody's parts have left without their tag (an untagged move, a count, a take
	under another job), and the stock that remains is given to **the jobs that received
	most recently first**: the newest delivery is the one most likely still on the shelf.
	It is a rule for sharing out a shortfall nobody recorded, so the uncapped balance is
	always shown beside it. A job with a zero or negative balance holds nothing.
	"""
	left = max(_qty(on_hand), 0.0)
	claims = [(project, _qty(balance), str(last_in or "")) for project, balance, last_in in tagged or ()]
	claims = [claim for claim in claims if claim[1] > TOLERANCE]
	# Newest receipt first. Two jobs that received on the same day are taken in the order of
	# their ids, so the answer is the same every time it is asked.
	claims.sort(key=lambda claim: claim[0])
	claims.sort(key=lambda claim: claim[2], reverse=True)
	out = {}
	for project, balance, _last_in in claims:
		share = min(balance, left)
		out[project] = share
		left -= share
	return out


def is_count(row):
	"""A Stock Reconciliation row that stores no change. ERPNext 16.52.1 and later write a
	count with ``actual_qty = 0`` and the counted balance in ``qty_after_transaction``."""
	return row.get("voucher_type") == RECONCILIATION and abs(_qty(row.get("actual_qty"))) <= TOLERANCE


def _key(voucher_type, voucher_no, item_code):
	return (_name(voucher_type), _name(voucher_no), _name(item_code))


def tag_pool(tags):
	"""``{(voucher_type, voucher_no, item_code): [[project, qty], ...]}`` from Project Shelf Tag
	rows (``voucher_type``, ``voucher_no``, ``item_code``, ``project``, ``qty``). A tag with no
	job, no document or no quantity is left out. The lists are consumed as the ledger is read
	(:func:`_named`), so one document's tags are used once however many of its rows there are."""
	pool = {}
	for tag in tags or ():
		key = _key(tag.get("voucher_type"), tag.get("voucher_no"), tag.get("item_code"))
		project = _name(tag.get("project"))
		qty = _qty(tag.get("qty"))
		if not all(key) or not project or abs(qty) <= TOLERANCE:
			continue
		pool.setdefault(key, []).append([project, qty])
	return pool


def _named(change, pooled):
	"""Share one untagged movement's ``change`` among the jobs its tags name: only tags of the
	change's own sign, and never more than the change. Takes what it uses out of ``pooled``.
	Returns ``[(project, qty), ...]`` signed like ``change``."""
	out = []
	left = abs(change)
	sign = 1.0 if change > 0 else -1.0
	for entry in pooled or ():
		if left <= TOLERANCE:
			break
		project, qty = entry
		if qty * sign <= TOLERANCE:
			continue
		take = min(abs(qty), left)
		out.append((project, sign * take))
		entry[1] = qty - sign * take
		left -= take
	return out


def _walk(ledger, pool, only_item=""):
	"""One step per ledger row that changes what is on the shelf, in ledger order:
	``{"code", "change", "before", "named", "rest", "tagged", "kind", "day", "row"}``.

	``named`` is ``[(project, qty)]``: the row's own project for a row that has one (``tagged``
	true), else the share its Project Shelf Tags give it (:func:`_named`). ``rest`` is what no
	job has, 0 for a tagged row. A count's change is worked out from the balance before it,
	and a count's own row never carries a job (a Stock Reconciliation has no project), so a
	count is named only by its tags."""
	balance = {}
	for row in ledger or ():
		code = _name(row.get("item_code"))
		if not code or (only_item and code != only_item):
			continue
		before = balance.get(code, 0.0)
		after = row.get("qty_after_transaction")
		if is_count(row):
			change = _qty(after) - before
			balance[code] = _qty(after)
			tag = None
			kind = KIND_COUNT
		else:
			change = _qty(row.get("actual_qty"))
			balance[code] = _qty(after) if after is not None else before + change
			tag = _name(row.get("project")) or None
			kind = KIND_MOVE if row.get("voucher_type") == STOCK_ENTRY else KIND_OTHER
		if abs(change) <= TOLERANCE:
			continue
		if tag:
			named, rest = [(tag, change)], 0.0
		else:
			named = _named(change, pool.get(_key(row.get("voucher_type"), row.get("voucher_no"), code)))
			rest = change - sum(qty for _project, qty in named)
			if abs(rest) <= TOLERANCE:
				rest = 0.0
		yield {
			"code": code,
			"change": change,
			"before": before,
			"named": named,
			"rest": rest,
			"tagged": bool(tag),
			"kind": kind,
			"day": str(row.get("posting_date") or "")[:10],
			"row": row,
		}


def _credit(entries, project, code, qty, day, counted=False):
	"""Add one job's share of a movement to its running figures for one item."""
	entry = entries.setdefault(
		(project, code),
		{"qty_in": 0.0, "qty_out": 0.0, "last_movement": "", "last_in": "", "counted": 0.0},
	)
	if qty > 0:
		entry["qty_in"] += qty
		entry["last_in"] = max(entry["last_in"], day)
	else:
		entry["qty_out"] -= qty
	entry["last_movement"] = max(entry["last_movement"], day)
	if counted:
		entry["counted"] += qty
	return entry


def shelf_summary(ledger, on_hand, project=None, item_code=None, limit=MOVEMENT_LIMIT, tags=None):
	"""Add the shelf's ledger up by job. The arithmetic behind ``project_shelf.shelf_stock``.

	``ledger`` is every live Stock Ledger row at the shelf, **in ledger order** (posting time,
	then creation), each with ``item_code``, ``project``, ``actual_qty``,
	``qty_after_transaction``, ``voucher_type``, ``voucher_no`` and ``posting_date``.
	``on_hand`` is ``{item_code: quantity}`` from ERPNext's on-hand table. ``tags`` are the
	Project Shelf Tags at the shelf (v1.843.0): each names a job for part of a movement that
	carries none (:func:`tag_pool`).

	Returns ``{"rows", "untagged", "untagged_movements", "exact"}``:

	* ``rows``: one per job and item whose tagged balance is not zero. ``qty`` is **what that
	  job has on the shelf: its tag balance, capped by what is physically there**
	  (:func:`allocate`), so it is never negative and an item's rows never add up to more
	  than its on-hand. ``tagged`` is the uncapped balance (``qty_in`` minus ``qty_out``),
	  and **a negative one is kept**: more left the shelf under that job than came in under
	  it, usually parts bought for one job and used on another. ``over_tagged`` is
	  ``tagged`` minus ``qty`` when the cap took something off, else 0. ``last_movement`` is
	  the date of the job's latest row and ``last_in`` of its latest arrival. ``counted`` is
	  the net its counts' tags gave it (negative: counted off the shelf).
	* ``untagged``: one per item whose on-hand differs from the sum of its jobs' tag
	  balances. ``qty`` is on hand minus tagged: positive is stock no job has a claim on,
	  negative is more claimed than is there. ``over_tagged`` is the claimed stock that is
	  not there, 0 or more.
	* ``untagged_movements``: the part of each movement no job has (no project, and not
	  covered by a tag), newest first, at most ``limit``. A count's change is worked out from
	  the balance before it.
	* ``exact``: no untagged difference and no negative balance.

	A count's own row never counts toward a job, whatever it says: a Stock Reconciliation has
	no project in ERPNext. Only its tags name its jobs. ``project`` and ``item_code`` narrow
	the answer; with ``project``, the untagged parts cover only the items that job has rows for.
	"""
	only_item = _name(item_code)
	only_project = _name(project)
	tagged = {}
	movements = []
	for step in _walk(ledger, tag_pool(tags), only_item):
		code = step["code"]
		counted = step["kind"] == KIND_COUNT and not step["tagged"]
		for tag, qty in step["named"]:
			_credit(tagged, tag, code, qty, step["day"], counted=counted)
		if step["rest"]:
			row = step["row"]
			movements.append(
				{
					"posting_date": step["day"],
					"voucher_type": row.get("voucher_type"),
					"voucher_no": row.get("voucher_no"),
					"item_code": code,
					"qty": step["rest"],
					"kind": step["kind"],
				}
			)

	rows = []
	tagged_by_item = {}
	claims_by_item = {}
	for (tag, code), entry in tagged.items():
		balance = entry["qty_in"] - entry["qty_out"]
		tagged_by_item[code] = tagged_by_item.get(code, 0.0) + balance
		claims_by_item.setdefault(code, []).append((tag, balance, entry["last_in"]))
	shares = {
		code: allocate((on_hand or {}).get(code), claims) for code, claims in claims_by_item.items()
	}
	for (tag, code), entry in tagged.items():
		balance = entry["qty_in"] - entry["qty_out"]
		if abs(balance) <= TOLERANCE:
			continue
		share = shares.get(code, {}).get(tag, 0.0)
		rows.append(
			{
				"project": tag,
				"item_code": code,
				"qty": share,
				"tagged": balance,
				"over_tagged": max(balance - share, 0.0) if balance > 0 else 0.0,
				"qty_in": entry["qty_in"],
				"qty_out": entry["qty_out"],
				"last_movement": entry["last_movement"],
				"last_in": entry["last_in"],
				"counted": entry["counted"],
			}
		)
	if only_project:
		rows = [row for row in rows if row["project"] == only_project]
	rows.sort(key=lambda row: (row["project"], row["item_code"]))

	codes = set(tagged_by_item)
	for code, qty in (on_hand or {}).items():
		if abs(_qty(qty)) > TOLERANCE and (not only_item or _name(code) == only_item):
			codes.add(_name(code))
	if only_project:
		codes &= {row["item_code"] for row in rows}
	untagged = []
	for code in sorted(codes):
		have = _qty((on_hand or {}).get(code))
		claimed = tagged_by_item.get(code, 0.0)
		if abs(have - claimed) > TOLERANCE:
			held = sum(shares.get(code, {}).values())
			positive = sum(balance for _tag, balance, _last in claims_by_item.get(code, ()) if balance > 0)
			untagged.append(
				{
					"item_code": code,
					"on_hand": have,
					"tagged": claimed,
					"qty": have - claimed,
					"over_tagged": max(positive - held, 0.0),
				}
			)

	if only_project:
		movements = [m for m in movements if m["item_code"] in codes]
	movements.reverse()
	return {
		"rows": rows,
		"untagged": untagged,
		"untagged_movements": movements[: max(int(limit or 0), 0)],
		"exact": not untagged and not any(row["tagged"] < -TOLERANCE for row in rows),
	}


# ---------------------------------------------------------------------------
# Whose a count's change is (v1.843.0)
# ---------------------------------------------------------------------------


def claims_of(rows, item_code):
	"""``[(project, tagged, last_in), ...]`` for one item from :func:`shelf_summary` rows: each
	job's uncapped tag balance and its latest arrival, the input :func:`allocate` and
	:func:`count_split` take."""
	code = _name(item_code)
	return [
		(row["project"], _qty(row.get("tagged")), row.get("last_in") or "")
		for row in rows or ()
		if row.get("item_code") == code
	]


def _plain(value):
	value = round(_qty(value), 6)
	return str(int(value)) if value == int(value) else str(value)


def _shares_problem(change, shares, holds, unclaimed, shorts=None):
	"""A sentence when a person's split of a count cannot be posted, else ``None``.

	On a count up, **no units can be no job's while a job is still short** (``shorts``: how many
	each job's claim is above what it holds). The shelf gives what is on hand to the jobs' claims
	before anything is unclaimed (:func:`allocate`), so a "no job" share would silently refill the
	short job instead, and the split the person saved would not be the one the shelf shows."""
	if any(qty < -TOLERANCE for qty in shares.values()):
		return "A job's share of a count cannot be less than nothing."
	total = sum(shares.values())
	if abs(total - abs(change)) > TOLERANCE:
		return (
			f"The shares add up to {_plain(total)}, and the count changes the shelf by {_plain(abs(change))}. "
			"Make them add up."
		)
	if change < 0:
		for project, qty in shares.items():
			if qty <= TOLERANCE:
				continue
			if project == NO_JOB:
				if qty > unclaimed + TOLERANCE:
					return f"Only {_plain(unclaimed)} of this item on the shelf belongs to no job."
				continue
			have = holds.get(project, 0.0)
			if have <= TOLERANCE:
				return f"{project} has none of this item on the shelf, so a count cannot take any off it."
			if qty > have + TOLERANCE:
				return f"{project} has only {_plain(have)} of this item on the shelf."
	elif shares.get(NO_JOB, 0.0) > TOLERANCE:
		still = {
			project: short - min(max(shares.get(project, 0.0), 0.0), short)
			for project, short in (shorts or {}).items()
			if short > TOLERANCE
		}
		still = {project: qty for project, qty in still.items() if qty > TOLERANCE}
		if still:
			named = ", ".join(f"{project} ({_plain(qty)})" for project, qty in sorted(still.items()))
			return (
				f"Found units cannot be no job's while a job is short of this item on the shelf: {named}. "
				"The shelf gives them to that job first. Give them to it, or to the job they were bought for."
			)
	return None


def count_split(before, counted, claims, chosen=None):
	"""Who a count's change at the shelf belongs to, and the tags that say so.

	``before`` is what the shelf had of the item, ``counted`` what was counted, ``claims`` the
	jobs' tag balances before the count (``[(project, tagged, last_in)]``, :func:`claims_of`).
	``chosen`` is a person's split, ``{project: quantity}`` of positive amounts with
	:data:`NO_JOB` for the part that is nobody's; ``None`` asks for the default.

	**Each job holds its capped share** (:func:`allocate`); what is on the shelf beyond every
	job's share belongs to no job (``unclaimed``).

	* **A count down** comes, by default, first out of the stock no job claims, then out of the
	  job whose delivery is oldest: the reverse of :func:`allocate`, which keeps the newest
	  delivery's stock. That is exactly each job's capped share before less its capped share
	  after. A person's split may take from any job up to what it holds, and from no job up to
	  ``unclaimed``.
	* **A count up** goes, by default, first back to a job whose claim was short (its parts had
	  left without its tag, so the claim already exists and no tag is written for that part),
	  then to the one job that has a claim on the item if exactly one has, else to no job. A
	  person's split may give units to any job; only what goes beyond a job's shortfall is a tag.

	Returns ``{"change", "unclaimed", "rows", "no_job", "tags", "choice", "problem"}``: ``rows``
	``[{project, tagged, holds, short, qty}]`` with each job's share of the change (``qty``,
	positive), most held first; ``no_job`` the part that is nobody's; ``tags``
	``[(project, signed qty)]`` to write; ``choice`` true when the default had to choose between
	sources (several jobs, or a job and unclaimed stock), so a person should look; ``problem`` a
	sentence when ``chosen`` cannot be posted (``rows`` still lists the jobs, ``tags`` is empty).
	"""
	before = max(_qty(before), 0.0)
	counted = max(_qty(counted), 0.0)
	change = counted - before
	claims = [(_name(p), _qty(b), str(last or "")) for p, b, last in claims or () if _name(p)]
	tagged = {}
	for project, balance, _last in claims:
		tagged[project] = tagged.get(project, 0.0) + balance
	holds = allocate(before, claims)
	unclaimed = max(before - sum(holds.values()), 0.0)
	candidates = sorted(p for p, b in tagged.items() if b > TOLERANCE)
	out = {
		"change": 0.0 if abs(change) <= TOLERANCE else change,
		"unclaimed": unclaimed,
		"rows": [],
		"no_job": 0.0,
		"tags": [],
		"choice": False,
		"problem": None,
	}

	def finish(shares, no_job, write=True):
		rows = []
		for project in set(candidates) | {p for p, q in shares.items() if q > TOLERANCE and p != NO_JOB}:
			have = holds.get(project, 0.0)
			rows.append(
				{
					"project": project,
					"tagged": tagged.get(project, 0.0),
					"holds": have,
					"short": max(tagged.get(project, 0.0) - have, 0.0),
					"qty": max(shares.get(project, 0.0), 0.0),
				}
			)
		rows.sort(key=lambda r: (-r["holds"], -r["tagged"], r["project"]))
		out["rows"] = rows
		out["no_job"] = max(no_job, 0.0) if abs(no_job) > TOLERANCE else 0.0
		tags = []
		for row in rows if write else ():
			if row["qty"] <= TOLERANCE:
				continue
			if change < 0:
				tags.append((row["project"], -row["qty"]))
			else:
				beyond = row["qty"] - min(row["qty"], row["short"])
				if beyond > TOLERANCE:
					tags.append((row["project"], beyond))
		out["tags"] = tags
		return out

	if abs(change) <= TOLERANCE:
		return finish({}, 0.0)
	if chosen is not None:
		shares = {}
		for project, qty in (chosen or {}).items():
			key = _name(project)
			shares[key] = shares.get(key, 0.0) + _qty(qty)
		shorts = {p: max(tagged.get(p, 0.0) - holds.get(p, 0.0), 0.0) for p in tagged}
		problem = _shares_problem(change, shares, holds, unclaimed, shorts)
		if problem:
			out["problem"] = problem
			return finish({}, 0.0, write=False)
		return finish(shares, shares.get(NO_JOB, 0.0))

	after = allocate(counted, claims)
	if change < 0:
		shares = {p: holds.get(p, 0.0) - after.get(p, 0.0) for p in holds}
		no_job = -change - sum(shares.values())
		sources = sum(1 for qty in holds.values() if qty > TOLERANCE) + (1 if unclaimed > TOLERANCE else 0)
		out["choice"] = sources > 1 and counted > TOLERANCE
		return finish(shares, no_job)
	shares = {p: after.get(p, 0.0) - holds.get(p, 0.0) for p in after}
	rest = change - sum(shares.values())
	no_job = 0.0
	if rest > TOLERANCE:
		if len(candidates) == 1:
			shares[candidates[0]] = shares.get(candidates[0], 0.0) + rest
		else:
			no_job = rest
	out["choice"] = len(candidates) > 1
	return finish(shares, no_job)


def count_shares(before, change, claims, tags):
	"""A count's split as a person would enter it, rebuilt from the tags it wrote: ``{project:
	qty}`` of positive amounts with :data:`NO_JOB` for the rest. What "Change whose" starts on.

	* **A count down**: each job's share is its tag (a count down writes one per share).
	* **A count up**: a share that refilled a short claim wrote **no tag** for the refill
	  (:func:`count_split`), so the tags alone would show the job too little, and saving the
	  dialog unchanged would take the refill away from it. So the shares are worked out the way
	  the shelf sees them: each job's capped share after the count (its claim plus its tags, the
	  tagged jobs as the newest arrivals) less its capped share before. A refill split between two
	  short jobs comes back in :func:`allocate`'s order, which is what the shelf shows either way.

	``before``, ``change`` and ``claims`` are :func:`count_context`'s; ``tags`` are the count's own
	``[(project, signed qty)]``."""
	before = max(_qty(before), 0.0)
	change = _qty(change)
	shares = {}
	if change < 0:
		for project, qty in tags or ():
			if _qty(qty) < -TOLERANCE:
				shares[_name(project)] = shares.get(_name(project), 0.0) - _qty(qty)
	elif change > 0:
		merged = {}
		for project, balance, last in claims or ():
			entry = merged.setdefault(_name(project), [0.0, ""])
			entry[0] += _qty(balance)
			entry[1] = max(entry[1], str(last or ""))
		for project, qty in tags or ():
			entry = merged.setdefault(_name(project), [0.0, ""])
			entry[0] += _qty(qty)
			if _qty(qty) > TOLERANCE:
				entry[1] = "~"  # after every date: the count is the newest arrival
		held = allocate(before, [(p, b, last) for p, b, last in claims or ()])
		after = allocate(before + change, [(p, entry[0], entry[1]) for p, entry in merged.items()])
		for project in set(held) | set(after):
			gain = after.get(project, 0.0) - held.get(project, 0.0)
			if gain > TOLERANCE:
				shares[project] = gain
	rest = abs(change) - sum(shares.values())
	if rest > TOLERANCE:
		shares[NO_JOB] = rest
	return shares


def chosen_split(jobs):
	"""A person's split as the page sends it (``{project: qty}``, or a JSON string of one, or
	``None``) as :func:`count_split` takes it: ``None`` for no split, else a dict of names to
	numbers. A key that is blank, ``"null"`` or ``"none"`` is :data:`NO_JOB`."""
	if jobs is None or jobs == "":
		return None
	if isinstance(jobs, str):
		import json

		try:
			jobs = json.loads(jobs)
		except ValueError:
			return {}
	if not isinstance(jobs, dict):
		return {}
	out = {}
	for project, qty in jobs.items():
		key = _name(project)
		if key.lower() in ("null", "none"):
			key = NO_JOB
		out[key] = out.get(key, 0.0) + _qty(qty)
	return out


def count_context(ledger, tags, item_code, voucher_type, voucher_no):
	"""What the shelf held of one item just before one document's rows, for a split made
	afterwards (the Bin Manager's "Change whose"): ``(before, change, claims)``, or ``None``
	when the document has no live row for the item at the shelf.

	Every ledger row before the document's first row is read as :func:`shelf_summary` reads it,
	with every tag **except the document's own**: those are the split being changed. ``change``
	is the document's whole change to the item there (a count's worked out from the balance
	before it); ``claims`` is ``[(project, tagged, last_in)]``."""
	code = _name(item_code)
	target = (_name(voucher_type), _name(voucher_no))
	others = [
		tag for tag in tags or () if (_name(tag.get("voucher_type")), _name(tag.get("voucher_no"))) != target
	]
	entries = {}
	before = None
	change = 0.0
	for step in _walk(ledger, tag_pool(others), code):
		row = step["row"]
		if (_name(row.get("voucher_type")), _name(row.get("voucher_no"))) == target:
			if before is None:
				before = step["before"]
			change += step["change"]
			continue
		if before is not None:
			break
		for tag, qty in step["named"]:
			_credit(entries, tag, code, qty, step["day"])
	if before is None:
		return None
	claims = [
		(project, entry["qty_in"] - entry["qty_out"], entry["last_in"])
		for (project, _code), entry in entries.items()
	]
	return before, change, claims


def settle_untagged(ledger, tags=()):
	"""The tags that settle every movement **off** the shelf that no job was named for: the
	cleanup of v1.843.0, keyed on this rule when it runs, not on a list.

	The ledger is read in order, as :func:`shelf_summary` reads it. A movement whose untagged
	part takes stock off the shelf, and that has no tag of its own yet, gets :func:`count_split`'s
	default for that part, worked out from what the shelf held at that moment. A transfer made
	by hand takes the same default as a count: it is the same question, whose units left.
	Movements onto the shelf are left alone (whose units they are cannot be known from the
	ledger). Each proposal is counted as it is made, so a later movement sees the claims the
	earlier ones left.

	Returns ``[{voucher_type, voucher_no, item_code, project, qty, posting_date, kind,
	choice}]``, in ledger order. ``choice`` says the default chose between sources."""
	pool = tag_pool(tags)
	tagged_keys = set(pool)
	entries = {}
	out = []
	for step in _walk(ledger, pool):
		code = step["code"]
		row = step["row"]
		for tag, qty in step["named"]:
			_credit(entries, tag, code, qty, step["day"])
		key = _key(row.get("voucher_type"), row.get("voucher_no"), code)
		if step["rest"] >= -TOLERANCE or key in tagged_keys:
			continue
		claims = [
			(project, entry["qty_in"] - entry["qty_out"], entry["last_in"])
			for (project, item), entry in entries.items()
			if item == code
		]
		split = count_split(step["before"], step["before"] + step["rest"], claims)
		for project, qty in split["tags"]:
			_credit(entries, project, code, qty, step["day"])
			out.append(
				{
					"voucher_type": row.get("voucher_type"),
					"voucher_no": row.get("voucher_no"),
					"item_code": code,
					"project": project,
					"qty": qty,
					"posting_date": step["day"],
					"kind": step["kind"],
					"choice": split["choice"],
				}
			)
	return out


def projects_summary(rows):
	"""One line per job in ``rows``: how many items it has on the shelf and how many units,
	counting only the items it has a positive balance of. Sorted by job."""
	out = {}
	for row in rows or ():
		entry = out.setdefault(row["project"], {"project": row["project"], "items": 0, "qty": 0.0})
		if row["qty"] > TOLERANCE:
			entry["items"] += 1
			entry["qty"] += row["qty"]
	return [out[name] for name in sorted(out)]


def item_exact(summary, item_code):
	"""Whether the tags account for every unit of one item on the shelf: no gap between its
	on-hand and its jobs' balances, and no job with a negative balance. Only then is "the one
	job that holds it" a fact rather than a guess, and only then is it used without asking
	(``api.stock_scan._transfer_project``). ``summary`` is a :func:`shelf_summary` answer."""
	code = _name(item_code)
	if any(row.get("item_code") == code for row in summary.get("untagged") or ()):
		return False
	return not any(
		row.get("item_code") == code and _qty(row.get("tagged")) < -TOLERANCE for row in summary.get("rows") or ()
	)


def holders(rows, item_code):
	"""``[(project, qty), ...]`` for one item: the jobs that hold it on the shelf, most first.
	``qty`` is the capped figure, so a job whose tagged stock has all left is not a holder."""
	held = [(row["project"], row["qty"]) for row in rows or () if row["item_code"] == item_code and row["qty"] > TOLERANCE]
	held.sort(key=lambda pair: (-pair[1], pair[0]))
	return held


def former_docname(shelf_docname, shelf_name=SHELF_NAME, former=FORMER_NAME):
	"""What the shelf's docname was before the rename: ``Project Shelf - SF`` answers
	``Stores - SF``. ``None`` when the shelf's docname does not start with its own name (a
	site that named another warehouse as its shelf has no old label to honour)."""
	docname = _name(shelf_docname)
	if not docname.startswith(shelf_name):
		return None
	return former + docname[len(shelf_name) :]
