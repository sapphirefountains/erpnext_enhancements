// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt

/**
 * Desk form script for the Asset doctype (ER-2026-420503).
 *
 * Two problems, both of which read to the user as "the asset form doesn't work".
 *
 * 1. AN ITEM CREATED INLINE SAVES, AND THEN VANISHES FROM THE FIELD.
 *
 *    ERPNext's `asset.js` filters `item_code` with `{is_fixed_asset: 1, is_stock_item: 0}`.
 *    Frappe's Item Quick Entry offers `is_fixed_asset` as an `allow_in_quick_entry` field
 *    with a **default of 0**, so an Item created from that link saves perfectly well and
 *    then fails the link filter — the field clears and it reads as "the item code I just
 *    made doesn't exist". That is step 4 of the original bug report, and nothing is
 *    actually failing to save, which is why it leaves no trace anywhere.
 *
 *    `frappe.ui.form.make_quick_entry` resolves `frappe.ui.form[<DocType>QuickEntryForm]`
 *    before falling back to the base class, and `check_quick_entry_doc()` keeps a
 *    pre-seeded `this.doc`. So subclassing is the framework's own extension point: seed
 *    the doc, and `update_doc()` (which only copies back keys the dialog rendered) leaves
 *    the seeded values intact all the way to `insert()`.
 *
 *    THE GUARD MATTERS AS MUCH AS THE OVERRIDE. `frappe.ui.form.ItemQuickEntryForm` is a
 *    global, and once this file has loaded it stays loaded for the rest of the session —
 *    so an un-guarded version would quietly make every Item created anywhere in that
 *    session a non-stock fixed asset. `ControlLink.new_doc()` sets `frappe._from_link`
 *    immediately before calling `make_quick_entry`, so we can require that the call came
 *    from the Asset form's own `item_code` field and otherwise behave exactly like the
 *    base class.
 *
 * 2. THREE MANDATORY OR NEAR-MANDATORY PICKERS ARE EMPTY, AND NOTHING SAYS WHY.
 *
 *    On a site with no Asset Category, no Location and no fixed-asset Item, the user
 *    meets three dead ends in a row with no explanation at any of them. `patches/
 *    seed_rental_asset_setup.py` creates the first two; this warns about whatever is
 *    still missing instead of letting them be discovered one failed save at a time.
 *    New documents only — an existing Asset proves its own preconditions were met.
 */

frappe.provide("erpnext_enhancements.asset");

// Values every asset item needs and the Quick Entry dialog will not supply on its own.
// `is_stock_item: 0` is not belt-and-braces: `Item.validate_fixed_asset` throws
// "Fixed Asset Item must be a non-stock item", and the dialog defaults it to 1.
const ASSET_ITEM_DEFAULTS = { is_fixed_asset: 1, is_stock_item: 0 };

/** True when this quick entry was opened from the Asset form's `item_code` link. */
function opened_from_asset_item_code() {
	const df = frappe._from_link && frappe._from_link.field_obj && frappe._from_link.field_obj.df;
	return Boolean(df && df.parent === "Asset" && df.fieldname === "item_code");
}

frappe.ui.form.ItemQuickEntryForm = class ItemQuickEntryForm extends frappe.ui.form.QuickEntryForm {
	check_quick_entry_doc() {
		super.check_quick_entry_doc();

		if (!opened_from_asset_item_code()) {
			// Any other Item quick entry in this session — leave it completely alone.
			return;
		}

		Object.assign(this.doc, ASSET_ITEM_DEFAULTS);

		// `Item.asset_category` is `mandatory_depends_on: is_fixed_asset`, so ticking the
		// box above makes it required. Carry the category across from the Asset when the
		// user already chose one; otherwise fall back to the only one that exists, if
		// exactly one does. With several we leave it blank on purpose rather than picking
		// for them — the dialog renders the field (it is `allow_in_quick_entry`), so an
		// unanswered choice is visible and answerable rather than silently guessed.
		const from_asset = frappe._from_link.doc && frappe._from_link.doc.asset_category;
		if (from_asset) {
			this.doc.asset_category = from_asset;
			return;
		}

		// Fire-and-forget: `setup()` carries on synchronously, so this may land either
		// side of `render_dialog()`. Both orders are covered — before it, `set_defaults()`
		// picks the value up out of `this.doc`; after it, `fields_dict` exists and we set
		// the control directly. `update_doc()` skips blank dialog values (`is_null`
		// treats "" as null), so a seeded category survives an untouched field either way.
		frappe.db
			.get_list("Asset Category", { fields: ["name"], limit: 2 })
			.then((rows) => {
				if (!rows || rows.length !== 1) {
					// None to pick, or a genuine choice to make. Leave it to the user —
					// the field is rendered, so an unanswered choice is visible rather
					// than silently guessed.
					return;
				}
				this.doc.asset_category = rows[0].name;
				const control = this.fields_dict && this.fields_dict.asset_category;
				if (control) {
					control.set_value(rows[0].name);
				}
			})
			.catch(() => {
				// A failed convenience lookup must not take the dialog down with it;
				// the field is rendered and the user can still pick.
			});
	}
};

/**
 * Warn about empty prerequisite tables on a NEW Asset, once, before the user hits them.
 *
 * Counts rather than fetches: all three are "is there at least one" questions, and a
 * `limit: 1` keeps this off the critical path of opening the form.
 */
function warn_about_missing_prerequisites(frm) {
	if (!frm.is_new() || frm.__ee_prereq_checked) {
		return;
	}
	frm.__ee_prereq_checked = true;

	Promise.all([
		frappe.db.get_list("Location", { fields: ["name"], limit: 1 }),
		frappe.db.get_list("Item", {
			filters: { is_fixed_asset: 1, is_stock_item: 0, disabled: 0 },
			fields: ["name"],
			limit: 1,
		}),
	])
		.then(([locations, asset_items]) => {
			const missing = [];
			if (!locations || !locations.length) {
				// The hard one: `Asset.location` is reqd, so with no Location the form
				// cannot be saved at all no matter what else is filled in.
				missing.push(
					__("No {0} exists yet, and an Asset cannot be saved without one.", [
						`<a href="/app/location/new">${__("Location")}</a>`,
					])
				);
			}
			if (!asset_items || !asset_items.length) {
				missing.push(
					__(
						"No Item is marked as a fixed asset yet, so the Item Code list will look empty. Type a new code and choose Create — it will be set up as an asset item for you."
					)
				);
			}
			if (missing.length) {
				frm.dashboard.set_headline_alert(missing.join("<br>"), "yellow");
			}
		})
		.catch(() => {
			// Advisory only. If the lookups fail the form still works exactly as before.
		});
}

frappe.ui.form.on("Asset", {
	refresh: warn_about_missing_prerequisites,
});
