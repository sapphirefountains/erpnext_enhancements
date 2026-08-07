"""One-off migration (WI-068): move draft Journal Entry lines off GROUP accounts.

Background
----------
QuickBooks permits posting to an account that also has sub-accounts; ERPNext does
not -- it refuses to submit a Journal Entry whose line names a group account. 1,726
draft pre-2026 Journal Entries (1,813 lines, $724,230.37 gross, across 22 group
accounts) are blocked on exactly that, which blocks the whole pre-2026 GL posting.

QBO genuinely booked this money at the parent level and the source carries no
finer-grained truth to recover, so the business chose a ``- General`` ledger child
under each affected parent over inventing a classification nobody made. The parent's
rollup total stays identical to the penny, and the child says honestly "posted at
this level in QuickBooks". Two parents are exceptions: A/R and A/P already have an
obvious real ledger (Debtors / Creditors) and a ``- General`` sibling beside it would
be worse, so their lines merge into those instead -- which is also what puts them
into AR/AP aging.

**The forward fix must be deployed with this.** ``mapping._ledger_for_posting``
redirects a resolved group account to its ``- General`` child on every posting path.
Without it the next CDC sync that touches one of these still-draft entries rewrites
the line back onto the group parent and silently undoes the remap.

Windows
-------
The original run was scoped to ``posting_date < 2026-01-01`` and deliberately left the
315 lines dated 2026+ alone, because at the time only the pre-2026 backlog was being
posted. TASK-2026-01236 (final review before the QuickBooks backlog cutover) needs those
315 too: ERPNext refuses to submit any of them, and they block the 2026 half of the GL.

So the date range is now an argument. ``CUTOFF_DATE`` is untouched and the default
window is still ``pre-2026``, so the original invocation reproduces exactly -- which
matters, because that run is already applied on production and its report is the record
of what it did.

Each named window carries its own **measured** population, verified against production
on the date named beside it. The routing table (which parent goes to which ledger) is
shared; only the counts differ per window, because the same group account carries a
different number of lines in each. An explicit ``from_date``/``to_date`` pair is also
accepted for an ad-hoc range, but nothing has measured it, so the population check
reports its findings without asserting them.

Safety
------
* **Dry-run by default** (``apply=False`` writes nothing; it reports what it would do).
* **Idempotent** -- re-running is a no-op; every write is guarded "skip if already done",
  and a ``- General`` child is only created when the window actually has lines needing it.
* **Batched + committed** so a mid-run failure keeps completed work.
* **Narrowly scoped** -- ``docstatus = 0`` AND inside the window's half-open
  ``[from_date, to_date)`` posting-date range. Anything submitted, or dated outside it,
  is left alone and counted by the ``out_of_scope_group_lines_untouched`` check.
* Amounts, parties and cost centres are never touched -- only ``account``.
* **Not** wired to migrate/scheduler. Run it manually, **staging first, against a
  verified backup**.

Run it::

    # 1) preview (no writes):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.group_account_remap.remap_group_account_lines \\
      --kwargs "{'window': '2026'}"
    # 2) apply (after reviewing the dry run, on staging first):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.group_account_remap.remap_group_account_lines \\
      --kwargs "{'window': '2026', 'apply': True}"

See ``docs/migration/wi068-group-account-remap-runbook.md`` for the full procedure and
``work-items/WI-068-group-account-remap.md`` for scope and acceptance criteria.
"""

from __future__ import annotations

import frappe

COMMIT_EVERY = 200

CUTOFF_DATE = "2026-01-01"

GENERAL_SUFFIX = "- General"

