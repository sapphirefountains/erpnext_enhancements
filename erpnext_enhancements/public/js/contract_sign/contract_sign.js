/* Public contract signing page (/contract-sign).
 *
 * Boot data arrives as window.CS_BOOT — an explicit server-side allowlist, never
 * a config endpoint. The recipient address is only ever present here masked.
 *
 * The CSRF header is sent ONLY when the server handed us a token. A Guest
 * session has none (validation short-circuits server-side), but a logged-in
 * staff member previewing this page does, and omitting it there is a hard 400.
 */
(function () {
	"use strict";

	var BOOT = window.CS_BOOT || {};
	var sid = null;
	var mode = "Typed";
	var strokes = 0;
	var pad = null;
	var ctx = null;
	var turnstileToken = null;

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
		var box = $("cs-error");
		if (!box) return;
		box.textContent = text;
		box.hidden = !text;
	}

	/* ---------------------------------------------------------------- mode */

	function setMode(next) {
		mode = next;
		var typed = next === "Typed";
		$("cs-mode-typed").classList.toggle("is-active", typed);
		$("cs-mode-drawn").classList.toggle("is-active", !typed);
		$("cs-mode-typed").setAttribute("aria-selected", String(typed));
		$("cs-mode-drawn").setAttribute("aria-selected", String(!typed));
		$("cs-typed-pane").hidden = !typed;
		$("cs-drawn-pane").hidden = typed;
		if (!typed && pad) sizePad();
	}

	function signedName() {
		var el = mode === "Typed" ? $("cs-signed-name") : $("cs-signed-name-drawn");
		return (el && el.value ? el.value : "").trim();
	}

	/* ------------------------------------------------------------ signature */

	function sizePad() {
		// Scale the backing store by devicePixelRatio: without it the exported
		// PNG is a blurry mess at print resolution.
		var ratio = window.devicePixelRatio || 1;
		var rect = pad.getBoundingClientRect();
		if (!rect.width) return;
		var w = Math.round(rect.width * ratio);
		var h = Math.round(rect.height * ratio);

		// Assigning width/height resets the bitmap even when the value is
		// unchanged. Without this guard every resize event — an Android URL bar
		// collapsing on scroll, a rotation, a desktop zoom — and every
		// Draw/Type/Draw toggle silently erased a signature the customer had
		// already drawn, and they were told to draw one they could still see.
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
		if (previous) {
			// Redraw in CSS pixels — the context is already scaled by ratio.
			ctx.drawImage(previous, 0, 0, rect.width, rect.height);
		}
	}

	function setupPad() {
		pad = $("cs-pad");
		if (!pad) return;
		sizePad();
		window.addEventListener("resize", sizePad);

		var drawing = false;
		function pos(event) {
			var rect = pad.getBoundingClientRect();
			return { x: event.clientX - rect.left, y: event.clientY - rect.top };
		}
		pad.addEventListener("pointerdown", function (event) {
			drawing = true;
			strokes += 1;
			pad.setPointerCapture(event.pointerId);
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
		$("cs-clear").addEventListener("click", function () {
			ctx.clearRect(0, 0, pad.width, pad.height);
			strokes = 0;
		});
	}

	/* Trim to the ink bounding box so the PDF doesn't get a mostly-empty image. */
	function trimmedSignature() {
		if (!pad || !strokes) return "";
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
		if (maxX < 0) return ""; // nothing actually drawn
		var pad2 = 6;
		minX = Math.max(0, minX - pad2);
		minY = Math.max(0, minY - pad2);
		maxX = Math.min(w - 1, maxX + pad2);
		maxY = Math.min(h - 1, maxY + pad2);
		var out = document.createElement("canvas");
		out.width = maxX - minX + 1;
		out.height = maxY - minY + 1;
		out.getContext("2d").drawImage(pad, minX, minY, out.width, out.height, 0, 0, out.width, out.height);
		return out.toDataURL("image/png");
	}

	/* ------------------------------------------------------------ turnstile */

	var turnstileRendered = false;

	function startTurnstile() {
		var host = $("cs-turnstile");
		// Guarded: the immediate call and the poll below are not mutually
		// exclusive on a warm cache, and Cloudflare refuses a second render.
		if (turnstileRendered || !host || !BOOT.turnstile_sitekey || !window.turnstile) return;
		turnstileRendered = true;
		window.turnstile.render(host, {
			sitekey: BOOT.turnstile_sitekey,
			action: BOOT.turnstile_action || "contract-sign",
			callback: function (token) {
				turnstileToken = token;
				begin();
			},
			"expired-callback": function () {
				turnstileToken = null;
				window.turnstile.reset();
			},
			"error-callback": function () {
				// The widget itself failed. Open the session flagged, so the server
				// records "we could not check" on the evidence rather than either
				// blocking a real customer or silently laundering it as a pass.
				widgetUnavailable();
			},
		});
	}

	function widgetUnavailable() {
		if (sid) return;
		turnstileToken = null;
		begin(true);
	}

	function begin(unavailable) {
		return post("erpnext_enhancements.project_enhancements.esign.portal.begin_signing", {
			ref: BOOT.ref,
			turnstile_token: turnstileToken,
			widget_unavailable: unavailable ? 1 : 0,
			esign_sid: sid,
		}).then(function (result) {
			if (result.esign_sid) sid = result.esign_sid;
			return result;
		});
	}

	/* ----------------------------------------------------------------- flow */

	function submit(event) {
		event.preventDefault();
		showError("");
		var button = $("cs-submit");
		button.disabled = true;

		var name = signedName();
		if (!name) {
			showError("Enter your full name as your signature.");
			button.disabled = false;
			return;
		}
		// Checked here as well as server-side: a blank address would otherwise
		// spend one of the customer's limited confirmation attempts.
		if (!requireEmail()) {
			button.disabled = false;
			return;
		}
		var image = mode === "Drawn" ? trimmedSignature() : "";
		if (mode === "Drawn" && !image) {
			showError("Draw your signature in the box, or switch to typing it.");
			button.disabled = false;
			return;
		}

		var ready = sid ? Promise.resolve({}) : begin();
		ready
			.then(function () {
				return post("erpnext_enhancements.project_enhancements.esign.portal.sign_contract", {
					esign_sid: sid,
					confirm_email: ($("cs-email").value || "").trim(),
					signature_mode: mode,
					signed_name: name,
					signature_image: image,
					signed_title: ($("cs-title").value || "").trim(),
					agree: $("cs-agree").checked ? 1 : 0,
				});
			})
			.then(function (result) {
				if (result && result.ok) {
					$("cs-form").hidden = true;
					$("cs-agreement").hidden = true;
					$("cs-done").hidden = false;
					// Offered only now, after the contract is executed and committed.
					if (result.autopay && result.autopay.offer) {
						$("cs-autopay").hidden = false;
					}
					window.scrollTo(0, 0);
					return;
				}
				showError((result && result.error) || "Something went wrong. Please try again.");
				if (result && result.retry_captcha && window.turnstile) {
					turnstileToken = null;
					window.turnstile.reset();
				}
				button.disabled = false;
			})
			.catch(function () {
				showError("Something went wrong. Please try again.");
				button.disabled = false;
			});
	}

	function requireEmail() {
		var field = $("cs-email");
		if (field && (field.value || "").trim()) return true;
		showError("Type the email address this agreement was sent to.");
		if (field) field.focus();
		return false;
	}

	function decline() {
		// The decline dialog never asked for the address, so without this a
		// decline silently failed AND burned a confirmation attempt.
		if (!requireEmail()) return;
		if (!window.confirm("Let us know you don't want to sign this agreement?")) return;
		var reason = window.prompt("Anything you'd like us to know? (optional)") || "";
		var ready = sid ? Promise.resolve({}) : begin();
		ready
			.then(function () {
				return post("erpnext_enhancements.project_enhancements.esign.portal.decline_contract", {
					esign_sid: sid,
					confirm_email: ($("cs-email").value || "").trim(),
					reason: reason,
				});
			})
			.then(function (result) {
				if (result && result.ok) {
					window.location.reload();
					return;
				}
				showError((result && result.error) || "Please confirm your email address first.");
			});
	}

	function startAutopay() {
		var button = $("cs-autopay-start");
		button.disabled = true;
		post("erpnext_enhancements.project_enhancements.esign.portal.start_autopay", {
			esign_sid: sid,
		})
			.then(function (result) {
				if (result && result.ok && result.checkout_url) {
					window.location.href = result.checkout_url;
					return;
				}
				// Never an error on a page that has just executed a contract.
				$("cs-autopay-note").hidden = false;
				button.disabled = true;
			})
			.catch(function () {
				$("cs-autopay-note").hidden = false;
			});
	}

	function skipAutopay() {
		$("cs-autopay").hidden = true;
		post("erpnext_enhancements.project_enhancements.esign.portal.decline_autopay", {
			esign_sid: sid,
		});
	}

	function freshLink() {
		var button = $("cs-fresh");
		if (button) button.disabled = true;
		post("erpnext_enhancements.project_enhancements.esign.portal.request_fresh_link", {
			ref: BOOT.ref,
		}).then(function () {
			var note = $("cs-fresh-done");
			if (note) note.hidden = false;
		});
	}

	/* ----------------------------------------------------------------- init */

	document.addEventListener("DOMContentLoaded", function () {
		var fresh = $("cs-fresh");
		if (fresh) fresh.addEventListener("click", freshLink);

		var autopayStart = $("cs-autopay-start");
		if (autopayStart) autopayStart.addEventListener("click", startAutopay);
		var autopaySkip = $("cs-autopay-skip");
		if (autopaySkip) autopaySkip.addEventListener("click", skipAutopay);

		var form = $("cs-form");
		if (!form) return; // a notice state — nothing else to wire up

		$("cs-mode-typed").addEventListener("click", function () {
			setMode("Typed");
		});
		$("cs-mode-drawn").addEventListener("click", function () {
			setMode("Drawn");
		});
		$("cs-signed-name").addEventListener("input", function (event) {
			$("cs-preview").textContent = event.target.value;
		});
		setupPad();
		form.addEventListener("submit", submit);
		$("cs-decline").addEventListener("click", decline);

		// Reading to the end emphasises the primary action but never gates it —
		// a hard scroll gate is theatre and breaks screen readers.
		var agreement = $("cs-agreement");
		if (agreement && "IntersectionObserver" in window) {
			var sentinel = document.createElement("div");
			agreement.appendChild(sentinel);
			new IntersectionObserver(function (entries) {
				if (entries[0].isIntersecting) $("cs-submit").classList.add("is-ready");
			}).observe(sentinel);
		}

		if (BOOT.turnstile_sitekey) {
			if (window.turnstile) startTurnstile();
			var poll = setInterval(function () {
				if (window.turnstile) {
					clearInterval(poll);
					startTurnstile();
				}
			}, 250);
			setTimeout(function () {
				clearInterval(poll);
				// challenges.cloudflare.com never loaded (blocked, offline, outage).
				// Open the session flagged rather than leaving the customer to hit
				// a misleading "check your email address" when they press Sign.
				if (!window.turnstile) widgetUnavailable();
			}, 10000);
		} else {
			// No keys configured: open the session anyway so signing still works.
			begin();
		}
	});
})();
