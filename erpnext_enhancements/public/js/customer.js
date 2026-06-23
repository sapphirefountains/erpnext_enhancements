/**
 * Customer form script.
 *
 * Targets: the "Customer" doctype form.
 * Loaded via: hooks.py `doctype_js["Customer"]` (with vue.global.js +
 *   comments.js + the unified tab controller).
 *
 * First form.on block: hides the Connections tab / dashboard, mounts the custom
 * Comments App into `custom_comments_field` (see comments.js), and adds
 * "Call via Triton" / "Send SMS" telephony buttons.
 *
 * Second form.on block (migrated from the "Create Contact from Accounts",
 * "Auto Reminder for Accounts" and "Accounts (Customer) Tables" Client Scripts):
 * adds a "Create" dropdown for related docs (Contact/Address/Lead/Prospect/
 * Opportunity/Project), defaults `custom_reminder_days` and the `custom_prospect`
 * reminder gate from the account status, and populates the related Projects/
 * Opportunities/Leads child tables on load.
 */
frappe.ui.form.on("Customer", {
	refresh: function (frm) {
		// Hide the Connections tab / Dashboard
		if (frm.fields_dict.connections_tab) {
			frm.set_df_property("connections_tab", "hidden", 1);
		}
		if (frm.dashboard) {
			frm.dashboard.hide();
		}

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


    add_triton_sms_button: function (frm) {
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
        btn.html(`<svg class="icon icon-sm" style="margin-right: 5px;"><use href="#icon-message"></use></svg> ${__('Send SMS')}`);
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

// Customer form scripts migrated from Client Scripts for version control.
// Sources:
//   - "Create Contact from Accounts (Customer)" (Customer, Form)
//   - "Auto Reminder for Accounts" (Customer, Form)
//   - "Accounts (Customer) Tables" (Customer, Form)
frappe.ui.form.on("Customer", {
    // Source: "Create Contact from Accounts (Customer)"
    // Adds a "Create" dropdown to spawn related docs pre-linked to this customer.
    refresh: function (frm) {
        if (frm.is_new()) {
            return;
        }

        frm.add_custom_button(
            __("Contact"),
            function () {
                frappe.new_doc("Contact", {
                    links: [{ link_doctype: "Customer", link_name: frm.doc.name }],
                });
            },
            __("Create")
        );

        frm.add_custom_button(
            __("Address"),
            function () {
                frappe.new_doc("Address", {
                    links: [{ link_doctype: "Customer", link_name: frm.doc.name }],
                });
            },
            __("Create")
        );

        frm.add_custom_button(
            __("Lead"),
            function () {
                frappe.new_doc("Lead", {
                    lead_name: frm.doc.customer_name,
                    company_name: frm.doc.customer_name,
                });
            },
            __("Create")
        );

        frm.add_custom_button(
            __("Prospect"),
            function () {
                frappe.new_doc("Prospect", { prospect_name: frm.doc.customer_name });
            },
            __("Create")
        );

        frm.add_custom_button(
            __("Opportunity"),
            function () {
                frappe.new_doc("Opportunity", {
                    opportunity_from: "Customer",
                    party_name: frm.doc.name,
                });
            },
            __("Create")
        );

        frm.add_custom_button(
            __("Project"),
            function () {
                frappe.new_doc("Project", {
                    project_name: frm.doc.customer_name,
                    customer: frm.doc.name,
                });
            },
            __("Create")
        );
    },

    // Source: "Auto Reminder for Accounts"
    // Default the reminder cadence and the Prospect gate from the account status.
    custom_account_status: function (frm) {
        let days = 0;
        switch (frm.doc.custom_account_status) {
            case "Prospect":
                days = 7;
                break;
            case "Opportunity":
                days = 3;
                break;
            case "Champion":
                days = 90;
                break;
            default:
                days = 0;
        }
        frm.set_value("custom_reminder_days", days);

        // Prospect and Champion are the statuses we actively follow up with, so
        // keep the Prospect gate (custom_prospect) in sync with the status: it
        // drives the daily inactivity reminder and reveals the Activity Reminder
        // section. The checkbox stays manually toggleable for other statuses.
        const prospect_statuses = ["Prospect", "Champion"];
        frm.set_value(
            "custom_prospect",
            prospect_statuses.includes(frm.doc.custom_account_status) ? 1 : 0
        );
    },

    // Source: "Accounts (Customer) Tables"
    // Populate the related Projects / Opportunities / Leads child tables on load.
    async onload(frm) {
        if (frm.is_new()) {
            return;
        }

        const project_table = "custom_projects";
        const opportunity_table = "custom_opportunities";
        const lead_table = "custom_leads";

        frm.clear_table(project_table);
        frm.clear_table(opportunity_table);
        frm.clear_table(lead_table);

        const [projects, opportunities] = await Promise.all([
            frappe.db.get_list("Project", {
                filters: { customer: frm.doc.name },
                fields: ["name", "project_name"],
            }),
            frappe.db.get_list("Opportunity", {
                filters: { party_name: frm.doc.name, opportunity_from: "Customer" },
                fields: ["name", "title"],
            }),
        ]);

        if (projects && projects.length > 0) {
            projects.forEach((p) => {
                frm.add_child(project_table, { project: p.name, project_name: p.project_name });
            });
        }

        if (opportunities && opportunities.length > 0) {
            opportunities.forEach((o) => {
                frm.add_child(opportunity_table, { opportunity: o.name, title: o.title });
            });
        }

        if (frm.doc.lead) {
            const lead = await frappe.db.get_value("Lead", frm.doc.lead, "lead_name");
            if (lead && lead.lead_name) {
                frm.add_child(lead_table, { lead: frm.doc.lead, lead_name: lead.lead_name });
            }
        }

        frm.refresh_field(project_table);
        frm.refresh_field(opportunity_table);
        frm.refresh_field(lead_table);
    },
});