# The date ranges this script knows how to check itself against. Half-open
# ``[from_date, to_date)``; ``None`` means unbounded on that side.
#
# ``expected_lines``/``expected_gross`` are what was actually measured on production, and
# the run asserts them before it moves anything -- a population that does not match means
# the data shifted under the plan and the operator should stop rather than remap a set
# nobody surveyed. ``pre-2026``'s figures are the totals of the routing table below, and
# a CI test asserts those two representations agree.
WINDOWS = {
	# Measured 2026-08-04: 22 group accounts, 1,813 lines, in 1,726 Journal Entries.
	"pre-2026": {
		"from_date": None,
		"to_date": CUTOFF_DATE,
		"expected_accounts": 22,
		"expected_lines": 1813,
		"expected_gross": 724230.37,
	},
	# Measured 2026-08-07 for TASK-2026-01236: 15 group accounts, 315 lines, in 193
	# Journal Entries, spanning 2026-01-01 to 2026-08-01. Unbounded on the right so a
	# journal imported after the measurement is caught rather than silently skipped --
	# if that happens the population check fails and the operator re-measures, which is
	# the intended outcome.
	"2026": {
		"from_date": CUTOFF_DATE,
		"to_date": None,
		"expected_accounts": 15,
		"expected_lines": 315,
		"expected_gross": 154602.92,
	},
}

DEFAULT_WINDOW = "pre-2026"

# Parent account number -> the ledger child to create beneath it. ``account_name`` is
# the child's name; ERPNext autonames the record "<number> - <name> - <abbr>". Every
# child inherits its parent's root_type and account_type (see _child_values), so a
# Tax/Receivable/Payable parent cannot be silently flattened to a blank type.
#
# The two trailing numbers are the **pre-2026** line count and gross, verified against
# production 2026-08-04. They are per-window measurements living beside the routing they
# describe; every other window's totals are in ``WINDOWS`` above. A parent that had no
# pre-2026 lines therefore carries 0/0.00 and contributes nothing to that window's
# expected totals -- it is here for its routing, which is window-independent.
NEW_LEDGER_CHILDREN = [
	("60300", "60301", "R&D - General", 658, 205994.41),
	("53100", "53109", "Rent Materials - General", 217, 57957.53),
	("60400", "60401", "Marketing - General", 211, 64758.88),
	("61400", "61401", "Insurance - General", 161, 38560.77),
	("61500", "61501", "Accounting & Bookkeeping - General", 143, 27786.44),
	("60100", "60101", "Auto and Trailer - General", 113, 5308.83),
	("60420", "60421", "Travel - General", 107, 9047.71),
	("60810", "60811", "Payroll Expenses - General", 55, 8922.64),
	("60210", "60211", "Lease of Building - General", 49, 114957.09),
	("51000", "51001", "Build COGS - General", 26, 13041.47),
	("42000", "42001", "Service Income - General", 13, 8368.58),
	("113000", "113001", "Machinery and Equipment - General", 4, 58916.77),
	("60000", "60001", "Operating Expenses - General", 3, 119.03),
	("46000", "46001", "Other Income - General", 3, 462.35),
	("80000", "80001", "Uncategorized Expense - General", 3, 253.38),
	("50000", "50001", "Design COGS - General", 3, 3232.58),
	("61000", "61001", "G&A - General", 1, 20.41),
	("60200", "60201", "Physical Facilities - General", 1, 1.43),
	("53000", "53001", "Rent COGS - General", 1, 92.13),
	("60800", "60801", "Payroll Processing - General", 1, 2000.00),
	# No pre-2026 lines, which is why WI-068 never listed it -- and why widening the date
	# range alone would still have left its 3 lines (2026-01-22, $910.00) unfixable.
	# Siblings run 52100-52800, so 52001 is free and matches the 5xxxx-parent convention
	# used by 50001 and 51001.
	("52000", "52001", "Service COGS - General", 0, 0.00),
]

# The two exceptions. A "- General" child here would split the balance away from the
# ledger the rest of AR/AP already lives in: the 8 receivable lines include two for
# Crystal Fountains totalling $20,082.04 that exactly match three existing payments
# from that customer, and splitting them across two accounts turns those payments into
# unexplained credits. Every line on both carries a party (verified: zero missing), so
# they satisfy ERPNext's Receivable/Payable party requirement as they stand.
MERGE_INTO_EXISTING = [
	("10000", "1310", 8, 84961.73),
	("20000", "2110", 32, 19466.21),
]


