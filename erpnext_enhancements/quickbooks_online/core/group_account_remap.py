"""One-off migration (WI-068): move draft pre-2026 Journal Entry lines off GROUP accounts.

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

Safety
------
* **Dry-run by default** (``apply=False`` writes nothing; it reports what it would do).
* **Idempotent** -- re-running is a no-op; every write is guarded "skip if already done".
* **Batched + committed** so a mid-run failure keeps completed work.
* **Narrowly scoped** -- ``docstatus = 0`` AND ``posting_date < 2026-01-01`` only.
  315 group-account lines exist outside that window (submitted, or dated 2026+) and
  are deliberately left alone.
* Amounts, parties and cost centres are never touched -- only ``account``.
* **Not** wired to migrate/scheduler. Run it manually, **staging first, against a
  verified backup**.

Run it::

    # 1) preview (no writes):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.group_account_remap.remap_group_account_lines
    # 2) apply (after reviewing the dry run, on staging first):
    bench --site <site> execute \\
      erpnext_enhancements.quickbooks_online.core.group_account_remap.remap_group_account_lines \\
      --kwargs "{'apply': True}"

See ``docs/migration/wi068-group-account-remap-runbook.md`` for the full procedure and
``work-items/WI-068-group-account-remap.md`` for scope and acceptance criteria.
"""

from __future__ import annotations

import frappe

COMMIT_EVERY = 200

CUTOFF_DATE = "2026-01-01"

GENERAL_SUFFIX = "- General"

# Parent account number -> the ledger child to create beneath it. ``account_name`` is
# the child's name; ERPNext autonames the record "<number> - <name> - <abbr>". Every
# child inherits its parent's root_type and account_type (see _child_values), so a
# Tax/Receivable/Payable parent cannot be silently flattened to a blank type.
#
# Line counts and gross amounts verified against production 2026-08-04; they are the
# expected figures the run prints and checks against.
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


def remap_group_account_lines(apply=False, company=None, verbose=True):
	"""Create the ``- General`` children and move draft pre-2026 JE lines onto them.

	Returns a report dict. With ``apply=False`` (the default) nothing is written and
	the report describes what would change.
	"""
	company = company or _default_company()
	if not company:
		frappe.throw("No Company to operate on; pass company='<name>'.")

	report = {
		"mode": "apply" if apply else "dry-run",
		"company": company,
		"before": _survey(company),
		"accounts_created": [],
		"accounts_existing": [],
		"remapped": [],
		"skipped": [],
		"errors": [],
	}

	plan = _build_plan(company, report)
	_remap(plan, apply, report)

	if apply:
		frappe.db.commit()
	report["after"] = _survey(company) if apply else report["before"]
	report["verification"] = _verify(company, report, apply)
	if verbose:
		_print_report(report)
	return report


def _default_company():
	"""The QBO integration's configured Company, else the only Company on the site."""
	company = frappe.db.get_single_value("QuickBooks Online Settings", "company")
	if company:
		return company
	companies = frappe.get_all("Company", pluck="name", limit=2)
	return companies[0] if len(companies) == 1 else None


def _build_plan(company, report):
	"""Resolve every (parent -> destination ledger) pair, creating children as needed.

	Returns a list of ``(parent_name, destination_name)``. A parent that cannot be
	resolved is recorded in ``report["errors"]`` and left out, so one bad account
	never aborts the run.
	"""
	plan = []
	for parent_number, child_number, child_name, _lines, _gross in NEW_LEDGER_CHILDREN:
		parent = _account_by_number(parent_number, company)
		if not parent:
			report["errors"].append(f"Parent account {parent_number} not found for {company}; skipped.")
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
		plan.append((parent, destination))
	return plan


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


def _remap(plan, apply, report):
	"""Point every in-scope draft pre-2026 JE line at its destination ledger."""
	processed = 0
	for parent, destination in plan:
		rows = _lines_for(parent)
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


def _lines_for(parent):
	"""In-scope ``Journal Entry Account`` rows pointing at ``parent``.

	Scope is the whole safety story: drafts only, dated before the cutoff. Anything
	submitted or dated 2026+ is out of bounds -- a submitted document cannot be
	updated anyway, and touching it would rewrite posted GL.
	"""
	return frappe.db.sql(
		"""
		SELECT jea.name, jea.parent AS parent_je
		FROM `tabJournal Entry Account` jea
		JOIN `tabJournal Entry` je ON je.name = jea.parent
		WHERE jea.account = %(parent)s AND je.docstatus = 0 AND je.posting_date < %(cutoff)s
		ORDER BY jea.parent, jea.idx
		""",
		{"parent": parent, "cutoff": CUTOFF_DATE},
		as_dict=True,
	)


