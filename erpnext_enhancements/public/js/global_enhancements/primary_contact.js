/**
 * Primary contact auto-fill.
 *
 * Targets: the Project, Opportunity, Lead, Supplier and Customer forms.
 * Loaded via: hooks.py `doctype_js` for ALL FIVE of those doctypes.
 *
 * When the `primary_contact` link changes, fetches that Contact's title / phone /
 * email and copies them into the read-through fields
 * primary_contact_job_title / primary_contact_phone / primary_contact_email
 * (clearing them when the contact is removed). Also wires up Frappe's standard
 * `frappe.contacts` sidebar widget on refresh.
 *
 * Two things were quietly wrong here until v1.198.0, and they compounded:
 *
 *  1. The file was registered under the "Lead" doctype_js entry ONLY, while the
 *     loop below binds five doctypes. `frappe.ui.form.on` registrations are global
 *     once the file is parsed, so on a Project this ran only if the user happened
 *     to have opened a Lead earlier in the same session — and did nothing
 *     otherwise. That is the kind of bug that looks like flakiness.
 *  2. It read `Contact.phone` / `Contact.mobile_no` (the stock fields) while the
 *     server writes and reads `custom_phone_number` / `custom_mobile_number`
 *     (sync_contact.py). So on the occasions it did run, it fetched columns this
 *     app does not populate and blanked the phone.
 *
 * Fixing only the registration would have spread defect 2 to four more forms, so
 * both halves land together.
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
				// custom_phone_number / custom_mobile_number, not the stock
				// phone / mobile_no: these are the columns sync_contact.py reads and
				// writes, and they are what the Contact form actually populates.
				// Falling back to the stock pair keeps any legacy row working.
				frappe.db.get_value('Contact', frm.doc.primary_contact,
					['custom_title', 'custom_phone_number', 'custom_mobile_number',
						'phone', 'mobile_no', 'custom_email'])
				.then(r => {
					if (r && r.message) {
						let values = r.message;
						frm.set_value('primary_contact_job_title', values.custom_title || '');
						frm.set_value('primary_contact_phone',
							values.custom_phone_number || values.custom_mobile_number ||
							values.phone || values.mobile_no || '');
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