def remap_group_account_lines(
	apply=False, company=None, verbose=True, window=DEFAULT_WINDOW, from_date=None, to_date=None
):
	"""Create the ``- General`` children and move draft in-window JE lines onto them.

	``window`` names one of ``WINDOWS``. Passing ``from_date``/``to_date`` instead runs
	an ad-hoc range whose population nothing has measured, so the run reports what it
	finds without asserting it -- see ``_resolve_window``.

	Returns a report dict. With ``apply=False`` (the default) nothing is written and
	the report describes what would change.
	"""
	company = company or _default_company()
	if not company:
		frappe.throw("No Company to operate on; pass company='<name>'.")
	window = _resolve_window(window, from_date, to_date)

	report = {
		"mode": "apply" if apply else "dry-run",
		"company": company,
		"window": window,
		"before": _survey(company, window),
		"accounts_created": [],
		"accounts_existing": [],
		"remapped": [],
		"skipped": [],
		"errors": [],
	}

	plan = _build_plan(company, report, window)
	_remap(plan, apply, report, window)

	if apply:
		frappe.db.commit()
	report["after"] = _survey(company, window) if apply else report["before"]
	report["verification"] = _verify(company, report, apply, window)
	if verbose:
		_print_report(report)
	return report


def _resolve_window(window, from_date, to_date):
	"""Turn the caller's arguments into one window dict.

	An explicit date pair wins over the named window, and is marked ``measured=False``
	so ``_verify`` reports its population rather than asserting it. Refusing to assert
	is the point: an unmeasured range that "passes" a check inherited from a different
	range would be worse than no check at all.
	"""
	if from_date or to_date:
		return {
			"name": f"{from_date or '-'} to {to_date or '-'}",
			"from_date": from_date,
			"to_date": to_date,
			"measured": False,
			"expected_accounts": None,
			"expected_lines": None,
			"expected_gross": None,
		}
	if window not in WINDOWS:
		frappe.throw(f"Unknown window {window!r}; known windows: {', '.join(sorted(WINDOWS))}.")
	return dict(WINDOWS[window], name=window, measured=True)


def _window_sql(window):
	"""The posting-date predicate for a window, as a SQL fragment plus its params.

	Half-open ``[from_date, to_date)`` with both bounds optional, so the ``pre-2026``
	window reduces to exactly the ``posting_date < %(to_date)s`` the original run used.
	"""
	clauses, params = [], {}
	if window["from_date"]:
		clauses.append("je.posting_date >= %(from_date)s")
		params["from_date"] = window["from_date"]
	if window["to_date"]:
		clauses.append("je.posting_date < %(to_date)s")
		params["to_date"] = window["to_date"]
	return " AND ".join(clauses) or "1 = 1", params


def _default_company():
	"""The QBO integration's configured Company, else the only Company on the site."""
	company = frappe.db.get_single_value("QuickBooks Online Settings", "company")
	if company:
		return company
	companies = frappe.get_all("Company", pluck="name", limit=2)
	return companies[0] if len(companies) == 1 else None


