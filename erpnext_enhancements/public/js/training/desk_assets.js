// Copyright (c) 2026, Sapphire Fountains and contributors
// For license information, please see license.txt
//
// TR.loadAssets — pull a versioned /assets file into a Desk Page, once.
//
// A Desk Page gets its own <name>.js and <name>.css from the page loader, and
// (through the page_js hook) any extra JS listed in hooks.py. Neither mechanism
// reaches a stylesheet that lives outside the page folder, and player.css is
// shared by three surfaces — the learner page, the authoring canvas and the
// preview harness — so it cannot move into any one of them. That is what this is
// for.
//
// WHY NOT frappe.require. It derives an asset's type with frappe.assets.extn(),
// which splits the URL on "?" and takes the LAST segment — so a cache-busted
// ".../player.css?v=1.428.1" reports its extension as the version string and is
// loaded as neither css nor js. It fails by doing nothing, which on a stylesheet
// means an unstyled page rather than an error.
//
// WHY THE VERSION TOKEN IS MANDATORY. Raw /assets are served with a one-year
// immutable Cache-Control and carry no content hash (public/README.md, ADR-0008),
// so an edit never reaches a device that already cached the file. That is the
// "fix works on desktop, phones still broken" bug. Callers pass the deploy
// version; this refuses a path that does not carry one, because the failure is
// invisible on the machine of whoever makes the change — their cache is cold.
//
// THIS IS THE THIRD COPY, AND THE LAST. The first was the classic Training
// Builder's load_player (deleted with the page in v1.422.0); the second is
// training_canvas.js's private tc_load_asset, which this replaces. The Desk
// learner page would have been the fourth.

(function () {
	"use strict";

	var TR = (window.TR = window.TR || {});

	// Marks the tags this has already inserted. Attribute rather than a module-level
	// Set: a Desk Page is created once and lives for the whole session, but a hard
	// reload starts a fresh module scope with the old tags still in the document.
	var MARKER = "data-ee-asset";

	function isStylesheet(url) {
		// Split the query off first. ".css?v=1.428.1".endsWith(".css") is false, and
		// getting this wrong loads a stylesheet through a <script> tag, which is
		// silent: no error, no styles.
		return String(url).split("?")[0].toLowerCase().endsWith(".css");
	}

	// Resolves when the asset is in the document. Resolves immediately if it already
	// is — a Desk Page's on_page_show fires on every route change into it, so this is
	// called far more often than it does anything.
	TR.loadAsset = function (url) {
		return new Promise(function (resolve, reject) {
			if (!url) {
				reject(new Error("TR.loadAsset: no url"));
				return;
			}
			if (String(url).indexOf("?v=") === -1) {
				// Refused rather than tolerated. An unversioned /assets URL works
				// perfectly on a cold cache and silently serves a year-old file to
				// everybody else, so the one machine that would notice is the one
				// least likely to.
				reject(new Error("TR.loadAsset: " + url + " carries no ?v= cache-bust token"));
				return;
			}

			var css = isStylesheet(url);
			var selector =
				(css ? "link[" : "script[") + MARKER + '="' + url.replace(/"/g, '\\"') + '"]';
			if (document.querySelector(selector)) {
				resolve();
				return;
			}

			var node = document.createElement(css ? "link" : "script");
			node.setAttribute(MARKER, url);
			if (css) {
				node.rel = "stylesheet";
				node.href = url;
			} else {
				// Ordered, not async: the caller may be loading files that compose
				// each other, and async would let the later one evaluate first.
				node.async = false;
				node.src = url;
			}
			node.onload = function () {
				resolve();
			};
			node.onerror = function () {
				reject(new Error("Could not load " + url));
			};
			document.head.appendChild(node);
		});
	};

	// One door to the manager's "how is this person doing" dialog, from anywhere in
	// the Desk. It lives here, in the global bundle, because its two callers are on
	// opposite sides of the app (the Training Insights page and the Employee form)
	// and neither can load a helper before the helper exists -- the same argument
	// that put TR.loadAssets here.
	//
	// The dialog ITSELF is loaded on demand, not shipped globally: a manager opens
	// it rarely and a learner never, so bundling it would charge everyone for a
	// screen almost nobody sees.
	TR.openPersonRecord = function (user, version) {
		if (!user) return Promise.resolve();
		return TR.loadAssets(
			[
				"/assets/erpnext_enhancements/css/training/person_record.css",
				"/assets/erpnext_enhancements/js/training/person_record.js",
			],
			version || (window.frappe && frappe.boot.versions && frappe.boot.versions.erpnext_enhancements) || "0"
		).then(function () {
			if (typeof TR.personRecord === "function") TR.personRecord(user);
		});
	};

	// paths: ["/assets/.../player.css", ...]; version: the deploy token.
	// Kept as one call because the caller almost always wants all of them or none,
	// and a partial load is the shape that renders a half-styled page.
	TR.loadAssets = function (paths, version) {
		var token = "?v=" + encodeURIComponent(version || "");
		return Promise.all(
			(paths || []).map(function (path) {
				return TR.loadAsset(path + token);
			})
		);
	};
})();
