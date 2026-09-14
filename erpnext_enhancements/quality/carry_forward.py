"""Carrying an unverified fix into the next inspection — WI-075 sub-phase F.

This is the mechanism that stops "we fixed it" being taken on faith, and it is the reason the
Quality Action lifecycle has five states rather than three.

The companion paper states the design and the reason together: *PM sign-off should not be
blocked from moving a project forward — if a fix is made, the team should keep working. But a
self-reported fix and a re-inspected fix are different levels of confidence, and only the second
one should be allowed to permanently close the record.*

So an action reaching ``PM Resolved`` is not closed. It is **claimed** by the next inspection
generated for its project, appears there as a row to answer, and closes only when somebody
standing in front of the work says it holds.

And the punch list comes free
------------------------------

An open punch-list item is structurally identical to a Quality Action awaiting re-verification:
raised against a standard, fixed by somebody, and not actually done until it has been looked at
again. So it *is* a Quality Action, flagged rather than modelled separately, and it inherits
this machinery instead of living in a spreadsheet somebody has to remember to check.

The claim, and why it is a stamp rather than a query
-----------------------------------------------------

Claiming writes ``custom_verifying_inspection`` on the action **in the same transaction** as the
generation that carried it. Without that, two inspections generated the same morning both carry
the same item and one of them closes it while the other is still open.

It is also why this runs synchronously rather than being enqueued: a prod deploy ``FLUSHDB``s the
queue redis and destroys every pending job, so a claim that lived in a background job could
vanish between generating an inspection and answering it.

Imports no ``frappe``. The decision table below is the part with consequences — an escalation
that ratchets twice, or a fix that closes without being looked at, are both silent — so it is
asserted on every push rather than whenever somebody next runs a bench.
"""

from erpnext_enhancements.quality import merge
from erpnext_enhancements.quality.lifecycle import (
	ACTION_CLOSED,
	ACTION_IN_PROGRESS,
	ACTION_PM_RESOLVED,
	escalate,
)

#: The section a carried fix appears under. Named so an inspector can see at a glance that these
#: rows are not part of the standard checklist — they are last time's failures.
CARRIED_SECTION = "Carried forward — fixes to re-check"

RESULT_PASS = "Pass"
RESULT_FAIL = "Fail"

#: Deliberately no "N/A". A carried fix either holds or it does not; "not applicable" on a
#: re-verification is ambiguous in a way that matters, because it would read as neither closed
#: nor failed and the item would sit forever. Leaving the row blank is the honest escape hatch,
#: and it releases the claim so the next inspection picks the item up again.
CARRIED_OPTIONS = f"{RESULT_PASS}\n{RESULT_FAIL}"


def carried_rows(actions, scope_note=""):
	"""Rows for open fixes awaiting re-verification, in the order they were raised.

	``actions`` is whatever the caller resolved as claimable — dicts or Frappe documents, both
	work. Each becomes one row an inspector answers.

	**Not mandatory, on purpose.** A mandatory row cannot be left blank, so an inspection at the
	pump vault would be unsubmittable because a fix in the plant room could not be checked from
	there. The spec is explicit that sign-off must not be blocked from moving a project forward,
	and nothing is lost by leaving it: an unanswered carried item releases its claim and is
	carried again by the next inspection. The pressure to answer comes from the item never going
	away, not from a locked form.
	"""
	rows = []
	for action in actions or []:
		name = merge._get(action, "name", "")
		subject = merge._get(action, "custom_subject", "") or merge._get(action, "name", "")
		raised = merge._get(action, "date", "") or merge._get(action, "creation", "")
		punch = merge._get(action, "custom_punch_list", 0)
		rows.append(
			{
				"source": merge.SOURCE_CARRIED,
				"source_key": merge.carried_key(name),
				"section_title": CARRIED_SECTION,
				"location_note": scope_note,
				"label": f"{'[Punch] ' if punch else ''}{subject}",
				"acceptance_criteria": (
					f"The fix reported on {raised} holds. Answer Fail if it has not been done, "
					f"or has been done and has not worked."
				),
				"check_type": "Pass/Fail",
				"method": "Re-inspection",
				"uom": "",
				"min_value": 0,
				"max_value": 0,
				"options": CARRIED_OPTIONS,
				"is_mandatory": 0,
				# A failed re-verification is the one that gets argued about, so it owes evidence.
				"requires_photo": 1,
			}
		)
	return rows


def decide(result, status, priority, reopen_count):
	"""What a re-verification does to the action. Returns ``(status, priority, reopens, keep)``.

	``keep`` is whether the claim stays on the action:

	* **Pass** closes it, and keeps the claim as the permanent record of *where* it was
	  verified. That is also what makes re-submitting an amended inspection a no-op.
	* **Fail** reopens it to ``In Progress``, escalates the priority **one** step, counts the
	  reopen, and releases the claim so the next inspection carries it again. It does not go back
	  to ``Open``: somebody has already worked on this, and pretending otherwise loses that.
	* **No answer** decides nothing and releases the claim. The item is carried again.

	The escalation is once per verification. A cancelled-and-resubmitted inspection must not
	ratchet a priority twice — which is why the caller checks that the action is still claimed by
	*this* inspection before calling, and why Fail releases the claim.
	"""
	result = (result or "").strip()
	if result == RESULT_PASS:
		return (ACTION_CLOSED, priority, reopen_count or 0, True)
	if result == RESULT_FAIL:
		return (ACTION_IN_PROGRESS, escalate(priority), (reopen_count or 0) + 1, False)
	return (status, priority, reopen_count or 0, False)


def claimable_filters(project, inspection_names_to_exclude=()):
	"""The filters that select actions this project's next inspection should carry.

	``PM Resolved`` and nothing else: an action still being worked on has nothing to verify, and
	a Closed or Verified one is finished. This is the spec's rule stated once, so the query and
	its test cannot drift.

	Note what is **not** here: no date filter. A ``<`` comparison on a nullable datetime silently
	matches every NULL row through Frappe's coalesce sentinel, and "resolved a while ago" is not
	the rule anyway — the rule is "resolved and not yet re-checked".
	"""
	return {
		"custom_project": project,
		"status": ACTION_PM_RESOLVED,
		"docstatus": ["!=", 2],
	}


def is_claimable(action, live_inspections):
	"""Whether an action is free to be carried, given the inspections currently in play.

	An action already claimed by an inspection that is still open or already submitted is left
	alone — otherwise two inspections carry the same item, both answer it, and the second one to
	be submitted silently overwrites the first one's verdict.
	"""
	claim = merge._get(action, "custom_verifying_inspection", "")
	if not claim:
		return True
	return claim not in (live_inspections or ())
