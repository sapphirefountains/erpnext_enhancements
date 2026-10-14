// Item form scripts migrated from Client Scripts for version control.
//
// Targets: the "Item" doctype form.
// Loaded via: hooks.py `doctype_js["Item"]` (with vue.global.js + comments.js;
//   the Comments App itself is auto-mounted for Item by comments_auto.js).
//
// Sources:
//   - "Item - Empty Description Field" (Item, Form)
//   - "Item - Show Item Code After Save" (Item, Form)
//
// Plus the way into the guided Item flow (form kit P8.1, TASK-2026-02790). An Item still opens
// here: the flow is not the default until it has been reviewed (form_kit/config/item.py,
// open_by_default False), so this button is how a person chooses it, for this Item or a new one.
// Shown only to people who may open the guided page (frappe.boot.page_info) and whose boot lists
// the Item flow (window.sf_guided.has_flow, public/js/guided_default.js).

frappe.ui.form.on("Item", {
    refresh: function (frm) {
        // Source: "Item - Show Item Code After Save"
        // Populate the display field with the saved item code (final name).
        if (!frm.is_new()) {
            frm.set_value("custom_item_identifier", frm.doc.name);
        } else {
            frm.set_value("custom_item_identifier", "");
        }

        if (frappe.boot.page_info && frappe.boot.page_info["guided-entry"]
                && window.sf_guided && window.sf_guided.has_flow && window.sf_guided.has_flow("Item")) {
            frm.add_custom_button(__("Guided"), () => {
                if (frm.is_new()) {
                    // Step 1 of a new Item on the guided page; this unsaved form is left behind.
                    frappe.set_route("guided-entry", "Item", "new");
                    return;
                }
                // Steps back rather than pushing when the form was opened from the guided page.
                window.sf_guided.to_guided(frm.doctype, frm.doc.name);
            });
        }
    },

    // Source: "Item - Empty Description Field"
    // Override the standard auto-fill of description from item_name with a no-op.
    item_name: function (frm) {
        return;
    },
});
