frappe.ui.form.on("Opportunity", {
    refresh: function (frm) {
        if (!frm.doc.__islocal) {
            frm.trigger("add_poseidon_sms_button");
        }
    },
    add_poseidon_sms_button: function (frm) {
        let btn = frm.add_custom_button(__('Send SMS'), function () {
            if (!frm.doc.party_name) {
                frappe.msgprint(__('Please link a Party Name first.'));
                return;
            }

            let doctype_to_check = frm.doc.opportunity_from === "Lead" ? "Lead" : "Customer";
            let field_to_check = doctype_to_check === "Lead" ? "mobile_no" : "custom_accounts_phone_number";

            frappe.db.get_value(doctype_to_check, frm.doc.party_name, field_to_check, function(r) {
                if (r && r[field_to_check]) {
                    if (erpnext_enhancements.telephony) {
                        erpnext_enhancements.telephony.show_sms_dialer(r[field_to_check], frm.doc.doctype, frm.doc.name);
                    }
                } else {
                    frappe.msgprint(`Party does not have a ${field_to_check} configured.`);
                }
            });
        });

        btn.removeClass('btn-default').addClass('btn-primary');
        btn.html(`<svg class="icon icon-sm" style="margin-right: 5px;"><use href="#icon-message"></use></svg> ${__('Send SMS')}`);
    }
});