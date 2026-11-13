/**
 * Purchase Order — the packages on their way: tracking numbers with the carrier's status.
 *
 * Targets: the "Purchase Order" doctype form.
 * Loaded via: hooks.py `doctype_js["Purchase Order"]`.
 *
 * ER-2026-1333277 (TASK-2026-03206). A compact banner at the top of the order lists each tracking
 * number with what FedEx or UPS last said, the carrier's estimated arrival (beside the purchaser's
 * own Expected Delivery Date, which nothing here changes), when it was last checked, and a link to
 * the carrier's own tracking page. "Add tracking number" and "Refresh" sit under it.
 *
 * Things this file is careful about:
 *
 * - **The form never becomes dirty.** Tracking numbers are their own records (Tracked Package), not
 *   rows of the order, so nothing here writes to `frm.doc`, calls `set_value` or reloads the doc.
 *   The banner is HTML put into its own slot, and every change goes through
 *   `api/package_tracking.py` and redraws from the answer.
 * - **Back and Forward behave.** No route change, no history entry: the add box is a dialog, and
 *   carrier pages open in a new tab (`rel="noopener noreferrer"`), so the order stays where it was.
 * - **Plain words.** "On its way", "Out for delivery today", "Arrives Fri Nov 15", "Checked 2 hours
 *   ago", never a carrier code.
 * - **Phones.** One line per package that wraps, nothing wider than the screen, buttons big enough
 *   to tap.
 * - **A plain doctype_js script, not a bundle.** It travels with the form's meta, so a tab open
 *   across a deploy gets this file and its endpoints together; there is no `frappe.require` bundle
 *   for it to be out of step with, and so no level guard is needed (CLAUDE.md).
 * - **Off means off.** While Carrier Tracking Settings' master switch is off the server answers
 *   `enabled: false` and nothing is drawn at all.
 * - **Quiet when there is nothing to say.** An order with packages always shows them (a canceled
 *   order read-only, so what was on its way stays visible). An order with none shows only a small
 *   "Add tracking number" button, and only while it can still have a package on its way and the
 *   person may add one (the server decides: `can_add`). Nothing on a new order, nothing for someone
 *   who cannot see tracking.
 */
frappe.provide("erpnext_enhancements.package_tracking");

