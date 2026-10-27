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
 *
 * `panel.unsaved()` says whether the open panel holds edits that are not saved, so a tap that opens a
 * record (records.js) can ask before it throws them away. The panel remembers its values the first time
 * the person touches it (a pointer or a key inside it, never a page's own set_value or a reply filling
 * in a row), and has changes when they differ from that. A value the page sets through the panel after
 * that (a late reply) moves the remembered value with it, so it is never counted as the person's edit;
 * one written straight into `fields_dict` is not seen, and at worst asks once for nothing.
 */

// JSON with every object's keys sorted, so two reads of the same values always compare equal.
export function canonical(value) {
	const sort = (v) => {
		if (Array.isArray(v)) return v.map(sort);
		if (v && typeof v === "object") {
			return Object.keys(v)
				.sort()
				.reduce((out, key) => Object.assign(out, { [key]: sort(v[key]) }), {});
		}
		return v;
	};
	try {
		return JSON.stringify(sort(value));
	} catch (e) {
		return "";
	}
}

export function create_panel(env) {
	const doc = env.doc;
	const drawer = env.drawer;
	let holder = null;
	let current = null;

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
			this.touched = null;
			const touch = (e) => {
				if (this.touched === null && e && e.isTrusted !== false) this.touched = this.values_key();
			};
			this.container.addEventListener("pointerdown", touch, true);
			this.container.addEventListener("keydown", touch, true);
		}

		values() {
			try {
				return this.get_values(true) || {};
			} catch (e) {
				return {};
			}
		}

		values_key() {
			return canonical(this.values());
		}

		// The page set these fields itself (a late reply): they are not the person's edits.
		rebase(fieldnames) {
			if (this.touched === null) return;
			const base = JSON.parse(this.touched || "{}");
			const now = this.values();
			fieldnames.forEach((name) => {
				if (name in now) base[name] = now[name];
				else delete base[name];
			});
			this.touched = canonical(base);
		}

		// Edits since the person first touched the panel (see the header).
		is_dirty() {
			return this.is_open() && this.touched !== null && this.values_key() !== this.touched;
		}

		get_values(ignore_errors) {
			return this.field_group.get_values(ignore_errors);
		}

		get_value(fieldname) {
			return this.field_group.get_value(fieldname);
		}

		set_value(fieldname, value) {
			const done = this.field_group.set_value(fieldname, value);
			Promise.resolve(done).then(() => this.rebase([fieldname]));
			return done;
		}

		set_values(values) {
			const done = this.field_group.set_values(values);
			Promise.resolve(done).then(() => this.rebase(Object.keys(values || {})));
			return done;
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
					if (current === this) current = null;
					// The drawer has taken the fields out of the page. A page that reads them after
					// hide() (a late reply filling in a row) still can: jQuery finds in a detached tree.
					if (typeof this.onhide === "function") this.onhide(how);
					if (typeof opts.on_close === "function") opts.on_close(how);
				},
			});
			this.display = true;
			this.touched = null;
			current = this;
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

	const panel = function panel(opts) {
		return new Panel(opts);
	};
	panel.unsaved = () => !!(current && current.is_dirty());
	return panel;
}
