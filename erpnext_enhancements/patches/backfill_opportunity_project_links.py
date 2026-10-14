"""Make the canonical Opportunity <-> Project links agree with the other two (v1.634.0, WI-024 step 1).

Three fields link a won deal to its Project, filled inconsistently over the years:

* ``Opportunity.custom_created_project`` -- the canonical **forward** link. The hand-off
  engine's idempotency and write-back field, and the only one the won-without-project
  warnings read (the Sales Dashboard backlog, the ``handoff_backlog`` KPI, the pipeline
  board's "Won -- awaiting project" column, the ``closed_won_handoff_status`` AI tool);
* ``Opportunity.custom_project`` -- hidden and legacy. Read here, **never written**: retiring
  it is a fixture change WI-024 leaves to the CRM workstream;
* ``Project.custom_opportunity`` -- the canonical **reverse** link.

On prod (2026-10-13) about 25 non-historical Closed Won deals that do have a Project still
raised the warning, because only the reverse link or only the legacy field named it. This
fills the canonical pair from the evidence already in the database, for every Closed Won
deal (historical or not: this is the link itself, not a warning rule). In order:

1. ``custom_project`` set, ``custom_created_project`` blank, and that Project exists ->
   copy it across.
2. Still blank, and Projects point back through ``custom_opportunity``: exactly one ->
   use it; several -> the one that is not Canceled, when exactly one is not; otherwise
   skip and list it for review. (CRM-OPP-2025-00653: PRJ-00597 Active, PRJ-00752 Canceled
   -> PRJ-00597.)
3. Every Closed Won deal whose ``custom_created_project`` names a Project with a blank
   ``custom_opportunity`` gives that Project its reverse link.

**One Project, one deal.** No rule hands a deal a Project that already belongs to a
different Opportunity -- by that Project's own ``custom_opportunity`` or by another deal's
``custom_created_project`` -- and rule 3 does not back-fill a Project while a different
Project already claims the deal (the guard ``reconciliation.link_opportunity_to_project``
applies to a person's link). Each of those is a *conflict*: nothing is written and the pair
is listed for review. A wrong link corrupts revenue attribution, which is the defect the
links exist to prevent. (CRM-OPP-2025-00665 -> PRJ-00581, whose reverse names
CRM-OPP-2025-00636.)

Never overwrites a non-blank ``custom_created_project`` or ``custom_opportunity``, and
leaves deals that are not Closed Won alone even when a Project points at them (prod:
CRM-OPP-2025-00600 Lost <- PRJ-00551, CRM-OPP-2025-00668 On Hold <- PRJ-00773); those are
listed in the summary only. "Blank" means blank after stripping, which is what WI-024's
``IFNULL(x,'')=''`` acceptance queries match under MariaDB's PAD SPACE collation.

**What stays non-zero on WI-024's two acceptance queries, by design**:

* ``custom_project`` set, ``custom_created_project`` blank -- a deal whose legacy value
  names a Project that does not exist or belongs to another deal, and that no
  back-pointing Project resolves (listed as ``conflicts``, or counted ``no_project`` when
  the Project is simply missing);
* a Project pointing back at a deal whose ``custom_created_project`` is blank -- a deal
  with several back-pointing Projects and not exactly one live one (``skipped_multiple``),
  or whose only candidate already belongs to another deal (``conflicts``).

Every write is ``frappe.db.set_value(..., update_modified=False)``, never ``doc.save()``:
an Opportunity save fires the closed-won project prompt (WI-024 hazard H3) and every save
fires the wildcard after_save hooks (H1); ``modified`` feeds the stalled-deal KPI.

Cannot raise -- a patch that raises aborts ``bench migrate``, which on this repo is the
deploy. Each write is guarded on its own, the review list goes to one Error Log, and the
printed tally is checked to add up (it reports a mismatch rather than asserting). Missing
columns -- a fresh install, ERPNext's test bootstrap -- make it a no-op. Safe twice: the
second run finds every link it could fill already filled.
"""

import frappe

FORWARD = "custom_created_project"
LEGACY = "custom_project"
REVERSE = "custom_opportunity"

#: This site's spelling (Property Setter ``Project-status-options``) and stock ERPNext's.
CANCELED_STATUSES = ("Canceled", "Cancelled")

