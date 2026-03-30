frappe.ui.form.on("Communication", {
    refresh: function (frm) {
        if (!frm.doc.__islocal && frm.doc.communication_medium === "SMS" && frm.doc.sent_or_received === "Received") {
            frm.trigger("add_suggest_reply_button");
        }
    },

    add_suggest_reply_button: function (frm) {
        let btn = frm.add_custom_button(__('Suggest Reply'), function () {
            btn.prop('disabled', true);
            frappe.show_alert({message: __('Drafting AI Reply...'), indicator: 'blue'});

            frappe.call({
                method: "erpnext_enhancements.api.communication.suggest_sms_reply",
                args: {
                    communication_name: frm.doc.name
                },
                callback: function (r) {
                    btn.prop('disabled', false);
                    if (!r.exc && r.message && r.message.status === "success") {
                        if (erpnext_enhancements.telephony) {
                            let phone = frm.doc.sender || frm.doc.phone_no;
                            let ref_doctype = frm.doc.reference_doctype;
                            let ref_name = frm.doc.reference_name;

                            if (!ref_doctype || !ref_name) {
                                // Try to extract from timeline links
                                if (frm.doc.timeline_links && frm.doc.timeline_links.length > 0) {
                                    ref_doctype = frm.doc.timeline_links[0].link_doctype;
                                    ref_name = frm.doc.timeline_links[0].link_name;
                                }
                            }

                            erpnext_enhancements.telephony.show_sms_dialer(phone, ref_doctype, ref_name, r.message.suggested_reply);
                        } else {
                            frappe.msgprint(__('Telephony service is not loaded.'));
                        }
                    } else {
                        frappe.msgprint(__('Failed to generate reply.'));
                    }
                }
            });
        });

        btn.removeClass('btn-default').addClass('btn-primary');
        btn.html(`<svg class="icon icon-sm" style="margin-right: 5px;"><use href="#icon-message"></use></svg> ${__('Suggest Reply')}`);
    }
});