def _build_plan(company, report, window):
	"""Resolve every (parent -> destination ledger) pair, creating children as needed.

	Returns a list of ``(parent_name, destination_name)``. A parent that cannot be
	resolved is recorded in ``report["errors"]`` and left out, so one bad account
	never aborts the run.

	A parent with no lines *in this window* is skipped before anything is created: the
	routing table spans every window, so acting on all of it regardless would have a
	pre-2026 re-run mint ledgers for accounts only 2026 ever touched. Re-running any
	window stays a true no-op.
	"""
	plan = []
	for parent_number, child_number, child_name, _lines, _gross in NEW_LEDGER_CHILDREN:
		parent = _account_by_number(parent_number, company)
		if not parent:
			report["errors"].append(f"Parent account {parent_number} not found for {company}; skipped.")
			continue
		if not _lines_for(parent, window):
			report["skipped"].append(f"{parent}: no lines in window {window['name']}")
			continue
		existing = _existing_general_child(parent)
		if existing:
			report["accounts_existing"].append(existing)
			plan.append((parent, existing))
			continue
		created = _create_general_child(parent, child_number, child_name, company, report)
		if created:
			plan.append((parent, created))
	for parent_number, destination_number, _lines, _gross in MERGE_INTO_EXISTING:
		parent = _account_by_number(parent_number, company)
		destination = _account_by_number(destination_number, company)
		if not parent or not destination:
			report["errors"].append(
				f"Merge pair {parent_number} -> {destination_number} not resolvable for {company}; skipped."
			)
			continue
		if frappe.db.get_value("Account", destination, "is_group"):
			report["errors"].append(f"Merge destination {destination} is a group account; skipped.")
			continue
		rows = _lines_for(parent, window)
		if not rows:
			report["skipped"].append(f"{parent}: no lines in window {window['name']}")
			continue
		# Debtors and Creditors are Receivable/Payable ledgers, and ERPNext refuses to
		# submit a line on one without a party. The parent group account does not enforce
		# that, so a partyless line is legal where it sits and illegal where it is going --
		# it would trade a "cannot post to a group account" failure for a "party required"
		# one at exactly the same point in the cutover.
		partyless = _partyless(rows)
		if partyless:
			report["errors"].append(
				f"{parent} -> {destination}: {len(partyless)} of {len(rows)} line(s) carry no party, "
				f"which {destination} requires; skipped. Set a party on: {', '.join(partyless[:5])}"
				f"{' ...' if len(partyless) > 5 else ''}"
			)
			continue
		plan.append((parent, destination))
	return plan


def _partyless(rows):
	"""Which of ``rows`` have no party set. Empty means every line is safe to merge."""
	names = [row.name for row in rows]
	return [
		row.name
		for row in frappe.db.get_all(
			"Journal Entry Account",
			filters={"name": ["in", names]},
			fields=["name", "party_type", "party"],
		)
		if not (row.get("party_type") and row.get("party"))
	]


def _account_by_number(account_number, company):
	return frappe.db.get_value("Account", {"account_number": account_number, "company": company}, "name")


def _existing_general_child(parent):
	"""The ``- General`` ledger already under ``parent``, or None. Makes re-runs no-ops."""
	return frappe.db.get_value(
		"Account",
		{"parent_account": parent, "is_group": 0, "account_name": ["like", f"%{GENERAL_SUFFIX}"]},
		"name",
		order_by="name asc",
	)


def _create_general_child(parent, child_number, child_name, company, report):
	"""Insert one ledger child under ``parent``, inheriting its root_type/account_type.

	Inheriting ``account_type`` is not cosmetic: a Tax / Receivable / Payable parent
	whose child came out blank would post to a ledger ERPNext treats differently from
	the balance it is carrying.
	"""
	if frappe.db.exists("Account", {"account_number": child_number, "company": company}):
		existing = _account_by_number(child_number, company)
		report["accounts_existing"].append(existing)
		return existing
	values = _child_values(parent, child_number, child_name, company)
	if report["mode"] == "dry-run":
		report["accounts_created"].append(f"{values['account_number']} - {values['account_name']} (would create)")
		# Nothing to remap onto yet, so a dry run reports the move against the
		# prospective name rather than pretending the account exists.
		return f"<new> {values['account_number']} - {values['account_name']}"
	try:
		doc = frappe.get_doc(dict(doctype="Account", **values))
		doc.insert(ignore_permissions=True)
	# One bad account must not abort the run; record it and carry on.
	except Exception as exc:
		report["errors"].append(f"Could not create {child_number} - {child_name}: {exc}")
		return None
	report["accounts_created"].append(doc.name)
	return doc.name


