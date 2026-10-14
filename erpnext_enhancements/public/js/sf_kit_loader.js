/**
 * window.sf_kit_ready(): sf_kit for a Desk page, loaded once, and never a page left waiting.
 *
 * Loaded via: erpnext_enhancements.bundle.js (global), so every Desk page can call it. The kit
 * itself stays out of the global bundle (sf_kit.bundle.js: "loaded only by a page that uses it");
 * this file is only the loader, the one copy of what seven pages each carried in v1.638.0's first
 * draft.
 *
 *   sf_kit_ready().then((sf) => {
 *       sf.state.render(el, "loading");
 *       sf.call(method, args, { quiet: true }).then(draw, (err) => ...);
 *   });
 *
 * It resolves to window.sf_kit when the kit arrived, otherwise to a stand-in of the same shape
 * (`plain_kit`): calls go through frappe.call, dialogs included, and a state is a line of plain
 * text with its one action as a plain button. It never rejects. v16's frappe.require runs its
 * callback even when a file fails to load (assets.js: "resolve even on error"), so "the callback
 * ran" does not mean "the kit is here"; window.sf_kit is checked after it, and a frappe.require
 * that throws (or is missing) lands on the stand-in too.
 *
 * One promise per Desk load (memoized): every page shares the first answer. A stand-in stays a
 * stand-in until the next full reload, as each page's own loader did.
 *
 * The first call on a Desk shows frappe's freeze overlay while the two files load: that is
 * frappe.require's own doing (assets.js), and expected. Later calls resolve at once.
 *
 * A page must still work without this file, i.e. in a Desk opened before the deploy that added it
 * (its global bundle is the old hashed one, while the page script comes fresh from getpage). Each
 * page guards with `typeof window.sf_kit_ready === "function"` and otherwise asks frappe.require
 * for the kit itself.
 */
(function () {
	if (typeof window === "undefined") return;

	let loading = null;

	// The kit's shape without the kit: what a page uses of it (esc, call, state.render, toast).
	function plain_kit() {
		return {
			esc: (v) => frappe.utils.escape_html(v == null ? "" : String(v)),
			call: (method, args, opts) =>
				Promise.resolve(
					frappe.call({
						method,
						args: args || {},
						silent: !!(opts && opts.quiet),
						freeze: !!(opts && opts.freeze),
					})
				).then(
					(r) => r && r.message,
					(xhr) =>
						Promise.reject({ status: (xhr && xhr.status) || 0, message: "", exc_type: "", session_expired: false })
				),
			state: {
				render(el, kind, opts) {
					opts = opts || {};
					const $el = $(el).empty();
					$('<div class="text-muted"></div>')
						.text(opts.title || "")
						.appendTo($el);
					if (typeof opts.on_action === "function") {
						$('<button class="btn btn-default btn-sm"></button>')
							.text(opts.action_label || __("Try again"))
							.on("click", opts.on_action)
							.appendTo($el);
					}
				},
			},
			toast: (message) => frappe.show_alert({ message, indicator: "orange" }),
		};
	}

	window.sf_kit_ready = function () {
		if (!loading) {
			loading = new Promise((resolve) => {
				const ready = () => resolve(window.sf_kit && window.sf_kit.state ? window.sf_kit : plain_kit());
				try {
					frappe.require(["sf_kit.bundle.js", "sf_kit.bundle.css"], ready);
				} catch (e) {
					// No frappe.require, or one that threw: the page carries on with the stand-in.
					ready();
				}
			});
		}
		return loading;
	};
})();
