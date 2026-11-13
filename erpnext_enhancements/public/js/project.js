/**
 * Project form script (this file's portion).
 *
 * Targets: the "Project" doctype form.
 * Loaded via: hooks.py `doctype_js["Project"]` — one of SEVERAL Project form
 *   scripts in that list (see also project_enhancements.js, project_merge.js,
 *   project_migrated_scripts.js, and the project_enhancements/* scripts).
 *
 * - Mirrors the saved Project name into the `custom_project_id` display field, without
 *   marking the form changed (v1.816.2; see the comment at the write). (The former SMS
 *   button was removed.)
 * - Maintenance Contract access: saved projects get either a "Maintenance
 *   Contract" button jumping to the project's Active contract, or
 *   "Create > Maintenance Contract" mapping a new one — the entry point for
 *   verbal/legacy arrangements with no Sales Order or written agreement
 *   (covered features prefill from the project's Serial Nos).
 */
frappe.ui.form.on("Project", {
    refresh: function (frm) {
        if (!frm.is_new()) {
            // Show the name WITHOUT marking the form changed. `frm.set_value` goes through
            // frappe.model.set_value, which dirties the form whenever the stored value differs
            // (empty on older Projects, stale after a rename): the form then opens as "Not Saved"
            // with nothing touched, and reload / navigation asks "Leave site?". Writing the doc
            // directly and redrawing the field does not dirty it, and the value still rides
            // along on whatever save the person makes next (the field is read-only, so only
            // this line ever sets it; read-only is a client-side flag, the server accepts it).
            if (frm.doc.custom_project_id !== frm.doc.name) {
                frm.doc.custom_project_id = frm.doc.name;
                frm.refresh_field("custom_project_id");
            }

            frappe.db
                .get_value("Sapphire Maintenance Contract",
                    { project: frm.doc.name, status: "Active" }, "name")
                .then((r) => {
                    const active = r.message && r.message.name;
                    if (active) {
                        // Jumping to the existing contract is a way of LOOKING at the
                        // project, so it groups under ERPNext's native "View" dropdown
                        // (Gantt Chart / Kanban Board) -- mirroring the create variant
                        // below, which already lives under "Create".
                        frm.add_custom_button(__("Maintenance Contract"), () => {
                            frappe.set_route("Form", "Sapphire Maintenance Contract", active);
                        }, __("View"));
                    } else {
                        frm.add_custom_button(__("Maintenance Contract"), () => {
                            frappe.model.open_mapped_doc({
                                method:
                                    "erpnext_enhancements.sapphire_maintenance.doctype.sapphire_maintenance_contract.sapphire_maintenance_contract.make_contract_from_project",
                                frm: frm,
                            });
                        }, __("Create"));
                    }
                });
        }
    }
});
