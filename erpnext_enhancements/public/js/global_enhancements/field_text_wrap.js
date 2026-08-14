/**
 * field_text_wrap.js — long field values wrap to three lines instead of
 * truncating to one, in child-table rows and on forms, reading and typing.
 *
 * Targets: every desk form, every doctype (global).
 * Loaded via: erpnext_enhancements.bundle.js (global desk bundle).
 * Styling: public/css/global_enhancements/field_text_wrap.css
 *          (shipped via desk_addons.bundle.scss) — that file carries the
 *          upstream story of why the CSS half is needed at all.
 *
 * Three things happen here:
 *   1. A `ee-wrap` class on <body> switches the whole stylesheet on. That is
 *      all the READ side needs — clamping a static grid cell to three lines is
 *      pure CSS, so it costs nothing per row rendered.
 *   2. An editable `Data` control gets a <textarea> instead of an <input>, so
 *      you can see the whole value while you are typing it. An <input> cannot
 *      wrap; there is no CSS answer to this half.
 *   3. A Text / Small Text / Long Text control being edited INSIDE A GRID gets
 *      sized the same way. It is already a textarea, but frappe pins it to one
 *      row-height there, so only the sizing half applies — never the Enter and
 *      newline handling, which a genuinely multi-line field must keep.
 *   4. Hovering a cell that is still clipped shows the rest in a floating
 *      panel. Delegated and lazy — nothing runs until a pointer lands on a
 *      genuinely overflowing cell.
 *
 * Gating: everything bails unless `frappe.boot.ee_field_text_wrap` is truthy —
 * the `field_text_wrap_enabled` switch on ERPNext Enhancements Settings,
 * shipped to the client by boot.boot_session. Toggling needs no deploy;
 * clients pick it up on their next page load.
 *
 * Same family as field_description_icons.js, and the hover panel deliberately
 * mirrors that file's tooltip rather than sharing it: the ⓘ tooltip sources its
 * content from the live `.help-box` element, which does not generalise.
 *
 * Fieldtypes NOT touched here (the coverage audit, in short): Link, Dynamic
 * Link, Select and Autocomplete get the CSS clamp but keep their real <input>
 * or <select> — awesomplete binds to an input and Enter picks a suggestion.
 * Read-only Data on a form already wraps (it renders into a <div>). Rich-text,
 * numeric, date and Check/Rating types are excluded outright. See the header of
 * field_text_wrap.css for the full reasoning.
 */
frappe.provide("erpnext_enhancements.field_text_wrap");

