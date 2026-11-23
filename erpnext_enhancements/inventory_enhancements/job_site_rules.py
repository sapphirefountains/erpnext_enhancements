# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Every judgement about job-site warehouses (v1.801.0). No Frappe: tested bench-free.

**What a job site is.** Material that leaves the shop for a Build job and sits at the site
for weeks is moved, not issued: a Material Transfer from the shop bin into that job's own
leaf Warehouse, under one group (``Inventory Scanner Settings.job_sites_warehouse``). The
owner's decision of 2026-11-11: one warehouse per project, not one generic "Job Site", because
ERPNext keeps on-hand by item and warehouse and has no project on that table -- a single
warehouse could say "40 fittings are on job sites" and never which site. About ten Build
projects start a year, so the tree grows by about ten leaves a year.

**What it must never do is look available.** The shop bin drops when the transfer posts, but
every figure that sums across warehouses would still count the stock: Stock Scan's search
total, the "Also at" list, and a reorder rule whose group is the whole company. The functions
here are the arithmetic that takes job sites back out (:func:`shop_and_site_totals`,
:func:`without_sites`); ``inventory_enhancements/job_sites.py`` reads the database and calls
them.

**The cost reaches the job when the material is used, not when it leaves the shop.** A
transfer moves no value. ERPNext's Project "consumed material cost" sums ledger value by
project over Stock Entries, and a transfer's two rows carry the same project and cancel. The
cost lands when someone takes the parts at the site (a Material Issue with the site's job),
which is why a take at a job site is always charged to that site's job (``site_take_project``).

Indentation is tabs (new file).
"""

import re

#: The group every site warehouse lives under, as ``warehouse_name``.
GROUP_NAME = "Job Sites"

#: What a site's ``warehouse_name`` starts with. The project id follows, then its name, so the
#: phone's lists, the voucher remarks and the Desk's stock reports all say which job without a
#: second lookup. The link that decides is the Warehouse's Project field, never this text.
SITE_PREFIX = "Job Site"

#: The Warehouse field that makes a warehouse a job site (``patches/add_job_sites_warehouse``).
SITE_FIELD = "custom_job_site_project"

#: Which Project Types get a site warehouse when the setting is blank. Service visits and small
#: jobs take parts with the job picked instead: nothing waits at the customer's for weeks.
DEFAULT_PROJECT_TYPES = ("Build",)

#: ERPNext builds the docname as ``<warehouse_name> - <abbr>`` and a docname holds 140
#: characters. 100 leaves room for any company abbreviation.
MAX_SITE_NAME = 100

_SPACES = re.compile(r"\s+")
_UNSAFE = re.compile(r"[^\w\s\-.,&()#'+]")


def _text(value):
	return _SPACES.sub(" ", str(value or "")).strip()


def project_types(setting):
	"""The Project Types that get a job site, from the setting's text (one per line, or
	comma-separated). Blank is :data:`DEFAULT_PROJECT_TYPES`. Order kept, duplicates dropped."""
	out = []
	for part in re.split(r"[\n,]", str(setting or "")):
		name = _text(part)
		if name and name not in out:
			out.append(name)
	return tuple(out) or DEFAULT_PROJECT_TYPES


def site_name(project, project_name=None):
	"""A site's ``warehouse_name``: ``Job Site <project id> <project name>``, single-spaced and
	cut to :data:`MAX_SITE_NAME`. The id is always whole; the name is what gets cut.

	A project named after its own id (``project_name`` blank or equal to the id) gets the id
	once. Production has project names that end in two spaces; a trailing space in a docname is
	invisible in every list and breaks every lookup typed by hand, so the text is collapsed.
	"""
	project = _text(project)
	# The name becomes a docname and a URL. Frappe refuses ``<`` and ``>`` in a name, and a slash
	# or a quote in one is trouble in every route and label that carries it, so anything outside
	# letters, digits and plain punctuation becomes a space before the text is collapsed.
	label = _text(_UNSAFE.sub(" ", str(project_name or "")))
	head = f"{SITE_PREFIX} {project}"
	if not label or label == project:
		return head
	return f"{head} {label}"[:MAX_SITE_NAME].rstrip()


def site_refusal(
	project, project_name, status, project_type, allowed_types, closed_statuses, has_site=False
):
	"""Why material cannot be sent to this job's site, as a sentence, or ``None``.

	* a job that is finished: material is not sent to a closed job;
	* a job of a type that does not keep material on site: the sentence says what to do
	  instead, because the person is standing at a bin with parts in hand. Asked only while
	  the job has no site (``has_site`` false): a site that exists was decided on already, by
	  a Stock Manager in the Desk if not by this rule.
	"""
	label = _text(project_name) or _text(project)
	if status in closed_statuses:
		return f"{label} is {status}. Pick a job that is still in progress."
	if has_site:
		return None
	allowed = tuple(allowed_types or DEFAULT_PROJECT_TYPES)
	if (project_type or "") not in allowed:
		kinds = " and ".join(allowed) if len(allowed) < 3 else ", ".join(allowed)
		return (
			f"Job sites are kept for {kinds} jobs, and {label} is not one. "
			"To use parts on it, go back, pick the job at the top of the page and take them."
		)
	return None


def send_refusal(project, project_name, status, closed_statuses):
	"""Why stock cannot be moved INTO a job's site that already exists, or ``None``.

	Only a finished job is refused. A site that exists is not held to the project type (a Stock
	Manager may have made one on purpose for another kind of job), and a job that has been put
	on hold still receives. The sentence says what can still be done, because a take at a
	finished job's site is refused too (its cost has nowhere to go): what is there comes back.

	The other direction is never refused: moving stock OUT of a finished job's site is how its
	leftovers get back on a shelf.
	"""
	if status not in closed_statuses:
		return None
	label = _text(project_name) or _text(project)
	return (
		f"{label} is {status}, so nothing more is sent to its job site. "
		"What is already there can be moved back to a shelf."
	)


def descendants(parent_map, root):
	"""Every warehouse under ``root``, at any depth, as a set of names (``root`` not included).

	``parent_map`` is ``{warehouse: parent_warehouse}``. Walked up each warehouse's own parent
	chain rather than read from the tree's left and right numbers: those numbers were out of
	step on production once (two groups at one position), and a parent is the part of a tree
	nobody can get wrong. A chain that loops, or runs past 64 levels, is not under anything.
	"""
	out = set()
	if not root:
		return out
	for name in parent_map or {}:
		seen = set()
		parent = parent_map.get(name)
		while parent and parent not in seen and len(seen) < 64:
			if parent == root:
				out.add(name)
				break
			seen.add(parent)
			parent = parent_map.get(parent)
	return out


def site_take_project(site_project, picked_project=None):
	"""The job a take is charged to. At a job site it is always the site's own job, whatever
	the page's job chip says: the chip is remembered for a shift, and parts sitting at one
	job's site are that job's until somebody moves them. Elsewhere it is the picked job."""
	return site_project or (picked_project or None)


def transfer_project(source_project, destination_project):
	"""The Project a transfer's stock entry carries: the destination site's job, else the
	source site's (stock coming back to the shop), else none (bin to bin, as before).

	Both ledger rows of one transfer carry the same project, so the two cancel in ERPNext's
	consumed-material sum whichever job it is. It is there so the stock ledger can be read by
	job: what went to the site, what came back.
	"""
	return destination_project or source_project or None


def shop_and_site_totals(bins, site_warehouses):
	"""``(shop, sites)``: an item's on-hand split into what the shop can use and what is on
	job sites. ``bins`` is ``[(warehouse, qty), ...]``; ``site_warehouses`` is a set of names.
	Stock Scan's search shows the first as the total and names the second beside it."""
	shop = sites = 0.0
	for warehouse, qty in bins:
		amount = float(qty or 0)
		if warehouse in site_warehouses:
			sites += amount
		else:
			shop += amount
	return shop, sites