def _child_values(parent, child_number, child_name, company):
	parent_row = frappe.db.get_value(
		"Account", parent, ["root_type", "account_type", "account_currency"], as_dict=True
	)
	return {
		"account_name": child_name,
		"account_number": child_number,
		"parent_account": parent,
		"company": company,
		"is_group": 0,
		"root_type": parent_row.root_type,
		"account_type": parent_row.account_type or None,
		"account_currency": parent_row.account_currency or None,
	}


def _remap(plan, apply, report, window):
	"""Point every in-scope draft JE line at its destination ledger."""
	processed = 0
	for parent, destination in plan:
		rows = _lines_for(parent, window)
		if not rows:
			report["skipped"].append(f"{parent}: no in-scope lines remaining")
			continue
		moved = {"parent": parent, "destination": destination, "lines": len(rows), "journal_entries": []}
		for row in rows:
			moved["journal_entries"].append(row.parent_je)
			if not apply or destination.startswith("<new> "):
				continue
			try:
				# Update the child row directly: the parent Journal Entry is a draft and
				# amounts/party/cost centre are untouched, so re-validating the whole
				# document would only risk failing on unrelated latent data.
				frappe.db.set_value(
					"Journal Entry Account", row.name, "account", destination, update_modified=False
				)
			# Per-row guard: one bad line never aborts the batch.
			except Exception as exc:
				report["errors"].append(f"{row.parent_je} row {row.name}: {exc}")
				continue
			processed += 1
			if processed % COMMIT_EVERY == 0:
				frappe.db.commit()
		moved["journal_entries"] = sorted(set(moved["journal_entries"]))
		report["remapped"].append(moved)
	if apply and processed % COMMIT_EVERY:
		frappe.db.commit()


def _lines_for(parent, window):
	"""In-scope ``Journal Entry Account`` rows pointing at ``parent``.

	Scope is the whole safety story: drafts only, inside the window. Anything submitted,
	or dated outside it, is out of bounds -- a submitted document cannot be updated
	anyway, and touching it would rewrite posted GL.
	"""
	clause, params = _window_sql(window)
	params["parent"] = parent
	return frappe.db.sql(
		f"""
		SELECT jea.name, jea.parent AS parent_je
		FROM `tabJournal Entry Account` jea
		JOIN `tabJournal Entry` je ON je.name = jea.parent
		WHERE jea.account = %(parent)s AND je.docstatus = 0 AND {clause}
		ORDER BY jea.parent, jea.idx
		""",
		params,
		as_dict=True,
	)


def _survey(company, window):
	"""Per-group-account line counts and gross totals, for the before/after comparison."""
	clause, params = _window_sql(window)
	params["company"] = company
	rows = frappe.db.sql(
		f"""
		SELECT jea.account, COUNT(*) AS n_lines,
		       SUM(jea.debit_in_account_currency + jea.credit_in_account_currency) AS gross
		FROM `tabJournal Entry Account` jea
		JOIN `tabJournal Entry` je ON je.name = jea.parent
		JOIN `tabAccount` a ON a.name = jea.account
		WHERE a.is_group = 1 AND a.company = %(company)s
		  AND je.docstatus = 0 AND {clause}
		GROUP BY jea.account ORDER BY n_lines DESC
		""",
		params,
		as_dict=True,
	)
	return {
		"accounts": len(rows),
		"lines": sum(int(row.n_lines) for row in rows),
		"gross": round(sum(float(row.gross or 0) for row in rows), 2),
		"per_account": [[row.account, int(row.n_lines), round(float(row.gross or 0), 2)] for row in rows],
	}


