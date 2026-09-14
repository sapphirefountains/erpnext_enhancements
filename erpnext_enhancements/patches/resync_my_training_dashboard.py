"""Land the My Training dashboard widget on a site that already has the workspace.

The widget itself is upserted by ``setup.custom_html_blocks.sync_custom_html_blocks``
on every migrate. This patch is about the *placement*, which lives on the Workspace
row — and workspaces are **timestamp-gated** by the importer, unlike DocTypes, which
are hash-gated. A file that does not read newer than the stored row is skipped in
silence, which is exactly what stranded the Training workspace's extra cards for
five weeks in v1.379.0.

``my_training.json`` carries a bumped ``modified``, which is already enough on a
site whose row is older. This is the belt to that suspenders: ``reload_doc(...,
force=True)`` rebuilds the row regardless of the age check, so the change lands
even on a row somebody has rearranged in the Desk since.

**Both halves of the placement matter, and only one of them is obvious.** v16 builds
a workspace's custom-block payload from the ``custom_blocks`` child table, not from
the ``content`` blob — so a placement written into ``content`` alone renders an
empty space with no error anywhere. The shipped JSON now carries both, and
``tests/test_dashboard_widgets.py`` asserts they agree.

Never raises. A patch that throws aborts ``bench migrate``, which on this repo is
the deploy.
"""

import frappe

WORKSPACE = "My Training"


def execute():
    try:
        frappe.reload_doc("training", "workspace", "my_training", force=True)
    except Exception:
        # A site without the workspace yet gets it from the normal fixture import;
        # there is nothing here worth failing a deploy over.
        frappe.log_error(
            title="resync_my_training_dashboard",
            message=frappe.get_traceback(),
        )
        return

    # The block itself is seeded by the after_migrate hook, which runs later in the
    # same migrate. Nothing to do here but make sure the placement survived.
    try:
        if not frappe.db.exists("Workspace", WORKSPACE):
            return
        doc = frappe.get_doc("Workspace", WORKSPACE)
        names = {row.custom_block_name for row in (doc.custom_blocks or [])}
        if "My Training Dashboard" not in names:
            frappe.log_error(
                title="resync_my_training_dashboard",
                message="My Training reloaded without its dashboard block; the seeder will re-append it.",
            )
    except Exception:
        frappe.log_error(
            title="resync_my_training_dashboard",
            message=frappe.get_traceback(),
        )
