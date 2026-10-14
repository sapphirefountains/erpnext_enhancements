/**
 * Reload a workspace's Custom HTML Blocks when you come back to the workspace.
 *
 * Targets: every Custom HTML Block that fetches data (custom_html_blocks/*.js).
 * Loaded via: erpnext_enhancements.bundle.js (global).
 *
 * THE BUG. The desk keeps a single Workspaces page, and v16's Workspace.show()
 * (frappe/public/js/frappe/views/workspace/workspace.js:91) returns early when the
 * workspace it would show is the one already shown:
 *
 *     if (this._page?.name === page.name) return; // already shown
 *
 * So from a dashboard to a form, a list or a desk page and back again (the Back
 * button, a breadcrumb, the sidebar) nothing is rebuilt and no block script runs:
 * every data block shows what it showed when the workspace was first opened, until
 * somebody reloads the browser tab. A block's script runs only when the workspace
 * RENDERS the page, which is the first visit and coming back from a different
 * workspace.
 *
 * THE API. Once its DOM is ready, a block calls
 *
 *     erpnext_enhancements.workspace_blocks.onWorkspaceReturn(root, load)
 *
 * with its shadow root and the function that fetches and draws. When the route comes
 * back to the workspace the block was drawn on, and the block is still on the page,
 * `load(root)` runs again. The block must still work when this file is absent (a
 * device holding a bundle cached from before it), so it guards the call:
 *
 *     const blocks = window.erpnext_enhancements && window.erpnext_enhancements.workspace_blocks;
 *     if (blocks && blocks.onWorkspaceReturn) blocks.onWorkspaceReturn(container, load);
 *
 * `load` is given the root. A block whose load reads its first argument as something
 * else (`load(force)`, `load(sign)`) registers a wrapper instead.
 *
 * THE ORDER OF EVENTS, and why a fresh block is not loaded twice. v16's router
 * (frappe/public/js/frappe/router.js, route(), lines 147-152) parses the route into a
 * NEW array (`this.current_route = await this.parse()`), calls render(), and only then
 * trigger("change"). render() reaches Workspace.show() synchronously, but a render
 * draws its blocks later: EditorJS renders behind `this.editor.isReady.then(...)`
 * (workspace.js:302-310), and each CustomBlockWidget awaits frappe.model.with_doc
 * before create_shadow_element runs the block script (custom_block_widget.js:26-30),
 * which then waits for its DOM on a timer. So a block drawn by a navigation registers
 * AFTER that navigation's "change", having already loaded itself, and the next
 * "change" that finds it is a real return. In case that order ever changes, each
 * registration also remembers the route array that was current when it registered:
 * a block registered under the route that is still current when "change" fires
 * belongs to this same navigation, and is left alone.
 *
 * Only blocks drawn on the workspace now being shown reload. From Home to Travel,
 * "change" fires while Home's blocks are still in the document (Travel's render has
 * not happened yet), and reloading them would be a wasted request per block. A
 * registration whose host has left the document (the page was rendered again, or
 * another workspace was rendered in its place) is dropped.
 *
 * LINKS INSIDE A BLOCK (v1.623.0). Desk's own link handler (frappe/public/js/frappe/
 * router.js:25-70) is a jQuery delegate on body. A click inside a block's shadow root
 * reaches body retargeted to the shadow HOST, so its `a` selector never matches, and every
 * block link was a full page load that booted the whole Desk again (and Back loaded it a
 * second time). Registering a root therefore also routes its `/desk` links in place, by
 * the same rules as that delegate: same host, no target="_blank", no onclick, no modifier
 * key, not a bare "#", query string into frappe.route_options, then frappe.set_route.
 * One click listener per root, on the root itself, so nothing outside the block changes.
 *
 * ON SCREEN (v1.641.0). A block that polls (a timer, a realtime event) must not fetch while
 * nobody can see it: leaving the workspace for a form or a list hides the block (display:none
 * on an ancestor) or, once the workspace renders again, detaches it, and its timers keep
 * firing. `onScreen(root)` answers for a shadow root (through its host) or an element: false
 * when the host has left the document or getClientRects() is empty under a display:none
 * ancestor, true otherwise. It FAILS OPEN: no host, a host without layout APIs (a document, a
 * test double) or anything that throws answers true, so a block polls as it did before. The
 * Task Dashboard and Who's Working each carried a copy of this check until v1.641.0; they ask
 * here now, and treat a missing helper (an older bundle) as on screen too.
 *
 * THE KIT AND THE TICKET (v1.649.0). Every block used to carry its own escape, its own "only
 * the newest answer draws" counter and its own loading / empty / error markup. Two helpers
 * replace them:
 *
 *     blocks.kit(root).then((sf) => ...)    sf_kit (public/js/sf_kit.bundle.js) for this root
 *     const current = blocks.ticket(root);  then `if (current()) draw(...)` when the answer lands
 *
 * kit(root) loads the kit once per page load (frappe.require, memoized), adopts sf_kit.CSS into
 * that root's adoptedStyleSheets once per root (a page stylesheet does not cross the shadow
 * boundary; the --sf-* tokens do), and resolves to window.sf_kit. It NEVER rejects and never
 * leaves a block undrawn: v16's frappe.require calls back even when the script failed to load
 * (assets.js: `script.onerror = resolve`, "for backward compatibility"), and it records the path
 * as executed either way, so a second require of the same path never fetches again. So when the
 * kit is not there after the callback it never will be, and kit() resolves to a stand-in instead:
 * esc, a state.render that writes the title and body as plain text plus a plain "Try again"
 * button for on_action, and a chip that is its escaped word. A ten-second timer resolves it too, in case a callback never comes.
 *
 * ticket(root) is the counter the blocks each wrote as `root.__ee_ticket`: every call takes the
 * next number on the root, and the function it returns says whether that number is still the
 * newest. The refresh button and a return to the workspace can both ask while an answer is on
 * its way; only the newest answer draws, and an answer only ever draws into the root it was for.
 *
 * A block still guards both: a device holding a bundle cached from before them has neither, and
 * the block then draws plain text with a stand-in of its own (custom_html_blocks/README.md).
 *
 * ONE HANDLER, NEVER A THROW. frappe.router is an event emitter
 * (frappe/public/js/frappe/event_emitter.js:26-28) whose off() wraps the handler in a
 * new function before it unbinds, so it can never remove one. This file binds exactly
 * one handler per page load, on the first registration. The emitter is jQuery's: an
 * exception in one handler stops the handlers after it and rejects router.route().
 * So every step here is inside try/catch, and one block's failing load never stops
 * the others.
 */
