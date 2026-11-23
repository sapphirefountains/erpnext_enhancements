/**
 * Previous / next in the form sidebar's attachment preview, with a "3 of 12" counter (v1.780.0).
 *
 * Targets: frappe.ui.form.Attachments (frappe/public/js/frappe/form/sidebar/attachments.js, read at
 * v16.53.0, the tag prod runs). Loaded via: erpnext_enhancements.bundle.js (global); the CSS half is
 * desk_enhancements.bundle.css ("Attachment preview arrows").
 *
 * Nikolas, 2026-11-03: "when clicking an attachment in a doctype the side preview opens just great.
 * I'd like to be able to navigate from here too, so two arrows, pointing left and right, to toggle
 * between file previews and all attachments of the document." Picked with him the same day: every
 * file of the document in the sidebar's order, the ones behind "Show all" included; wrap at the
 * ends; Left and Right arrow keys while the pane is open; and a "3 of 12" counter in the pane's
 * header.
 *
 * WHAT IS WRAPPED. show_attachment_preview(attachment, file_url) draws the pane into
 * this.attachment_preview (a jQuery object over the `.attachment-preview` div in the page sidebar):
 * a header with the file name (.attachment-preview-title) and .attachment-preview-actions holding
 * the open-in-new-tab link, a copy button and a close button (frappe.ui.button.html), then the body
 * (iframe, img, csv table or "Preview not available"). hide_attachment_preview() adds `hidden` and
 * empties it. Neither changes the route or history: the pane is DOM only, and so is this module.
 * The wrapper calls the original, then puts a Previous button, the counter and a Next button at the
 * front of .attachment-preview-actions, made with frappe.ui.button.html so they match the three
 * already there. One file in the list: no arrows and no counter. setup_preview_area(), which the
 * constructor runs once per instance, is wrapped too: see RELEASE below.
 *
 * THE ORDER. The rows are read from the sidebar itself: every `.attachment-row` under this.parent,
 * top to bottom, each resolved to its File by the id in the pill's `/desk/file/<id>` icon link
 * (add_attachment: `<a href="/desk/file/${fileid}" class="attachment-icon">`), else by the label's
 * file URL. After the last row come the files that have no row yet, the ones behind "Show all", in
 * the order "Show all" draws them: render_attachments() keeps one row per file_name (the first
 * occurrence in get_attachments() wins) and inserts each row directly under the Attachments heading
 * (`.insertAfter(this.add_attachment_wrapper)`), so the rows read in REVERSE of the array; and the
 * array is File's default sort, creation descending (frappe/desk/form/load.py get_attachments,
 * db_query.py set_order_by), so the top row is the OLDEST File record and "Show all" reveals the
 * newer ones below it (checked on prod's PRJ-00754, 41 files, 2026-11-03). So "3 of 41" beside ten
 * rows is the third row on screen, and 11 to 41 are reached by stepping while they have no row to
 * point at; "Show all" draws them in that same order. Reading the rows rather than recomputing them
 * keeps the counter on the row even when frappe's own order differs from the array's: it slices the
 * last ten BEFORE it de-duplicates, and an upload sits at the top until reload_docinfo re-sorts it.
 * With no rows to read (no this.parent, or none drawn) the array order above is used as is. The
 * list is read afresh on every step, so an upload or a delete while the pane is open (refresh()
 * redraws the rows) is in it: a current file that was deleted steps to the first row, and an
 * emptied list closes the pane.
 *
 * KEYS. Left = previous, Right = next, bound as keydown.attachment_preview_nav on document while the
 * pane is open (beside frappe's own keydown.attachment_preview, which is Escape) and unbound when
 * it closes. "Open" means VISIBLE: in the document, not `.hidden`, and on a page that is shown
 * (jQuery's :visible), because leaving a form for a list, a report or a workspace hides the form's
 * page div and leaves the pane element exactly as it was; a handler that only checked the element
 * would then take every Left and Right on every page until the next form render. Ignored while a
 * modifier key is held, inside an input, textarea, select or contenteditable, when something
 * earlier already called preventDefault (a grid, a dropdown, an editor), while a dialog
 * (.modal.show) is open, and on a held key's repeats (each step mounts an iframe). A PDF's iframe
 * takes the keys while it has focus; click the header first.
 *
 * RELEASE. Two things end a pane without hide_attachment_preview() ever running, and each must
 * release the keys and forget the instance:
 *   - A route change. formview.show_doc removes the pane element on every form render, and a list
 *     or report merely hides the form's page. frappe.router "change" (router.js route(), fired after
 *     render()) closes a pane that is still attached through the original hide, and otherwise just
 *     unbinds; bound once, on the first open.
 *   - A sidebar rebuild. Save, frm.refresh(), reload_doc and a realtime update all reach
 *     form.js render_form, which builds a new frappe.ui.form.Sidebar; its make() empties
 *     page.sidebar, deleting the old pane, and constructs a new Attachments. That constructor's
 *     setup_preview_area() is the signal: when the instance that was open has no pane in the
 *     document any more, it is released before the new one is set up.
 *
 * NO HISTORY ENTRY, on purpose. Nik's rule is that custom UI never breaks the browser's Back and
 * Forward. An earlier draft pushed one URL-less history entry per open pane so that Back closed it.
 * At v16.53.0 that cannot be made clean: router.js:18-22 registers a popstate listener that calls
 * frappe.router.route() on EVERY popstate with no same-path check, and route() reaches
 * FormFactory.show_doc() -> render() -> frm.refresh(name) (views/formview.js), which rebuilds the
 * whole form: a new Sidebar ("Show all" and the scroll reset), the refresh handlers again, and
 * form.js:447 removes the dirty form's beforeunload prompt, which only dirty() puts back. The repo
 * had already paid for this once: capture/panel.js:36-37 refuses a Desk history entry because
 * "frappe's router re-routes on every popstate, so the panel's own Back would re-render the form
 * underneath". So the pane takes no entry. Escape and the close button close it, as in frappe; Back
 * leaves the form, as frappe does; the arrows, the counter and the keys remain.
 *
 * Installed once on the prototype (a marker guards a second evaluation), and only when the five
 * methods it relies on, frappe.ui.button.html and frappe.utils.escape_html are functions, so an
 * older or newer frappe is left exactly as it is. Nothing here touches window.history.
 */
