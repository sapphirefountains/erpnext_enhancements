/**
 * Project form script (this file's portion).
 *
 * Targets: the "Project" doctype form.
 * Loaded via: hooks.py `doctype_js["Project"]` — one of SEVERAL Project form
 *   scripts in that list (see also project_enhancements.js, project_merge.js,
 *   project_migrated_scripts.js, and the project_enhancements/* scripts).
 *
 * - Mirrors the saved Project name into the `custom_project_id` display field.
 *   (The former SMS button was removed.)
 * - Maintenance Contract access: saved projects get either a "Maintenance
 *   Contract" button jumping to the project's Active contract, or
 *   "Create > Maintenance Contract" mapping a new one — the entry point for
 *   verbal/legacy arrangements with no Sales Order or written agreement
 *   (covered features prefill from the project's Serial Nos).
 */
frappe.ui.form.on("Project", {
    refresh: function (frm) {
        if (!frm.is_new()) {
            frm.set_value('custom_project_id', frm.doc.name);

            frappe.db
                .get_value("Sapphire Maintenance Contract",
                    { project: frm.doc.name, status: "Active" }, "name")
                .then((r) => {
                    const active = r.message && r.message.name;
                    if (active) {
                        frm.add_custom_button(__("Maintenance Contract"), () => {
                            frappe.set_route("Form", "Sapphire Maintenance Contract", active);
                        });
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
