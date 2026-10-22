"""One product in several sizes, chosen on a visit's chemical line (v1.712.0).

Nik, 2026-10-21: "add option for what size tabits (1" or 3")", and "the tabit size should
reflect across all maintenance forms that have chemicals or items used or added". Chlorine
tablets come in 1" and 3" buckets, which are bought, stocked and priced apart, so each size is
its own Item (his choice that day, over a size field on one item that would bill a 3" tablet
like a 1" one). Two Item fields tie them together:

* **Size Family** (``custom_size_family``): items with the same family are one product in
  different sizes ("Chlorine Tablets").
* **Size** (``custom_size``): this item's size within it (1", 3").

The Visit Wizard shows a chemical line whose item has a family of two or more as a size
toggle, and a tap records the other size's item (``api.maintenance_visit`` returns
:func:`size_choices` with the visit). Every other form shows a chemical by its row's
``item_name``; :func:`label_consumables` (the record's ``validate``) and
``chemical_billing.item_terms`` put the size on a name that does not already say it, so the
desk form, the review email, the Visit Review page, the customer's report, the portal, the
sign-off page and the invoice all show it without each knowing about sizes.

Nothing but ``frappe`` is imported, and both Item fields are read behind ``has_column``: the
record's ``validate`` runs during ERPNext's own test bootstrap and on a fresh install before
fixtures sync, when the custom fields do not exist yet (CLAUDE.md, "Defensive hooks").
"""

import re

import frappe

FAMILY_FIELD = "custom_size_family"
SIZE_FIELD = "custom_size"

_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def _has_size_fields():
	try:
		return bool(frappe.db.has_column("Item", FAMILY_FIELD) and frappe.db.has_column("Item", SIZE_FIELD))
	except Exception:
		return False


def size_key(size):
	"""Sort sizes as a person reads them: 1" before 3" before 10", then by the text."""
	text = (size or "").strip()
	match = _NUMBER.search(text)
	return (0 if match else 1, float(match.group()) if match else 0.0, text.lower())


#: A Data field's length (``item_name`` on the visit's row): a longer value fails the save.
NAME_LENGTH = 140


def with_size(name, size):
	"""The name with its size, unless the name already says it.

	``("CHLORINE TABLETS", '3"')`` reads ``CHLORINE TABLETS (3")``; ``('CHLORINE TABLETS, 3"',
	'3"')`` is left as it is. The size must stand as its own token, so ``1"`` is not found in
	``11"`` or ``1.1"``; compared without case, and ``3 "`` matches ``3"``. Never longer than
	:data:`NAME_LENGTH`: a long name is cut, with "…", so that the size still shows.
	"""
	name = name or ""
	size = (size or "").strip()
	if not size:
		return name
	if _says(name, size):
		return name
	if not name:
		return size[:NAME_LENGTH]
	suffix = f" ({size})"
	if len(name) + len(suffix) > NAME_LENGTH:
		room = NAME_LENGTH - len(suffix) - 1
		if room < 1:
			return name[:NAME_LENGTH]
		name = name[:room].rstrip() + "…"
	return f"{name}{suffix}"


def _normal(text):
	"""Lower case, one space between words, none before an inch or foot mark."""
	return re.sub(r"\s+(?=[\"'])", "", re.sub(r"\s+", " ", text)).strip().lower()


def _says(name, size):
	pattern = r"(?<![\w.])" + re.escape(_normal(size)) + r"(?!\w)"
	return re.search(pattern, _normal(name)) is not None


def item_sizes(items):
	"""{item: {"family", "size"}} for these item codes, only those with both fields set."""
	items = sorted({item for item in items or [] if item})
	if not items or not _has_size_fields():
		return {}
	found = {}
	for row in frappe.get_all(
		"Item", filters={"name": ["in", items]}, fields=["name", FAMILY_FIELD, SIZE_FIELD]
	):
		family = (row.get(FAMILY_FIELD) or "").strip()
		size = (row.get(SIZE_FIELD) or "").strip()
		if family and size:
			found[row.name] = {"family": family, "size": size}
	return found


def size_choices():
	"""Every enabled item that is one size of a product sold in two or more, for the wizard.

	Returns ``{family: [{"item", "item_name", "size"}, ...]}``, sizes in reading order. A
	family with one enabled item is left out: there is nothing to choose. Never raises; the
	wizard opens without size toggles rather than not at all.
	"""
	if not _has_size_fields():
		return {}
	try:
		rows = frappe.get_all(
			"Item",
			filters={FAMILY_FIELD: ["is", "set"], SIZE_FIELD: ["is", "set"], "disabled": 0},
			fields=["name", "item_name", FAMILY_FIELD, SIZE_FIELD],
		)
	except Exception:
		frappe.log_error(title="Visit Wizard: item sizes failed", message=frappe.get_traceback())
		return {}
	return group_choices(rows)


def group_choices(rows):
	"""Group Item rows into :func:`size_choices`' shape (pure, for the bench-free suite)."""
	families = {}
	for row in rows or []:
		family = (row.get(FAMILY_FIELD) or "").strip()
		size = (row.get(SIZE_FIELD) or "").strip()
		if not family or not size:
			continue
		families.setdefault(family, []).append(
			{
				"item": row.get("name"),
				"item_name": with_size(row.get("item_name") or row.get("name"), size),
				"size": size,
			}
		)
	return {
		family: sorted(choices, key=lambda choice: size_key(choice["size"]))
		for family, choices in families.items()
		if len(choices) > 1
	}


def label_consumables(doc):
	"""Put each chemical's size on its row's ``item_name`` where the name does not say it.

	``item_name`` is fetched from the Item on save, so this runs on every save and is a no-op
	once the name carries the size. Rows of an item with no size are left alone.
	"""
	rows = [row for row in doc.get("consumables") or [] if row.get("item")]
	sizes = item_sizes(row.get("item") for row in rows)
	for row in rows:
		size = (sizes.get(row.get("item")) or {}).get("size")
		if size:
			row.item_name = with_size(row.get("item_name") or row.get("item"), size)
