# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Correct five stale strings in the four draft training courses, before they publish.

The four courses (TRN-CRS-00003..00006, 97 lessons) were written by the spec materializer on
2026-08-05 and have sat in Draft since. Three of the five corrections are things that have
simply gone false in the thirteen months since; one is a DocType that does not exist on this
site at all and never did; one is a name that is **correct** and is being disambiguated rather
than renamed.

None of this content lives in a repo file. It exists only in ``tabTraining Content Block`` on
production, so a patch is the only way to reach it.

What is being fixed, and how each was verified
---------------------------------------------------------------------------

**(a) Expense Claim does not exist here.** TRN-LSN-000080 tells the reader that Document
Intake creates "Purchase Invoice, Expense Claim, Payment Entry". There is no `Expense Claim`
DocType on this site — HRMS is not installed. `accounting_intake/actions/base.py` registers
exactly five actions, and the one named "Create Expense Claim"
(`receipt_expense.py:179`) is refused by `_require_expense_claims()` and is never even
proposed, because `expense_claims_available()` returns False. An out-of-pocket receipt is
billed as a **Reimbursement Bill** — itself a Purchase Invoice, raised against the employee's
own reimbursement supplier. This has been wrong since v1.388.0 and is the only one of the five
that would actively mislead somebody trying to do the task.

**(b) "Plaid Settings" is CORRECT and must not be renamed.** The briefing for this work said
to rename it to "Plaid Banking Settings". That would have been wrong.
`patches/rename_plaid_settings_doctype.py` (v1.361.0) renamed *this app's* Single out of the
way and handed the name `Plaid Settings` back to ERPNext's native integration — and the
Invoicing workspace's Banking card still carries a Workspace Link labelled exactly "Plaid
Settings". Renaming the lesson would have named a card that is not on the page. Both DocTypes
exist side by side now, so the lesson disambiguates instead.

**(c) and (d) Two statements the date-stamped titles were hiding.** Both lessons are titled
"... as of 5 August 2026". Removing that stamp without fixing what sits underneath would strip
the only visible warning while leaving two false claims in a published course:

* TRN-LSN-000091 says "the general ledger holds four rows". Measured 2026-09-11: it held
  exactly **4** rows created before 2026-08-05 and holds **31,452** now, with 1,324 submitted
  Sales Invoices, 1,312 Payment Entries and 11,084 Journal Entries. The claim was precisely
  right when written.
* TRN-LSN-000107 says "Fiscal Year 2027 does not exist". It exists (2027-01-01 to 2027-12-31,
  enabled).

Two neighbouring claims in the same bullet list were re-checked and are **still true**, so
they are deliberately left alone: zero Sales Orders exist, and zero Purchase Invoices are
submitted (10 exist, all drafts).

**(e) The date stamps come out of the titles.** Both lessons already carry the warning
elsewhere — their `summary` fields read "A dated snapshot of the migration" and "An honest
status list" — and TRN-LSN-000091 opens with a callout saying so explicitly. TRN-LSN-000107
has no equivalent, so it gets the same sentence. The stamp matters more than it looks:
``_materialize_lessons`` freezes lesson titles into ``toc_json`` at publish, and the version
cannot be edited afterwards.

Why the guards are the way they are
---------------------------------------------------------------------------

Matching is done in Python with ``in`` / ``str.replace``, never a SQL ``LIKE``. MariaDB's
default collation is PAD SPACE, so a trailing-space comparison in SQL is vacuously true, and
these strings carry em-dashes and ``&amp;`` entities besides.

Every row is checked for ``docstatus == 0`` on its parent version first.
``TrainingLesson._reject_edits_to_published_version`` returns **early** when
``frappe.flags.in_patch`` is set, so the controller will NOT stop this patch rewriting a
published lesson — and if it did, ``published_content_json``, ``toc_json`` and
``content_hash`` would silently disagree with the blocks, with nothing recomputing them. The
guard has to live here.

Nothing throws. A patch that raises aborts ``bench migrate``, which on this repo is the
deploy. Every row is wrapped, and a target that has already been fixed, or whose text has been
edited by hand since, is skipped and reported rather than forced.

