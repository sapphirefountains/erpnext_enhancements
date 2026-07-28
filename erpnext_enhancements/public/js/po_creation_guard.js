/**
 * Hide the "Create > Purchase Order" affordances from users who cannot create one.
 *
 * Targets: the "Material Request" form.
 * Loaded via: hooks.py `doctype_js["Material Request"]`.
 *
 * WI-066 moved Purchase Order create/write/submit onto the "PO Creator" role, so
 * eleven people who could raise a PO yesterday cannot today. ERPNext's own button
 * carries no permission guard — material_request.js adds it on docstatus/status
 * alone — and `get_mapped_doc` only fails later, at `check_permission("create")`.
 * The result is a red "Insufficient Permission" dialog reached by clicking a
 * button that looked available, which reads as a bug rather than as policy.
 *
 * Removing the button is the honest signal: a team lead raises the request, a
 * PO Creator converts it. The message on the form says so, so nobody has to guess
 * who to ask. Our doctype_js loads after erpnext's, so remove_custom_button lands.
 */
frappe.ui.form.on("Material Request", {
    refresh(frm) {
        if (frappe.model.can_create("Purchase Order")) return;

        // erpnext adds these under the "Create" group; both map to a Purchase Order.
        frm.remove_custom_button(__("Purchase Order"), __("Create"));
        frm.remove_custom_button(__("Subcontracted Purchase Order"), __("Create"));

        const convertible =
            frm.doc.docstatus === 1 &&
            frm.doc.material_request_type === "Purchase" &&
            frm.doc.status !== "Stopped" &&
            flt(frm.doc.per_ordered) < 100;

        if (convertible) {
            frm.dashboard.add_comment(
                __(
                    "This request is ready to order. Creating the Purchase Order needs the PO Creator role — ask one of them to convert it."
                ),
                "blue",
                true
            );
        }
    },
});