(function () {
	if (typeof frappe === "undefined" || !frappe.ui || !frappe.ui.form) return;
	const Attachments = frappe.ui.form.Attachments;
	if (typeof Attachments !== "function" || !Attachments.prototype) return;
	const proto = Attachments.prototype;
	if (proto.__ee_preview_nav) return;
	const needed = [
		"show_attachment_preview",
		"hide_attachment_preview",
		"get_attachments",
		"get_file_url",
		"setup_preview_area",
	];
	for (let i = 0; i < needed.length; i++) {
		if (typeof proto[needed[i]] !== "function") return;
	}
	// Both are used to draw the arrows; a frappe without either would throw inside show.
	if (!frappe.ui.button || typeof frappe.ui.button.html !== "function") return;
	if (!frappe.utils || typeof frappe.utils.escape_html !== "function") return;
	if (typeof window === "undefined" || typeof document === "undefined" || typeof $ !== "function") return;
	proto.__ee_preview_nav = true;

	const KEYS_NS = "keydown.attachment_preview_nav";
	const NAV_CLASS = "attachment-preview-nav";
	const PREV_CLASS = "attachment-preview-prev";
	const NEXT_CLASS = "attachment-preview-next";
	const COUNT_CLASS = "attachment-preview-count";
	const FILE_ROUTE = "/file/"; // the pill's icon link: /desk/file/<id>

	const original_show = proto.show_attachment_preview;
	const original_hide = proto.hide_attachment_preview;
	const original_setup = proto.setup_preview_area;
	const original_refresh = typeof proto.refresh === "function" ? proto.refresh : null;

	// The instance whose pane is open (one form is on screen at a time), and whether the router's
	// change event has been hooked yet.
	let active = null;
	let route_hooked = false;

	function warn(message, error) {
		try {
			console.warn("attachment_preview_nav: " + message, error);
		} catch (e) {
			// nothing left to tell
		}
	}

	function translate(text, args) {
		if (typeof __ === "function") return __(text, args);
		return text.replace(/\{(\d+)\}/g, (match, index) => (args && args[index] !== undefined ? args[index] : match));
	}

	function pane_element(me) {
		const pane = me && me.attachment_preview;
		if (!pane) return null;
		if (pane.nodeType === 1) return pane;
		return pane[0] && pane[0].nodeType === 1 ? pane[0] : null;
	}

	/** In the document and not closed: what frappe's own hide would still have to undo. */
	function pane_attached(me) {
		const el = pane_element(me);
		return Boolean(el && document.body && document.body.contains(el) && !el.classList.contains("hidden"));
	}

	/** Attached AND on screen: false once the form's page is hidden under a list or a report. */
	function pane_open(me) {
		if (!pane_attached(me)) return false;
		try {
			return $(pane_element(me)).is(":visible");
		} catch (error) {
			return true; // a jQuery that cannot answer: fall back to attached
		}
	}

	function unique_by_name(all) {
		const seen = {};
		return all.filter((attachment) => {
			const key = attachment && attachment.file_name;
			if (Object.prototype.hasOwnProperty.call(seen, key)) return false;
			seen[key] = true;
			return true;
		});
	}

	function file_id_from_row(row) {
		const icon = row.querySelector(".attachment-icon");
		const href = icon && icon.getAttribute("href");
		if (!href) return "";
		const at = href.lastIndexOf(FILE_ROUTE);
		if (at === -1) return "";
		const tail = href.slice(at + FILE_ROUTE.length).split(/[?#]/)[0];
		try {
			return decodeURIComponent(tail);
		} catch (error) {
			return tail;
		}
	}

	function file_url_from_row(row) {
		const label = row.querySelector(".attachment-file-label");
		return (label && label.getAttribute("href")) || "";
	}

	/** The rows the sidebar has drawn, top to bottom, each resolved to its File record. */
	function drawn_rows(me, all) {
		const parent = me.parent && (me.parent.nodeType === 1 ? me.parent : me.parent[0]);
		if (!parent || typeof parent.querySelectorAll !== "function") return [];
		const by_id = {};
		const by_url = {};
		all.forEach((attachment) => {
			if (!attachment) return;
			if (attachment.name && !by_id[attachment.name]) by_id[attachment.name] = attachment;
			const url = me.get_file_url(attachment);
			if (url && !by_url[url]) by_url[url] = attachment;
		});
		const out = [];
		const rows = parent.querySelectorAll(".attachment-row");
		for (let i = 0; i < rows.length; i++) {
			const file = by_id[file_id_from_row(rows[i])] || by_url[file_url_from_row(rows[i])];
			if (file && out.indexOf(file) === -1) out.push(file);
		}
		return out;
	}

	/** The rows as drawn, then the files "Show all" has not drawn yet (see THE ORDER above). */
	function drawn_files(me) {
		const all = me.get_attachments() || [];
		const show_all_order = unique_by_name(all).reverse();
		const rows = drawn_rows(me, all);
		if (!rows.length) return show_all_order;
		const drawn_names = {};
		rows.forEach((file) => {
			drawn_names[file.file_name] = true;
		});
		const rest = show_all_order.filter((file) => !Object.prototype.hasOwnProperty.call(drawn_names, file.file_name));
		return rows.concat(rest);
	}

	function index_of(me, files) {
		const current = me.__ee_preview_current;
		if (!current) return -1;
		let at = -1;
		if (current.name) at = files.findIndex((file) => file.name === current.name);
		if (at === -1 && me.__ee_preview_url) {
			at = files.findIndex((file) => me.get_file_url(file) === me.__ee_preview_url);
		}
		return at;
	}

	function step(me, delta) {
		const files = drawn_files(me);
		if (!files.length) {
			me.hide_attachment_preview();
			return;
		}
		const at = index_of(me, files);
		let next;
		if (at === -1) next = delta > 0 ? 0 : files.length - 1;
		else next = (at + delta + files.length) % files.length;
		const file = files[next];
		me.show_attachment_preview(file, me.get_file_url(file));
	}

	function counter_text(me, files) {
		const at = index_of(me, files);
		if (at === -1) return translate("{0} files", [files.length]);
		return translate("{0} of {1}", [at + 1, files.length]);
	}

	function on_button(me, delta) {
		return function (event) {
			if (event) {
				if (typeof event.preventDefault === "function") event.preventDefault();
				if (typeof event.stopPropagation === "function") event.stopPropagation();
			}
			try {
				step(me, delta);
			} catch (error) {
				warn("step", error);
			}
		};
	}

	function draw_nav(me) {
		const el = pane_element(me);
		if (!el) return;
		const actions = el.querySelector(".attachment-preview-actions");
		if (!actions) return;
		const stale = actions.querySelector("." + NAV_CLASS);
		if (stale) stale.remove();
		const files = drawn_files(me);
		if (files.length < 2) return;
		const previous_label = translate("Previous file");
		const next_label = translate("Next file");
		const html =
			'<span class="' +
			NAV_CLASS +
			'">' +
			frappe.ui.button.html({
				icon: "chevron-left",
				variant: "ghost",
				title: previous_label,
				css_class: PREV_CLASS,
				attrs: { "aria-label": previous_label },
			}) +
			'<span class="' +
			COUNT_CLASS +
			'" aria-live="polite">' +
			frappe.utils.escape_html(counter_text(me, files)) +
			"</span>" +
			frappe.ui.button.html({
				icon: "chevron-right",
				variant: "ghost",
				title: next_label,
				css_class: NEXT_CLASS,
				attrs: { "aria-label": next_label },
			}) +
			"</span>";
		// At the front: before the open-in-new-tab link, the copy and the close buttons.
		actions.insertAdjacentHTML("afterbegin", html);
		const previous = actions.querySelector("." + PREV_CLASS);
		const next = actions.querySelector("." + NEXT_CLASS);
		if (previous) previous.addEventListener("click", on_button(me, -1));
		if (next) next.addEventListener("click", on_button(me, 1));
	}

	function redraw_counter(me) {
		const el = pane_element(me);
		if (!el) return;
		const nav = el.querySelector("." + NAV_CLASS);
		const files = drawn_files(me);
		if (!nav || files.length < 2) {
			// The buttons were not there, or should not be any more: draw from scratch.
			draw_nav(me);
			return;
		}
		const count = nav.querySelector("." + COUNT_CLASS);
		if (count) count.textContent = counter_text(me, files);
	}

	function typing_in(target) {
		if (!target) return false;
		if (target.isContentEditable) return true;
		const tag = (target.tagName || "").toUpperCase();
		return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
	}

	/** Someone before us (a grid, a dropdown, an editor) already took the key. */
	function already_handled(event) {
		if (typeof event.isDefaultPrevented === "function" && event.isDefaultPrevented()) return true;
		const native = event.originalEvent || event;
		return Boolean(native && native.defaultPrevented);
	}

	function is_repeat(event) {
		const native = event.originalEvent || event;
		return Boolean(native && native.repeat);
	}

	function dialog_open() {
		try {
			return Boolean(document.querySelector(".modal.show"));
		} catch (error) {
			return false;
		}
	}

	function bind_keys(me) {
		$(document)
			.off(KEYS_NS)
			.on(KEYS_NS, (event) => {
				try {
					if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
					if (!pane_open(me)) {
						// Closed, removed by a form render, or under another page: let go.
						$(document).off(KEYS_NS);
						return;
					}
					if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
					if (typing_in(event.target)) return;
					if (already_handled(event) || is_repeat(event) || dialog_open()) return;
					if (typeof event.preventDefault === "function") event.preventDefault();
					step(me, event.key === "ArrowLeft" ? -1 : 1);
				} catch (error) {
					warn("key", error);
				}
			});
	}

	function unbind_keys() {
		$(document).off(KEYS_NS);
	}

	/** Forget an instance whose pane is already gone: keys off, nothing current. */
	function forget(me) {
		unbind_keys();
		if (active === me) active = null;
		if (me) {
			me.__ee_preview_current = null;
			me.__ee_preview_url = "";
		}
	}

	/** Close through frappe's hide while there is still a pane to close; otherwise just forget. */
	function release(me) {
		if (!me) return;
		if (pane_attached(me)) me.hide_attachment_preview();
		else forget(me);
	}

	function on_route_change() {
		try {
			release(active);
		} catch (error) {
			warn("route", error);
		}
	}

	function hook_route_change() {
		if (route_hooked) return;
		if (typeof frappe.router === "undefined" || !frappe.router || typeof frappe.router.on !== "function") return;
		frappe.router.on("change", on_route_change);
		route_hooked = true;
	}

	proto.show_attachment_preview = function (attachment, file_url) {
		const result = original_show.apply(this, arguments);
		try {
			if (active && active !== this) forget(active);
			this.__ee_preview_current = attachment || null;
			this.__ee_preview_url = file_url || (attachment ? this.get_file_url(attachment) : "");
			active = this;
			draw_nav(this);
			bind_keys(this);
			hook_route_change();
		} catch (error) {
			warn("show", error);
		}
		return result;
	};

	proto.hide_attachment_preview = function () {
		const result = original_hide.apply(this, arguments);
		try {
			forget(this);
		} catch (error) {
			warn("hide", error);
		}
		return result;
	};

	proto.setup_preview_area = function () {
		try {
			// A new instance is being built (Save, frm.refresh(), a realtime update): the sidebar
			// was emptied under the old one, so its pane is gone without a hide. Release it.
			if (active && active !== this && !pane_attached(active)) forget(active);
		} catch (error) {
			warn("setup", error);
		}
		return original_setup.apply(this, arguments);
	};

	if (original_refresh) {
		proto.refresh = function () {
			const result = original_refresh.apply(this, arguments);
			try {
				if (pane_open(this)) redraw_counter(this);
			} catch (error) {
				warn("refresh", error);
			}
			return result;
		};
	}
})();