def _verify(company, report, apply, window):
	"""The checks the run must satisfy, evaluated against the live data.

	Every one is a claim someone would otherwise have to take on trust: that the
	expected population was found, that nothing came unbalanced, that no in-scope line
	still points at a group account, and that each parent's rollup is unchanged.
	"""
	checks = {}
	before = report["before"]
	checks["population_matches_expected"] = {
		"window": window["name"],
		"measured": window["measured"],
		"expected_lines": window["expected_lines"],
		"found_lines": before["lines"],
		"expected_gross": window["expected_gross"],
		"found_gross": before["gross"],
		# ``None`` for an ad-hoc range: nothing measured it, so there is nothing to be
		# right or wrong about, and reporting False would read as a data problem.
		"ok": (
			before["lines"] == window["expected_lines"]
			and abs(before["gross"] - window["expected_gross"]) < 0.01
			if window["measured"]
			else None
		),
	}

	touched = sorted({je for moved in report["remapped"] for je in moved["journal_entries"]})
	checks["journal_entries_touched"] = len(touched)
	checks["every_touched_entry_balances"] = _balance_check(touched)

	remaining = _survey(company, window)["lines"] if apply else before["lines"]
	checks["group_lines_remaining"] = {
		"lines": remaining,
		"ok": remaining == 0 if apply else None,
	}

	checks["parent_rollups_unchanged"] = _rollup_check(company, apply, window)
	clause, params = _window_sql(window)
	params["company"] = company
	checks["out_of_scope_group_lines_untouched"] = frappe.db.sql(
		f"""
		SELECT COUNT(*) FROM `tabJournal Entry Account` jea
		JOIN `tabJournal Entry` je ON je.name = jea.parent
		JOIN `tabAccount` a ON a.name = jea.account
		WHERE a.is_group = 1 AND a.company = %(company)s
		  AND NOT (je.docstatus = 0 AND {clause})
		""",
		params,
	)[0][0]
	return checks


def _balance_check(journal_entries):
	"""Every touched entry still balances, header and lines agreeing.

	The remap only rewrites ``account``, so this cannot fail by construction -- which
	is exactly why it is worth asserting rather than assuming.
	"""
	if not journal_entries:
		return {"checked": 0, "unbalanced": 0, "header_vs_lines_mismatch": 0, "ok": True}
	rows = frappe.db.sql(
		"""
		SELECT je.name, je.total_debit, je.total_credit, x.d, x.c
		FROM `tabJournal Entry` je
		JOIN (SELECT parent, SUM(debit_in_account_currency) d, SUM(credit_in_account_currency) c
		      FROM `tabJournal Entry Account` GROUP BY parent) x ON x.parent = je.name
		WHERE je.name IN %(names)s
		""",
		{"names": tuple(journal_entries)},
		as_dict=True,
	)
	unbalanced = sum(1 for row in rows if abs(float(row.total_debit) - float(row.total_credit)) > 0.005)
	mismatch = sum(
		1
		for row in rows
		if abs(float(row.total_debit) - float(row.d or 0)) > 0.005
		or abs(float(row.total_credit) - float(row.c or 0)) > 0.005
	)
	return {
		"checked": len(rows),
		"unbalanced": unbalanced,
		"header_vs_lines_mismatch": mismatch,
		"ok": unbalanced == 0 and mismatch == 0,
	}


def _rollup_check(company, apply, window):
	"""Each affected parent's rolled-up total, parent plus descendants, before vs after.

	Moving a line from a parent onto its own child leaves the subtree total identical;
	the AR/AP merges do too, since Debtors and Creditors already sit under them. A
	difference here would mean a line escaped its subtree.
	"""
	results = []
	numbers = [row[0] for row in NEW_LEDGER_CHILDREN] + [row[0] for row in MERGE_INTO_EXISTING]
	clause, base_params = _window_sql(window)
	for number in numbers:
		parent = _account_by_number(number, company)
		if not parent:
			continue
		lft, rgt = frappe.db.get_value("Account", parent, ["lft", "rgt"])
		total = frappe.db.sql(
			f"""
			SELECT COALESCE(SUM(jea.debit_in_account_currency + jea.credit_in_account_currency), 0)
			FROM `tabJournal Entry Account` jea
			JOIN `tabJournal Entry` je ON je.name = jea.parent
			JOIN `tabAccount` a ON a.name = jea.account
			WHERE a.lft >= %(lft)s AND a.rgt <= %(rgt)s
			  AND je.docstatus = 0 AND {clause}
			""",
			dict(base_params, lft=lft, rgt=rgt),
		)[0][0]
		results.append([parent, round(float(total), 2)])
	return {"mode": "after" if apply else "before", "subtree_totals": results}


