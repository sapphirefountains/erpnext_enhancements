/* Public visit sign-off page (/visit-sign-off, v1.614.0).
 *
 * Boot data arrives as window.VS_BOOT, an allowlist the controller (www/visit_sign_off.py)
 * builds: the state, the link's ref and the session's CSRF token. The token is the only
 * authority the server takes (sapphire_maintenance/sign_off.py::sign_visit).
 *
 * One screen. Signing swaps the form for a thank-you in place and the page adds no history
 * entries anywhere, so the browser's Back and Forward keep doing what they did before it
 * (scripts/test_web_flow_history.js visit-sign-off).
 *
 * The pad is the contract signing page's (public/js/contract_sign/contract_sign.js): the
 * backing store is scaled by devicePixelRatio, resizing never erases what was drawn, and the
 * PNG sent is trimmed to the ink.
 *
 * The CSRF header is sent only when the server handed us a token. A Guest session has none,
 * but a signed-in staff member opening the link does, and omitting it there is a 400.
 */
(function () {
	"use strict";

	var BOOT = window.VS_BOOT || {};
	var pad = null;
	var ctx = null;
	var strokes = 0;
	var sending = false;

	function $(id) {
		return document.getElementById(id);
	}

	function post(method, body) {
		var headers = { "Content-Type": "application/json" };
		if (BOOT.csrf_token) headers["X-Frappe-CSRF-Token"] = BOOT.csrf_token;
		return fetch("/api/method/" + method, {
			method: "POST",
			headers: headers,
			body: JSON.stringify(body || {}),
		})
			.then(function (r) {
				return r.json().catch(function () {
					return {};
				});
			})
			.then(function (payload) {
				return payload.message || {};
			});
	}

	function showError(text) {
		var box = $("vs-error");
		if (!box) return;
		box.textContent = text || "";
		box.hidden = !text;
	}

	/* ------------------------------------------------------------ signature */

	function sizePad() {
		var ratio = window.devicePixelRatio || 1;
		var rect = pad.getBoundingClientRect();
		if (!rect.width) return;
		var w = Math.round(rect.width * ratio);
		var h = Math.round(rect.height * ratio);
		// Assigning width/height clears the bitmap even when unchanged, so a URL bar collapsing
		// on scroll would erase a signature the client can still see.
		if (ctx && pad.width === w && pad.height === h) return;

		var previous = null;
		if (ctx && strokes && pad.width && pad.height) {
			previous = document.createElement("canvas");
			previous.width = pad.width;
			previous.height = pad.height;
			previous.getContext("2d").drawImage(pad, 0, 0);
		}
		pad.width = w;
		pad.height = h;
		ctx = pad.getContext("2d");
		ctx.scale(ratio, ratio);
		ctx.lineWidth = 2;
		ctx.lineCap = "round";
		ctx.lineJoin = "round";
		ctx.strokeStyle = "#111";
		if (previous) ctx.drawImage(previous, 0, 0, rect.width, rect.height);
	}

	function setupPad() {
		pad = $("vs-pad");
		if (!pad) return;
		sizePad();
		window.addEventListener("resize", sizePad);

		var drawing = false;
		function pos(event) {
			var rect = pad.getBoundingClientRect();
			return { x: event.clientX - rect.left, y: event.clientY - rect.top };
		}
		pad.addEventListener("pointerdown", function (event) {
			if (!ctx) sizePad();
			if (!ctx) return;
			drawing = true;
			strokes += 1;
			if (pad.setPointerCapture) pad.setPointerCapture(event.pointerId);
			var p = pos(event);
			ctx.beginPath();
			ctx.moveTo(p.x, p.y);
			event.preventDefault();
		});
		pad.addEventListener("pointermove", function (event) {
			if (!drawing) return;
			var p = pos(event);
			ctx.lineTo(p.x, p.y);
			ctx.stroke();
			event.preventDefault();
		});
		["pointerup", "pointercancel", "pointerleave"].forEach(function (name) {
			pad.addEventListener(name, function () {
				drawing = false;
			});
		});
		var clear = $("vs-clear");
		if (clear) {
			clear.addEventListener("click", function () {
				if (ctx) ctx.clearRect(0, 0, pad.width, pad.height);
				strokes = 0;
			});
		}
	}

	/* Trimmed to the ink's bounding box, so the report doesn't print a mostly empty image. */
	function signatureImage() {
		if (!pad || !strokes || !ctx) return "";
		var w = pad.width;
		var h = pad.height;
		var data = ctx.getImageData(0, 0, w, h).data;
		var minX = w;
		var minY = h;
		var maxX = -1;
		var maxY = -1;
		for (var y = 0; y < h; y++) {
			for (var x = 0; x < w; x++) {
				if (data[(y * w + x) * 4 + 3] > 8) {
					if (x < minX) minX = x;
					if (x > maxX) maxX = x;
					if (y < minY) minY = y;
					if (y > maxY) maxY = y;
				}
			}
		}
		if (maxX < 0) return "";
		var margin = 6;
		minX = Math.max(0, minX - margin);
		minY = Math.max(0, minY - margin);
		maxX = Math.min(w - 1, maxX + margin);
		maxY = Math.min(h - 1, maxY + margin);
		var out = document.createElement("canvas");
		out.width = maxX - minX + 1;
		out.height = maxY - minY + 1;
		out.getContext("2d").drawImage(pad, minX, minY, out.width, out.height, 0, 0, out.width, out.height);
		return out.toDataURL("image/png");
	}

	/* ----------------------------------------------------------------- sign */

	function done() {
		$("vs-form").hidden = true;
		$("vs-done").hidden = false;
		if (window.scrollTo) window.scrollTo(0, 0);
	}

	function submit(event) {
		if (event && event.preventDefault) event.preventDefault();
		if (sending) return;
		showError("");
		var name = (($("vs-name") || {}).value || "").trim();
		if (!name) return showError("Please type your full name.");
		var image = signatureImage();
		if (!image) return showError("Please sign in the box.");
		if (!($("vs-confirm") || {}).checked) return showError("Please tick the box to confirm the service was performed.");

		sending = true;
		var button = $("vs-submit");
		if (button) button.disabled = true;
		post("erpnext_enhancements.sapphire_maintenance.sign_off.sign_visit", {
			ref: BOOT.ref,
			signed_name: name,
			signature_image: image,
			confirmed: 1,
		})
			.then(function (answer) {
				if (answer.ok) return done();
				showError(answer.error || "That didn't go through. Please try again.");
			})
			.catch(function () {
				// No answer: it may have gone through. Signing again is safe (a second Sign on a
				// signed link answers "already"), so the button comes back.
				showError("We couldn't reach the server. Check your connection and press Sign again.");
			})
			.then(function () {
				sending = false;
				if (button) button.disabled = false;
			});
	}

	function start() {
		if (BOOT.state !== "signable") return;
		setupPad();
		var form = $("vs-form");
		if (form) form.addEventListener("submit", submit);
	}

	if (document.readyState === "loading" || !document.readyState) {
		document.addEventListener("DOMContentLoaded", start);
	} else {
		start();
	}
})();
