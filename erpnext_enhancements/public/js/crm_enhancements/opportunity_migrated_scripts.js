// Opportunity form scripts migrated from Client Scripts for version control.
//
// Targets: the Opportunity DocType form.
// Loaded via: hooks.py `doctype_js["Opportunity"]`.
//
// Bundles the behaviours that previously lived as database Client Scripts:
//   1. Make the Scope/Schedule/Budget rank fields mandatory once status is
//      "Closed Won".
//   2. Enforce that those three ranks are unique among the ones that are filled.
//   3. Show/hide the per-value-stream scope fields based on the selected streams.
//
// Note: a "Create Project" button variant used to live here too. Project
// creation is now gated entirely behind the "Create project now?" prompt
// (create_project_prompt.js + crm_enhancements/project_prompt.py), so the button
// — and this file's duplicate `project_creation_status` listener (the form's
// canonical one lives in crm_enhancements/opportunity.js) — were removed.
//
// Sources:
//   - "Opportunity Status for Rankings Mandatory" (Opportunity, Form)
//   - "Opportunity Scope Schedule Budget Ranking" (Opportunity, Form)
//   - "Opportunities Show/Hide Custom Fields Value Stream" (Opportunity, Form)

frappe.ui.form.on("Opportunity", {
    refresh: function (frm) {
        set_rank_fields_mandatory(frm);
        toggle_value_stream_fields(frm);
    },

    status: function (frm) {
        set_rank_fields_mandatory(frm);
    },

    // Source: "Opportunity Scope Schedule Budget Ranking"
    custom_scope_rank: function (frm) {
        validate_ranking(frm);
    },
    custom_schedule_rank: function (frm) {
        validate_ranking(frm);
    },
    custom_budget_rank: function (frm) {
        validate_ranking(frm);
    },

    // Source: "Opportunities Show/Hide Custom Fields Value Stream"
    custom_value_stream: function (frm) {
        toggle_value_stream_fields(frm);
    },
});

// Source: "Opportunity Status for Rankings Mandatory"
// Make the three rank fields mandatory when status is 'Closed Won'.
function set_rank_fields_mandatory(frm) {
    const reqd = frm.doc.status === "Closed Won" ? 1 : 0;
    frm.set_df_property("custom_scope_rank", "reqd", reqd);
    frm.set_df_property("custom_schedule_rank", "reqd", reqd);
    frm.set_df_property("custom_budget_rank", "reqd", reqd);
    frm.refresh_fields();
}

// Source: "Opportunity Scope Schedule Budget Ranking"
// Scope/Schedule/Budget ranks must be unique among the filled ones.
function validate_ranking(frm) {
    let ranks = [frm.doc.custom_scope_rank, frm.doc.custom_schedule_rank, frm.doc.custom_budget_rank];
    let filled_ranks = ranks.filter((rank) => rank);

    if (filled_ranks.length > 1) {
        let unique_ranks = new Set(filled_ranks);
        if (unique_ranks.size !== filled_ranks.length) {
            frappe.msgprint({
                title: __("Validation Error"),
                indicator: "red",
                message: __("Scope, Schedule, and Budget must have unique rankings."),
            });
            frappe.validated = false;
        } else {
            frappe.validated = true;
        }
    }
}

// Source: "Opportunities Show/Hide Custom Fields Value Stream"
// Show/hide scope fields depending on the selected value streams.
function toggle_value_stream_fields(frm) {
    const option_fieldname_in_child_table = "value_stream";

    const field_map = {
        Design: ["custom_design_scope"],
        Build: ["custom_build_scope"],
        Service: ["custom_service_scope"],
        Rent: ["custom_rent_scope", "custom_rent"],
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
