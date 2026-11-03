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
 * ends; Left and Right arrow keys while the pane is open; a "3 of 12" counter in the pane's header;
 * and Back closes the pane first, a second Back leaves the form.
 *
 * WHAT IS WRAPPED. show_attachment_preview(attachment, file_url) draws the pane into
 * this.attachment_preview (a jQuery object over the `.attachment-preview` div in the page sidebar):
 * a header with the file name (.attachment-preview-title) and .attachment-preview-actions holding
 * the open-in-new-tab link, a copy button and a close button (frappe.ui.button.html), then the body
 * (iframe, img, csv table or "Preview not available"). hide_attachment_preview() adds `hidden` and
 * empties it. Neither changes the route or history: the pane is DOM only. The wrapper calls the
 * original, then puts a Previous button, the counter and a Next button at the front of
 * .attachment-preview-actions, made with frappe.ui.button.html so they match the three already
 * there. One file in the list: no arrows and no counter.
 *
 * THE ORDER. render_attachments() keeps one row per file_name (the first occurrence in
 * get_attachments() wins) and inserts each row directly under the Attachments heading
 * (add_attachment: `.insertAfter(this.add_attachment_wrapper)`), so the rows read in REVERSE of
 * the array. The array is File's default sort, creation descending (frappe/desk/form/load.py
 * get_attachments, db_query.py set_order_by), so the top row is the OLDEST File record and "Show
 * all" reveals the newer ones; checked on prod's PRJ-00754 (41 files) on 2026-11-03. The arrows
 * follow the rows as drawn, top to bottom: "3 of 12" is the third row on screen, and the rows
 * behind "Show all" are reached the same way. The list is read afresh on every step, so an upload
 * or a delete while the pane is open (refresh() redraws the rows) is in it: a current file that
 * was deleted steps to the first row, and an emptied list closes the pane.
 *
 * KEYS. Left = previous, Right = next, bound as keydown.attachment_preview_nav on document while the
 * pane is open (beside frappe's own keydown.attachment_preview, which is Escape) and unbound when
 * it closes. Ignored while a modifier key is held, inside an input, textarea, select or
 * contenteditable, and once the pane has left the document (formview.show_doc removes it on every
 * form render). A PDF's iframe takes the keys while it has focus; click the header first.
 *
 * BACK AND FORWARD. Nik's rule: custom UI never breaks the browser's Back and Forward, and a surface
 * with more than one screen gives each screen a history entry so Back returns to the one before.
 * The open pane is a screen. When it opens from closed, ONE entry is pushed:
 * history.pushState({ ee_attachment_preview: true }, "") with no URL. A URL change would make
 * frappe's router render the form again (router.js push_state() -> route(), and formview.show_doc()
 * removes the pane), and the same URL with a new state renders nothing. Stepping to another file
 * pushes nothing: one entry per open pane, not per file. On popstate, when the pane is open and the
 * new state carries no marker, the pane closes through the original hide, with nothing pushed or
 * popped again (the router's own popstate handler then runs route() on the same path and
 * show_doc removes the pane element; both find it already closed). When the pane closes any other
 * way (the close button, Escape, sidebar_memory hiding the sidebar) while history.state still
 * carries the marker, history.back() consumes the entry, so Forward never reopens a stale pane; a
 * flag keeps that back's own popstate from closing anything again. A route change made with the
 * pane open leaves the marker entry behind the new one: Back onto it shows the form with no pane
 * (the router removed it) and the next Back goes on, one Back that changes nothing on screen.
 * replaceState cannot remove an entry that is no longer current.
 *
 * Installed once on the prototype (a marker guards a second evaluation), and only when the four
 * methods it relies on are functions, so an older or newer frappe is left exactly as it is.
 */
