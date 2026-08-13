/**
 * Sapphire Fountains — first-touch attribution capture.
 *
 * Reads campaign parameters on entry, stores them in a first-party cookie that
 * never overwrites a non-empty value, and copies them into the hidden fields of
 * every Fluent Form on the page.
 *
 * The rules this implements are the ones in docs/attribution-runbook.md. Two are
 * worth restating here because they are the ones that look like bugs:
 *
 *  - FIRST TOUCH WINS. The campaign that earned the visitor gets credit, not
 *    whichever parameter happened to be on the URL when they finally converted.
 *    So a non-empty stored value is never replaced within the cookie's life.
 *  - COOKIE, NOT localStorage. It has to survive the www -> erp subdomain hop
 *    and be readable if a form ever posts server-side.
 *
 * No jQuery, no build step. This runs on a WordPress site we do not otherwise
 * control, so it depends on nothing.
 */
(function () {
	"use strict";

	var COOKIE = "sf_attr";
	var COOKIE_DAYS = 90;

	/* Cookie domain must be the registrable domain, not the host, or a value set
	   on www is invisible to any other subdomain. */
	var COOKIE_DOMAIN = ".sapphirefountains.com";

	/* Hosts that are "us". A referrer from one of these is internal navigation
	   and must not be recorded as the referrer that produced the visit. */
	var OWN_HOSTS = ["sapphirefountains.com", "www.sapphirefountains.com", "erp.sapphirefountains.com"];

	/* Query parameter -> stored key. gbraid/wbraid are the click IDs Google Ads
	   sends instead of gclid when iOS blocks the usual one; a campaign that only
	   reads gclid silently loses that traffic. */
	var PARAM_KEYS = [
		"utm_source",
		"utm_medium",
		"utm_campaign",
		"utm_content",
		"utm_term",
		"gclid",
		"gbraid",
		"wbraid",
		"msclkid",
	];

	/* Every key the cookie may hold, and therefore every hidden field we fill. */
	var ALL_KEYS = PARAM_KEYS.concat(["landing_page", "first_referrer", "first_seen"]);

	function readCookie(name) {
		var parts = ("; " + document.cookie).split("; " + name + "=");
		if (parts.length !== 2) {
			return null;
		}
		try {
			return JSON.parse(decodeURIComponent(parts.pop().split(";").shift()));
		} catch (e) {
			/* A malformed cookie is treated as absent rather than thrown away
			   loudly — a parse error must not stop the form from submitting. */
			return null;
		}
	}

	function writeCookie(name, value) {
		var expires = new Date(Date.now() + COOKIE_DAYS * 864e5).toUTCString();
		var secure = location.protocol === "https:" ? "; Secure" : "";
		document.cookie =
			name +
			"=" +
			encodeURIComponent(JSON.stringify(value)) +
			"; expires=" + expires +
			"; path=/" +
			"; domain=" + COOKIE_DOMAIN +
			"; SameSite=Lax" +
			secure;
	}

	function currentParams() {
		var found = {};
		var search = window.location.search || "";
		if (!search) {
			return found;
		}
		var query;
		try {
			query = new URLSearchParams(search);
		} catch (e) {
			return found;
		}
		PARAM_KEYS.forEach(function (key) {
			var value = query.get(key);
			if (value) {
				/* Cap it. A hostile or broken link should not be able to push a
				   multi-kilobyte cookie onto every visitor. */
				found[key] = String(value).slice(0, 255);
			}
		});
		return found;
	}

	function externalReferrer() {
		var ref = document.referrer || "";
		if (!ref) {
			return "";
		}
		try {
			var host = new URL(ref).hostname.toLowerCase();
			if (OWN_HOSTS.indexOf(host) !== -1) {
				return "";
			}
		} catch (e) {
			return "";
		}
		return ref.slice(0, 255);
	}

	/**
	 * Merge this pageview into the stored attribution, first-touch wins.
	 * Returns the merged object.
	 */
	function capture() {
		var stored = readCookie(COOKIE) || {};
		var incoming = currentParams();
		var changed = false;

		Object.keys(incoming).forEach(function (key) {
			if (!stored[key]) {
				stored[key] = incoming[key];
				changed = true;
			}
		});

		/* Landing page and referrer describe the FIRST page of the visit, so they
		   are only ever written when absent. */
		if (!stored.landing_page) {
			stored.landing_page = (location.pathname + location.search).slice(0, 255);
			changed = true;
		}
		if (!stored.first_referrer) {
			var ref = externalReferrer();
			if (ref) {
				stored.first_referrer = ref;
				changed = true;
			}
		}
		if (!stored.first_seen) {
			stored.first_seen = new Date().toISOString();
			changed = true;
		}

		if (changed) {
			writeCookie(COOKIE, stored);
		}
		return stored;
	}

	/**
	 * Copy stored values into hidden inputs.
	 *
	 * Only fields that already exist in the Fluent Forms builder are filled. A
	 * field injected here would reach WordPress but never reach ERPNext: the
	 * webhook serialises Fluent Forms' own submission data, so anything not in
	 * the form's schema is dropped. That is the single most common way this
	 * integration is wired up wrong.
	 */
	function fill(values) {
		ALL_KEYS.forEach(function (key) {
			var inputs = document.querySelectorAll('input[name="' + key + '"]');
			for (var i = 0; i < inputs.length; i++) {
				/* Do not clobber a value a human or another script already set. */
				if (!inputs[i].value) {
					inputs[i].value = values[key] || "";
				}
			}
		});
	}

	function run() {
		var values = capture();
		fill(values);

		/* Fluent Forms renders some layouts after DOMContentLoaded, and
		   conversational forms render each step on demand — so a single pass at
		   load misses them. Re-fill on mutation and once more at submit, which is
		   the only moment that actually has to be correct. */
		if (window.MutationObserver) {
			var observer = new MutationObserver(function () {
				fill(values);
			});
			observer.observe(document.documentElement, { childList: true, subtree: true });
		}
		document.addEventListener(
			"submit",
			function () {
				fill(values);
			},
			true
		);
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", run);
	} else {
		run();
	}
})();