(function () {
	if (window.__ee_field_text_wrap_loaded) return;
	window.__ee_field_text_wrap_loaded = true;

	const BODY_CLASS = "ee-wrap";
	const INPUT_CLASS = "ee-wrap-input";
	const PANEL_CLASS = "ee-wrap-panel";
	// Where a clipped static cell lives. Kept in one place because both the
	// hover panel's delegated selector and the clipping test use it.
	const CELL_SELECTOR = ".grid-body .grid-static-col .static-area";

	function is_enabled() {
		return !!(frappe.boot && frappe.boot.ee_field_text_wrap);
	}

	// --- The switch -----------------------------------------------------------

	// Read at ready/app_ready rather than at script evaluation: this bundle is
	// included in <head> and frappe.boot is not guaranteed to be populated yet.
	function apply_body_class() {
		if (!document.body) return;
		document.body.classList.toggle(BODY_CLASS, is_enabled());
	}

	// --- Line budget ----------------------------------------------------------

	let cached_max_lines = null;

	// The CSS variable is the single source of truth for the clamp, so the
	// textarea and the static cell can never disagree about how tall "three
	// lines" is.
	function max_lines() {
		if (cached_max_lines === null) {
			const raw = getComputedStyle(document.documentElement).getPropertyValue(
				"--ee-wrap-lines"
			);
			const parsed = parseInt(raw, 10);
			cached_max_lines = parsed > 0 ? parsed : 3;
		}
		return cached_max_lines;
	}

	// --- The textarea swap ----------------------------------------------------

	// Deliberately narrow. `df.fieldtype === "Data"` and nothing else: every
	// subclass of ControlData (Link, Date, Int, Password, Attach, Color, ...)
	// carries its own fieldtype, so none of them can reach this branch.
	function wants_textarea(control) {
		if (!is_enabled()) return false;
		const df = control && control.df;
		if (!df) return false;
		if (df.fieldtype !== "Data") return false;
		// Data + options is a TYPED field — URL, Email, Phone, Barcode, Name.
		// make_input() gives several of them special treatment (the URL link
		// button, for one), and none of them holds prose.
		if (df.options) return false;
		if (df.read_only) return false;
		return true;
	}

	// Height is driven by the `rows` ATTRIBUTE, never an inline style. frappe
	// sets `.editable-row textarea { height: 43px !important }` (grid.scss:312)
	// and an inline `style.height` loses to !important; field_text_wrap.css
	// clears those fixed heights with a more specific !important rule, which
	// leaves `rows` as the thing that actually decides the height.
	function autosize(el) {
		if (!el || el.tagName !== "TEXTAREA") return;
		const style = getComputedStyle(el);
		let line = parseFloat(style.lineHeight);
		// `line-height: normal` parses to NaN.
		if (!line) line = parseFloat(style.fontSize) * 1.4;
		if (!line) return;

		// scrollHeight includes padding but not border.
		const padding = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);

		if (el.rows !== 1) el.rows = 1;
		const content = el.scrollHeight - padding;
		// A hidden field (inactive tab, collapsed section) measures 0 — leave it
		// at one row and let the rescan on `shown.bs.tab` size it when it lands.
		if (content <= 0) return;

		const rows = Math.min(max_lines(), Math.max(1, Math.round(content / line)));
		if (el.rows !== rows) el.rows = rows;
	}

	// Every route a newline can take into the box — paste, drag-and-drop, IME —
	// ends in an `input` event, so one guard here covers all of them. A Data
	// column is a varchar; a newline in it is a data change, not a formatting
	// one. Rewrites only when a newline is actually present, so the caret does
	// not jump on ordinary typing.
	//
	// Stripping HERE, rather than in a capture-phase paste handler, is enough to
	// keep the newline out of the MODEL as well, for two reasons that are worth
	// writing down because neither is obvious:
	//   - in a grid, ControlData.bind_change_event binds only `change`, which
	//     fires on blur — long after this has run;
	//   - on a form it also binds `input`, but debounced by 500ms, so its
	//     handler reads get_input_value() well after this synchronous rewrite.
	// If either of those changes upstream, this needs to move to the paste event.
	function strip_newlines(el) {
		const before = el.value;
		if (before.indexOf("\n") === -1 && before.indexOf("\r") === -1) return;
		const caret = el.selectionStart;
		const head = before.slice(0, caret).replace(/[\r\n]+/g, " ");
		el.value = before.replace(/[\r\n]+/g, " ");
		el.setSelectionRange(head.length, head.length);
	}

	function enhance(control) {
		const $input = control && control.$input;
		if (!$input || !$input.length) return;
		const el = $input[0];
		if (el.tagName !== "TEXTAREA") return;

		$input
			.removeAttr("type") // meaningless, and invalid, on a textarea
			.attr("rows", 1)
			.addClass(INPUT_CLASS);

		$input.on("input.eeWrap", function () {
			strip_newlines(this);
			autosize(this);
		});

		$input.on("focus.eeWrap", function () {
			autosize(this);
		});

		$input.on("keydown.eeWrap", function (e) {
			if (e.key === "Enter") {
				// No newline: preserves the single-line semantics the <input>
				// had, on a column that is a varchar.
				e.preventDefault();
				// ...but Enter in a real <input> ALSO fires `change`, which is
				// what committed the value. A textarea does not, and in a grid
				// that is the only binding there is — ControlData.bind_change_event
				// attaches its debounced `input` handler only when !in_grid().
				// Firing it here keeps Enter meaning exactly what it meant before.
				$(this).trigger("change");
				// Not stopPropagation: a dialog's primary action is bound further
				// up and must still see the key.
				return;
			}
			if (e.which === 38 || e.which === 40) {
				// grid_row.js:1282 binds its own keydown on this element and
				// :1392 reads `e.which === 40` to move between rows. In a
				// soft-wrapped textarea the caret would move as well, so the row
				// changed and the caret jumped at once. preventDefault stops the
				// caret; propagation is left alone on purpose so frappe's row
				// navigation still runs and behaves exactly as it does today.
				if ($(this).closest(".grid-static-col").length) e.preventDefault();
			}
		});

		autosize(el);
	}

	// Text / Small Text / Long Text are ALREADY textareas — nothing to swap. They
	// need help only inside a grid cell, where `.editable-row textarea { height:
	// 43px !important }` (grid.scss:312) overrides the inline height ControlText
	// and ControlSmallText set (300px / 150px) and pins them to one row-height.
	// Adding the class lets field_text_wrap.css clear that with an !important of
	// its own — which also neutralises the inline height, since a stylesheet
	// !important beats an inline declaration — leaving `rows` to size the box.
	//
	// Deliberately NOT enhance(): a multi-line field must keep its Enter key and
	// its newlines. This adds sizing and nothing else.
	function enhance_multiline_cell(control) {
		const $input = control && control.$input;
		if (!$input || !$input.length) return;
		const el = $input[0];
		if (el.tagName !== "TEXTAREA") return;
		if ($input.hasClass(INPUT_CLASS)) return;
		// On a form these controls are already tall enough to read; the fixed
		// height only hurts in a grid.
		if (!$input.closest(".grid-static-col").length) return;

		$input.addClass(INPUT_CLASS);
		$input.on("input.eeWrap", function () {
			autosize(this);
		});
		$input.on("focus.eeWrap", function () {
			autosize(this);
		});
		autosize(el);
	}

	// Size every wrapped control under `root` — used after a form render and
	// when a tab is revealed, where the controls existed but measured 0 because
	// their pane was hidden.
	function rescan(root) {
		const $root = root && root.jquery ? root : $(root || document);
		$root.find("textarea." + INPUT_CLASS).each(function () {
			autosize(this);
		});
	}

	function patch_control_data() {
		const ControlData = frappe.ui && frappe.ui.form && frappe.ui.form.ControlData;
		if (!ControlData || ControlData.prototype.__ee_wrap_patched) return;

		const orig_make_input = ControlData.prototype.make_input;
		if (typeof orig_make_input === "function") {
			ControlData.prototype.make_input = function () {
				if (this.$input || !wants_textarea(this)) {
					return orig_make_input.apply(this, arguments);
				}
				// v16's make_input() builds its element from the STATIC
				// `this.constructor.html_element` ("input"). Flipping that static
				// for the duration of one synchronous call is far narrower than
				// replacing the class: every subclass was bound to the original
				// ControlData at definition time, long before this bundle ran, so
				// none of them can be caught by it. JS is single-threaded and
				// make_input() never yields, so nothing else can observe the
				// swapped value — but the restore MUST be in a `finally`, or one
				// control that throws turns every Link, Date and Int field
				// rendered afterwards into a textarea.
				const previous = ControlData.html_element;
				ControlData.html_element = "textarea";
				try {
					orig_make_input.apply(this, arguments);
				} finally {
					ControlData.html_element = previous;
				}
				try {
					enhance(this);
				} catch (e) {
					// Never let one weird field break the form.
					// eslint-disable-next-line no-console
					console.warn("field_text_wrap: skipped", this.df && this.df.fieldname, e);
				}
			};
		}

		// Without this a form that OPENS on a long value shows a one-row box
		// until you touch it — set_formatted_input is what puts the value in.
		const orig_set_formatted_input = ControlData.prototype.set_formatted_input;
		if (typeof orig_set_formatted_input === "function") {
			ControlData.prototype.set_formatted_input = function () {
				const ret = orig_set_formatted_input.apply(this, arguments);
				if (this.$input && this.$input.hasClass(INPUT_CLASS)) {
					autosize(this.$input[0]);
				}
				return ret;
			};
		}

		ControlData.prototype.__ee_wrap_patched = true;
	}

	// ControlText carries its OWN `static html_element = "textarea"`, so it can
	// never be caught by the swap above — it needs its own hook. Patching
	// ControlText covers ControlLongText (a plain alias) and ControlSmallText,
	// which reaches it through super.make_input().
	function patch_control_text() {
		const ControlText = frappe.ui && frappe.ui.form && frappe.ui.form.ControlText;
		if (!ControlText || ControlText.prototype.__ee_wrap_patched) return;

		const orig_make_input = ControlText.prototype.make_input;
		if (typeof orig_make_input === "function") {
			ControlText.prototype.make_input = function () {
				const ret = orig_make_input.apply(this, arguments);
				if (is_enabled()) {
					try {
						enhance_multiline_cell(this);
					} catch (e) {
						// eslint-disable-next-line no-console
						console.warn("field_text_wrap: skipped", this.df && this.df.fieldname, e);
					}
				}
				return ret;
			};
		}

		// set_formatted_input is inherited from ControlData.prototype, which the
		// patch above already wraps — so these controls get the same
		// size-on-value-set behaviour for free.
		ControlText.prototype.__ee_wrap_patched = true;
	}

	// --- Hover panel: whatever did not fit in the clamp -----------------------

	let $panel = null;
	// Mirrors the `ee-visible` class as a plain boolean purely so the global
	// capture-phase scroll listener below can bail without touching the DOM. It
	// fires on every scroll anywhere in the desk — including a grid being
	// scrolled — and this script loads on every page.
	let panel_visible = false;

	function get_panel() {
		if (!$panel) {
			$panel = $('<div class="' + PANEL_CLASS + '" role="tooltip"></div>');
			$panel.on("mouseenter", () => $panel.addClass("ee-hovered"));
			$panel.on("mouseleave", () => {
				$panel.removeClass("ee-hovered");
				hide_panel();
			});
			$("body").append($panel);
		}
		return $panel;
	}

	// Two property reads, and only when a pointer actually stops on a cell —
	// nothing about this feature costs anything per row rendered.
	//
	// Testing VERTICAL overflow also keeps the panel confined, for free, to the
	// cells the stylesheet actually clamped: an unclamped cell is still
	// `white-space: nowrap`, so its content is one line and scrollHeight equals
	// clientHeight no matter how far the text runs off the side.
	function is_clipped(el) {
		return el.scrollHeight > el.clientHeight + 1;
	}

	function show_panel(el) {
		const html = ($(el).html() || "").trim();
		if (!html) return;
		const $p = get_panel();
		// The cell's own formatted HTML, so links stay links. frappe.format has
		// already run frappe.dom.remove_script_and_style over it.
		$p.html(html).addClass("ee-visible");
		panel_visible = true;
		position_panel(el, $p);
	}

	function position_panel(el, $p) {
		const r = el.getBoundingClientRect();
		// Measure after the content and display are set.
		const pw = $p.outerWidth();
		const ph = $p.outerHeight();
		const vw = window.innerWidth;
		const vh = window.innerHeight;
		const gap = 6;

		let top = r.bottom + gap;
		// Flip above if it would overflow the bottom.
		if (top + ph > vh - 4 && r.top - gap - ph > 4) {
			top = r.top - gap - ph;
		}
		let left = r.left;
		if (left + pw > vw - 4) left = vw - 4 - pw;
		if (left < 4) left = 4;

		$p.css({ top: Math.round(top) + "px", left: Math.round(left) + "px" });
	}

	function hide_panel() {
		// Don't hide while the pointer is over the panel itself — it can hold a
		// link the user is reaching for.
		if ($panel && $panel.hasClass("ee-hovered")) return;
		force_hide_panel();
	}

	function force_hide_panel() {
		if (!panel_visible) return;
		panel_visible = false;
		if ($panel) $panel.removeClass("ee-hovered ee-visible");
	}

	// Delegated, so it covers cells rendered long after this ran and costs
	// nothing per row.
	$(document)
		.on("mouseenter.eeWrap", CELL_SELECTOR, function () {
			if (!is_enabled()) return;
			if (!is_clipped(this)) return;
			show_panel(this);
		})
		.on("mouseleave.eeWrap", CELL_SELECTOR, function () {
			// Deferred so moving the pointer onto the panel keeps it open.
			setTimeout(hide_panel, 60);
		});

	$(document).on("keydown.eeWrap", (e) => {
		if (e.key === "Escape") force_hide_panel();
	});
	$(document).on("click.eeWrap", (e) => {
		if (!panel_visible) return;
		if ($(e.target).closest("." + PANEL_CLASS).length) return;
		force_hide_panel();
	});
	// Capture-phase so scrolling an inner container — the grid itself, usually —
	// dismisses the position:fixed panel; jQuery .on() cannot register capture
	// listeners.
	document.addEventListener("scroll", force_hide_panel, true);
	$(window).on("resize.eeWrap", force_hide_panel);

	// --- Wiring ---------------------------------------------------------------

	patch_control_data();
	patch_control_text();
	$(() => apply_body_class());
	$(document).on("app_ready", () => {
		apply_body_class();
		patch_control_data();
		patch_control_text();
		rescan(document);
	});
	// A deferred pass after the form's render chain settles (setTimeout runs
	// after run_serially's microtask chain, i.e. after refresh_fields).
	// Idempotent — autosize is a no-op when the row count is already right.
	$(document).on("form-refresh", () => setTimeout(() => rescan(document), 0));
	// Controls on an inactive tab measure 0 and stay at one row until their pane
	// is revealed; this is where they get their real size.
	$(document).on("shown.bs.tab", (e) => {
		const target = e && e.target && $(e.target).attr("href");
		setTimeout(() => rescan(target ? $(target) : document), 0);
	});

	erpnext_enhancements.field_text_wrap.rescan = rescan;
	erpnext_enhancements.field_text_wrap.autosize = autosize;
})();
