"""Merging a master template with a project's contracted criteria, and freezing the result.

This is the centre of the whole programme. An inspection is generated from two halves:

* the **master template** — what Sapphire checks on every job of this type, its standard of care;
* the project's **acceptance criteria** — what was promised on *this* job, from its locked
  Scope of Work.

They are merged into one ordered list of rows, and that list is **copied onto the inspection**
rather than linked to. A later edit to the master template, or to a reusable section, cannot
reach an inspection that has already been generated.

Why copying, and not linking
-----------------------------

Templates improve. Somebody will tighten a tolerance, reword a check, or retire a section, and
they should be able to. But an inspection that already happened must not change retroactively
because the master changed the following week — that is the difference between a historical
record and a description of today's standards wearing a date.

So the links this module's callers keep back to the source rows are **provenance only and are
never re-read at render**. In particular: no ``fetch_from`` on any result field. A single one
would silently un-freeze the snapshot the moment somebody edits the source, and it would look
completely normal in a diff.

Why the hash
------------

``spec_hash`` is computed over the merged list at generation and stored on the instance. It is
the authoritative provenance, in a way ``Project Inspection Template.revision`` deliberately is
not: the revision cannot see a change made *inside* a referenced section, while the hash is
taken over the rows that actually landed. Two inspections with the same hash contained the same
checks, whatever either template says today.

Imports only ``hashlib`` and ``json`` — no ``frappe``. There is no Frappe integration-test job
in CI, so the freeze property would otherwise be untestable until somebody ran a bench by hand,
and "a later template edit does not alter a generated inspection" is exactly the property that
is invisible until it has already gone wrong.
"""

import hashlib
import json

#: Where a row came from. Stored on every result row so an inspection can say, per line,
#: whether it is the company's standard or this contract's promise.
SOURCE_MASTER = "Master Template"
SOURCE_ADDENDUM = "Project Addendum"
#: Nothing writes this until sub-phase F adds carry-forward. It is in the vocabulary from the
#: start on purpose: adding an option to a Select once rows exist is a data migration, because
#: an off-options value makes a row unsaveable and no ignore flag bypasses the check.
SOURCE_CARRIED = "Carried Action"

#: The fields that decide whether two merged specs are the same inspection. Presentation-only
#: fields (location notes, section instructions) are deliberately excluded: moving a note should
#: not read as a different set of checks.
_IDENTITY_FIELDS = (
	"source",
	"source_key",
	"label",
	"acceptance_criteria",
	"check_type",
	"method",
	"uom",
	"min_value",
	"max_value",
	"options",
	"is_mandatory",
	"requires_photo",
)


def _get(row, field, default=None):
	if isinstance(row, dict):
		value = row.get(field, default)
	else:
		value = getattr(row, field, default)
	return default if value is None else value


def master_key(section, item_key):
	"""Provenance for a row that came from a reusable section."""
	return f"M:{section}:{item_key}"


def addendum_key(scope_of_work, criterion_key):
	"""Provenance for a row that came from this project's locked scope."""
	return f"A:{scope_of_work}:{criterion_key}"


def carried_key(quality_action):
	"""Provenance for a fix being re-verified. Written by sub-phase F, not by this one."""
	return f"C:{quality_action}"


def master_rows(template_sections, items_for_section):
	"""Rows from the master template, in the order an inspector works through them.

	``template_sections`` is the template's own child table; ``items_for_section`` is a callable
	taking a section name and returning that section's checks. Passing the lookup in rather than
	doing it here is what keeps this module free of ``frappe`` — and it is also what lets a test
	hand it a dict.
	"""
	rows = []
	for section_row in template_sections or []:
		section = _get(section_row, "section", "")
		if not section:
			continue
		location_note = _get(section_row, "location_note", "")
		for item in items_for_section(section) or []:
			item_key = _get(item, "item_key", "")
			rows.append(
				{
					"source": SOURCE_MASTER,
					"source_key": master_key(section, item_key),
					"section_title": section,
					"location_note": location_note,
					"label": _get(item, "label", ""),
					"acceptance_criteria": _get(item, "acceptance_criteria", ""),
					"check_type": _get(item, "check_type", "Pass/Fail"),
					"method": _get(item, "method", ""),
					"uom": _get(item, "uom", ""),
					"min_value": _get(item, "min_value", 0),
					"max_value": _get(item, "max_value", 0),
					"options": _get(item, "options", ""),
					"is_mandatory": 1 if _get(item, "is_mandatory", 0) else 0,
					"requires_photo": 1 if _get(item, "requires_photo", 0) else 0,
				}
			)
	return rows


