/**
 * @file Reading a contract on screen — the complete agreement, filled in.
 * @description
 * One renderer shared by everywhere a contract is read without printing it:
 * the Preview button on Project Contract, the Contracts tab on Project, and
 * "View Agreement" on Sapphire Maintenance Contract.
 *
 * What comes back from `get_contract_html` is the same HTML the print format
 * puts on paper — the Contract Template's legal language rendered over the
 * contract's data — together with that print format's own CSS. Both are
 * server-supplied on purpose: nothing here re-implements the document's
 * styling, so the agreement on screen and the agreement in the PDF cannot
 * drift into saying (or looking like) two different things.
 *
 * The preview accepts an unsaved document, so the finished contract can be
 * read while it is still being written rather than saved and printed to find
 * out what it says. Signed contracts return their executed instrument instead
 * of a fresh render, and the viewer labels them as such.
 *
 * Loaded via hooks.py `doctype_js` for Project, Project Contract and Sapphire
 * Maintenance Contract.
 */

frappe.provide("erpnext_enhancements.contract_viewer");

(function () {
	const M =
		"erpnext_enhancements.project_enhancements.doctype.project_contract.project_contract";
	const STYLE_ID = "ee-contract-viewer-style";

	// The print CSS assumes paper: it sets black type and no background,
	// because wkhtmltopdf prints onto white. On screen the agreement needs its
	// sheet drawn under it or it is unreadable in a dark theme. Emitted BEFORE
	// the print CSS so the print format's own rules win wherever they overlap.
	const PAPER_CSS =
		".contract-doc { background: #fff; color: #000; padding: 16px 20px; border-radius: 6px; }";

	/**
	 * Publish the print format's CSS once per page.
	 *
	 * Every rule in it is already scoped under `.contract-doc` (see the
	 * "Project Contract Print" fixture), so a single document-level <style> is
	 * enough for the dialog and any number of inline panels, and re-rendering
	 * never stacks duplicates.
	 */
	function ensure_styles(css) {
		let style = document.getElementById(STYLE_ID);
		if (!style) {
			style = document.createElement("style");
			style.id = STYLE_ID;
			document.head.appendChild(style);
		}
		const text = `${PAPER_CSS}\n${css || ""}`;
		if (style.textContent !== text) style.textContent = text;
	}

	/**
	 * Fetch a rendered contract.
	 *
	 * @param {Object} args - `{name}` for a stored contract, or `{doc}` with a
	 *   form's current values (unsaved edits included).
	 * @param {Object} [options]
	 * @param {boolean} [options.freeze=true] - freeze the page while rendering.
	 *   Callers that show their own progress (the Project tab's inline expand)
	 *   pass false; the dialog, which has nothing on screen yet, does not.
	 * @returns {Promise<Object>} `{html, css, executed, title, name}`.
	 */
	function fetch(args, options) {
		const freeze = !options || options.freeze !== false;
		return frappe
			.call({
				method: `${M}.get_contract_html`,
				args: args,
				freeze: freeze,
				freeze_message: __("Rendering the agreement…"),
			})
			.then((r) => r.message);
	}

	/**
	 * Paint a fetched contract into a container element.
	 *
	 * @param {HTMLElement|jQuery} container - where the agreement goes.
	 * @param {Object} payload - a `fetch()` result.
	 * @param {Object} [options]
	 * @param {string} [options.template_key] - adds the `contract-<key>` class
	 *   the print format uses for per-type styling.
	 */
	function paint(container, payload, options) {
		const opts = options || {};
		ensure_styles(payload.css);
		const $wrap = $(container).empty();
		const $doc = $("<div>")
			.addClass("contract-doc")
			.addClass(opts.template_key ? `contract-${opts.template_key}` : "")
			.html(payload.html);

		if (payload.executed) {
			$wrap.append(
				$("<div class='text-muted small' style='margin-bottom:8px'></div>").text(
					__("Executed copy — this is the document that was signed, not a fresh render.")
				)
			);
		}
		$wrap.append($doc);
		return $doc;
	}

	/**
	 * Open a contract in a dialog.
	 *
	 * @param {Object} options
	 * @param {string} [options.name] - stored contract to render.
	 * @param {Object} [options.doc] - unsaved form values to render instead.
	 * @param {string} [options.template_key]
	 * @param {string} [options.print_name] - contract to open the print view
	 *   for; omitted (e.g. an unsaved draft) hides the print action.
	 * @param {string} [options.title]
	 */
	function open(options) {
		const opts = options || {};
		const args = opts.name ? { name: opts.name } : { doc: JSON.stringify(opts.doc || {}) };

		return fetch(args).then((payload) => {
			if (!payload) return;
			const d = new frappe.ui.Dialog({
				title: opts.title || payload.title || __("Contract"),
				size: "extra-large",
				fields: [{ fieldtype: "HTML", fieldname: "body" }],
				// Reading, not deciding: the only action is to take it to paper.
				primary_action_label: opts.print_name ? __("Print / PDF") : null,
				primary_action: opts.print_name
					? () => frappe.set_route("print", "Project Contract", opts.print_name)
					: null,
			});
			// The agreement is set in a fixed-height scroller rather than growing
			// the dialog: a full owner contract runs many pages, and a dialog that
			// tall pushes its own actions off the screen.
			d.fields_dict.body.$wrapper.css({
				"max-height": "70vh",
				"overflow-y": "auto",
				padding: "0 12px",
			});
			paint(d.fields_dict.body.$wrapper, payload, { template_key: opts.template_key });
			d.show();
			return d;
		});
	}

	Object.assign(erpnext_enhancements.contract_viewer, { fetch, paint, open, ensure_styles });
})();