Safe twice: each edit is keyed on the old substring still being present.
"""

import frappe

#: (Training Content Block name, expected block_key, old substring, new substring).
#: Keyed on the block's document NAME and its key together -- a name alone could be reused by
#: a future materialize, and a key alone is only unique within its lesson.
BLOCK_EDITS = [
	(
		"vou23f85n3",
		"ch6l2b3",
		"Whatever it then creates — Purchase Invoice, Expense Claim, Payment Entry — "
		"<strong>is created as a draft, never submitted</strong>.",
		"Whatever it then creates — <strong>Purchase Invoice</strong>, "
		"<strong>Reimbursement Bill</strong> (also a Purchase Invoice, raised against the "
		"employee's own reimbursement supplier rather than the merchant), "
		"<strong>Payment Entry</strong> or <strong>Purchase Receipt</strong> — "
		"<strong>is created as a draft, never submitted</strong>. There is no Expense Claim "
		"on this site: the HR app that owns it is not installed, and an out-of-pocket "
		"receipt is billed as a vendor bill, which is how QuickBooks already records it.",
	),
	(
		"tf7451n25i",
		"ch1l2b2",
		"Bank Reconciliation Statement, Plaid Settings</li>",
		"Bank Reconciliation Statement, Plaid Settings (ERPNext's own bank feed — "
		"<em>not</em> the same thing as <strong>Plaid Banking Settings</strong>, which "
		"configures the bank-balance widget on your Finance Hub)</li>",
	),
	(
		"0j4l3ncsg2",
		"ch6-l2-b2",
		"<strong>Nothing is submitted.</strong> Every invoice, payment and journal entry on "
		"the system is a draft, and the general ledger holds four rows. Financial reports "
		"read essentially zero and that is expected.",
		"<strong>Sales invoices, payments and journal entries are now submitted.</strong> "
		"This changed after the course was written: the general ledger held four rows on "
		"5 August 2026 and holds tens of thousands now. Purchase invoices are still all "
		"drafts, so the payables side of the reports still reads zero.",
	),
	(
		"gmv3bcln92",
		"ch5l3b3",
		"One hard prerequisite that has not been done yet: <strong>Fiscal Year 2027 does not "
		"exist</strong> in the system, and ERPNext rejects any posting dated in a year with "
		"no Fiscal Year record. Nothing dated 2027 can be entered until it is created.",
		"One hard prerequisite has since been met: <strong>Fiscal Year 2027 now exists</strong> "
		"(1 January to 31 December 2027). ERPNext rejects any posting dated in a year with no "
		"Fiscal Year record, so nothing dated 2027 could have been entered until it was "
		"created.",
	),
	(
		# TRN-LSN-000091 already opens with this disclaimer; TRN-LSN-000107 does not, and it
		# is about to lose the date stamp from its title.
		"gmvtuterre",
		"ch5l3b1",
		"<div class=\"ql-editor read-mode\"><p>QuickBooks remains the official book of record",
		"<div class=\"ql-editor read-mode\"><p>Everything below is true on the date of writing "
		"and is expected to change at or before cutover on 1 January 2027. If something here "
		"looks different when you read it, that is progress, not an error in the course.</p>"
		"<p>QuickBooks remains the official book of record",
	),
]

#: Lesson name -> (old title, new title). The stamp is redundant: both lessons say they are a
#: dated snapshot in their own `summary`, and both now open with the disclaimer.
TITLE_EDITS = [
	(
		"TRN-LSN-000091",
		"What is not live yet, as of 5 August 2026",
		"What is not live yet",
	),
	(
		"TRN-LSN-000107",
		"What is still in flight, as of 5 August 2026",
		"What is still in flight",
	),
]


def execute():
	_fix_blocks()
	_fix_titles()


def _is_draft(parent_lesson):
	"""True only if the lesson's course version is still unsubmitted.

	Not delegated to the controller: `_reject_edits_to_published_version` returns early
	under `frappe.flags.in_patch`, so it would wave this straight through.
	"""
	version = frappe.db.get_value("Training Lesson", parent_lesson, "course_version")
	if not version:
		return False
	return frappe.db.get_value("Training Course Version", version, "docstatus") == 0


def _fix_blocks():
	for name, key, old, new in BLOCK_EDITS:
		try:
			row = frappe.db.get_value(
				"Training Content Block", name, ["parent", "block_key", "content"], as_dict=True
			)
			if not row:
				print(f"  content block {name} is gone -- skipped")
				continue
			if row.block_key != key:
				print(f"  content block {name} has key {row.block_key!r}, expected {key!r} -- skipped")
				continue
			if not _is_draft(row.parent):
				print(f"  {row.parent} is already published -- skipped")
				continue
			content = row.content or ""
			if old not in content:
				# Already corrected, or edited by hand since. Either way, not ours to force.
				print(f"  content block {name} no longer carries the old text -- skipped")
				continue
			frappe.db.set_value("Training Content Block", name, "content", content.replace(old, new))
			print(f"  content block {name} ({row.parent}) corrected")
		except Exception:
			frappe.log_error(
				f"Could not correct training content block {name}\n{frappe.get_traceback()}",
				"Training content fix",
			)


def _fix_titles():
	for lesson, old, new in TITLE_EDITS:
		try:
			current = frappe.db.get_value("Training Lesson", lesson, "lesson_title")
			if current != old:
				print(f"  {lesson} title is {current!r}, not the expected one -- skipped")
				continue
			if not _is_draft(lesson):
				print(f"  {lesson} is already published -- skipped")
				continue
			frappe.db.set_value("Training Lesson", lesson, "lesson_title", new)
			print(f"  {lesson} retitled {new!r}")
		except Exception:
			frappe.log_error(
				f"Could not retitle training lesson {lesson}\n{frappe.get_traceback()}",
				"Training content fix",
			)