def addendum_rows(criteria, scope_of_work, milestone, section_title="Contracted for this project"):
	"""Rows from the locked Scope of Work, for the criteria this milestone checks.

	Only criteria naming *this* milestone are included. A criterion that names no milestone is
	**not** silently dropped — :func:`unassigned_criteria` reports it, and the caller is expected
	to say so out loud, because a contracted promise that quietly never gets inspected is the
	exact failure this programme exists to end.
	"""
	rows = []
	for criterion in criteria or []:
		if _get(criterion, "inspect_at_milestone", "") != milestone:
			continue
		rows.append(
			{
				"source": SOURCE_ADDENDUM,
				"source_key": addendum_key(scope_of_work, _get(criterion, "criterion_key", "")),
				"section_title": section_title,
				"location_note": "",
				"label": _get(criterion, "criterion", ""),
				"acceptance_criteria": _get(criterion, "pass_standard", ""),
				"check_type": "Pass/Fail",
				"method": _get(criterion, "verification_method", ""),
				"uom": _get(criterion, "uom", ""),
				"min_value": 0,
				"max_value": 0,
				"options": "",
				# Contracted criteria are always mandatory: they were sold.
				"is_mandatory": 1,
				"requires_photo": 1 if _get(criterion, "is_hold_point", 0) else 0,
			}
		)
	return rows


def unassigned_criteria(criteria):
	"""Criteria that name no milestone, as ``[(criterion_key, text), ...]``.

	These will never appear on any inspection until somebody assigns them. Reported rather than
	dropped, for the reason above.
	"""
	out = []
	for criterion in criteria or []:
		if _get(criterion, "inspect_at_milestone", ""):
			continue
		out.append((_get(criterion, "criterion_key", ""), _get(criterion, "criterion", "")))
	return out


def merge(master, addendum, carried=()):
	"""The frozen row list: master first, then contracted criteria, then anything re-verified.

	Order is deliberate and stable. The company's standard of care comes first because that is
	how an inspector works a site; what was specifically promised on this job comes after it,
	where it reads as an addition rather than as a replacement.

	``sequence`` is assigned here and is 1-based, so the inspection's own row order does not
	depend on child-table ``idx`` surviving a save.
	"""
	rows = list(master) + list(addendum) + list(carried)
	for position, row in enumerate(rows, start=1):
		row["sequence"] = position
	return rows


def duplicate_source_keys(rows):
	"""Source keys appearing twice, sorted. Empty when the merge is sound.

	A duplicate means two rows claim the same provenance, so a result recorded against one of
	them is ambiguous. It is reachable: the same section listed twice on a template, or a
	criterion whose key was copied by a grid duplicate.
	"""
	seen = {}
	for row in rows or []:
		key = _get(row, "source_key", "")
		if not key:
			continue
		seen[key] = seen.get(key, 0) + 1
	return sorted(key for key, count in seen.items() if count > 1)


def spec_hash(rows):
	"""A stable content hash over the merged list. The instance's authoritative provenance.

	Order-sensitive, because the order an inspector works through checks is part of what was
	inspected. Computed over ``_IDENTITY_FIELDS`` only, so moving a location note does not read
	as a different inspection.

	``sort_keys`` and a fixed separator make this reproducible across Python versions and
	dict-ordering changes — a hash that drifts with the interpreter would be worse than no hash,
	because it would report every historical inspection as altered.
	"""
	canonical = [
		[_stringify(_get(row, field, "")) for field in _IDENTITY_FIELDS] for row in rows or []
	]
	blob = json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
	return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _stringify(value):
	"""Normalise so 0 and 0.0 and "0" do not hash differently.

	Frappe hands back Decimals and floats for numeric fields depending on how the row was
	loaded, and a hash that changed because a value arrived as ``0`` rather than ``0.0`` would
	report an unaltered inspection as altered.
	"""
	if isinstance(value, bool):
		return "1" if value else "0"
	if isinstance(value, float) and value.is_integer():
		return str(int(value))
	return str(value)