(function () {
	if (typeof frappe === "undefined" || typeof frappe.provide !== "function") return;
	const api = frappe.provide("erpnext_enhancements.workspace_blocks");
	// Evaluated twice (it should not be), the first registry stays the only one.
	if (typeof api.onWorkspaceReturn === "function") return;

	let registrations = [];
	let bound = false;

	function warn(message, error) {
		try {
			console.warn("workspace_block_return: " + message, error);
		} catch (e) {
			// nothing left to tell
		}
	}

	function currentRoute() {
		if (typeof frappe.get_route === "function") return frappe.get_route();
		return (frappe.router && frappe.router.current_route) || null;
	}

	// v16's route for a workspace (router.js, convert_to_standard_route):
	// ["Workspaces", name] for a public one, ["Workspaces", "private", name] for a
	// private one. Workspace.get_page_to_show() reads the name the same way.
	function workspaceOf(route) {
		if (!Array.isArray(route) || route[0] !== "Workspaces") return null;
		return (route[1] === "private" ? route[2] : route[1]) || null;
	}

	// The workspace a block is drawn on: the route's, or when the route has already
	// moved on (the block took its time to register, and you had left), the page the
	// workspace view last rendered.
	function shownWorkspace(route) {
		const name = workspaceOf(route);
		if (name) return name;
		const page = frappe.workspace && frappe.workspace._page;
		return (page && page.name) || null;
	}

	function onPage(registration) {
		const host = registration.root && registration.root.host;
		return Boolean(host && host.isConnected);
	}

	function run(registration) {
		try {
			const result = registration.load(registration.root);
			if (result && typeof result.then === "function") {
				result.then(null, (error) => warn("a block's reload failed", error));
			}
		} catch (error) {
			warn("a block's reload failed", error);
		}
	}

	function onRouteChange() {
		try {
			registrations = registrations.filter(onPage);
			const route = currentRoute();
			const name = workspaceOf(route);
			if (!name) return;
			registrations.slice().forEach((registration) => {
				if (registration.workspace !== name) return;
				if (registration.route === route) return;
				run(registration);
			});
		} catch (error) {
			warn("route change", error);
		}
	}

	// A click on a `/desk` link inside a block, routed the way router.js routes one outside.
	function onBlockClick(event) {
		try {
			if (event.defaultPrevented || event.button > 0) return;
			if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
			const target = event.target && event.target.closest ? event.target.closest("a[href]") : null;
			if (!target) return;
			const href = target.getAttribute("href") || "";
			if (!href || href.charAt(0) === "#") return;
			if (target.getAttribute("target") === "_blank" || target.getAttribute("onclick")) return;
			if (target.hasAttribute && target.hasAttribute("download")) return;
			if (target.hostname !== window.location.hostname) return;
			const router = frappe.router;
			if (!router || typeof router.is_app_route !== "function" || !router.is_app_route(target.pathname)) return;
			if (typeof frappe.set_route !== "function") return;
			if (target.search) {
				const options = {};
				new URLSearchParams(target.search).forEach((value, key) => {
					options[key] = value;
				});
				frappe.route_options = options;
			}
			if (target.hash) frappe.route_hash = target.hash;
			event.preventDefault();
			frappe.set_route(target.pathname);
		} catch (error) {
			warn("a block's link", error);
		}
	}

	const routed = typeof WeakSet === "function" ? new WeakSet() : null;

	function routeLinks(root) {
		try {
			if (!root || typeof root.addEventListener !== "function") return false;
			if (routed) {
				if (routed.has(root)) return true;
				routed.add(root);
			} else {
				if (root.__ee_links_routed) return true;
				root.__ee_links_routed = true;
			}
			root.addEventListener("click", onBlockClick);
			return true;
		} catch (error) {
			warn("route links", error);
			return false;
		}
	}

	/** Route a block's `/desk` links in place. Registering with onWorkspaceReturn does it too. */
	api.routeLinks = routeLinks;

	/**
	 * Is this block on screen? `root` is a block's shadow root (asked through its host) or
	 * an element. Fails open: true whenever it cannot tell.
	 */
	api.onScreen = function (root) {
		try {
			// A shadow root's host is an element; an anchor's `host` is a string, so it answers
			// for itself.
			const host = root && root.host && typeof root.host === "object" ? root.host : root;
			if (!host || typeof host.getClientRects !== "function") return true;
			return Boolean(host.isConnected && host.getClientRects().length > 0);
		} catch (error) {
			warn("on screen", error);
			return true;
		}
	};

	/**
	 * A ticket on `root`: the function returned says whether this is still the newest one taken.
	 */
	api.ticket = function (root) {
		try {
			const holder = root && typeof root === "object" ? root : null;
			if (!holder) return () => true;
			const mine = (holder.__ee_ticket = (holder.__ee_ticket || 0) + 1);
			return () => holder.__ee_ticket === mine;
		} catch (error) {
			warn("ticket", error);
			return () => true;
		}
	};

	const KIT_ASSET = "sf_kit.bundle.js";
	const KIT_WAIT_MS = 10000;
	const ENTITIES = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" };
	let kitLoaded = null;
	let standIn = null;
	let kitSheet = null;
	let kitSheetText = null;
	const adopted = typeof WeakSet === "function" ? new WeakSet() : null;

	function usableKit() {
		const sf = window.sf_kit;
		if (!sf || typeof sf.esc !== "function" || typeof sf.chip !== "function") return null;
		return sf.state && typeof sf.state.render === "function" ? sf : null;
	}

	function translate(text) {
		return typeof __ === "function" ? __(text) : text;
	}

	// What a block gets when the kit did not load: enough to draw, as plain text.
	function getStandIn() {
		if (standIn) return standIn;
		const esc = (value) =>
			(value === null || value === undefined ? "" : String(value)).replace(/[&<>"'`]/g, (ch) => ENTITIES[ch]);
		const titles = {
			loading: "Loading…",
			error: "Something went wrong.",
			"no-access": "You do not have access to this.",
		};
		standIn = {
			stand_in: true,
			esc,
			chip: (label) => esc(label === null || label === undefined ? "" : String(label).trim()),
			state: {
				// The title as text, then the body as text, then a plain button for on_action:
				// without it a failed Weather or Astrology (no refresh button) could never retry.
				render(el, kind, opts) {
					const host = el && el.jquery ? el[0] : el;
					if (!host) return null;
					opts = opts || {};
					host.textContent = opts.title || translate(titles[kind] || "Nothing here yet.");
					try {
						const doc = host.ownerDocument || (typeof document !== "undefined" ? document : null);
						if (!doc || typeof host.appendChild !== "function") return host;
						if (opts.body) host.appendChild(doc.createTextNode(" " + opts.body));
						if (opts.action_label || typeof opts.on_action === "function") {
							const button = doc.createElement("button");
							button.type = "button";
							button.textContent =
								opts.action_label || (typeof __ === "function" ? __("Try again") : "Try again");
							if (typeof opts.on_action === "function") {
								button.addEventListener("click", (event) => {
									if (event && typeof event.preventDefault === "function") event.preventDefault();
									opts.on_action(event);
								});
							}
							host.appendChild(doc.createTextNode(" "));
							host.appendChild(button);
						}
					} catch (error) {
						warn("the stand-in panel", error);
					}
					return host;
				},
			},
		};
		return standIn;
	}

	function adoptStyles(root, sf) {
		try {
			// A shadow root only (it has a host): a block that fell back to `document` styles nothing.
			if (!root || !root.host || typeof sf.CSS !== "string" || !Array.isArray(root.adoptedStyleSheets)) return;
			if (typeof CSSStyleSheet !== "function") return;
			if (adopted) {
				if (adopted.has(root)) return;
			} else if (root.__ee_kit_styles) {
				return;
			}
			// One constructed sheet for every block on the page; a newer kit's text replaces it.
			if (!kitSheet || kitSheetText !== sf.CSS) {
				kitSheet = new CSSStyleSheet();
				kitSheet.replaceSync(sf.CSS);
				kitSheetText = sf.CSS;
			}
			root.adoptedStyleSheets = root.adoptedStyleSheets.concat([kitSheet]);
			if (adopted) adopted.add(root);
			else root.__ee_kit_styles = true;
		} catch (error) {
			warn("adopt the kit's styles", error);
		}
	}

	function loadKit() {
		if (kitLoaded) return kitLoaded;
		kitLoaded = new Promise((resolve) => {
			let timer = null;
			const done = () => {
				if (timer) clearTimeout(timer);
				resolve();
			};
			try {
				if (usableKit() || typeof frappe.require !== "function") {
					resolve();
					return;
				}
				timer = setTimeout(done, KIT_WAIT_MS);
				const pending = frappe.require([KIT_ASSET], done);
				if (pending && typeof pending.then === "function") pending.then(null, done);
			} catch (error) {
				warn("load the kit", error);
				done();
			}
		});
		return kitLoaded;
	}

	/**
	 * sf_kit for a block's shadow root: loaded once, its CSS adopted into `root` once, and a
	 * plain-text stand-in when it could not load. Always resolves.
	 */
	api.kit = function (root) {
		return loadKit().then(() => {
			const sf = usableKit();
			if (!sf) return getStandIn();
			adoptStyles(root, sf);
			return sf;
		});
	};

	function bind() {
		if (bound) return true;
		const router = frappe.router;
		if (!router || typeof router.on !== "function") return false;
		router.on("change", onRouteChange);
		bound = true;
		return true;
	}

	/**
	 * Reload `load(root)` whenever the route comes back to the workspace this block is
	 * drawn on. Returns true when it will, false when it cannot (no router, no root).
	 * Registering the same root again replaces its earlier registration.
	 */
	api.onWorkspaceReturn = function (root, load) {
		try {
			if (!root || typeof load !== "function") return false;
			routeLinks(root);
			if (!bind()) return false;
			const route = currentRoute();
			const registration = { root, load, route, workspace: shownWorkspace(route) };
			const index = registrations.findIndex((row) => row.root === root);
			if (index === -1) {
				registrations.push(registration);
			} else {
				registrations[index] = registration;
			}
			return true;
		} catch (error) {
			warn("register", error);
			return false;
		}
	};
})();
