/**
 * Sapphire Fountains — attribution capture for the WordPress site.
 *
 * Reads campaign parameters on arrival, keeps them in a first-party cookie, and
 * copies them into the hidden fields of every Fluent Form on the page, so the
 * Fluent Forms webhook can hand them to ERPNext's submit_web_lead.
 *
 * The rules, restated here because they are the ones that look like bugs:
 *
 *  - FIRST TOUCH WITHIN A SESSION. Once a visit has a value for a key, nothing
 *    later in that visit overwrites it. A session ends after SESSION_MINUTES
 *    without a pageview (the Google Analytics definition).
 *  - A NEW VISIT THAT ARRIVES WITH CAMPAIGN TAGS STARTS A NEW TOUCH. The whole
 *    stored touch is replaced, landing page and referrer included, so a paid click
 *    never inherits the referrer of an earlier organic visit. A visit WITHOUT tags
 *    leaves the stored touch alone for the cookie's 90 days, so typing the address
 *    in a week later does not erase the campaign that found the customer.
 *    NEW_TAGGED_SESSION_REPLACES = false turns this into pure 90-day first touch.
 *  - COOKIE, NOT localStorage. It has to survive the www -> erp subdomain hop and be
 *    readable if a form ever posts server-side.
 *
 * utm_id is the spend-to-lead join key (TASK-2026-01570): each ad platform writes
 * its own campaign ID into it with a dynamic URL macro, and ERPNext matches it
 * against Ad Campaign.external_id. See README.md, "Tag the ads".
 *
 * No jQuery, no build step: this runs on a WordPress site we do not otherwise
 * control, so it depends on nothing. Under node (no `document`) it exports its
 * functions instead of running — scripts/test_sf_attribution.js uses that.
 */
