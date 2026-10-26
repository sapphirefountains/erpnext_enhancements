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
 * "Signed copy" (v1.744.0, `open_signed_copy`) also lives here: on a Signed
 * contract it opens the signed PDF, else the executed copy with a note that no
 * signed scan is on file and an Attach scan shortcut. What to open is decided
 * on the server (project_enhancements/signed_copy.py).
 *
 * Loaded via hooks.py `doctype_js` for Project, Project Contract and Sapphire
 * Maintenance Contract, and `doctype_list_js` for the Project Contract list.
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
	 * @param {HTMLElement|jQuery} [options.note] - shown above the agreement.
	 * @param {string} [options.secondary_action_label]
	 * @param {Function} [options.secondary_action] - called with the dialog.
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
				secondary_action_label: opts.secondary_action ? opts.secondary_action_label : null,
				secondary_action: opts.secondary_action ? () => opts.secondary_action(d) : null,
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
			if (opts.note) d.fields_dict.body.$wrapper.prepend(opts.note);
			d.show();
			return d;
		});
	}

	// -----------------------------------------------------------------------
	// Signed copy (v1.744.0)
	// -----------------------------------------------------------------------

	const SIGNED = "erpnext_enhancements.project_enhancements.signed_copy";

	/**
	 * Ask the server what Signed copy opens for a contract. The forms do not
	 * need this: they get the same answer in `__onload.signed_copy`.
	 *
	 * @param {string} name - Project Contract name.
	 * @returns {Promise<Object>} a `signed_copy()` payload.
	 */
	function fetch_signed_copy(name) {
		return frappe
			.call({
				method: `${SIGNED}.get_signed_copy`,
				args: { project_contract: name },
				freeze: true,
				freeze_message: __("Finding the signed copy…"),
			})
			.then((r) => r.message);
	}

	/**
	 * Open a file in a new tab. From a form this runs inside the click, so no
	 * popup blocker gets a say. From the list it follows a request, and Safari
	 * blocks a window opened there; a link is offered instead of nothing
	 * happening.
	 */
	function open_file(url) {
		const win = window.open(url, "_blank");
		if (win) {
			win.opener = null;
			return;
		}
		const $link = $("<a target='_blank' rel='noopener'></a>")
			.attr("href", url)
			.text(__("Open the signed copy"));
		frappe.msgprint({ title: __("Signed copy"), message: $("<div>").append($link).html() });
	}

	function signed_line(info) {
		if (info.signed_by && info.signed_on) {
			return __("Signed by {0} on {1}.", [
				info.signed_by,
				frappe.datetime.str_to_user(info.signed_on),
			]);
		}
		return "";
	}

	/**
	 * The note above the executed copy when there is no file to open. Built
	 * with .text() throughout: names and file names are typed by people.
	 */
	function missing_note(info, on_filed) {
		const $note = $("<div class='alert alert-warning small' style='margin-bottom:12px'></div>");
		if (info.route === "esign") {
			$note.text(
				__(
					"The signed PDF is not attached to this contract. Below is the executed copy, the document that was signed online."
				)
			);
			return $note;
		}
		$note.append(
			$("<div></div>").text(
				__(
					"No scanned signed copy is on file. This was marked signed on paper; below is the agreement as it read when it was marked signed."
				) +
					" " +
					signed_line(info)
			)
		);
		// A scan someone attached from the sidebar, under its own name, is
		// probably already here: offer to file it rather than upload it twice.
		(info.attachments || []).forEach((file) => {
			const $row = $("<div style='margin-top:6px'></div>");
			$row.append(
				$("<a target='_blank' rel='noopener'></a>").attr("href", file.file_url).text(file.file_name)
			);
			$row.append(" ");
			$row.append(
				$("<button class='btn btn-xs btn-default'></button>")
					.text(__("Use as signed copy"))
					.on("click", (e) =>
						file_scan(info.name, file.name, on_filed).then((filed) => {
							if (filed) $(e.currentTarget).closest(".modal").modal("hide");
						})
					)
			);
			$note.append($row);
		});
		return $note;
	}

	/** Upload a scan or photo of the paper, then file it as the signed copy. */
	function attach_scan(name, on_filed) {
		new frappe.ui.FileUploader({
			doctype: "Project Contract",
			docname: name,
			dialog_title: __("Attach the signed scan"),
			allow_multiple: false,
			make_attachments_public: false,
			allow_toggle_private: false,
			allow_web_link: false,
			allow_google_drive: false,
			disable_file_browser: true,
			restrictions: { allowed_file_types: [".pdf", ".png", ".jpg", ".jpeg"] },
			on_success(file_doc) {
				if (file_doc && file_doc.name) file_scan(name, file_doc.name, on_filed);
			},
		});
	}

	function file_scan(name, file, on_filed) {
		return frappe
			.call({
				method: `${SIGNED}.record_signed_scan`,
				args: { project_contract: name, file: file },
				freeze: true,
			})
			.then((r) => {
				if (!r.message) return null;
				frappe.show_alert(
					{ message: __("Signed copy on file: {0}", [r.message.file_name]), indicator: "green" },
					7
				);
				on_filed && on_filed(r.message);
				return r.message;
			});
	}

	/**
	 * The "Signed copy" button.
	 *
	 * @param {Object} info - a `signed_copy()` payload.
	 * @param {Object} [options]
	 * @param {string} [options.template_key]
	 * @param {Function} [options.on_filed] - after a scan is filed (the form
	 *   reloads, so its onload answer includes the new file).
	 */
	function open_signed_copy(info, options) {
		const opts = options || {};
		if (!info || !info.signed) {
			frappe.msgprint(__("This contract is not signed yet."));
			return;
		}
		if (info.file_url) {
			open_file(info.file_url);
			return;
		}
		const attach = info.can_attach
			? (d) => {
					d && d.hide();
					attach_scan(info.name, opts.on_filed);
			  }
			: null;

		if (!info.executed) {
			// Signed, with no file and nothing frozen: a contract marked signed
			// before v1.611.0 froze its copy. A live render would put words in
			// the customer's mouth, so say so rather than show one.
			const d = new frappe.ui.Dialog({
				title: __("Signed copy — {0}", [info.name]),
				fields: [{ fieldtype: "HTML", fieldname: "body" }],
				primary_action_label: attach ? __("Attach scan") : null,
				primary_action: attach ? () => attach(d) : null,
			});
			const why =
				info.route === "esign"
					? __("the signed PDF is not attached, and the signature request kept no executed copy.")
					: __("no scan was attached, and no copy was kept when it was marked signed.");
			d.fields_dict.body.$wrapper.append(
				$("<p></p>").text(
					__("No signed copy is on file: {0}", [why]) +
						" " +
						__(
							"Preview Contract shows the agreement as its template reads today, which may not be what was signed."
						) +
						" " +
						signed_line(info)
				)
			);
			d.show();
			return;
		}

		return open({
			name: info.name,
			template_key: opts.template_key,
			print_name: info.name,
			title: __("Signed copy — {0}", [info.name]),
			note: missing_note(info, opts.on_filed),
			secondary_action_label: __("Attach scan"),
			secondary_action: attach,
		});
	}

	Object.assign(erpnext_enhancements.contract_viewer, {
		fetch,
		paint,
		open,
		ensure_styles,
		fetch_signed_copy,
		open_signed_copy,
		attach_scan,
	});
})();