def without_sites(projected_map, site_warehouses, ancestors):
	"""ERPNext's rolled-up projected quantities with job-site stock taken back out of every
	group above a site.

	``projected_map`` is ``reorder_item.get_item_warehouse_projected_qty``'s answer,
	``{item: {warehouse or group: qty}}``: each bin's projected quantity, added to every
	ancestor. ``ancestors`` is ``{warehouse: (parent, grandparent, ...)}``. A reorder rule whose
	``warehouse_group`` is the company's root would otherwise read stock on a job site (and an
	order on its way to one) as stock the shop has, and stay quiet when the shelf is empty.

	Returns a new map; the argument is not changed. The site's own figure stays, so a rule
	written for a site itself (nobody has one) would still read it.
	"""
	sites = set(site_warehouses or ())
	if not sites:
		return projected_map
	out = {}
	for item_code, by_warehouse in (projected_map or {}).items():
		row = dict(by_warehouse or {})
		for warehouse in sites.intersection(row):
			qty = float(row.get(warehouse) or 0)
			if not qty:
				continue
			for group in ancestors.get(warehouse, ()):
				if group in row:
					row[group] = float(row[group] or 0) - qty
		out[item_code] = row
	return out


def split_places(places):
	"""``(shop, sites)``: the places an item is on hand, the shop's first as they were and the
	job sites after. Each place is a dict with a truthy ``job_site`` when it is one."""
	shop = [p for p in places if not p.get("job_site")]
	sites = [p for p in places if p.get("job_site")]
	return shop, sites
