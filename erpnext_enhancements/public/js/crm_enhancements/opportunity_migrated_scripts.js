// Opportunity form scripts migrated from Client Scripts for version control.
//
// Targets: the Opportunity DocType form.
// Loaded via: hooks.py `doctype_js["Opportunity"]`.
//
// Bundles four behaviours that previously lived as database Client Scripts:
//   1. Make the Scope/Schedule/Budget rank fields mandatory once status is
//      "Closed Won".
//   2. Enforce that those three ranks are unique among the ones that are filled.
//   3. Show/hide the per-value-stream scope fields based on the selected streams.
//   4. A simpler "Create Project" button variant (see opportunity.js for the
//      richer dialog; both enqueue the same background project-creation API).
//
// Sources:
//   - "Opportunity Status for Rankings Mandatory" (Opportunity, Form)
//   - "Opportunity Scope Schedule Budget Ranking" (Opportunity, Form)
//   - "Opportunities Show/Hide Custom Fields Value Stream" (Opportunity, Form)
//   - "Opportunity - Create Project Button" (Opportunity, Form)

frappe.ui.form.on("Opportunity", {
    refresh: function (frm) {
        set_rank_fields_mandatory(frm);
        toggle_value_stream_fields(frm);
        toggle_create_project_button(frm);

        // Real-time listener for background project creation completion.
        frappe.realtime.on("project_creation_status", function (data) {
            if (data.opportunity_name === frm.doc.name) {
                if (data.status === "success") {
                    frappe.show_alert(
                        {
                            message: __(
                                `Project <a href="/app/project/${data.project_doc.name}">${data.project_doc.name}</a> created successfully.`
                            ),
                            indicator: "green",
                        },
                        10
                    );
                    frm.reload_doc();
                } else {
                    frappe.show_alert(
                        {
                            message: __(
                                "Project creation failed. Please check the Error Log for details."
                            ),
                            indicator: "red",
                        },
                        10
                    );
                }
            }
        });
    },

    status: function (frm) {
        set_rank_fields_mandatory(frm);
        toggle_create_project_button(frm);
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

// Source: "Opportunity - Create Project Button"
// NOTE: the original Client Script called `crm_enhancements.crm_enhancements.api.enqueue_project_creation`
// with an `user` arg; corrected here to the app's actual method/signature
// (erpnext_enhancements.crm_enhancements.api.enqueue_project_creation(opportunity_name, users, project_template)).
function toggle_create_project_button(frm) {
    const can_create =
        frm.doc.status === "Closed Won" &&
        !frm.doc.custom_created_project &&
        (frappe.user.has_role("Employee") || frappe.user.has_role("System Manager"));

    if (!can_create) {
        frm.remove_custom_button("Create Project");
        return;
    }

    frm.add_custom_button(__("Create Project"), function () {
        let dialog = new frappe.ui.Dialog({
            title: "Select Project Template",
            fields: [
                {
                    label: "Project Template",
                    fieldname: "project_template",
                    fieldtype: "Link",
                    options: "Project Template",
                    reqd: 1,
                },
                {
                    label: "Project Status",
                    fieldname: "project_status",
                    fieldtype: "Select",
                    options: "Active\nCompleted",
                    default: "Active",
                },
            ],
            primary_action_label: "Create Project",
            primary_action: function (values) {
                dialog.get_primary_btn().prop("disabled", true).html("Queuing...");
                dialog.body.innerHTML = `
                    <div class="progress">
                        <div class="progress-bar progress-bar-striped progress-bar-animated" style="width: 100%"></div>
                    </div>
                    <div class="text-center" style="margin-top: 10px;">
                        Adding job to the queue...
                    </div>`;

                frappe.call({
                    method: "erpnext_enhancements.crm_enhancements.api.enqueue_project_creation",
                    args: {
                        opportunity_name: frm.doc.name,
                        users: frappe.session.user,
                        project_template: values.project_template,
                    },
                    callback: function (r) {
                        dialog.hide();
                        if (r.message && r.message.status === "queued") {
                            frappe.show_alert({
                                message: __(
                                    "Project creation started in the background. Awaiting completion..."
                                ),
                                indicator: "blue",
                            });
                            frm.remove_custom_button("Create Project");
                        }
                    },
                });
            },
        });

        dialog.show();
    }).addClass("btn-primary");
}
