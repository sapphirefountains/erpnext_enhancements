/**
 * Sapphire Maintenance Section form script.
 *
 * The items grid is shared by all four section types, but only some columns
 * apply to each: `item` to Chemical Dosing, `uom`/`min_value`/`max_value` to
 * Water Chemistry, `options`/`is_mandatory` to Equipment Inspection. This
 * script shows/hides those grid columns whenever the section type changes, and
 * restricts the dosing Item picker to stock items in the configured
 * consumables item group.
 */

const EE_SECTION_COLUMNS = {
	"Chemical Dosing": ["item"],
	"Water Chemistry": ["uom", "min_value", "max_value"],
	"Equipment Inspection": ["options", "is_mandatory"],
	"Cleaning Tasks": [],
};

function ee_toggle_item_columns(frm) {
	const all_columns = ["item", "uom", "min_value", "max_value", "options", "is_mandatory"];
	const visible = EE_SECTION_COLUMNS[frm.doc.section_type] || all_columns;
	all_columns.forEach((column) => {
		const grid_field = frm.fields_dict.items;
		if (grid_field && grid_field.grid) {
			grid_field.grid.update_docfield_property(column, "hidden", visible.includes(column) ? 0 : 1);
		}
	});
	frm.refresh_field("items");
}

frappe.ui.form.on("Sapphire Maintenance Section", {
	setup(frm) {
		frm.set_query("item", "items", function () {
			const filters = [["is_stock_item", "=", 1]];
			if (frm._ee_consumables_item_group) {
				filters.push(["item_group", "=", frm._ee_consumables_item_group]);
			}
			return { filters: filters };
		});
		frappe.db
			.get_single_value("ERPNext Enhancements Settings", "consumables_item_group")
			.then((group) => {
				frm._ee_consumables_item_group = group;
			});
	},

	refresh(frm) {
		ee_toggle_item_columns(frm);
	},

	section_type(frm) {
		ee_toggle_item_columns(frm);
	},
});
