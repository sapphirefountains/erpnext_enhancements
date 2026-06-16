/* ==========================================================================
   LOGIN PAGE ENHANCEMENTS (JS)

   Appends a small legal footer (Privacy Policy + End-User License Agreement
   links) beneath the login card. Loaded via hooks.py `web_include_js`, so it
   runs on website pages; it does nothing unless the current page is /login.
   Styling lives in login_enhancements.bundle.css (.ee-login-legal-footer).
   ========================================================================== */
(function () {
	function injectLegalFooter() {
		// Only act on the login page (also covers the forgot/signup views, which
		// Frappe toggles within the same /login page).
		if (window.location.pathname.replace(/\/+$/, "") !== "/login") {
			return;
		}
		// Idempotent: never insert twice (handles re-entrant calls below).
		if (document.querySelector(".ee-login-legal-footer")) {
			return;
		}

		const footer = document.createElement("div");
		footer.className = "ee-login-legal-footer";

		const privacy = document.createElement("a");
		privacy.href = "/privacy-policy";
		privacy.textContent = "Privacy Policy";

		const separator = document.createElement("span");
		separator.className = "ee-login-legal-sep";
		separator.setAttribute("aria-hidden", "true");
		separator.textContent = "·"; // middot

		const eula = document.createElement("a");
		eula.href = "/eula";
		eula.textContent = "End-User License Agreement";

		footer.appendChild(privacy);
		footer.appendChild(separator);
		footer.appendChild(eula);

		// Prefer placing the footer directly beneath the login card; fall back to
		// the login wrapper or the body across Frappe versions.
		const card = document.querySelector(".page-card");
		if (card && card.parentNode) {
			card.insertAdjacentElement("afterend", footer);
			return;
		}
		const host =
			document.querySelector(".for-login") ||
			document.querySelector(".login-content") ||
			document.body;
		host.appendChild(footer);
	}

	function run() {
		injectLegalFooter();
		// Safety net in case the login card mounts a tick after DOM ready; the
		// idempotency guard makes the second call a no-op once inserted.
		window.setTimeout(injectLegalFooter, 300);
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", run);
	} else {
		run();
	}
})();
