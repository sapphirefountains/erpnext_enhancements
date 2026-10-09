/**
 * Task form client script (Task Enhancements).
 *
 * Loaded onto the Task form via hooks.py `doctype_js["Task"]`
 * (entry: "task_enhancements/doctype/task/task.js").
 *
 * Behaviour: on refresh, if the task is a group (`is_group`) and already
 * saved, it calls the whitelisted backend method `get_child_tasks_html`
 * and renders the returned descendant-task tree into the form's
 * `custom_child_tasks_table` HTML field. For non-group or new tasks the
 * field is cleared. `add_toggle_functionality` delegates a click handler so
 * the expand/collapse toggle icons in the rendered tree fold their children.
 *
 * The verbose console.log lines are left-in debugging aids.
 */
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

/*
 * Project Planner Phase 5: "Preview customer email" on a customer-facing task.
 *
 * Shows the customer date confirmation this task would send (subject, recipient, the email itself
 * in a sandboxed frame) and why it would not send now. It never sends, and works while
 * confirmations are switched off in Project Planner Settings (they ship off). System Manager and
 * Projects Manager only; the endpoint checks the same roles.
 */
const EE_CUSTOMER_PREVIEW = {
    method: "erpnext_enhancements.api.project_planner.preview_customer_confirmation",
    roles: ["System Manager", "Projects Manager"],
};

frappe.ui.form.on("Task", {
    refresh(frm) {
        if (frm.is_new() || !frm.doc.custom_customer_visit) return;
        if (!EE_CUSTOMER_PREVIEW.roles.some((role) => frappe.user.has_role(role))) return;
        frm.add_custom_button(__("Preview customer email"), () => ee_preview_customer_email(frm.doc.name));
    },
});

function ee_preview_customer_email(name) {
    frappe.call({
        method: EE_CUSTOMER_PREVIEW.method,
        args: { doctype: "Task", name },
        freeze: true,
        freeze_message: __("Rendering the customer email…"),
        callback: (r) => {
            const answer = (r && r.message) || {};
            const esc = (value) => frappe.utils.escape_html(value == null ? "" : String(value));
            const status = answer.would_send ? __("This email would be sent.") : __("This email would not be sent now.");
            const to = answer.recipient ? __("To: {0}", [answer.recipient]) : __("To: nobody (no email address found)");
            const parts = [`<p><b>${esc(status)}</b><br>${esc(to)}</p>`];
            if (answer.subject) parts.push(`<p>${esc(__("Subject: {0}", [answer.subject]))}</p>`);
            const notes = (answer.notes || []).map((text) => `<li>${esc(text)}</li>`).join("");
            if (notes) parts.push(`<ul>${notes}</ul>`);
            if (answer.error) parts.push(`<p><b>${esc(answer.error)}</b></p>`);
            parts.push(
                `<iframe class="ee-customer-preview" sandbox="" title="${esc(__("Email preview"))}" ` +
                    'style="display:block;width:100%;min-height:420px;border:1px solid var(--border-color);border-radius:8px;background:#ffffff"></iframe>'
            );
            const dialog = new frappe.ui.Dialog({
                title: __("Customer email preview"),
                size: "large",
                fields: [{ fieldtype: "HTML", fieldname: "preview", options: parts.join("") }],
            });
            dialog.show();
            const $frame = dialog.$wrapper.find("iframe.ee-customer-preview");
            if (answer.html) $frame.attr("srcdoc", answer.html);
            else $frame.hide();
        },
    });
}