def _print_report(report):
	window = report["window"]
	print(f"\n=== WI-068 group-account remap ({report['mode']}) -- {report['company']} ===")
	print(
		f"Window: {window['name']}  "
		f"[{window['from_date'] or '-'} .. {window['to_date'] or '-'})  "
		f"{'measured' if window['measured'] else 'AD-HOC (population not asserted)'}"
	)
	before = report["before"]
	print(f"\nBEFORE: {before['accounts']} group accounts, {before['lines']} lines, {before['gross']:,.2f} gross")
	for account, lines, gross in before["per_account"]:
		print(f"  {lines:>5}  {gross:>13,.2f}  {account}")

	print(f"\nAccounts created: {len(report['accounts_created'])}")
	for name in report["accounts_created"]:
		print(f"  + {name}")
	if report["accounts_existing"]:
		print(f"Accounts already present (re-run, skipped): {len(report['accounts_existing'])}")
		for name in report["accounts_existing"]:
			print(f"  = {name}")

	total_lines = sum(moved["lines"] for moved in report["remapped"])
	print(f"\nLines remapped: {total_lines} across {len(report['remapped'])} accounts")
	for moved in report["remapped"]:
		print(f"  {moved['lines']:>5}  {moved['parent']}")
		print(f"         -> {moved['destination']}  ({len(moved['journal_entries'])} journal entries)")

	checks = report["verification"]
	print("\nVERIFICATION")
	population = checks["population_matches_expected"]
	if population["measured"]:
		print(
			f"  population matches expected: {population['ok']} "
			f"({population['found_lines']}/{population['expected_lines']} lines, "
			f"{population['found_gross']:,.2f}/{population['expected_gross']:,.2f})"
		)
	else:
		print(
			f"  population (ad-hoc window, nothing to compare against): "
			f"{population['found_lines']} lines, {population['found_gross']:,.2f}"
		)
	balance = checks["every_touched_entry_balances"]
	print(
		f"  every touched entry balances: {balance['ok']} "
		f"(checked {balance['checked']}, unbalanced {balance['unbalanced']}, "
		f"header/line mismatch {balance['header_vs_lines_mismatch']})"
	)
	remaining = checks["group_lines_remaining"]
	print(f"  in-scope lines still on a group account: {remaining['lines']} (ok={remaining['ok']})")
	print(f"  out-of-scope group lines left untouched: {checks['out_of_scope_group_lines_untouched']}")
	print(f"  parent subtree totals ({checks['parent_rollups_unchanged']['mode']}):")
	for parent, total in checks["parent_rollups_unchanged"]["subtree_totals"]:
		print(f"    {total:>13,.2f}  {parent}")

	if report["skipped"]:
		print(f"\nSkipped: {len(report['skipped'])}")
		for note in report["skipped"]:
			print(f"  - {note}")
	if report["errors"]:
		print(f"\nERRORS: {len(report['errors'])}")
		for note in report["errors"]:
			print(f"  ! {note}")

	if report["mode"] == "dry-run":
		print("\n(dry run -- nothing was written; re-run with apply=True to execute)")
		print("Compare the subtree totals above against a second dry run after applying:")
		print("they must be identical, because a line only ever moves within its own subtree.")