(function () {
	if (typeof frappe === "undefined" || !frappe.ui || !frappe.ui.form) return;
	const Attachments = frappe.ui.form.Attachments;
	if (typeof Attachments !== "function" || !Attachments.prototype) return;
	const proto = Attachments.prototype;
	if (proto.__ee_preview_nav) return;
	const needed = ["show_attachment_preview", "hide_attachment_preview", "get_attachments", "get_file_url"];
	for (let i = 0; i < needed.length; i++) {
		if (typeof proto[needed[i]] !== "function") return;
	}
	if (typeof window === "undefined" || typeof document === "undefined" || typeof $ !== "function") return;
	proto.__ee_preview_nav = true;

	const STATE_KEY = "ee_attachment_preview";
	const KEYS_NS = "keydown.attachment_preview_nav";
	const NAV_CLASS = "attachment-preview-nav";
	const PREV_CLASS = "attachment-preview-prev";
	const NEXT_CLASS = "attachment-preview-next";
	const COUNT_CLASS = "attachment-preview-count";

	const original_show = proto.show_attachment_preview;
	const original_hide = proto.hide_attachment_preview;
	const original_refresh = typeof proto.refresh === "function" ? proto.refresh : null;

	// The instance whose pane is open (one form is on screen at a time), a history.back() of ours
	// still in flight, and a close that popstate itself asked for.
	let active = null;
	let consuming = false;
	let from_popstate = false;

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

	function pane_open(me) {
		const el = pane_element(me);
		return Boolean(el && document.body && document.body.contains(el) && !el.classList.contains("hidden"));
	}

	function has_marker(state) {
		return Boolean(state && typeof state === "object" && state[STATE_KEY]);
	}

	/** The rows as the sidebar draws them, top to bottom (see THE ORDER above). */
	function drawn_files(me) {
		const all = me.get_attachments() || [];
		const seen = {};
		const unique = all.filter((attachment) => {
			const key = attachment && attachment.file_name;
			if (Object.prototype.hasOwnProperty.call(seen, key)) return false;
			seen[key] = true;
			return true;
		});
		return unique.reverse();
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

	function bind_keys(me) {
		$(document)
			.off(KEYS_NS)
			.on(KEYS_NS, (event) => {
				try {
					if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
					if (event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
					if (typing_in(event.target)) return;
					if (!pane_open(me)) {
						$(document).off(KEYS_NS);
						return;
					}
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

	function push_entry() {
		try {
			if (has_marker(window.history.state)) return; // a marker entry is already on top: reuse it
			const state = {};
			state[STATE_KEY] = true;
			window.history.pushState(state, "");
		} catch (error) {
			warn("history", error);
		}
	}

	function consume_entry() {
		try {
			if (!has_marker(window.history.state)) return;
			consuming = true;
			window.history.back();
		} catch (error) {
			consuming = false;
			warn("history", error);
		}
	}

	function on_popstate() {
		try {
			if (consuming) {
				consuming = false;
				return;
			}
			const me = active;
			if (!me || !pane_open(me)) return;
			if (has_marker(window.history.state)) return;
			from_popstate = true;
			try {
				me.hide_attachment_preview();
			} finally {
				from_popstate = false;
			}
		} catch (error) {
			warn("popstate", error);
		}
	}

	proto.show_attachment_preview = function (attachment, file_url) {
		const was_open = pane_open(this);
		const result = original_show.apply(this, arguments);
		try {
			this.__ee_preview_current = attachment || null;
			this.__ee_preview_url = file_url || (attachment ? this.get_file_url(attachment) : "");
			active = this;
			if (!was_open) {
				consuming = false;
				push_entry();
			}
			draw_nav(this);
			bind_keys(this);
		} catch (error) {
			warn("show", error);
		}
		return result;
	};

	proto.hide_attachment_preview = function () {
		const was_open = pane_open(this);
		const result = original_hide.apply(this, arguments);
		try {
			unbind_keys();
			if (active === this) active = null;
			this.__ee_preview_current = null;
			this.__ee_preview_url = "";
			if (was_open && !from_popstate) consume_entry();
		} catch (error) {
			warn("hide", error);
		}
		return result;
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

	window.addEventListener("popstate", on_popstate);
})();
