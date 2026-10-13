"""Store every approved phone, email, website, address part and name in its one form (form kit P2-C).

The values typed before P2-B normalized on save. Nik approved the rewrite on 2026-10-13 from
the Data Normalization Preview report (TASK-2026-02751), with no exceptions. The work, and
every rule for what it leaves alone, is in ``utils/normalize_cleanup.py``; this only runs it
and prints the counts into the migrate log. The same counts are left as a comment on the
report.

Cannot raise (a patch that raises aborts ``bench migrate``, which on this repo is the deploy);
safe twice (a second run plans nothing).
"""


def execute():
	try:
		from erpnext_enhancements.utils import normalize_cleanup

		s = normalize_cleanup.run()
		print(
			f"Normalization cleanup: {s['written']} of {s['planned']} written, "
			f"{s['edited_since']} edited since, {s['left_blank']} left blank, {s['failed']} failed"
			+ (f"; {s['stopped']}" if s["stopped"] else "")
		)
	except Exception:
		try:
			import frappe

			frappe.log_error(title="Data normalization cleanup", message="the patch could not run")
		except Exception:
			pass
