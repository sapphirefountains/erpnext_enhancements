/**
 * Primary contact auto-fill.
 *
 * Targets: the Project, Opportunity, Lead, Supplier and Customer forms.
 * Loaded via: hooks.py `doctype_js` for those doctypes (e.g. it is listed under
 * the "Lead" entry; the loop below binds the shared behaviour to all five).
 *
 * When the `primary_contact` link changes, fetches that Contact's title / phone /
 * email and copies them into the read-through fields
 * primary_contact_job_title / primary_contact_phone / primary_contact_email
 * (clearing them when the contact is removed). Also wires up Frappe's standard
 * `frappe.contacts` sidebar widget on refresh.
 */
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
