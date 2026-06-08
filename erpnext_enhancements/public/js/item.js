// Item form scripts migrated from Client Scripts for version control.
// Sources:
//   - "Item - Empty Description Field" (Item, Form)
//   - "Item - Show Item Code After Save" (Item, Form)

frappe.ui.form.on("Item", {
    refresh: function (frm) {
        // Source: "Item - Show Item Code After Save"
        // Populate the display field with the saved item code (final name).
        if (!frm.is_new()) {
            frm.set_value("custom_item_identifier", frm.doc.name);
        } else {
            frm.set_value("custom_item_identifier", "");
        }
    },

    // Source: "Item - Empty Description Field"
    // Override the standard auto-fill of description from item_name with a no-op.
    item_name: function (frm) {
        return;
    },
});
