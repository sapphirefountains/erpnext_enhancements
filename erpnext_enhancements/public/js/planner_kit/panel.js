/*
 * Planner kit: edit in a side panel. A `frappe.ui.FieldGroup` hosted in the drawer, with the parts of
 * the `frappe.ui.Dialog` interface the planners' card dialogs use, so the same field list and the
 * same save code run unchanged; only the container moved:
 *
 *   const dialog = planner_kit.panel({ title, fields, width, key, owner, push, reopen });
 *   dialog.set_primary_action(label, (values) => ...);   // values = get_values(), null when a
 *                                                        // required field is blank (as a Dialog)
 *   dialog.$wrapper.find(...)                            // the panel's own markup
 *   dialog.show() · hide() · get_value · set_value · set_values · get_values · fields_dict
 *   dialog.onhide = () => ...
 *
 * Why a FieldGroup rather than a new form: the Project Planner's card dialog is a Table of crew rows,
 * MultiSelectPills, a Table of equipment and the Phase 5 boxes, and every behavior on it is pinned
 * by tests (fields, save_dialog, the needs_reason prompt). Hosting the same FieldGroup keeps all of
 * that and changes only where it is drawn. The fields are made at once, in a hidden holder attached
 * to the page (as a Dialog's are, in its hidden modal), so a page may bind its buttons with
 * `$wrapper.find(...)` before `show()`; `show()` moves them into the drawer.
 */

export function create_panel(env) {
	const doc = env.doc;
	const drawer = env.drawer;
	let holder = null;

	function staging() {
		if (!holder) {
			holder = doc.createElement("div");
			holder.className = "pk-staging";
			holder.hidden = true;
			doc.body.appendChild(holder);
		}
		return holder;
	}

	class Panel {
		constructor(opts) {
			this.opts = opts || {};
			this.title = this.opts.title || "";
			this.display = false;
			this.handle = null;
			this.primary = null;
			this.onhide = null;
			this.container = doc.createElement("div");
			this.container.className = "pk-panel";
			staging().appendChild(this.container);
			this.$wrapper = env.$(this.container);
			this.wrapper = this.container;
			this.field_group = new env.frappe.ui.FieldGroup({
				fields: this.opts.fields || [],
				body: this.container,
				no_submit_on_enter: true,
			});
			this.field_group.make();
			this.fields_dict = this.field_group.fields_dict;
			this.fields_list = this.field_group.fields_list;
		}

		get_values(ignore_errors) {
			return this.field_group.get_values(ignore_errors);
		}

		get_value(fieldname) {
			return this.field_group.get_value(fieldname);
		}

		set_value(fieldname, value) {
			return this.field_group.set_value(fieldname, value);
		}

		set_values(values) {
			return this.field_group.set_values(values);
		}

		get_field(fieldname) {
			return this.fields_dict[fieldname];
		}

		set_primary_action(label, fn) {
			this.primary = { label, fn };
			if (this.handle) this.handle.set_actions(this.actions());
		}

		set_title(title) {
			this.title = title || "";
			if (this.handle) this.handle.set_title(this.title);
		}

		actions() {
			if (!this.primary) return [];
			return [
				{
					label: this.primary.label,
					primary: true,
					on_click: () => {
						const values = this.get_values();
						if (!values) return;
						this.primary.fn(values);
					},
				},
			];
		}

		show() {
			const opts = this.opts;
			this.handle = drawer.open({
				title: this.title,
				subtitle: opts.subtitle || "",
				body: this.container,
				width: opts.width || 560,
				key: opts.key || null,
				owner: opts.owner || null,
				push: opts.push || null,
				reopen: opts.reopen || null,
				tools: opts.tools || null,
				actions: this.actions(),
				on_close: (how) => {
					this.display = false;
					this.handle = null;
					// The drawer has taken the fields out of the page. A page that reads them after
					// hide() (a late reply filling in a row) still can: jQuery finds in a detached tree.
					if (typeof this.onhide === "function") this.onhide(how);
					if (typeof opts.on_close === "function") opts.on_close(how);
				},
			});
			this.display = true;
			return this;
		}

		hide() {
			if (this.handle && this.handle.is_open()) this.handle.close();
		}

		is_open() {
			return !!(this.handle && this.handle.is_open());
		}

		// Done with it: drop the hidden fields (a page opens a new panel per card).
		destroy() {
			this.hide();
			if (this.container.parentNode) this.container.parentNode.removeChild(this.container);
		}
	}

	return function panel(opts) {
		return new Panel(opts);
	};
}