#: Forward stage: one outcome per Closed Won deal whose forward link started blank.
FORWARD_OUTCOMES = (
	"forward_from_custom_project",
	"forward_from_reverse",
	"skipped_multiple",
	"forward_conflicts",
	"forward_failed",
	"no_project",
)
#: Reverse stage: one outcome per Closed Won deal with a forward link after the forward stage.
REVERSE_OUTCOMES = (
	"reverse_backfilled",
	"reverse_already_set",
	"reverse_conflicts",
	"dangling",
	"reverse_failed",
)


def _blank(value):
	return not str(value or "").strip()


def _has(doctype, column):
	try:
		return bool(frappe.db.has_column(doctype, column))
	except Exception:
		return False


def _write(doctype, name, field, value):
	frappe.db.set_value(doctype, name, field, value, update_modified=False)


def execute():
	counts = dict.fromkeys(FORWARD_OUTCOMES + REVERSE_OUTCOMES, 0)
	review = []  # human-readable lines: conflicts and skips
	not_won = []
	universe = None
	try:
		if not (_has("Opportunity", FORWARD) and _has("Project", REVERSE)):
			print(
				f"backfill_opportunity_project_links: Opportunity.{FORWARD} or Project.{REVERSE} "
				"does not exist; nothing to do."
			)
			return _tally(counts)
		universe = _run(counts, review, not_won, has_legacy=_has("Opportunity", LEGACY))
	except Exception:
		try:
			frappe.log_error(
				title="Opportunity-Project link backfill failed",
				message=frappe.get_traceback(),
			)
		except Exception:
			pass
		print("backfill_opportunity_project_links: aborted early; see the Error Log.")
	try:
		_report(counts, review, not_won, universe)
	except Exception:
		pass
	return _tally(counts)


def _run(counts, review, not_won, has_legacy):
	fields = ["name", FORWARD] + ([LEGACY] if has_legacy else [])
	won = {
		row.name: row
		for row in frappe.get_all("Opportunity", filters={"status": "Closed Won"}, fields=fields)
	}

	projects = {row.name: row for row in frappe.get_all("Project", fields=["name", "status", REVERSE])}
	# Opportunity -> the Projects whose reverse link names it.
	claims = {}
	for project in projects.values():
		if not _blank(project.get(REVERSE)):
			claims.setdefault(project.get(REVERSE), []).append(project.name)
	# Project -> every Opportunity (any status) whose forward link names it.
	taken = {}
	for row in frappe.get_all("Opportunity", filters={FORWARD: ["is", "set"]}, fields=["name", FORWARD]):
		if not _blank(row.get(FORWARD)):
			taken.setdefault(row.get(FORWARD), set()).add(row.name)

	def owner_conflict(opp, project):
		"""Another deal that already owns ``project``, or None."""
		reverse = projects[project].get(REVERSE)
		if not _blank(reverse) and reverse != opp:
			return reverse
		others = sorted(taken.get(project, set()) - {opp})
		return others[0] if others else None

	# The two partitions the tally is checked against (see _check).
	universe = {"forward": sum(1 for row in won.values() if _blank(row.get(FORWARD))), "reverse": 0}

	# -- Forward stage: rules 1 and 2 ------------------------------------------------------
	for name in sorted(won):
		opp = won[name]
		if not _blank(opp.get(FORWARD)):
			continue
		choice, outcome, notes, conflict = None, None, [], False

		# Rule 1: the legacy field, when its Project exists and is not another deal's.
		legacy = opp.get(LEGACY) if has_legacy else None
		if not _blank(legacy):
			other = owner_conflict(name, legacy) if legacy in projects else None
			if legacy not in projects:
				notes.append(f"{name}: custom_project {legacy} does not exist")
			elif other:
				conflict = True
				notes.append(f"{name}: custom_project {legacy} belongs to {other}")
			else:
				choice, outcome = legacy, "forward_from_custom_project"

		# Rule 2: the Projects pointing back; with several, the only one not Canceled.
		if choice is None and claims.get(name):
			candidates = sorted(claims[name])
			if len(candidates) > 1:
				live = [p for p in candidates if projects[p].get("status") not in CANCELED_STATUSES]
				candidates = live if len(live) == 1 else candidates
			if len(candidates) > 1:
				outcome = "skipped_multiple"
				notes.append(
					f"{name}: several Projects point back at it ({', '.join(candidates)}); none chosen"
				)
			else:
				other = owner_conflict(name, candidates[0])
				if other:
					conflict = True
					notes.append(f"{name}: Project {candidates[0]} points back at it but belongs to {other}")
				else:
					choice, outcome = candidates[0], "forward_from_reverse"

		if choice is None:
			if outcome is None:
				outcome = "forward_conflicts" if conflict else "no_project"
			counts[outcome] += 1
			review.extend(notes)
			continue

		try:
			_write("Opportunity", name, FORWARD, choice)
		except Exception:
			counts["forward_failed"] += 1
			_log_row_failure("Opportunity", name, f"{FORWARD} -> {choice}")
			continue
		opp[FORWARD] = choice
		taken.setdefault(choice, set()).add(name)
		counts[outcome] += 1

	# -- Reverse stage: rule 3 -------------------------------------------------------------
	for name in sorted(won):
		target = won[name].get(FORWARD)
		if _blank(target):
			continue
		universe["reverse"] += 1
		project = projects.get(target)
		if project is None:
			counts["dangling"] += 1
			review.append(f"{name}: custom_created_project {target} does not exist")
			continue
		reverse = project.get(REVERSE)
		if reverse == name:
			counts["reverse_already_set"] += 1
			continue
		if not _blank(reverse):
			counts["reverse_conflicts"] += 1
			review.append(f"{name}: custom_created_project {target}, but {target} points at {reverse}")
			continue
		rivals = [p for p in claims.get(name, []) if p != target]
		if rivals:
			counts["reverse_conflicts"] += 1
			review.append(
				f"{name}: custom_created_project {target} has no reverse link, but {', '.join(sorted(rivals))} "
				"already points at the deal"
			)
			continue
		try:
			_write("Project", target, REVERSE, name)
		except Exception:
			counts["reverse_failed"] += 1
			_log_row_failure("Project", target, f"{REVERSE} -> {name}")
			continue
		project[REVERSE] = name
		claims.setdefault(name, []).append(target)
		counts["reverse_backfilled"] += 1

	# -- Rule 4: reported, never written ---------------------------------------------------
	others = sorted(opp for opp in claims if opp not in won)
	if others:
		status = {
			row.name: row.status
			for row in frappe.get_all(
				"Opportunity", filters={"name": ["in", others]}, fields=["name", "status"]
			)
		}
		for opp in others:
			not_won.append(f"{opp} ({status.get(opp, 'does not exist')}) <- {', '.join(sorted(claims[opp]))}")

	return universe


