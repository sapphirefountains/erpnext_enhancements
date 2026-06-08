frappe.ui.form.on("Contact", {
    refresh: function (frm) {
        if (!frm.doc.__islocal) {
            frm.trigger("render_comments_section");
            frm.trigger("add_triton_call_button");
            frm.trigger("add_triton_sms_button");
        }
    },

    render_comments_section: function (frm) {
        if (erpnext_enhancements && erpnext_enhancements.render_comments_app) {
            erpnext_enhancements.render_comments_app(frm, "custom_comments_field");
        } else {
            console.error("erpnext_enhancements.render_comments_app is not defined.");
        }
    },


    add_triton_call_button: function (frm) {
        let btn = frm.add_custom_button(__('Call via Triton'), function () {
            let target_number = frm.doc.custom_phone_number || frm.doc.mobile_no || frm.doc.phone;

            if (!target_number) {
                frappe.msgprint(__('No phone number found.'));
                return;
            }

            frappe.call({
                method: "erpnext_enhancements.api.telephony.trigger_outbound_call",
                args: {
                    doctype: frm.doc.doctype,
                    docname: frm.doc.name,
                    target_number: target_number
                },
                callback: function (r) {
                    if (!r.exc) {
                        frappe.show_alert({
                            message: __('Call initiated via Triton.'),
                            indicator: 'green'
                        });
                    }
                }
            });
        });

        btn.removeClass('btn-default').addClass('btn-primary');
        btn.html(`<svg class="icon icon-sm" style="margin-right: 5px;"><use href="#icon-call"></use></svg> ${__('Call via Triton')}`);
    },

    add_triton_sms_button: function (frm) {
        let btn = frm.add_custom_button(__('Send SMS'), function () {
            let target_number = frm.doc.custom_phone_number || frm.doc.mobile_no || frm.doc.phone;

            if (!target_number) {
                frappe.msgprint(__('No phone number found.'));
                return;
            }

            if (erpnext_enhancements.telephony) {
                erpnext_enhancements.telephony.show_sms_dialer(target_number, frm.doc.doctype, frm.doc.name);
            } else {
                frappe.msgprint(__('Telephony service is not loaded.'));
            }
        });

        btn.removeClass('btn-default').addClass('btn-primary');
        btn.html(`<svg class="icon icon-sm" style="margin-right: 5px;"><use href="#icon-message"></use></svg> ${__('Send SMS')}`);
    }
});

// Migrated from Client Script "Contact - Show Account Linked to Contact".
// Mirror the first linked Customer into the custom_account field.
frappe.ui.form.on("Contact", {
    refresh: function (frm) {
        let customer_link = (frm.doc.links || []).find((l) => l.link_doctype === "Customer");
        if (customer_link) {
            frm.set_value("custom_account", customer_link.link_name);
        } else {
            frm.set_value("custom_account", "");
        }
    },
});