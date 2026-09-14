# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""A failed check becomes a Non-Conformance and a corrective action — WI-075 sub-phase E.

This is the hinge the build spec describes: *a passed inspection quietly feeds performance
metrics; a failed one becomes a Non-Conformance, which becomes a Quality Action that the PM
resolves.* Everything downstream — the carry-forward in F, the Critical alert in G, the vendor
scorecard in N — hangs off records this module creates.

Runs on ``on_submit``, not on ``validate``
-------------------------------------------

An inspection in progress has failures in it that are about to be corrected on the spot, and
raising an NCR for each keystroke would make the NCR list unusable and train people to ignore
it. Submitting is the moment the inspector says "this is what I found".

What it deliberately does not do
---------------------------------

It does not decide severity. The spec's Minor / Major / Critical is a judgement about
consequence, and nothing in a checklist row carries that: a failed nozzle-pattern check might be
cosmetic or might be the reason the feature cannot be handed over. So every NCR is raised
**Minor** and the PM sets it. Guessing would be worse than asking, because a wrong Critical
pages three people and a wrong Minor hides a real problem — and an auto-assigned severity reads
as if somebody assessed it.

It does not raise one NCR per inspection. One per **failed check**, because each traces to a
different standard and each is closed separately. Grouping them would make the thing an NCR is
for — citing exactly what was agreed and exactly what failed — impossible.

Idempotency
-----------

Keyed on the result row's ``source_key``, which is frozen at generation and unique within an
inspection. Re-submitting an amended inspection does not produce a second NCR for a check that
already has one. Reached through ``custom_inspection`` + ``custom_inspection_source_key``
rather than a name convention, so a renamed document does not orphan the link.
"""

import frappe
from frappe import _

from erpnext_enhancements.quality import lifecycle, merge


def route_failures(doc, method=None):
	"""``Project Quality Inspection`` ``on_submit``: raise an NCR + action per failed check.

	Never raises. A failure to open a corrective record must not undo the submission of an
	inspection that a person has just signed — the inspection is the evidence, and losing it to
	protect its follow-up would be the wrong trade. Anything that goes wrong is logged with the
	inspection named, and ``backfill_missing_ncrs`` can be re-run.
	"""
	try:
		created = open_records_for_failures(doc)
	except Exception:
		# No bare re-raise out of here: it would publish this frame's locals to the Error Log,
		# and a result row can contain customer-visible inspection notes.
		frappe.log_error(
			title="Quality: could not raise non-conformances",
			message=f"inspection={doc.name}\n\n{frappe.get_traceback()}",
		)
		return

	if created:
		frappe.msgprint(
			_("{0} failed checks raised a Non-Conformance each. Set the severity on any that are more than Minor.").format(
				len(created)
			),
			title=_("Non-conformances raised"),
			indicator="orange",
		)


def open_records_for_failures(doc):
	"""Create the missing NCR/action pairs. Returns the NCR names created, oldest first."""
	created = []
	for row in doc.results or []:
		if not _is_failure(row):
			continue
		if _already_raised(doc.name, row.source_key):
			continue
		created.append(_raise_pair(doc, row))
	return created


def _is_failure(row):
	"""One definition, shared with the tally that drives ``fail_count``.

	Importantly this is **not** "anything that is not Pass": ``N/A`` means the check did not
	apply, and raising a non-conformance for it would be a finding against nobody.
	"""
	answer = (row.outcome or "").strip()
	return answer in merge.FAILING_ANSWERS or bool(row.out_of_range)


def _already_raised(inspection, source_key):
	return bool(
		frappe.db.exists(
			"Non Conformance",
			{"custom_inspection": inspection, "custom_inspection_source_key": source_key},
		)
	)


def _raise_pair(doc, row):
	ncr = frappe.new_doc("Non Conformance")
	ncr.update(
		{
			"subject": _subject(row),
			"status": lifecycle.NCR_OPEN,
			"details": _details(doc, row),
			"custom_project": doc.project,
			# Minor, and set by a person. See the module docstring.
			"custom_severity": "Minor",
			"custom_responsible_party": "Internal Crew",
			"custom_source": "Inspection",
			"custom_inspection": doc.name,
			"custom_inspection_source_key": row.source_key,
			"custom_scope_of_work": doc.scope_of_work,
			"custom_raised_on": frappe.utils.nowdate(),
			"custom_raised_by": frappe.session.user,
		}
	)
	ncr.insert(ignore_permissions=True)

	action = frappe.new_doc("Quality Action")
	action.update(
		{
			"custom_subject": _subject(row),
			"status": lifecycle.ACTION_OPEN,
			"custom_source_type": "NCR",
			"custom_project": doc.project,
			"custom_non_conformance": ncr.name,
			"custom_inspection": doc.name,
			# The inspector owns it until the PM reassigns. An action with no owner is an
			# action nobody is going to do.
			"custom_assigned_to": doc.inspector or frappe.session.user,
			"custom_priority": "Medium",
			"date": frappe.utils.nowdate(),
		}
	)
	action.insert(ignore_permissions=True)

	frappe.db.set_value(
		"Non Conformance", ncr.name, "custom_quality_action", action.name, update_modified=False
	)
	frappe.db.set_value(
		"Inspection Result", row.name, "non_conformance", ncr.name, update_modified=False
	)
	return ncr.name


def _subject(row):
	"""Short enough for a list view, specific enough to recognise without opening it."""
	label = (row.label or "").strip().replace("\n", " ")
	return (label[:117] + "...") if len(label) > 120 else label


def _details(doc, row):
	"""Everything an argument about this failure would need, in the record itself.

	Written out rather than left as links on purpose: this is the text somebody pastes into an
	email to a subcontractor, and a link they cannot open is not evidence.
	"""
	parts = [
		f"<p><b>Check:</b> {frappe.utils.escape_html(row.label or '')}</p>",
		f"<p><b>Passes when:</b> {frappe.utils.escape_html(row.acceptance_criteria or '')}</p>",
		f"<p><b>Recorded:</b> {frappe.utils.escape_html(row.outcome or '(no answer)')}</p>",
	]
	if row.check_type == "Measurement":
		parts.append(
			f"<p><b>Measured:</b> {row.measured_value} {frappe.utils.escape_html(row.uom or '')}"
			f" (range {row.min_value}–{row.max_value})</p>"
		)
	if row.notes:
		parts.append(f"<p><b>Inspector's note:</b> {frappe.utils.escape_html(row.notes)}</p>")
	parts.append(
		f"<p><b>Source:</b> {frappe.utils.escape_html(row.source or '')} — "
		f"{frappe.utils.escape_html(row.section_title or '')}</p>"
	)
	parts.append(
		f"<p>Raised from inspection {doc.name} at milestone {frappe.utils.escape_html(doc.milestone or '')}.</p>"
	)
	return "".join(parts)
