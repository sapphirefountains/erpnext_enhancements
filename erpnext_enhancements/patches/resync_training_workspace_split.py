"""Force the two Training workspaces to re-sync from their app JSON (v1.429.1).

The split: **Training** keeps the authoring and reporting console and gains an
"Open Training" Page shortcut; **My Training** is new and is the learner's own
workspace. Both need to land on a site whose rows already exist.

The importer's staleness rule is the reason this patch exists, and it is not the
one most people assume — it goes two opposite ways. A **DocType** JSON is
hash-gated (`import_file.py` carries an explicit `doc["doctype"] != "DocType"`
escape on the age check), so editing the file is enough. **Everything else
importable, `Workspace` included, is timestamp-gated**: the file must read *newer*
than the stored row or the import is skipped in silence. That is what stranded the
Training workspace's extra cards for five weeks in v1.379.0.

Both files carry a bumped `modified`, which is already enough on a site whose rows
are older. This is the belt to that suspenders: `reload_doc(..., force=True)`
rebuilds the row regardless of the age check, which is what makes the change land
on a row that has somehow been touched more recently than the file — a workspace
someone rearranged in the Desk, for instance.

**Why a learner workspace at all, given /desk/learn exists.** Because the one they
already see is the wrong one. `training.json` carries `roles: []`, which does not
mean "nobody": it means no restriction beyond the module gate, and all fifteen
Training Learner holders hold read DocPerms on fourteen Training doctypes. So the
authoring console — *Record a session*, *My Drafts*, *Awaiting Review* — has been
in every learner's sidebar all along, and there was no way to start a course from
it. Splitting is what makes the sidebar tell the truth about who you are.

Deliberately overwrites the desk copy of both. These are app-owned records and the
repo is the source of truth; somebody's own arrangement lives in a *private*
Workspace, which this does not touch.

No sidebar record is listed. This repo ships no `workspace_sidebar` file for
Training, so there would be nothing to reload and naming it would log an error on
every migrate for ever.
"""

import frappe

# (module, workspace). The HR one is here because its "Open my training" tile was a
# URL pointing at /training, which is now a redirect -- a desk user clicking it would
# leave the app and come straight back, and a Website User would be sent to a login
# page. It is a Page shortcut now.
WORKSPACES = (
	("training", "training"),
	("training", "my_training"),
	("hr_enhancements", "hr"),
)


def execute():
	for module, name in WORKSPACES:
		try:
			frappe.reload_doc(module, "workspace", name, force=True)
		except Exception:
			# A workspace that will not import must not abort a migrate, and on this
			# repo `bench migrate` IS the deploy: the release carries schema changes
			# and a learner-facing page that matter more than a desk layout.
			frappe.log_error(
				f"Could not re-sync the {name} workspace\n{frappe.get_traceback()}",
				"Workspace sync",
			)
