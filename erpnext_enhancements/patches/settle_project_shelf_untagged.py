"""Name the job of every movement off the Project Shelf that never named one (v1.843.0).

The owner's yes of 2026-11-24 ("Yes, all recommended": a count names its jobs with a Project
Shelf Tag that posts nothing, and the cleanup is keyed on the rule when it runs, not on a list).

**What was wrong.** Which job owns stock on the Project Shelf is read from the project on each
Stock Ledger row there (``inventory_enhancements/project_shelf_rules.py``). A count is a Stock
Reconciliation, which carries no project, and a transfer made by hand can carry none either. So
a count that took parts off the shelf changed what is there and no job's share, and the job
went on claiming parts that are gone. The Bin Manager caps each job at what is on hand, but the
stale claim stays, and the next delivery of the item for another job is absorbed by it. On
production on 2026-11-24 that was 231 units of 9 items: seven Bin Manager counts that day
(218 units, all to zero) and one transfer to two bins on 2026-09-23 (13 units).

**What this writes.** Project Shelf Tags, and nothing else: no GL entry, no stock ledger row, no
change to any count, transfer, item or job. Each tag names the job for a movement that left the
shelf without one (``project_shelf_rules.settle_untagged``):

* the ledger is read in order, as the shelf's own figures read it;
* a movement whose untagged part took stock **off** the shelf, and that has no tag yet, gets the
  default split a count gets (``count_split``): first out of stock no job claims, then out of
  the job whose delivery is oldest, worked out from what the shelf held at that moment;
* a movement **onto** the shelf is left alone: whose units they were cannot be read from the
  ledger.

The accounting of every movement is untouched: a count's difference stayed on the stock
adjustment account with no project, and still does. After this, each job's share of the shelf
is what is physically there.

**The rows it would have written on production on 2026-11-24**, each with one job holding the
item, so the default chose nothing (the rule run over the shelf's 60 live ledger rows that
afternoon, read-only; afterwards the shelf read exact, and a second run proposed nothing):

=====================  ==============  =========  =====
Document               Item            Job        Tag
=====================  ==============  =========  =====
MAT-STE-2026-00002     AF12Z-30-10-21  PRJ-00759  -7
MAT-STE-2026-00002     LM75-23B24      PRJ-00759  -6
MAT-RECO-2026-00235    436-020         PRJ-00604  -1
MAT-RECO-2026-00236    ECX2071-24R     PRJ-00759  -6
MAT-RECO-2026-00237    lmus-revh-05    PRJ-00701  -8
MAT-RECO-2026-00238    ZEV-018568-8pk  PRJ-00746  -1
MAT-RECO-2026-00239    THN6            PRJ-00701  -200
MAT-RECO-2026-00240    GCX3131         PRJ-00701  -1
MAT-RECO-2026-00241    SCE-30EL2410LP  PRJ-00701  -1
=====================  ==============  =========  =====

A count posted between that day and this deploy is settled the same way, by the same rule the
live code applies from this release on.

**Safe twice.** A movement that already has a tag is skipped, so a second run writes nothing.
**Never raises**: a patch that raises aborts ``bench migrate``, which on this repository is the
deploy (CLAUDE.md). A failure is logged once and printed, and the shelf reads as it did before.
Each tag is printed (document, item, job, quantity: no person's name).

Post model sync, so the Project Shelf Tag DocType exists.
"""

import frappe

NOTE = (
	"Settled by the v1.843.0 cleanup: this left the Project Shelf with no job named "
	"(a count, or a transfer made by hand). The default rule named the job."
)


def execute():
	try:
		from erpnext_enhancements.inventory_enhancements import project_shelf
		from erpnext_enhancements.inventory_enhancements import project_shelf_rules as rules

		shelf = project_shelf.warehouse()
		if not shelf:
			print("Project Shelf cleanup: no Project Shelf is set up, so there is nothing to settle.")
			return
		plan = project_shelf.settle_plan()
		if not plan:
			print(
				"Project Shelf cleanup: every movement off the shelf already names its job. Nothing written."
			)
			return
		written = 0
		for tag in plan:
			project_shelf.write_tags(
				tag["voucher_type"],
				tag["voucher_no"],
				tag["item_code"],
				[(tag["project"], tag["qty"])],
				rules.TAG_CLEANUP,
				note=NOTE,
				shelf=shelf,
			)
			written += 1
			guessed = " (the default chose between jobs)" if tag.get("choice") else ""
			print(
				f"Project Shelf cleanup: {tag['voucher_no']} {tag['item_code']} -> "
				f"{tag['project']} {tag['qty']:g}{guessed}"
			)
		print(f"Project Shelf cleanup: {written} tag(s) written. No GL entry and no stock ledger row.")
	except Exception:
		try:
			frappe.log_error(title="Project Shelf cleanup could not finish")
		except Exception:
			pass
		print(
			"Project Shelf cleanup: could not finish (an Error Log row says why). Nothing else in this "
			"migrate depends on it; run it again with bench --site <site> execute "
			"erpnext_enhancements.patches.settle_project_shelf_untagged.execute"
		)
