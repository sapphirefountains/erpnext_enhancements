/**
 * window.sf_kit_ready(): sf_kit for a Desk page, loaded once, and never a page left waiting.
 *
 * Loaded via: erpnext_enhancements.bundle.js (global), so every Desk page can call it. The kit
 * itself stays out of the global bundle (sf_kit.bundle.js: "loaded only by a page that uses it");
 * this file is only the loader, the one copy of what seven pages each carried in v1.645.0's first
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

	const parse = (value) => {
		if (typeof value !== "string") return value;
		try {
			return JSON.parse(value);
		} catch (e) {
			return null;
		}
	};

	// A server message as plain text. DOMParser builds an inert tree (nothing runs, nothing loads);
	// without one, tags are dropped.
	function words(html) {
		const text = String(html == null ? "" : html);
		if (typeof DOMParser === "function") {
			const body = new DOMParser().parseFromString(text, "text/html").body;
			return ((body && body.textContent) || "").replace(/\s+/g, " ").trim();
		}
		return text.replace(/<[^>]*>/g, " ").replace(/\s+/g, " ").trim();
	}

	// A failed frappe.call in the kit's rejection shape: { status, message, exc_type,
	// session_expired }. Expiry is read the way sf_kit/call.js reads it (is_session_expired): a 401,
	// exc_type SessionExpired / AuthenticationError (in the body or in one of its _server_messages),
	// the body's session_expired flag, or a 403 to a page that was signed in whose user_id cookie is
	// now gone or Guest (v16 deletes the cookies with the first 401). The kit itself is not imported
	// here: this file is what runs when the kit could not load.
	function rejection(xhr) {
		const status = Number(xhr && xhr.status) || 0;
		const body = parse(xhr && (xhr.responseJSON || xhr.responseText));
		const object = body && typeof body === "object" ? body : {};
		const list = parse(object._server_messages);
		const entries = (Array.isArray(list) ? list : []).map((m) => parse(m)).filter((m) => m && typeof m === "object");
		const types = [object.exc_type].concat(entries.map((m) => m.exc_type));
		const user = frappe.session && (frappe.session.logged_in_user || frappe.session.user);
		const cookie = /(?:^|;\s*)user_id=([^;]*)/.exec(typeof document !== "undefined" ? String(document.cookie || "") : "");
		const expired =
			status === 401 ||
			types.includes("SessionExpired") ||
			types.includes("AuthenticationError") ||
			!!object.session_expired ||
			(status === 403 && !!user && user !== "Guest" && (!cookie || cookie[1] === "Guest"));
		const exc_type = expired ? "SessionExpired" : typeof object.exc_type === "string" ? object.exc_type : "";
		const message = expired ? "" : entries.map((m) => words(m.message)).filter(Boolean).join(" ");
		return { status, message, exc_type, session_expired: expired };
	}

	// The kit's shape without the kit: what a page uses of it (esc, call, state.render, toast, chip,
	// tiles.html). A chip is its word, and a tile a line of text (title, status word, value,
	// detail): with no kit there is no kit stylesheet either, so markup shaped like the kit's would
	// draw as an unstyled jumble. The words carry the meaning, so nothing a page says is lost.
	function plain_kit() {
		const escape = (v) => frappe.utils.escape_html(v == null ? "" : String(v));
		const filled = (v) => v != null && typeof v !== "object" && String(v).trim() !== "";
		const line = (item) =>
			item && typeof item === "object"
				? `<p>${[item.title, item.status_label, item.value, item.detail].filter(filled).map(escape).join(" · ")}</p>`
				: "";
		return {
			esc: escape,
			chip: (label) => escape(label),
			tiles: { html: (items) => `<div>${(Array.isArray(items) ? items : []).map(line).join("")}</div>` },
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
					(xhr) => Promise.reject(rejection(xhr))
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
