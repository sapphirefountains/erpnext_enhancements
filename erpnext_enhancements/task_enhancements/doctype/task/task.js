frappe.ui.form.on("Task", {
    refresh: function(frm) {
        // --- Start of Debugging ---
        console.log("Task Enhancements: Refresh event triggered.");
        console.log("Is Group:", frm.doc.is_group);
        console.log("Is New:", frm.is_new());
        // --- End of Debugging ---

        if (frm.doc.is_group && !frm.is_new()) {
            console.log("Task Enhancements: Conditions met, calling backend method.");
            frappe.call({
                method: "erpnext_enhancements.task_enhancements.doctype.task.task.get_child_tasks_html",
                args: {
                    task_name: frm.doc.name
                },
                callback: function(r) {
                    console.log("Task Enhancements: Received response from backend.", r);
                    if (r.message) {
                        frm.fields_dict.custom_child_tasks_table.html(r.message);
                        frm.refresh_field("custom_child_tasks_table");
                        add_toggle_functionality();
                    } else {
                        console.log("Task Enhancements: Backend returned no message.");
                    }
                }
            });
        } else {
            console.log("Task Enhancements: Conditions NOT met, clearing HTML field.");
            frm.fields_dict.custom_child_tasks_table.html("");
            frm.refresh_field("custom_child_tasks_table");
        }
    }
});

function add_toggle_functionality() {
    // We need to target the form, as the custom table ID might not be on the top-level element
    $(".form-layout").on("click", ".toggle-child-tasks", function(e) {
        e.preventDefault();
        $(this).closest("li").children("ul").toggle();
        $(this).toggleClass("fa-plus-square fa-minus-square");
    });
}
