frappe.ui.form.on("Customer", {
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.trigger("render_comments_section");
            frm.trigger("add_poseidon_call_button");
            frm.trigger("add_poseidon_sms_button");
		}
	},

	render_comments_section: function (frm) {
		if (erpnext_enhancements && erpnext_enhancements.render_comments_app) {
			erpnext_enhancements.render_comments_app(frm, "custom_comments_field");
		} else {
			console.error("erpnext_enhancements.render_comments_app is not defined.");
		}
	},


    add_poseidon_sms_button: function (frm) {
        let btn = frm.add_custom_button(__('Send SMS'), function () {
            let target_number = frm.doc.custom_accounts_phone_number || frm.doc.custom_phone_number;

            if (!target_number) {
                frappe.msgprint(__('No phone number found (checked custom_accounts_phone_number and custom_phone_number).'));
                return;
            }

            if (erpnext_enhancements.telephony) {
                erpnext_enhancements.telephony.show_sms_dialer(target_number, frm.doc.doctype, frm.doc.name);
            }
        });

        btn.removeClass('btn-default').addClass('btn-primary');
    }

    add_poseidon_call_button: function (frm) {
        let btn = frm.add_custom_button(__('Call via Poseidon'), function () {
            let target_number = frm.doc.custom_accounts_phone_number || frm.doc.custom_phone_number;

            if (!target_number) {
                frappe.msgprint(__('No phone number found (checked custom_accounts_phone_number and custom_phone_number).'));
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
                            message: __('Call initiated via Poseidon.'),
                            indicator: 'green'
                        });
                    }
                }
            });
        });

        btn.removeClass('btn-default').addClass('btn-primary');
        btn.html(`<svg class="icon icon-sm" style="margin-right: 5px;"><use href="#icon-call"></use></svg> ${__('Call via Poseidon')}`);
    }
});