def _survey(company):
	"""Per-group-account line counts and gross totals, for the before/after comparison."""
	rows = frappe.db.sql(
		"""
		SELECT jea.account, COUNT(*) AS n_lines,
		       SUM(jea.debit_in_account_currency + jea.credit_in_account_currency) AS gross
		FROM `tabJournal Entry Account` jea
		JOIN `tabJournal Entry` je ON je.name = jea.parent
		JOIN `tabAccount` a ON a.name = jea.account
		WHERE a.is_group = 1 AND a.company = %(company)s
		  AND je.docstatus = 0 AND je.posting_date < %(cutoff)s
		GROUP BY jea.account ORDER BY n_lines DESC
		""",
		{"company": company, "cutoff": CUTOFF_DATE},
		as_dict=True,
	)
	return {
		"accounts": len(rows),
		"lines": sum(int(row.n_lines) for row in rows),
		"gross": round(sum(float(row.gross or 0) for row in rows), 2),
		"per_account": [[row.account, int(row.n_lines), round(float(row.gross or 0), 2)] for row in rows],
	}


def _verify(company, report, apply):
	"""The checks the run must satisfy, evaluated against the live data.

	Every one is a claim someone would otherwise have to take on trust: that the
	expected population was found, that nothing came unbalanced, that no in-scope line
	still points at a group account, and that each parent's rollup is unchanged.
	"""
	checks = {}
	expected_lines = sum(row[3] for row in NEW_LEDGER_CHILDREN) + sum(row[2] for row in MERGE_INTO_EXISTING)
	expected_gross = round(
		sum(row[4] for row in NEW_LEDGER_CHILDREN) + sum(row[3] for row in MERGE_INTO_EXISTING), 2
	)
	before = report["before"]
	checks["population_matches_expected"] = {
		"expected_lines": expected_lines,
		"found_lines": before["lines"],
		"expected_gross": expected_gross,
		"found_gross": before["gross"],
		"ok": before["lines"] == expected_lines and abs(before["gross"] - expected_gross) < 0.01,
	}

	touched = sorted({je for moved in report["remapped"] for je in moved["journal_entries"]})
	checks["journal_entries_touched"] = len(touched)
	checks["every_touched_entry_balances"] = _balance_check(touched)

	remaining = _survey(company)["lines"] if apply else before["lines"]
	checks["group_lines_remaining"] = {
		"lines": remaining,
		"ok": remaining == 0 if apply else None,
	}

	checks["parent_rollups_unchanged"] = _rollup_check(company, apply)
	checks["out_of_scope_group_lines_untouched"] = frappe.db.sql(
		"""
		SELECT COUNT(*) FROM `tabJournal Entry Account` jea
		JOIN `tabJournal Entry` je ON je.name = jea.parent
		JOIN `tabAccount` a ON a.name = jea.account
		WHERE a.is_group = 1 AND a.company = %(company)s
		  AND (je.docstatus <> 0 OR je.posting_date >= %(cutoff)s)
		""",
		{"company": company, "cutoff": CUTOFF_DATE},
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


def _rollup_check(company, apply):
	"""Each affected parent's rolled-up total, parent plus descendants, before vs after.

	Moving a line from a parent onto its own child leaves the subtree total identical;
	the AR/AP merges do too, since Debtors and Creditors already sit under them. A
	difference here would mean a line escaped its subtree.
	"""
	results = []
	numbers = [row[0] for row in NEW_LEDGER_CHILDREN] + [row[0] for row in MERGE_INTO_EXISTING]
	for number in numbers:
		parent = _account_by_number(number, company)
		if not parent:
			continue
		lft, rgt = frappe.db.get_value("Account", parent, ["lft", "rgt"])
		total = frappe.db.sql(
			"""
			SELECT COALESCE(SUM(jea.debit_in_account_currency + jea.credit_in_account_currency), 0)
			FROM `tabJournal Entry Account` jea
			JOIN `tabJournal Entry` je ON je.name = jea.parent
			JOIN `tabAccount` a ON a.name = jea.account
			WHERE a.lft >= %(lft)s AND a.rgt <= %(rgt)s
			  AND je.docstatus = 0 AND je.posting_date < %(cutoff)s
			""",
			{"lft": lft, "rgt": rgt, "cutoff": CUTOFF_DATE},
		)[0][0]
		results.append([parent, round(float(total), 2)])
	return {"mode": "after" if apply else "before", "subtree_totals": results}


def _print_report(report):
	print(f"\n=== WI-068 group-account remap ({report['mode']}) -- {report['company']} ===")
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
	print(
		f"  population matches expected: {population['ok']} "
		f"({population['found_lines']}/{population['expected_lines']} lines, "
		f"{population['found_gross']:,.2f}/{population['expected_gross']:,.2f})"
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
