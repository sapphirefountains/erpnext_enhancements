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
