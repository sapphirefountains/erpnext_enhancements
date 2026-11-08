// Opportunity form scripts migrated from Client Scripts for version control.
//
// Targets: the Opportunity DocType form.
// Loaded via: hooks.py `doctype_js["Opportunity"]`.
//
// Bundles the behaviour that previously lived as a database Client Script:
//   1. Show/hide the per-value-stream scope fields based on the selected streams.
//
// Note: a "Create Project" button variant used to live here too. Project
// creation is now gated entirely behind the "Create project now?" prompt
// (create_project_prompt.js + crm_enhancements/project_prompt.py), so the button
// — and this file's duplicate `project_creation_status` listener (the form's
// canonical one lives in crm_enhancements/opportunity.js) — were removed.
//
// Source:
//   - "Opportunities Show/Hide Custom Fields Value Stream" (Opportunity, Form)

frappe.ui.form.on("Opportunity", {
    refresh: function (frm) {
        toggle_value_stream_fields(frm);
    },

    // Source: "Opportunities Show/Hide Custom Fields Value Stream"
    custom_value_stream: function (frm) {
        toggle_value_stream_fields(frm);
    },
});

// Source: "Opportunities Show/Hide Custom Fields Value Stream"
// Show/hide scope fields depending on the selected value streams.
function toggle_value_stream_fields(frm) {
    const option_fieldname_in_child_table = "value_stream";

    const field_map = {
        Design: ["custom_design_scope"],
        Build: ["custom_build_scope"],
        Service: ["custom_service_scope"],
        Events: ["custom_rent_scope", "custom_rent"],
    };

    const selected_values = (frm.doc.custom_value_stream || [])
        .map((row) => row[option_fieldname_in_child_table])
        .filter(Boolean);

    const all_dependent_fields = [].concat.apply([], Object.values(field_map));

    all_dependent_fields.forEach((field) => {
        if (field) {
            frm.set_df_property(field, "hidden", 1);
        }
    });

    selected_values.forEach((value) => {
        if (field_map[value]) {
            field_map[value].forEach((field_to_show) => {
                if (field_to_show) {
                    frm.set_df_property(field_to_show, "hidden", 0);
                }
            });
        }
    });

    frm.refresh_fields();
}
