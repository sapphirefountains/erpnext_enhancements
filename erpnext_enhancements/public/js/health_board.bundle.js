/**
 * window.sf_health_board: the one health dashboard, drawn on sf_kit tiles (v1.651.0,
 * TASK-2026-02821). Integrations Health (/desk/integrations-health) and the Device Fleet Dashboard
 * (/desk/device-fleet-dashboard) are both thin callers of it. Until then the fleet page was a copy
 * of the health page with its class prefix changed: two inline stylesheets, two green / amber /
 * red colour maps, and a coloured dot as the only sign of a tile's or a metric's status.
 *
 * Loaded only by those two pages, `frappe.require(["health_board.bundle.js"], ...)`, never
 * app_include_js. A bundle (content-hashed name) rather than a raw /assets path, for the
 * year-immutable cache in CLAUDE.md. It carries no kit of its own: it asks window.sf_kit_ready()
 * (public/js/sf_kit_loader.js, in the global bundle) for sf_kit, and every tile, chip, state panel
 * and toast it draws is the kit's, so the colours are the kit's tokens in light and dark alike.
 *
 *   sf_health_board.mount(page, {
 *       method: "erpnext_enhancements.api.device_dashboard.get_fleet_health",
 *       loading: __("Loading fleet status…"),
 *       failed: __("Could not load the fleet status."),
 *       refused: __("You do not have access to the Device Fleet Dashboard."),
 *       meta: (data) => __("{0} devices · as of {1}", [data.total || 0, data.generated_at || ""]),
 *       tiles: (data, board) => (data.tiles || []).map(board.tile),
 *       after: ({ sf, data, tile }) => { ... },   // optional; tile(key) is that tile, as jQuery
 *   });
 *
 * tile(check) turns one health check as the server sends it into a kit tile. Both
 * api/integrations_health.py and api/device_dashboard.py build the same shape: { key, label,
 * status, headline, notes, metrics: [{ label, value, tone }], links: [{ label, route }] }. The
 * label is the title; the headline and the notes are the detail line; the status and each
 * metric's tone go to the kit as they are (it reads green / amber / red, and draws a toned metric's
 * value as a chip); a link's route goes through the kit's safe_href rule, so only a /desk/ route
 * or an http(s) URL becomes an href. Every field is escaped by the kit; nothing here writes a
 * server string into markup.
 *
 * States, as S3.1 (v1.645.0) drew them for the other dashboards: the kit's loading panel on the
 * first load; a failed first load draws the error panel with Try again, no-access on a 403, or the
 * signed-out panel with Reload when the session has lapsed; a Refresh that fails while tiles are
 * showing keeps them and says so in a toast. Calls go on the kit's quiet path, so frappe puts up
 * no dialog of its own, and a reply that lands after a newer Refresh was asked for is dropped.
 * Nothing here writes history.
 *
 * No fallback for a Desk without the loader: a Desk whose assets.json knows this bundle's hashed
 * name was built after the loader shipped (v1.645.0), so it has window.sf_kit_ready. If it is
 * missing anyway, the page says so in a line of text rather than throwing.
 */
(function () {
	if (typeof window === "undefined") return;

	function blank(value) {
		return value === undefined || value === null || typeof value === "object" || String(value).trim() === "";
	}

	function list(value) {
		return Array.isArray(value) ? value : [];
	}

	// The headline and then each note, as one line: a part that does not end in a stop gets one,
	// so "Not configured" and "Add the credentials." read as two sentences, not one run-on.
	function detail(parts) {
		const kept = list(parts)
			.filter((part) => !blank(part))
			.map((part) => String(part).trim());
		return kept.map((part, i) => (i < kept.length - 1 && !/[.!?:…]$/.test(part) ? part + "." : part)).join(" ");
	}

	function tile(check) {
		const c = check && typeof check === "object" ? check : {};
		return {
			key: c.key,
			title: c.label,
			status: c.status,
			detail: detail([c.headline].concat(list(c.notes))),
			metrics: list(c.metrics),
			links: list(c.links),
		};
	}

	function mount(page, opts) {
		opts = opts || {};
		const $root = $('<div class="sf-health-board" style="padding: var(--sf-space-15) 0 var(--sf-space-30);"></div>').appendTo(page.body);
		const $meta = $(
			'<p class="sf-health-meta" style="margin: 0 0 var(--sf-space-15); color: var(--sf-text-muted); font-size: var(--sf-text-sm);"></p>'
		).appendTo($root);
		const $content = $('<div class="sf-health-content"></div>').appendTo($root);
		let loaded = false;
		let asked = 0;

		// A board that never loaded: the way back in when the session has lapsed (the quiet path
		// puts up no sign-in dialog of frappe's), no access on a 403, else the error with Try again.
		function failed(sf, err) {
			err = err || {};
			if (err.session_expired) {
				sf.state.render($content, "error", {
					title: __("You have been signed out."),
					body: __("Reload the page to sign in again."),
					action_label: __("Reload"),
					on_action: () => window.location.reload(),
				});
			} else if (err.status === 403) {
				sf.state.render($content, "no-access", { title: opts.refused, body: err.message });
			} else {
				sf.state.render($content, "error", { title: opts.failed, body: err.message, on_action: () => load() });
			}
		}

		function draw(sf, data) {
			$meta.text(typeof opts.meta === "function" ? opts.meta(data) : "");
			$content.html(sf.tiles.html(typeof opts.tiles === "function" ? opts.tiles(data, board) : []));
			if (typeof opts.after !== "function") return;
			// The tile drawn for `key`, found by comparing the attribute rather than by building a
			// selector out of a server value.
			const find = (key) =>
				$content.find(".sf-tile").filter(function () {
					return this.getAttribute("data-key") === String(key);
				});
			opts.after({ sf, data, tile: find });
		}

		function load() {
			const seq = ++asked;
			if (typeof window.sf_kit_ready !== "function") {
				$content.text(__("This page could not load. Reload the page to try again."));
				return;
			}
			window.sf_kit_ready().then((sf) => {
				if (seq !== asked) return;
				if (!loaded) sf.state.render($content, "loading", { title: opts.loading });
				sf.call(opts.method, {}, { quiet: true }).then(
					(message) => {
						if (seq !== asked) return;
						if (message && typeof message === "object") {
							loaded = true;
							draw(sf, message);
						} else if (!loaded) {
							sf.state.render($content, "empty", {
								title: __("No status came back."),
								body: __("Refresh to ask again."),
							});
						}
					},
					(err) => {
						if (seq !== asked) return;
						if (!loaded) return failed(sf, err);
						err = err || {};
						sf.toast(
							err.session_expired
								? __("You have been signed out. Reload the page to sign in again.")
								: err.status === 403
									? __("You no longer have access to this page. Showing what was last loaded.")
									: __("Could not refresh. Showing what was last loaded."),
							{ tone: "warning" }
						);
					}
				);
			});
		}

		page.set_primary_action(__("Refresh"), () => load(), "refresh");
		load();
		return { load };
	}

	const board = { version: 1, tile, detail, mount };
	window.sf_health_board = board;
})();
