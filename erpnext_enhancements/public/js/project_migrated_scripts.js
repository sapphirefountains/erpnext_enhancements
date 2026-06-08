// Project form scripts migrated from Client Scripts for version control.
// Sources:
//   - "Project - Hide Activity and Connections" (Project, Form)
//   - "Project Stakeholders - Filtering Logic" / "Project - Filter Address and Contacts"
//     (Project, Form) -- these two source scripts were identical; merged here.

frappe.ui.form.on("Project", {
    refresh: function (frm) {
        // Source: "Project - Hide Activity and Connections"
        // Hide the form dashboard (activity / connections) once it renders.
        let attempts = 0;
        const maxAttempts = 50; // ~5 seconds (50 * 100ms)

        const dashboardHider = setInterval(() => {
            const dashboard = $(".form-dashboard");
            if (dashboard.length > 0) {
                dashboard.hide();
                clearInterval(dashboardHider);
                return;
            }
            attempts++;
            if (attempts >= maxAttempts) {
                clearInterval(dashboardHider);
            }
        }, 100);
    },
});

// Source: "Project Stakeholders - Filtering Logic" (deduped)
// Filter contact/address on the stakeholders child table by the selected party,
// and back-fill the party from a selected contact.
frappe.ui.form.on("Project Stakeholder", {
    party_name: function (frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        frm.set_query("contact_person", "stakeholders", function () {
            return {
                filters: {
                    link_doctype: row.party_type,
                    link_name: row.party_name,
                },
            };
        });
        frm.set_query("address", "stakeholders", function () {
            return {
                query: "frappe.contacts.doctype.address.address.address_query",
                filters: {
                    link_doctype: row.party_type,
                    link_name: row.party_name,
                },
            };
        });
    },

    party_type: function (frm, cdt, cdn) {
        frm.model.set_value(cdt, cdn, "party_name", "");
        frm.model.set_value(cdt, cdn, "contact_person", "");
        frm.model.set_value(cdt, cdn, "address", "");
    },

    contact_person: function (frm, cdt, cdn) {
        let row = locals[cdt][cdn];

        // Only when a contact is selected and the party is still empty, so a
        // manual party selection is never overridden.
        if (row.contact_person && !row.party_name) {
            frappe.db.get_doc("Contact", row.contact_person).then((contact) => {
                if (contact.links && contact.links.length > 0) {
                    let primary_link = contact.links[0];
                    frm.model.set_value(cdt, cdn, "party_type", primary_link.link_doctype, false);
                    frm.model.set_value(cdt, cdn, "party_name", primary_link.link_name);
                }
            });
        }
    },
});
