frappe.ui.form.on("Lead", {
	refresh: function (frm) {
		if (!frm.doc.__islocal) {
			frm.trigger("add_triton_call_button");
		}
	},

    add_triton_call_button: function (frm) {
        let btn = frm.add_custom_button(__('Call via Triton'), function () {
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
                            message: __('Call initiated via Triton.'),
                            indicator: 'green'
                        });
                    }
                }
            });
        });

        btn.removeClass('btn-default').addClass('btn-primary');
        btn.html(`<svg class="icon icon-sm" style="margin-right: 5px;"><use href="#icon-call"></use></svg> ${__('Call via Triton')}`);
    }
});

// Migrated from Client Script "Lead Create Opportunity Button".
// Adds a "Create Opportunity" button on saved, non-converted leads.
frappe.ui.form.on("Lead", {
    refresh: function (frm) {
        if (frm.is_new() || frm.doc.opportunity) {
            return;
        }

        frm.add_custom_button(__("Create Opportunity"), () => {
            const lead = frm.doc;
            frappe.new_doc("Opportunity", {
                opportunity_from: "Lead",
                lead: lead.name,
                party_name: lead.party_name,
                contact_person: lead.contact_person,
                company: lead.company,
                campaign: lead.campaign,
                source: lead.source,
                territory: lead.territory,
                custom_opportunity_name: lead.lead_name,
                opportunity_owner: lead.owner,
                custom_description: lead.custom_lead_details,
            });
        }).addClass("btn-primary");
    },
});
