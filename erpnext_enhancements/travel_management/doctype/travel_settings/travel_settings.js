// Travel Settings form script.
//
// HRMS (Frappe HR) is an optional dependency of the Travel Management module.
// When it is not installed the Expense Claim Type doctype is absent, so the
// Expense Claim Types mapping here is meaningless — and a stored value would
// 404 this form on load (Frappe resolves link titles via getdoc). The server
// controller clears those fields on save and flags availability via __onload;
// here we hide the whole section and explain why, so the surviving settings
// (per-diem and mileage rates, automation toggles) stay usable.

frappe.ui.form.on('Travel Settings', {
	refresh(frm) {
		const available = !!(frm.doc.__onload && frm.doc.__onload.expense_claims_available);

		frm.toggle_display('expense_types_section', available);

		if (!available) {
			frm.dashboard.clear_comment();
			frm.dashboard.add_comment(
				__(
					'The HR module (Frappe HR) is not installed, so Expense Claim Type mapping and travel expense-claim generation are unavailable. The per-diem and mileage rates below still apply. Install <code>hrms</code> to enable travel finance.'
				),
				'yellow',
				true
			);
		}
	},
});
