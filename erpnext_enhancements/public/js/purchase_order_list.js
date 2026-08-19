/*
 * Purchase Order list view: Order Stage, readable and settable without opening the order.
 *
 * Targets: the Purchase Order list.
 * Loaded via: hooks.py `doctype_list_js["Purchase Order"]`.
 *
 * ER-2026-276347 asked for two things the field itself could not give:
 *
 * 1. **A stage you can read at a glance.** Frappe already renders a Select in a list cell
 *    as an `indicator-pill`, and colours it with `frappe.utils.guess_colour()` — which
 *    word-matches a fixed vocabulary (Open, Pending, Closed, Completed, Confirmed,
 *    Submitted, …). Not one of the seven stage names hits it, so every stage came out the
 *    same grey. A pill that is always grey is a label, not an indicator. `formatters`
 *    replaces the cell wholesale, so the colours below are the whole fix.
 * 2. **A stage you can change from the row.** Clicking a pill opens a picker over it;
 *    choosing a stage writes immediately.
 *
 * Two things about this file that are easy to get wrong:
 *
 * **It extends `frappe.listview_settings["Purchase Order"]`, it does not assign it.**
 * erpnext's own `purchase_order_list.js` owns `get_indicator` (the To Receive and Bill /
 * To Bill / Closed pill), `add_fields`, and the Close / Reopen action items. Our
 * `doctype_list_js` entry loads after it, so a bare assignment would silently drop all
 * three — the list would look fine and quietly lose the buttons.
 *
 * **The write goes through `frappe.db.set_value`, not a new endpoint.** That is
 * `frappe.client.set_value`, which loads the document and calls `save()` — so permissions
 * are enforced per document and the edit is versioned like any other. It works on a
 * submitted order only because `custom_order_stage` is `allow_on_submit`: a save on a
 * submitted document runs as `update_after_submit`, which permits exactly the
 * allow-on-submit fields and throws on anything else. A **cancelled** order cannot be
 * saved at all, so those get a plain, unclickable pill rather than a picker that would
 * fail on submit.
 *
 * Styles are inline rather than a stylesheet on purpose: global CSS in this app ships as
 * an esbuild bundle, and one small floating menu is not worth a bundle entry.
 */

frappe.listview_settings["Purchase Order"] = frappe.listview_settings["Purchase Order"] || {};

