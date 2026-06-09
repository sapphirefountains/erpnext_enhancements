/**
 * Travel Trip form script.
 *
 * Targets: the "Travel Trip" doctype form (and the Trip Ground Transport child
 *   table).
 * Loaded via: hooks.py `doctype_js["Travel Trip"]`.
 *
 * The Travel Trip refresh handler is currently a stub. The child-table handler
 * sets `transport_ref_doctype` (Vehicle vs Supplier) from the selected
 * `transport_type` so the dynamic-link field points at the right doctype.
 */
frappe.ui.form.on('Travel Trip', {
    refresh: function(frm) {
        // Any refresh logic
    }
});

frappe.ui.form.on('Trip Ground Transport', {
    transport_type: function(frm, cdt, cdn) {
        var row = locals[cdt][cdn];
        if (row.transport_type === 'Company Fleet') {
            frappe.model.set_value(cdt, cdn, 'transport_ref_doctype', 'Vehicle');
        } else if (row.transport_type === 'Rental/Third Party') {
            frappe.model.set_value(cdt, cdn, 'transport_ref_doctype', 'Supplier');
        } else {
             frappe.model.set_value(cdt, cdn, 'transport_ref_doctype', '');
        }
    }
});
