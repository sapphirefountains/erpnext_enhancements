/**
 * Project form script (this file's portion).
 *
 * Targets: the "Project" doctype form.
 * Loaded via: hooks.py `doctype_js["Project"]` — one of SEVERAL Project form
 *   scripts in that list (see also project_enhancements.js, project_merge.js,
 *   project_migrated_scripts.js, and the project_enhancements/* scripts).
 *
 * Minimal: mirrors the saved Project name into the `custom_project_id` display
 * field. (The former SMS button was removed.)
 */
frappe.ui.form.on("Project", {
    refresh: function (frm) {
        // Removed SMS button

        if (!frm.is_new()) {
            frm.set_value('custom_project_id', frm.doc.name);
        }
    }
});