(function () {
	const DOCTYPE = "Purchase Order";
	const FIELD = "custom_order_stage";
	const MENU_CLASS = "ee-order-stage-menu";
	const PILL_CLASS = "ee-order-stage-pill";

	// Mirrors erpnext_enhancements/po_order_stage.py STAGES, in the same order. A stage
	// missing here renders grey and still works; a stage here that the field does not
	// offer would be written and then refuse to save, so the test suite asserts the two
	// lists match rather than trusting this comment.
	const STAGES = [
		"Created",
		"Awaiting Confirmation",
		"Awaiting Fulfillment",
		"Waiting for Delivery",
		"Waiting for Pickup",
		"Partially Fulfilled",
		"Received",
	];

	// Colour is the feature, so it carries meaning rather than variety: grey is "nothing
	// has happened yet", warm is "somebody owes us an answer", cool is "it is on its way",
	// green is "done". Every name is one of frappe's own indicator colours — an invented
	// one renders as an unstyled pill.
	const COLOURS = {
		Created: "gray",
		"Awaiting Confirmation": "orange",
		"Awaiting Fulfillment": "yellow",
		"Waiting for Delivery": "blue",
		"Waiting for Pickup": "purple",
		"Partially Fulfilled": "cyan",
		Received: "green",
	};

	const settings = frappe.listview_settings[DOCTYPE];

	// Merged, not replaced: erpnext's get_indicator reads per_received / per_billed /
	// advance_payment_status off the row, and dropping them makes its pill fall through to
	// nothing on every order.
	settings.add_fields = (settings.add_fields || []).concat([FIELD]);

	function stage_pill(stage, docname, editable) {
		const colour = COLOURS[stage] || "gray";
		const label = frappe.utils.escape_html(__(stage));
		if (!editable) {
			return `<span class="indicator-pill ${colour} ellipsis" title="${label}">
				<span class="ellipsis">${label}</span>
			</span>`;
		}
		return `<span class="indicator-pill ${colour} ellipsis ${PILL_CLASS}"
			data-po="${frappe.utils.escape_html(docname)}"
			data-stage="${frappe.utils.escape_html(stage)}"
			style="cursor: pointer;"
			title="${__("Click to change the order stage")}">
			<span class="ellipsis">${label}</span>
		</span>`;
	}

	settings.formatters = Object.assign({}, settings.formatters, {
		[FIELD]: function (value, df, doc) {
			// The field has a default, so a blank only happens on a row that predates it.
			// Showing the default would be a guess; an em dash is the truth.
			if (!value) {
				return `<span class="text-muted">&mdash;</span>`;
			}
			const editable = frappe.model.can_write(DOCTYPE) && cint(doc.docstatus) !== 2;
			return stage_pill(value, doc.name, editable);
		},
	});

	function close_menu() {
		$("." + MENU_CLASS).remove();
	}

	function set_stage($pill, docname, stage) {
		if (stage === $pill.attr("data-stage")) return;
		frappe.dom.freeze();
		frappe.db
			.set_value(DOCTYPE, docname, FIELD, stage)
			.then(function () {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __("{0} is now {1}", [docname, __(stage)]),
					indicator: "green",
				});
				// Repaint the one pill rather than the list. A full refresh loses the
				// reader's scroll position, and the whole point of this control is that
				// reading and changing happen in the same place.
				$pill.replaceWith(stage_pill(stage, docname, true));
				const rows = cur_list && cur_list.data ? cur_list.data : [];
				const row = rows.find(function (d) {
					return d.name === docname;
				});
				if (row) row[FIELD] = stage;
				// Unless a stage filter is on, in which case the row just repainted may no
				// longer belong in this list at all, and leaving it sitting there is a lie.
				const filters = cur_list ? cur_list.get_filters_for_args() || [] : [];
				const filtered = filters.some(function (f) {
					return f[1] === FIELD;
				});
				if (filtered) cur_list.refresh();
			})
			.catch(function () {
				// frappe.db.set_value has already shown the server's message. Unfreezing is
				// all that is left, and it has to happen on both paths.
				frappe.dom.unfreeze();
			});
	}

	function open_menu($pill) {
		close_menu();
		const docname = $pill.attr("data-po");
		const current = $pill.attr("data-stage");
		const $menu = $(`<div class="${MENU_CLASS}"></div>`).css({
			position: "absolute",
			"z-index": 1050,
			background: "var(--fg-color, #fff)",
			border: "1px solid var(--border-color, #e2e2e2)",
			"border-radius": "var(--border-radius-md, 6px)",
			"box-shadow": "var(--shadow-md, 0 4px 8px rgba(0, 0, 0, 0.12))",
			padding: "4px",
			"min-width": "200px",
		});
		STAGES.forEach(function (stage) {
			const attr = frappe.utils.escape_html(stage);
			$(`<div class="ee-order-stage-option" data-stage="${attr}"></div>`)
				.css({ padding: "4px 6px", cursor: "pointer", "border-radius": "4px" })
				.append(stage_pill(stage, docname, false))
				.append(stage === current ? " &check;" : "")
				.appendTo($menu);
		});
		$("body").append($menu);

		const rect = $pill[0].getBoundingClientRect();
		// Flip upwards when the row sits near the bottom of the window, which on a list of
		// a hundred-odd orders is most of them.
		const above = rect.bottom + $menu.outerHeight() + 8 > window.innerHeight;
		$menu.css({
			top: above
				? rect.top + window.scrollY - $menu.outerHeight() - 4
				: rect.bottom + window.scrollY + 4,
			left: Math.min(
				rect.left + window.scrollX,
				window.innerWidth - $menu.outerWidth() - 12,
			),
		});

		$menu.on("click", ".ee-order-stage-option", function (event) {
			event.preventDefault();
			event.stopPropagation();
			const stage = $(this).attr("data-stage");
			close_menu();
			set_stage($pill, docname, stage);
		});
	}

	// Delegated from the document and bound once at load rather than inside onload(): rows
	// are re-rendered on every refresh, and the selector is specific enough that no other
	// doctype's list can reach it.
	$(document)
		.on("click." + PILL_CLASS, "." + PILL_CLASS, function (event) {
			// Both are load-bearing. A list row sits inside a link, and a click on a list
			// cell is also frappe's filter-by-this-value gesture.
			event.preventDefault();
			event.stopPropagation();
			open_menu($(this));
		})
		.on("click." + MENU_CLASS, function (event) {
			if (!$(event.target).closest("." + MENU_CLASS + ", ." + PILL_CLASS).length) {
				close_menu();
			}
		})
		.on("keydown." + MENU_CLASS, function (event) {
			if (event.key === "Escape") close_menu();
		});

	const original_onload = settings.onload;
	settings.onload = function (listview) {
		// erpnext's onload adds the Close and Reopen action items. Losing them would be
		// this file's doing and would look like erpnext's bug.
		if (original_onload) original_onload(listview);

		listview.page.add_action_item(__("Set Order Stage"), function () {
			const names = listview.get_checked_items(true);
			if (!names.length) return;
			frappe.prompt(
				{
					fieldname: "stage",
					label: __("Order Stage"),
					fieldtype: "Select",
					options: STAGES.join("\n"),
					reqd: 1,
				},
				function (values) {
					frappe
						.xcall(
							"frappe.desk.doctype.bulk_update.bulk_update.submit_cancel_or_update_docs",
							{
								doctype: DOCTYPE,
								docnames: names,
								action: "update",
								data: { [FIELD]: values.stage },
							},
						)
						.then(function () {
							listview.clear_checked_items();
							listview.refresh();
						});
				},
				__("Set Order Stage on {0} orders", [names.length]),
				__("Update"),
			);
		});
	};
})();
