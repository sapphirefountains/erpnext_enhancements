// Item list view styling migrated from Client Script "Adjust List Sizes" (Item, List).
// Widens Item Name / Item Code columns and compacts the ID column.
//
// Targets: the "Item" doctype list view.
// Loaded via: hooks.py `doctype_list_js["Item"]`.
//
// Wraps frappe.listview_settings["Item"].refresh to inject a <style> block (kept
// idempotent by removing the prior #custom-item-list-css before re-appending).
//
// Plus "New item (guided)" (v1.691.0, ER-2026-1320546): the guided Item flow, with its
// Describe it card, one tap from the list Parker starts at. Beside + Add Item, not instead of
// it: an Item still opens in its form (form_kit/config/item.py, open_by_default False), so the
// button only routes to /desk/guided-entry/Item/new. Shown to people who may open the guided
// page, whose boot lists the Item flow (window.sf_guided.has_flow, public/js/guided_default.js)
// and who may create an Item. Wraps ERPNext's own onload rather than replacing it.

frappe.listview_settings["Item"] = frappe.listview_settings["Item"] || {};

(function () {
    const original_onload = frappe.listview_settings["Item"].onload;

    frappe.listview_settings["Item"].onload = function (listview) {
        if (original_onload) {
            // ERPNext's onload (item_list.js, version-16) relabels the list's columns.
            original_onload.call(this, listview);
        }
        if (frappe.boot.page_info && frappe.boot.page_info["guided-entry"]
                && window.sf_guided && window.sf_guided.has_flow && window.sf_guided.has_flow("Item")
                && frappe.model.can_create("Item")) {
            listview.page.add_inner_button(__("New item (guided)"), () => {
                frappe.set_route("guided-entry", "Item", "new");
            });
        }
    };

    const original_refresh = frappe.listview_settings["Item"].refresh;

    frappe.listview_settings["Item"].refresh = function (listview) {
        if (original_refresh) {
            original_refresh(listview);
        }

        const custom_css = `
            <style id="custom-item-list-css">
                /* ITEM NAME: Very Wide (the main focus) */
                .list-row-col[data-name="item_name"],
                .list-subject {
                    flex: 4 !important;
                    min-width: 300px !important;
                }

                /* ITEM CODE: Medium-Large */
                .list-row-col[data-name="item_code"] {
                    flex: 2 !important;
                    min-width: 180px !important;
                    text-align: left !important;
                }

                /* ID: Compact & Right Aligned */
                .list-row-col[data-name="name"] {
                    flex: 1 !important;
                    max-width: 150px !important;
                    text-align: right !important;
                    margin-right: 10px !important;
                    font-weight: bold;
                }
            </style>
        `;

        $("style#custom-item-list-css").remove();
        $("head").append(custom_css);
    };
})();
