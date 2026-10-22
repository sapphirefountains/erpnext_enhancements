frappe.provide("erpnext_enhancements.dashboard_components");

/**
 * Reusable column visibility selector for the dashboard list tables.
 *
 * Targets: the Project Dashboard list tabs (active internal, completed, priority).
 * Loaded via: hooks.py `app_include_js` (global) — it is one of the few dashboard
 * components preloaded globally so the other lazily-required tab components can
 * instantiate `erpnext_enhancements.dashboard_components.ColumnSelector`
 * immediately.
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
	 * Injects the column-selector styles once. The dashboard runs inside a
	 * Custom HTML Block whose CSS field may not be present on every install, so
	 * the component ships its own styles rather than relying on external CSS.
	 * This is what keeps the menu collapsed behind the button instead of
	 * rendering every checkbox inline.
	 */
	_inject_styles() {
		if (document.getElementById("dashboard-column-selector-styles")) {
			return;
		}
		$("<style id='dashboard-column-selector-styles'>").html(`
			.dashboard-list-toolbar { display: flex; align-items: center; gap: 8px; margin-bottom: 0.5rem; }
			.dashboard-column-selector { position: relative; display: inline-block; }
			.column-selector-menu {
				display: none;
				position: absolute;
				right: 0;
				top: calc(100% + 4px);
				z-index: 1050;
				background: var(--popover-bg, var(--card-bg));
				border: 1px solid var(--border-color);
				border-radius: 6px;
				padding: 6px 0;
				min-width: 160px;
				box-shadow: 0 4px 12px rgba(0, 0, 0, 0.12);
			}
			.column-selector-item {
				display: flex;
				align-items: center;
				gap: 8px;
				padding: 6px 14px;
				margin: 0;
				font-weight: normal;
				cursor: pointer;
				white-space: nowrap;
			}
			.column-selector-item:hover { background-color: var(--fg-hover-color); }
			.column-selector-item input { margin: 0; }
			.column-selector-divider { height: 1px; margin: 6px 0; background: var(--border-color); }
			.column-selector-action {
				display: block; width: 100%; padding: 6px 14px; border: 0;
				background: transparent; text-align: left; white-space: nowrap; cursor: pointer;
			}
			.column-selector-action:hover { background-color: var(--fg-hover-color); }
			.column-selector-toggle.column-selector-icon {
				display: inline-flex; align-items: center; justify-content: center;
				width: 32px; height: 28px; padding: 0;
			}
			.hidden-column { display: none !important; }
		`).appendTo("head");
	}

	/**
	 * Renders the "Columns" dropdown button into `container`.
	 * @param {JQuery} container Element to append the button to.
	 * @param {Function} onChange Called after a column is toggled.
	 * @param {{icon?: boolean, actions?: Array<{label: string, onClick: Function}>}} [options]
	 *   `icon` draws the button as the three-dot icon (tooltip and aria-label "Columns")
	 *   instead of the word, and `actions` adds buttons under the checkboxes (the
	 *   dashboard puts "Reset widths" there). The word button stays the default.
	 */
	render_button(container, onChange, options) {
		const opts = options || {};
		this._inject_styles();

		// Inline SVG on purpose: this renders inside a Custom HTML Block's shadow root, where
		// frappe.utils.icon()'s `<use href="#icon-...">` cannot reach the page's sprite.
		const dots =
			'<svg viewBox="0 0 16 16" width="16" height="16" fill="currentColor" aria-hidden="true" focusable="false">' +
			'<circle cx="3" cy="8" r="1.5"/><circle cx="8" cy="8" r="1.5"/><circle cx="13" cy="8" r="1.5"/></svg>';
		const label = __("Columns");

		const wrapper = $(`
			<div class="dashboard-column-selector">
				<button type="button" class="btn btn-sm btn-default column-selector-toggle ${
					opts.icon ? "column-selector-icon" : ""
				}" aria-haspopup="true" aria-expanded="false" ${
					opts.icon
						? `title="${frappe.utils.escape_html(label)}" aria-label="${frappe.utils.escape_html(label)}"`
						: ""
				}>${opts.icon ? dots : `<i class="fa fa-columns mr-1"></i> ${frappe.utils.escape_html(label)}`}</button>
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

		const actions = Array.isArray(opts.actions) ? opts.actions : [];
		if (actions.length) {
			menu.append('<div class="column-selector-divider"></div>');
			actions.forEach((action) => {
				const btn = $(
					`<button type="button" class="column-selector-action">${frappe.utils.escape_html(
						action.label
					)}</button>`
				);
				btn.on("click", () => {
					close();
					if (typeof action.onClick === "function") action.onClick();
				});
				menu.append(btn);
			});
		}

		let closeHandler = null;
		const close = () => {
			menu.hide();
			toggle.attr("aria-expanded", "false");
			if (closeHandler) {
				$(document).off("click.columnSelector", closeHandler);
				closeHandler = null;
			}
		};

		toggle.on("click", (e) => {
			e.stopPropagation();
			const isOpen = menu.is(":visible");
			// Close any other open column menus first. The dashboard draws inside a shadow
			// root, so look in the root this button is in, not only the page's document.
			$(wrapper[0].getRootNode()).find(".column-selector-menu").not(menu).hide();
			if (isOpen) {
				close();
				return;
			}
			menu.show();
			toggle.attr("aria-expanded", "true");
			closeHandler = (ev) => {
				// composedPath sees through the shadow boundary, where ev.target is only the host.
				const oe = ev.originalEvent || ev;
				const path = typeof oe.composedPath === "function" ? oe.composedPath() : [];
				const inside = path.length
					? path.indexOf(wrapper[0]) !== -1
					: $(ev.target).closest(".dashboard-column-selector").length > 0;
				if (!inside) close();
			};
			$(document).on("click.columnSelector", closeHandler);
		});

		wrapper.on("keydown", (e) => {
			if (e.key === "Escape" && menu.is(":visible")) {
				close();
				toggle.trigger("focus");
			}
		});

		return wrapper;
	}
};