def _log_row_failure(doctype, name, what):
	try:
		frappe.log_error(
			title="Opportunity-Project link backfill: write failed",
			message=f"{doctype} {name}: {what}\n{frappe.get_traceback()}",
			reference_doctype=doctype,
			reference_name=name,
		)
	except Exception:
		print(f"backfill_opportunity_project_links: write failed for {doctype} {name}: {what}")


def _tally(counts):
	"""The six figures the release notes quote, plus the partition behind them."""
	return {
		"forward_from_custom_project": counts["forward_from_custom_project"],
		"forward_from_reverse": counts["forward_from_reverse"],
		"reverse_backfilled": counts["reverse_backfilled"],
		"skipped_multiple": counts["skipped_multiple"],
		"conflicts": counts["forward_conflicts"] + counts["reverse_conflicts"],
		"failed": counts["forward_failed"] + counts["reverse_failed"],
		"detail": {k: counts[k] for k in FORWARD_OUTCOMES + REVERSE_OUTCOMES},
	}


def _check(counts, universe):
	"""Every deal lands in exactly one outcome per stage. Reports; never asserts."""
	if universe is None:
		return "not checked (the run stopped early)"
	problems = []
	for stage, keys in (("forward", FORWARD_OUTCOMES), ("reverse", REVERSE_OUTCOMES)):
		got = sum(counts[k] for k in keys)
		if got != universe[stage]:
			problems.append(f"{stage} outcomes sum to {got}, expected {universe[stage]}")
	if problems:
		return "MISMATCH: " + "; ".join(problems)
	return (
		f"consistent ({universe['forward']} blank forward links, {universe['reverse']} forward links checked)"
	)


def _report(counts, review, not_won, universe):
	tally = _tally(counts)
	summary = {k: v for k, v in tally.items() if k != "detail"}
	print(f"backfill_opportunity_project_links: {summary}")
	print(f"backfill_opportunity_project_links: detail {tally['detail']}")
	print(f"backfill_opportunity_project_links: {_check(counts, universe)}")
	for line in review:
		print(f"  review: {line}")
	for line in not_won:
		print(f"  not Closed Won, left alone: {line}")
	if review:
		try:
			frappe.log_error(
				title="Opportunity-Project links need review",
				message="WI-024 link backfill (v1.634.0) wrote nothing for these:\n" + "\n".join(review),
			)
		except Exception:
			pass
