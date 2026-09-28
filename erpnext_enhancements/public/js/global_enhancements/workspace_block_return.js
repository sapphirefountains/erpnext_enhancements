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
