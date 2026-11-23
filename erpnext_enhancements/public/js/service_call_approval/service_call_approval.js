/* Public Service Call approval page (/service-call-approval, v1.778.0).
 *
 * Boot data arrives as window.SA_BOOT, an allowlist the controller (www/service_call_approval.py)
 * builds: the state, the link's ref and the session's CSRF token. The token is the only authority
 * the server takes (sapphire_maintenance/service_call_approval.py).
 *
 * One screen. Approving or declining swaps the form for a thank-you in place and the page adds no
 * history entries anywhere, so the browser's Back and Forward keep doing what they did before it.
 *
 * The CSRF header is sent only when the server handed us a token: a Guest session has none, but a
 * signed-in staff member opening the link does, and omitting it there is a 400.
 */
(function () {
	"use strict";

	var BOOT = window.SA_BOOT || {};
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
		var box = $("sa-error");
		if (!box) return;
		box.textContent = text || "";
		box.hidden = !text;
	}

	function finish(id) {
		$("sa-form").hidden = true;
		$(id).hidden = false;
		if (window.scrollTo) window.scrollTo(0, 0);
	}

	function busy(on) {
		sending = on;
		var approve = $("sa-submit");
		var decline = $("sa-decline-btn");
		if (approve) approve.disabled = on;
		if (decline) decline.disabled = on;
	}

	function approve(event) {
		if (event && event.preventDefault) event.preventDefault();
		if (sending) return;
		showError("");
		var name = (($("sa-name") || {}).value || "").trim();
		if (!name) return showError("Please type your full name.");
		busy(true);
		post("erpnext_enhancements.sapphire_maintenance.service_call_approval.approve_service_call", {
			ref: BOOT.ref,
			approved_name: name,
		})
			.then(function (answer) {
				if (answer.ok) return finish("sa-done");
				showError(answer.error || "That didn't go through. Please try again.");
			})
			.catch(function () {
				// No answer: it may have gone through. Approving again is safe (a second Approve on
				// an approved link answers "already"), so the button comes back.
				showError("We couldn't reach the server. Check your connection and press Approve again.");
			})
			.then(function () {
				busy(false);
			});
	}

	function decline() {
		if (sending) return;
		showError("");
		var reason = (($("sa-reason") || {}).value || "").trim();
		busy(true);
		post("erpnext_enhancements.sapphire_maintenance.service_call_approval.decline_service_call", {
			ref: BOOT.ref,
			reason: reason,
		})
			.then(function (answer) {
				if (answer.ok) return finish("sa-declined");
				showError(answer.error || "That didn't go through. Please try again.");
			})
			.catch(function () {
				showError("We couldn't reach the server. Check your connection and try again.");
			})
			.then(function () {
				busy(false);
			});
	}

	function start() {
		if (BOOT.state !== "open") return;
		var form = $("sa-form");
		if (form) form.addEventListener("submit", approve);
		var button = $("sa-decline-btn");
		if (button) button.addEventListener("click", decline);
	}

	if (document.readyState === "loading" || !document.readyState) {
		document.addEventListener("DOMContentLoaded", start);
	} else {
		start();
	}
})();
