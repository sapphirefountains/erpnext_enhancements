# Copyright (c) 2026, Sapphire Fountains and contributors
# For license information, please see license.txt

"""Point the site-only Notifications at group addresses (TASK-2026-01240).

A role recipient resolves to whoever currently holds that role, so a departmental
alert lands in a handful of personal inboxes — and stops landing anywhere the day
somebody leaves or a role is reshuffled. A group address survives both.

**Why a patch and not a fixture, for these six.** Twenty-one Notifications exist on
this site; only six are repo-managed, and those six are handled properly in
``fixtures/notification.json``. The six below were created in the UI and carry
2.4k–3k characters of HTML body each. Fixturing them means transplanting ~15k
characters of email template into the repo, where it would immediately begin
drifting from whatever anybody edits on the site next — a bigger and worse change
than the one this task asked for. Repointing the recipients is the change; the
templates stay where they are.

The consequence, stated rather than hidden: if somebody edits these recipients in
the UI later, nothing here puts them back. The fixtured six *are* re-applied on
every migrate.

**Deliberately untouched**, and each for a reason:

* ``Error Log``, ``Integration Request`` — platform alerts on ``System Manager``.
  A role follows whoever is actually administering the system; a shared inbox
  nobody owns does not. Confirmed with Nik.
* ``New ToDo Created``, ``Remind Me Email``, ``Material Request Receipt
  Notification`` — addressed by document field (``allocated_to``, ``user``,
  ``owner``). These are aimed at one person by design, which is the exception the
  task calls out.
* ``Email Team on Opportunity Won``, ``Material Request Received``, ``Material
  Request Submission Notification`` — already on group addresses.
* ``Notification for new fiscal year`` — a disabled duplicate of ``New Fiscal Year
  Created``. Left alone rather than quietly deleted; see the PR.

``company@`` is the whole-company mailing list and is therefore not a target for
any routine alert.
"""

import frappe

# notification -> the group addresses that replace its role recipients.
REPOINT = {
	"New Lead Created": ["sales@sapphirefountains.com"],
	"New Opportunity": ["sales@sapphirefountains.com"],
	"New Project Created": ["billing@sapphirefountains.com"],
	"Project Type Change Alert": [
		"billing@sapphirefountains.com",
		"operations@sapphirefountains.com",
	],
	"Task Completed": ["operations@sapphirefountains.com"],
	"New Fiscal Year Created": ["billing@sapphirefountains.com"],
}


def execute():
	if not frappe.db.exists("DocType", "Notification"):
		return

	for name, addresses in REPOINT.items():
		if not frappe.db.exists("Notification", name):
			# Created in the UI on this site; a fresh site legitimately has none.
			continue

		doc = frappe.get_doc("Notification", name)
		target = ", ".join(addresses)

		# Idempotent: already a single cc row pointing at the same addresses.
		rows = doc.get("recipients") or []
		if len(rows) == 1 and (rows[0].cc or "") == target and not rows[0].receiver_by_role:
			continue

		# Recipients addressed by document field are personal BY DESIGN and stay.
		# None of the six have one today, but leaving the rule in the code means a
		# row added later is not silently swallowed by a re-run.
		kept = [row for row in rows if row.receiver_by_document_field]

		doc.set("recipients", [])
		for row in kept:
			doc.append("recipients", {"receiver_by_document_field": row.receiver_by_document_field})
		doc.append("recipients", {"cc": target})
		doc.save(ignore_permissions=True)

	frappe.db.commit()
