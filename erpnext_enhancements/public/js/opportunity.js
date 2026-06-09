/**
 * Opportunity form script (this app's portion).
 *
 * Targets: the "Opportunity" doctype form.
 * Loaded via: hooks.py `doctype_js["Opportunity"]` — note this is only ONE of
 *   several Opportunity form scripts in that list (the substantive CRM logic
 *   lives in crm_enhancements/opportunity.js and the migrated-scripts file).
 *
 * Currently a near-empty refresh stub; the former SMS button was removed.
 */
frappe.ui.form.on("Opportunity", {
    refresh: function (frm) {
        // Removed SMS button
    }
});