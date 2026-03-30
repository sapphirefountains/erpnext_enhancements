frappe.ui.form.on("Project", {
    refresh: function (frm) {
        if (!frm.doc.__islocal) {
            frm.trigger("add_poseidon_sms_button");
        }
    },
    add_poseidon_sms_button: function (frm) {
        let btn = frm.add_custom_button(__('Send SMS'), function () {
            if (!frm.doc.customer) {
                frappe.msgprint(__('Please link a Customer first.'));
                return;
            }
            frappe.db.get_value("Customer", frm.doc.customer, "custom_accounts_phone_number", function(r) {
                if (r && r.custom_accounts_phone_number) {
                    if (erpnext_enhancements.telephony) {
                        erpnext_enhancements.telephony.show_sms_dialer(r.custom_accounts_phone_number, frm.doc.doctype, frm.doc.name);
                    }
                } else {
                    frappe.msgprint(__('Customer does not have a custom_accounts_phone_number configured.'));
                }
            });
        });

        btn.removeClass('btn-default').addClass('btn-primary');
        btn.html(`<svg class="icon icon-sm" style="margin-right: 5px;"><use href="#icon-message"></use></svg> ${__('Send SMS')}`);
    }
});