(function (factory) {
	"use strict";
	var api = factory();
	if (typeof document === "undefined") {
		if (typeof module === "object" && module && module.exports) {
			module.exports = api;
		}
		return;
	}
	api.boot({
		document: document,
		location: window.location,
		now: function () {
			return Date.now();
		},
		MutationObserver: window.MutationObserver,
	});
})(function () {
	"use strict";

	var COOKIE = "sf_attr";
	var COOKIE_DAYS = 90;

	/* The registrable domain, so a value set on www is visible on erp. Only used on
	   a host inside it: a browser silently refuses a cookie for a foreign domain, so
	   on a WP Engine staging host (*.wpengine.com) the cookie falls back to host-only
	   rather than vanishing. */
	var COOKIE_DOMAIN = "sapphirefountains.com";

	var SESSION_MINUTES = 30;
	var NEW_TAGGED_SESSION_REPLACES = true;

	/* Browsers cap one cookie at ~4096 bytes including its name and attributes. */
	var MAX_COOKIE_CHARS = 3800;

	/* Hosts that are "us". A referrer from one of these is internal navigation and
	   must not be recorded as the referrer that produced the visit. */
	var OWN_HOSTS = ["sapphirefountains.com", "www.sapphirefountains.com", "erp.sapphirefountains.com"];

	/* Query parameters worth keeping. gbraid/wbraid are what Google Ads sends instead
	   of gclid when iOS blocks the usual click ID; msclkid is Microsoft Ads. fbclid is
	   deliberately absent: Meta adds it to every outbound link, organic posts included,
	   so it says "came from Facebook" and not "paid". */
	var PARAM_KEYS = [
		"utm_source",
		"utm_medium",
		"utm_campaign",
		"utm_id",
		"utm_content",
		"utm_term",
		"gclid",
		"gbraid",
		"wbraid",
		"msclkid",
	];

	/* What describes one touch. Replaced together, never piecemeal. */
	var TOUCH_KEYS = PARAM_KEYS.concat(["landing_page", "first_referrer", "touch_at"]);

	/* Hidden fields the script fills. Each must ALSO exist in the Fluent Forms
	   builder, or the webhook drops it — see README.md. */
	var FIELD_KEYS = PARAM_KEYS.concat(["landing_page", "first_referrer"]);

	/* Given up first, in order, if the cookie would be too large. */
	var SHED_ORDER = ["first_referrer", "utm_term", "utm_content", "landing_page"];

	function paramsFrom(search) {
		var found = {};
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
			if (value && value.trim()) {
				/* Cap it. A hostile or broken link must not push a multi-kilobyte
				   cookie onto every visitor. */
				found[key] = String(value).trim().slice(0, 255);
			}
		});
		return found;
	}

	function externalReferrer(ref) {
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

	function hasTouch(stored) {
		return TOUCH_KEYS.some(function (key) {
			return !!stored[key];
		});
	}

	/**
	 * Merge one pageview into the stored attribution. Pure: returns a new object.
	 *
	 * `page` is {params, path, referrer}; `nowMs` is the time of the pageview.
	 */
	function merge(stored, page, nowMs) {
		var out = {};
		Object.keys(stored || {}).forEach(function (key) {
			out[key] = stored[key];
		});

		var lastSeen = Date.parse(out.last_seen || "");
		var newSession = isNaN(lastSeen) || nowMs - lastSeen > SESSION_MINUTES * 60000;
		var tagged = Object.keys(page.params || {}).length > 0;

		if (newSession && tagged && NEW_TAGGED_SESSION_REPLACES && hasTouch(out)) {
			TOUCH_KEYS.forEach(function (key) {
				delete out[key];
			});
		}

		/* Landing page and referrer describe where a touch BEGAN, so they are only
		   written when a touch begins -- never patched onto an older one by a later,
		   untagged visit. */
		var startingTouch = !out.touch_at;
		var nowIso = new Date(nowMs).toISOString();

		if (startingTouch || !newSession) {
			PARAM_KEYS.forEach(function (key) {
				var value = (page.params || {})[key];
				if (value && !out[key]) {
					out[key] = value;
				}
			});
		}
		if (startingTouch) {
			/* Still fill-blanks: a replaced touch has already been cleared, and a
			   cookie written before touch_at existed keeps what it had. */
			if (!out.landing_page) {
				out.landing_page = String(page.path || "/").slice(0, 255);
			}
			var ref = externalReferrer(page.referrer);
			if (ref && !out.first_referrer) {
				out.first_referrer = ref;
			}
			out.touch_at = nowIso;
		}
		if (!out.first_seen) {
			out.first_seen = nowIso;
		}
		out.last_seen = nowIso;
		return out;
	}

	function serialize(values) {
		return encodeURIComponent(JSON.stringify(values));
	}

	/* Kept whole for as long as anything else can give way: the keys ERPNext joins
	   spend on, and the two that name the campaign. */
	var PROTECTED_KEYS = ["utm_id", "gclid", "gbraid", "wbraid", "msclkid", "utm_source", "utm_campaign"];

	/**
	 * Make the cookie fit. Pure, and guaranteed to terminate: shed whole keys in
	 * SHED_ORDER, then halve the longest unprotected value, then -- only if that is
	 * still not enough -- the longest protected one. Encoding is what makes this
	 * necessary: a quote costs six characters once URI-encoded, so a hostile link
	 * can outgrow any fixed list of keys to drop.
	 */
	function fitCookie(values) {
		var out = {};
		Object.keys(values).forEach(function (key) {
			out[key] = values[key];
		});
		function fits() {
			return serialize(out).length <= MAX_COOKIE_CHARS;
		}
		for (var i = 0; i < SHED_ORDER.length && !fits(); i++) {
			delete out[SHED_ORDER[i]];
		}
		[false, true].forEach(function (touchProtected) {
			while (!fits()) {
				var longest = null;
				Object.keys(out).forEach(function (key) {
					var isProtected = PROTECTED_KEYS.indexOf(key) !== -1;
					if (typeof out[key] !== "string" || !out[key] || isProtected !== touchProtected) {
						return;
					}
					if (longest === null || out[key].length > out[longest].length) {
						longest = key;
					}
				});
				if (longest === null) {
					return;
				}
				out[longest] = out[longest].slice(0, Math.floor(out[longest].length / 2));
			}
		});
		return out;
	}

	function cookieDomainFor(hostname) {
		hostname = (hostname || "").toLowerCase();
		if (hostname === COOKIE_DOMAIN || hostname.slice(-(COOKIE_DOMAIN.length + 1)) === "." + COOKIE_DOMAIN) {
			return "." + COOKIE_DOMAIN;
		}
		return "";
	}

	function readCookie(doc) {
		var parts = ("; " + (doc.cookie || "")).split("; " + COOKIE + "=");
		if (parts.length !== 2) {
			return null;
		}
		try {
			var parsed = JSON.parse(decodeURIComponent(parts.pop().split(";").shift()));
			return parsed && typeof parsed === "object" ? parsed : null;
		} catch (e) {
			/* Malformed reads as absent: a parse error must never stop a form. */
			return null;
		}
	}

	function writeCookie(doc, location, values, nowMs) {
		var domain = cookieDomainFor(location.hostname);
		doc.cookie =
			COOKIE +
			"=" +
			serialize(fitCookie(values)) +
			"; expires=" +
			new Date(nowMs + COOKIE_DAYS * 864e5).toUTCString() +
			"; path=/" +
			(domain ? "; domain=" + domain : "") +
			"; SameSite=Lax" +
			(location.protocol === "https:" ? "; Secure" : "");
	}

	/**
	 * Copy stored values into hidden inputs that already exist. A field injected
	 * here would reach WordPress and never ERPNext: the webhook serialises Fluent
	 * Forms' own submission data, so anything not in the form's schema is dropped.
	 */
	function fill(doc, values) {
		FIELD_KEYS.forEach(function (key) {
			var inputs = doc.querySelectorAll('input[name="' + key + '"]');
			for (var i = 0; i < inputs.length; i++) {
				/* Do not clobber a value a human or another script already set. */
				if (!inputs[i].value) {
					inputs[i].value = values[key] || "";
				}
			}
		});
	}

	function boot(env) {
		var doc = env.document;
		var location = env.location;
		var nowMs = env.now();
		var values = merge(
			readCookie(doc) || {},
			{ params: paramsFrom(location.search), path: location.pathname + location.search, referrer: doc.referrer },
			nowMs
		);
		writeCookie(doc, location, values, nowMs);

		function run() {
			fill(doc, values);
			/* Fluent Forms renders some layouts after DOMContentLoaded, and
			   conversational forms render each step on demand, so one pass at load
			   misses them. Re-fill on mutation, and once more at submit -- the only
			   moment that actually has to be right. */
			if (env.MutationObserver) {
				new env.MutationObserver(function () {
					fill(doc, values);
				}).observe(doc.documentElement, { childList: true, subtree: true });
			}
			doc.addEventListener(
				"submit",
				function () {
					fill(doc, values);
				},
				true
			);
		}

		if (doc.readyState === "loading") {
			doc.addEventListener("DOMContentLoaded", run);
		} else {
			run();
		}
		return values;
	}

	return {
		COOKIE: COOKIE,
		PARAM_KEYS: PARAM_KEYS,
		FIELD_KEYS: FIELD_KEYS,
		SESSION_MINUTES: SESSION_MINUTES,
		MAX_COOKIE_CHARS: MAX_COOKIE_CHARS,
		paramsFrom: paramsFrom,
		externalReferrer: externalReferrer,
		merge: merge,
		fitCookie: fitCookie,
		serialize: serialize,
		cookieDomainFor: cookieDomainFor,
		readCookie: readCookie,
		fill: fill,
		boot: boot,
	};
});
