"""Version-control the Project `PRJ-` naming continuity (WI-009).

The live sites name every Project ``PRJ-#####`` via a hand-created
**Document Naming Rule** (prefix ``PRJ-``, 5 digits) that overrides the
doctype's ``naming_series:`` autoname. That rule is a plain DB record owned by
Administrator — it is NOT carried by the app, so a fresh site, a restore, or a
second company would silently fall back to the stock ``PROJ-.####`` series and
break naming continuity (Drive folder names, PRJ- muscle memory, any PRJ-
prefix assumption).

This seed patch makes that naming mechanism part of the app. It is
create-only-if-absent and never touches an existing rule's live counter, so on
the production/test sites (which already have the rule) it is a pure no-op, and
on a fresh site it establishes the same ``PRJ-`` rule starting after any
projects that somehow already exist.

A Document Naming Rule (not a fixture) is the right vehicle precisely because
the record carries a live ``counter`` that a fixture would overwrite on every
deploy. The stock ``naming_series`` Property Setter is left in place as an inert
fallback — the rule takes precedence while enabled.
"""

import frappe


def execute():
	# The rule is identified by (document_type, prefix), not its hash name.
	existing = frappe.db.exists(
		"Document Naming Rule",
		{"document_type": "Project", "prefix": "PRJ-", "disabled": 0},
	)
	if existing:
		return  # production/test already have it, at their own live counter

	# Fresh site: start the counter after any pre-existing PRJ- projects so a
	# newly-seeded rule can never re-mint an existing name.
	max_existing = (
		frappe.db.sql(
			"SELECT MAX(CAST(SUBSTRING(name, 5) AS UNSIGNED)) FROM `tabProject` "
			"WHERE name LIKE 'PRJ-%'"
		)[0][0]
		or 0
	)

	rule = frappe.new_doc("Document Naming Rule")
	rule.document_type = "Project"
	rule.prefix = "PRJ-"
	rule.prefix_digits = 5
	rule.disabled = 0
	rule.priority = 0
	rule.counter = int(max_existing)
	rule.insert(ignore_permissions=True)
