/*
 * "Add multiple" -> "Create a new <DocType>" keeps what was searched for (ER-2026-520743).
 *
 * A grid's "Add multiple" button opens frappe.ui.form.LinkSelector (form/link_selector.js, in
 * desk.bundle.js). When a search finds nothing it offers "Create a new Item", and that button
 * calls `frappe.new_doc(me.doctype)` with no values (v16, line ~154), so the code someone had
 * just typed into the search box was gone and the new Item opened blank. The button is drawn
 * inside `search()`'s callback, with its handler bound directly on the element, so there is
 * nothing to configure. Instead a capture-phase listener on the dialog sees the click first and
 * makes the same call with the search text:
 *
 * - `name_field` is the route option Frappe's own Link control uses for a typed value;
 *   `frappe.model.get_new_doc` writes it into the doctype's `field:` autoname field, or into
 *   its title field. The autoname field is also set by name (`item_code` for an Item), so the
 *   form kit's guided Item flow, which reads `frappe.route_options` itself, finds it too.
 * - The record made in Quick Entry is then added to the grid, the way picking a search result
 *   does (`set_in_grid`, which asks for the quantity when the grid has one). That only happens
 *   when Quick Entry ran as a dialog; when it falls back to the full form the person has left
 *   the order anyway.
 * - `frappe._from_link` is cleared first. A Link control's "Create a new" that was cancelled
 *   leaves it set, and QuickEntryForm.process_after_insert then puts the new record into that
 *   stale link field instead of calling after_insert.
 *
 * Applies to every LinkSelector, not only Items: an unmatched search is the obvious name for
 * whatever is being created. Nothing changes when the search box is empty.
 * Pure helpers under erpnext_enhancements.link_selector_create; scripts/test_new_item_prefill.mjs.
 */

frappe.provide("erpnext_enhancements.link_selector_create");

(function () {
	const ns = erpnext_enhancements.link_selector_create;
	const MARK = "__ee_create_keeps_search";

	// The route options for a new `doctype` named after the search text `txt`.
	ns.route_options = function (doctype, txt) {
		const value = String(txt || "").trim();
		if (!value) return {};
		const opts = { name_field: value };
		const meta = frappe.get_meta(doctype);
		const autoname = String((meta && meta.autoname) || "");
		if (autoname.startsWith("field:")) opts[autoname.slice(6)] = value;
		return opts;
	};

	// Quick Entry calls this with itself when it renders as a dialog, or with the new doc when
	// it opens the full form instead (quick_entry.js `setup()`).
	ns.after_create = function (selector, dialog) {
		const QuickEntryForm = frappe.ui.form.QuickEntryForm;
		if (!(QuickEntryForm && dialog instanceof QuickEntryForm) || dialog.after_insert) return;
		dialog.after_insert = (doc) => ns.use_created(selector, doc && doc.name);
	};

	ns.use_created = function (selector, name) {
		if (!name) return Promise.resolve();
		const show = () => {
			selector.dialog.fields_dict.txt.set_input(name);
			selector.start = 0;
			selector.search();
		};
		if (selector.target && selector.target.is_grid) {
			return selector.set_in_grid(name).then(show);
		}
		show();
		return Promise.resolve();
	};

	ns.create = function (selector) {
		const txt = selector.dialog.get_value("txt");
		frappe._from_link = null;
		return frappe.new_doc(selector.doctype, ns.route_options(selector.doctype, txt), (dialog) =>
			ns.after_create(selector, dialog)
		);
	};

	ns.watch = function (selector) {
		const wrapper = selector.dialog && selector.dialog.$wrapper && selector.dialog.$wrapper[0];
		if (!wrapper || wrapper[MARK]) return;
		wrapper[MARK] = true;
		wrapper.addEventListener(
			"click",
			(event) => {
				const target = event.target;
				if (!(target && target.closest && target.closest(".new-doc"))) return;
				event.preventDefault();
				event.stopPropagation();
				ns.create(selector);
			},
			true
		);
	};

	function install() {
		const LinkSelector = frappe.ui && frappe.ui.form && frappe.ui.form.LinkSelector;
		if (!LinkSelector) {
			$(document).one("app_ready", install);
			return;
		}
		const make = LinkSelector.prototype.make;
		if (make[MARK]) return;
		const wrapped = function () {
			const result = make.apply(this, arguments);
			try {
				ns.watch(this);
			} catch (e) {
				// Without the watch the button does what frappe's own does: a blank new record.
				console.warn("link selector create watch skipped", e);
			}
			return result;
		};
		wrapped[MARK] = true;
		LinkSelector.prototype.make = wrapped;
	}

	install();
})();