def mandatory_count(rows):
	"""How many rows must be answered before the inspection can be submitted."""
	return sum(1 for row in rows or [] if _get(row, "is_mandatory", 0))


# ---------------------------------------------------------------------------
# Answering a frozen row
# ---------------------------------------------------------------------------
#
# Here rather than in the DocType controller for the same reason as everything above: a
# controller imports ``frappe``, and there is no Frappe integration-test job in CI, so logic
# that lives there is not exercised until somebody starts a bench by hand. What counts as a
# failure decides whether an NCR is raised, which is not something to leave unasserted.

#: Offered when a frozen row names no options of its own.
DEFAULT_OPTIONS = ("Pass", "Fail", "N/A")

#: Answers that mean the check did not pass. Held explicitly rather than inferred as "anything
#: that is not Pass", which would count **N/A as a failure** and raise a non-conformance for a
#: check that did not apply to this job.
FAILING_ANSWERS = ("Fail",)


def allowed_answers(row):
	"""The answers this row accepts, from its own frozen options.

	A Select cannot do this — its options are fixed per field, not per row — which is why
	``outcome`` is Data. ``Sapphire Maintenance Result.selection`` is Data for the same reason.
	Derived in one place so the form, the controller and any future wizard cannot drift apart.
	"""
	raw = (_get(row, "options", "") or "").strip()
	if not raw:
		return DEFAULT_OPTIONS
	return tuple(line.strip() for line in raw.splitlines() if line.strip())


def is_out_of_range(row):
	"""Whether a measurement falls outside the row's **frozen** bounds.

	Read from the row's own copy, never from the section it came from: re-reading would mean a
	tolerance tightened next year silently re-judged a reading taken this year.
	"""
	if _get(row, "check_type", "") != "Measurement":
		return False
	value = _get(row, "measured_value", None)
	if value in (None, ""):
		return False
	value = float(value)
	minimum = float(_get(row, "min_value", 0) or 0)
	maximum = float(_get(row, "max_value", 0) or 0)
	if minimum and value < minimum:
		return True
	return bool(maximum and value > maximum)


def _answer(row):
	return (_get(row, "outcome", "") or "").strip()


def tally(rows):
	"""Progress over a set of answered rows.

	``fail_count`` counts a row once even when it both reads ``Fail`` and is out of range —
	two reasons for the same finding are still one finding, and double-counting would inflate
	the failure rate that first-pass yield is computed from.
	"""
	rows = rows or []
	mandatory_total = sum(1 for r in rows if _get(r, "is_mandatory", 0))
	mandatory_answered = sum(1 for r in rows if _get(r, "is_mandatory", 0) and _answer(r))
	fail_count = sum(1 for r in rows if _answer(r) in FAILING_ANSWERS or is_out_of_range(r))
	return {
		"mandatory_total": mandatory_total,
		"mandatory_answered": mandatory_answered,
		"fail_count": fail_count,
		"completion_percent": (mandatory_answered / mandatory_total * 100) if mandatory_total else 0,
	}


def unanswered_mandatory(rows):
	"""1-based positions of mandatory rows with no answer."""
	return [
		_get(row, "idx", position)
		for position, row in enumerate(rows or [], start=1)
		if _get(row, "is_mandatory", 0) and not _answer(row)
	]


def missing_evidence(rows):
	"""1-based positions of **failed** rows that owe a photo and have none.

	Only failures. Demanding a photo of every passing check trains people to attach anything,
	which is worse than not asking; a failure is the one that gets argued about later.
	"""
	return [
		_get(row, "idx", position)
		for position, row in enumerate(rows or [], start=1)
		if _get(row, "requires_photo", 0)
		and not _get(row, "photo", "")
		and (_answer(row) in FAILING_ANSWERS or is_out_of_range(row))
	]