(function () {
	const ns = erpnext_enhancements.package_tracking;
	const API = "erpnext_enhancements.api.package_tracking.";
	const SLOT_CLASS = "ee-pkg-banner";
	const STYLE_ID = "ee-pkg-style";

	// Status words as the server stores them (Tracked Package.status) -> what a person reads, and a
	// tone for the pill. "Not Tracked" is a number of another carrier: recorded, never checked.
	const STATUS = {
		"Not Checked Yet": { words: __("Not checked yet"), tone: "gray" },
		"Label Created": { words: __("Label created, not shipped yet"), tone: "gray" },
		"In Transit": { words: __("On its way"), tone: "blue" },
		"Out for Delivery": { words: __("Out for delivery today"), tone: "orange" },
		Delivered: { words: __("Delivered"), tone: "green" },
		"Delivery Problem": { words: __("Delivery problem"), tone: "red" },
		Unknown: { words: __("Status unknown"), tone: "gray" },
		"Not Tracked": { words: __("Not checked automatically"), tone: "gray" },
	};

	function esc(value) {
		return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
	}

	function day(value) {
		// A Date (YYYY-MM-DD) or the date part of a carrier's local time, in the user's date format.
		if (!value) return "";
		const text = String(value).slice(0, 10);
		try {
			return frappe.datetime.str_to_user(text);
		} catch (e) {
			return text;
		}
	}

	function clock(value) {
		// The carrier's local time of day as written ("14:14"), never converted: it is the time on
		// the dock where the package was handed over.
		const text = String(value || "");
		return text.length >= 16 ? text.slice(11, 16) : "";
	}

	function ago(value) {
		if (!value) return __("not checked yet");
		try {
			return __("checked {0}", [frappe.datetime.prettyDate(value)]);
		} catch (e) {
			return __("checked {0}", [String(value)]);
		}
	}

	function safe_url(url) {
		// The server builds FedEx and UPS pages and accepts only http(s) links for Other; this is the
		// second line, so a stored value can never become a javascript: href.
		return /^https?:\/\/[^\s"'<>]+$/i.test(url || "") ? url : "";
	}

	function carrier_label(row) {
		if (row.resolved_carrier === "Unknown") return __("Carrier unknown");
		// A bare 12- or 15-digit number is probably FedEx; say it is a guess (carriers.detect).
		if (row.carrier_guessed) return __("{0} (guessed)", [row.resolved_carrier]);
		return row.resolved_carrier || "";
	}

	/** The pieces of one package's line, as plain values. Exposed for the node harness. */
	ns.describe = function (row, live_carriers) {
		const status = STATUS[row.status] || STATUS.Unknown;
		const live = (live_carriers || []).includes(row.resolved_carrier);
		const delivered = row.status === "Delivered";
		const parts = {
			carrier: carrier_label(row),
			number: row.tracking_number || "",
			url: safe_url(row.tracking_url),
			status: status.words,
			tone: status.tone,
			when: "",
			detail: row.status_description && row.status_description !== row.status ? row.status_description : "",
			where: row.last_location || "",
			checked: ["FedEx", "UPS"].includes(row.resolved_carrier) ? ago(row.last_checked) : "",
			note: "",
		};
		if (delivered && row.delivered_on) {
			const at = clock(row.delivered_on);
			parts.when = at ? __("Delivered {0} at {1}", [day(row.delivered_on), at]) : __("Delivered {0}", [day(row.delivered_on)]);
		} else if (row.estimated_delivery) {
			parts.when = __("Carrier says it arrives {0}", [day(row.estimated_delivery)]);
		}
		if (row.polling_stopped && !delivered && row.stop_reason) {
			parts.note = row.stop_reason;
		} else if (row.last_error && !delivered && live) {
			parts.note = row.last_error;
		}
		return parts;
	};

	/** The banner's HTML for an answer from get_packages. Exposed for the node harness. */
	ns.banner_html = function (data, doc) {
		if (!data || data.enabled === false) return "";
		const rows = data.packages || [];
		const live = data.live_carriers || [];
		if (!rows.length) {
			// No card and no "nothing here" sentence: one small button, only where a number can go.
			return data.can_add
				? `<div class="ee-pkg-addline"><button type="button" class="btn btn-default btn-xs ee-pkg-add">${esc(
						__("Add tracking number")
				  )}</button></div>`
				: "";
		}

		const head = [`<span class="ee-pkg-title">${esc(__("Packages on their way"))}</span>`];
		if (doc && doc.custom_expected_delivery_date) {
			head.push(
				`<span class="ee-pkg-muted">${esc(__("Your expected date: {0}", [day(doc.custom_expected_delivery_date)]))}</span>`
			);
		}

		const lines = rows.map((row) => {
			const p = ns.describe(row, live);
			const number = p.url
				? `<a href="${esc(p.url)}" target="_blank" rel="noopener noreferrer" title="${esc(__("Open the carrier's tracking page"))}">${esc(p.number)}</a>`
				: `<span>${esc(p.number)}</span>`;
			const bits = [
				`<span class="ee-pkg-pill ee-pkg-${esc(p.tone)}">${esc(p.status)}</span>`,
				p.when ? `<span>${esc(p.when)}</span>` : "",
				p.detail ? `<span class="ee-pkg-muted">${esc(p.detail)}</span>` : "",
				p.where ? `<span class="ee-pkg-muted">${esc(p.where)}</span>` : "",
				p.checked ? `<span class="ee-pkg-muted">${esc(p.checked)}</span>` : "",
			].filter(Boolean);
			const remove = data.can_remove
				? `<button type="button" class="btn btn-link btn-xs ee-pkg-remove" data-name="${esc(row.name)}" aria-label="${esc(
						__("Remove tracking number {0}", [p.number])
				  )}">${esc(__("Remove"))}</button>`
				: "";
			const note = p.note ? `<div class="ee-pkg-note">${esc(p.note)}</div>` : "";
			return (
				`<div class="ee-pkg-row">` +
				`<div class="ee-pkg-line"><span class="ee-pkg-carrier">${esc(p.carrier)}</span> ${number} ${bits.join(" ")} ${remove}</div>` +
				note +
				`</div>`
			);
		});

		const actions = [];
		if (data.can_add) {
			actions.push(`<button type="button" class="btn btn-default btn-sm ee-pkg-add">${esc(__("Add tracking number"))}</button>`);
		}
		if (data.can_refresh) {
			actions.push(`<button type="button" class="btn btn-default btn-sm ee-pkg-refresh">${esc(__("Refresh"))}</button>`);
		}
		const tracked = rows.some((row) => ["FedEx", "UPS"].includes(row.resolved_carrier));
		const off_note =
			tracked && !live.length
				? `<div class="ee-pkg-muted">${esc(
						__("Live status from FedEx and UPS is switched off. The tracking links still open the carrier's page.")
				  )}</div>`
				: "";
		return (
			`<div class="ee-pkg-card">` +
			`<div class="ee-pkg-head">${head.join(" ")}</div>` +
			lines.join("") +
			off_note +
			(actions.length ? `<div class="ee-pkg-actions">${actions.join(" ")}</div>` : "") +
			`</div>`
		);
	};

	function inject_style() {
		if (document.getElementById(STYLE_ID)) return;
		const style = document.createElement("style");
		style.id = STYLE_ID;
		style.textContent = `
			.${SLOT_CLASS} { margin: 0 0 var(--margin-md, 12px); }
			.${SLOT_CLASS}:empty { display: none; }
			.ee-pkg-addline .btn { min-height: 28px; }
			.ee-pkg-card { border: 1px solid var(--border-color, #e2e6e9); border-radius: var(--border-radius-md, 8px);
				padding: 8px 12px; background: var(--card-bg, #fff); font-size: var(--text-sm, 13px); max-width: 100%; }
			.ee-pkg-head { display: flex; flex-wrap: wrap; gap: 4px 12px; align-items: baseline; margin-bottom: 4px; }
			.ee-pkg-title { font-weight: 600; color: var(--heading-color, inherit); }
			.ee-pkg-row { padding: 4px 0; border-top: 1px solid var(--border-color, #e2e6e9); }
			.ee-pkg-row:first-of-type { border-top: 0; }
			.ee-pkg-line { display: flex; flex-wrap: wrap; gap: 4px 10px; align-items: center; overflow-wrap: anywhere; }
			.ee-pkg-carrier { font-weight: 600; }
			.ee-pkg-muted, .ee-pkg-note { color: var(--text-muted, #6c7680); }
			.ee-pkg-note { font-size: var(--text-xs, 12px); margin-top: 2px; }
			.ee-pkg-pill { border-radius: 999px; padding: 1px 8px; font-size: var(--text-xs, 12px); white-space: nowrap;
				background: var(--bg-gray, #f3f3f3); color: var(--text-color, inherit); }
			.ee-pkg-blue { background: var(--bg-blue, #e8f1fd); color: var(--text-on-blue, #1f4f99); }
			.ee-pkg-orange { background: var(--bg-orange, #fff1e6); color: var(--text-on-orange, #8a4a00); }
			.ee-pkg-green { background: var(--bg-green, #e6f4ea); color: var(--text-on-green, #1e6b35); }
			.ee-pkg-red { background: var(--bg-red, #fdecec); color: var(--text-on-red, #9b1c1c); }
			.ee-pkg-actions { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 6px; }
			.ee-pkg-actions .btn { min-height: 32px; }
			.ee-pkg-remove { padding: 0 4px; color: var(--text-muted, #6c7680); }
		`;
		document.head.appendChild(style);
	}

	function slot(frm) {
		const layout = frm.layout && frm.layout.wrapper;
		if (!layout) return null;
		const $layout = $(layout);
		let $slot = $layout.children("." + SLOT_CLASS);
		if (!$slot.length) $slot = $(`<div class="${SLOT_CLASS}"></div>`).prependTo($layout);
		return $slot;
	}

	function still_showing(frm, name) {
		return frm && frm.doc && frm.doc.name === name;
	}

	function draw(frm, data) {
		const $slot = slot(frm);
		if (!$slot) return;
		inject_style();
		$slot.html(ns.banner_html(data, frm.doc));
		$slot.find(".ee-pkg-add").on("click", () => add_dialog(frm));
		$slot.find(".ee-pkg-refresh").on("click", (event) => refresh_now(frm, $(event.currentTarget)));
		$slot.find(".ee-pkg-remove").on("click", (event) => remove(frm, $(event.currentTarget).attr("data-name")));
	}

	function call(method, args, type) {
		return frappe.call({ method: API + method, args: args, type: type || "POST" });
	}

	function load(frm) {
		const name = frm.doc.name;
		const ticket = (frm.__ee_pkg_ticket = (frm.__ee_pkg_ticket || 0) + 1);
		frappe
			.call({ method: API + "get_packages", args: { purchase_order: name }, type: "GET", freeze: false })
			.then(
				(r) => {
					if (ticket !== frm.__ee_pkg_ticket || !still_showing(frm, name)) return;
					draw(frm, (r && r.message) || {});
				},
				() => {
					// A failed read leaves no banner rather than an error on every order opened.
					const $slot = slot(frm);
					if ($slot) $slot.empty();
				}
			);
	}

	function add_dialog(frm) {
		const name = frm.doc.name;
		const dialog = new frappe.ui.Dialog({
			title: __("Add tracking number"),
			fields: [
				{
					fieldname: "tracking_number",
					fieldtype: "Data",
					label: __("Tracking number"),
					reqd: 1,
					description: __("Paste it as it is. Spaces and dashes are fine."),
				},
				{
					fieldname: "carrier",
					fieldtype: "Select",
					label: __("Carrier"),
					options: ["Auto", "FedEx", "UPS", "Other"].join("\n"),
					default: "Auto",
					description: __("Auto works out FedEx or UPS from the number. Pick Other for any other delivery service."),
				},
				{
					fieldname: "link_url",
					fieldtype: "Data",
					label: __("Tracking link (optional)"),
					depends_on: "eval:doc.carrier==='Other'",
					description: __("Where the package can be followed, if the supplier gave a link."),
				},
			],
			primary_action_label: __("Add"),
			primary_action(values) {
				dialog.disable_primary_action();
				call("add_tracking_number", {
					purchase_order: name,
					tracking_number: values.tracking_number,
					carrier: values.carrier || "Auto",
					link_url: values.carrier === "Other" ? values.link_url || "" : "",
				}).then(
					(r) => {
						dialog.hide();
						if (still_showing(frm, name)) draw(frm, (r && r.message) || {});
						frappe.show_alert({ message: __("Tracking number added"), indicator: "green" });
					},
					() => dialog.enable_primary_action()
				);
			},
		});
		dialog.show();
	}

	function remove(frm, row_name) {
		const name = frm.doc.name;
		frappe.confirm(__("Remove this tracking number from the order?"), () => {
			call("remove_tracking_number", { name: row_name }).then((r) => {
				if (still_showing(frm, name)) draw(frm, (r && r.message) || {});
			});
		});
	}

	function refresh_now(frm, $button) {
		const name = frm.doc.name;
		$button.prop("disabled", true).text(__("Checking…"));
		call("refresh_order", { purchase_order: name }).then(
			(r) => {
				const data = (r && r.message) || {};
				if (still_showing(frm, name)) draw(frm, data);
				if (data.too_soon) {
					frappe.show_alert({ message: __("Checked less than a minute ago."), indicator: "blue" });
				}
			},
			() => $button.prop("disabled", false).text(__("Refresh"))
		);
	}

	frappe.ui.form.on("Purchase Order", {
		refresh(frm) {
			const $slot = slot(frm);
			if (!$slot) return;
			if (frm.is_new()) {
				$slot.empty();
				return;
			}
			// A canceled order is asked too: its packages stay readable (the server offers no Add,
			// Remove or Refresh on it). An amended one has had them moved to the amendment.
			load(frm);
		},
	});
})();
