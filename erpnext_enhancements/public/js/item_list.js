// Item list view styling migrated from Client Script "Adjust List Sizes" (Item, List).
// Widens Item Name / Item Code columns and compacts the ID column.

frappe.listview_settings["Item"] = frappe.listview_settings["Item"] || {};

(function () {
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
