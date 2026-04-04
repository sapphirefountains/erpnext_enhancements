frappe.ui.form.on("Project", {
    refresh: function (frm) {
        // Removed SMS button

        if (!frm.is_new()) {
            frm.set_value('custom_project_id', frm.doc.name);
        }
    }
});