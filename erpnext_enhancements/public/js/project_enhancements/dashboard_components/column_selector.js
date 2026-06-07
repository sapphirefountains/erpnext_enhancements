/* global erpnext_enhancements */
frappe.provide("erpnext_enhancements.dashboard_components");

/**
 * Reusable column visibility selector for the dashboard list tables.
 *
 * Each table cell (`<th>`/`<td>`) is tagged with `dashcol dashcol-<key>` classes.
 * This helper renders a "Columns" dropdown of checkboxes and toggles the
 * `hidden-column` class on the matching cells. The user's choice is persisted
 * to localStorage under the supplied storage key.
 */
erpnext_enhancements.dashboard_components.ColumnSelector = class ColumnSelector {
	/**
	 * @param {string} storageKey localStorage key used to persist hidden columns.
	 * @param {Array<{key: string, label: string, locked?: boolean}>} columns Column definitions.
	 */
	constructor(storageKey, columns) {
		this.storageKey = storageKey;
		this.columns = columns;
		this.hidden = this._load();
	}

	_load() {
		try {
			const raw = localStorage.getItem(this.storageKey);
			const parsed = raw ? JSON.parse(raw) : [];
			return Array.isArray(parsed) ? parsed : [];
		} catch (e) {
			return [];
		}
	}

	_save() {
		try {
			localStorage.setItem(this.storageKey, JSON.stringify(this.hidden));
		} catch (e) {
			// Ignore storage errors (e.g. private browsing mode)
		}
	}

	is_hidden(key) {
		return this.hidden.indexOf(key) !== -1;
	}

	set_hidden(key, hidden) {
		const idx = this.hidden.indexOf(key);
		if (hidden && idx === -1) {
			this.hidden.push(key);
		} else if (!hidden && idx !== -1) {
			this.hidden.splice(idx, 1);
		}
		this._save();
	}

	/** Toggles the `hidden-column` class on all tagged cells within `root`. */
	apply(root) {
		this.columns.forEach((col) => {
			root.find(`.dashcol-${col.key}`).toggleClass("hidden-column", this.is_hidden(col.key));
		});
	}

	/**
	 * Renders the "Columns" dropdown button into `container`.
	 * @param {JQuery} container Element to append the button to.
	 * @param {Function} onChange Called after a column is toggled.
	 */
	render_button(container, onChange) {
		const wrapper = $(`
			<div class="dashboard-column-selector">
				<button type="button" class="btn btn-sm btn-default column-selector-toggle">
					<i class="fa fa-columns mr-1"></i> Columns
				</button>
				<div class="column-selector-menu"></div>
			</div>
		`).appendTo(container);

		const menu = wrapper.find(".column-selector-menu");

		this.columns.forEach((col) => {
			const checked = this.is_hidden(col.key) ? "" : "checked";
			const disabled = col.locked ? "disabled" : "";
			const item = $(`
				<label class="column-selector-item ${col.locked ? "text-muted" : ""}">
					<input type="checkbox" data-key="${frappe.utils.escape_html(
						col.key
					)}" ${checked} ${disabled}>
					<span>${frappe.utils.escape_html(col.label)}</span>
				</label>
			`);
			menu.append(item);
		});

		menu.find("input[type=checkbox]").on("change", (e) => {
			const cb = $(e.currentTarget);
			this.set_hidden(cb.data("key"), !cb.prop("checked"));
			if (typeof onChange === "function") {
				onChange();
			}
		});

		const toggle = wrapper.find(".column-selector-toggle");
		toggle.on("click", (e) => {
			e.stopPropagation();
			const isOpen = menu.is(":visible");
			// Close any other open column menus first
			$(".column-selector-menu").hide();
			if (!isOpen) {
				menu.show();
				const closeHandler = (ev) => {
					if (!$(ev.target).closest(".dashboard-column-selector").length) {
						menu.hide();
						$(document).off("click.columnSelector", closeHandler);
					}
				};
				$(document).on("click.columnSelector", closeHandler);
			}
		});

		return wrapper;
	}
};
