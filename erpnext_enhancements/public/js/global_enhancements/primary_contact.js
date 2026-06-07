const primary_contact_doctypes = ['Project', 'Opportunity', 'Lead', 'Supplier', 'Customer'];

primary_contact_doctypes.forEach(doctype => {
	frappe.ui.form.on(doctype, {

		refresh: function(frm) {
			if (frappe.contacts && frappe.contacts.setup) {
				frappe.contacts.setup(frm);
			}
		},
		primary_contact: function(frm) {
			if (frm.doc.primary_contact) {
				// Fetch contact details
				frappe.db.get_value('Contact', frm.doc.primary_contact,
					['custom_title', 'phone', 'mobile_no', 'custom_email'])
				.then(r => {
					if (r && r.message) {
						let values = r.message;
						frm.set_value('primary_contact_job_title', values.custom_title || '');
						frm.set_value('primary_contact_phone', values.phone || values.mobile_no || '');
						frm.set_value('primary_contact_email', values.custom_email || '');
					}
				});
			} else {
				// Clear details if contact is removed
				frm.set_value('primary_contact_job_title', '');
				frm.set_value('primary_contact_phone', '');
				frm.set_value('primary_contact_email', '');
			}
		}
	});
});
