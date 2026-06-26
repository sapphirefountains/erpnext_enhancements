/**
 * Fix Quill @-mention rendering in rich-text editors (Frappe v16.24.x).
 *
 * Targets: every Frappe `ControlTextEditor` (any rich-text field created with
 *   `enable_mentions`), including the custom Comments App "New Note" / "Reply"
 *   dialogs (see comments.js) and Frappe's own timeline comment box.
 * Loaded via: hooks.py `app_include_js` -> erpnext_enhancements.bundle.js (global).
 *
 * Why this exists
 * ---------------
 * Frappe core's mention pipeline became internally inconsistent around v16.24.x.
 * The mention *module* (`quill-mention/quill.mention.js` `getItemData()`) still
 * returns the display value as an HTML string when the mention has a link, e.g.
 *
 *     <a class="mention-link" href="/app/user/x" target="_blank">Full Name
 *
 * but the embed *blot* (`quill-mention/blots/mention.js` `MentionBlot.create`)
 * was hardened to insert that value with `valueSpan.textContent = data.value`
 * (an XSS fix). textContent escapes the markup, so instead of a rendered pill
 * the user sees the raw anchor tag as literal text:
 *
 *     @<a class="mention-link" href="...">Full Name
 *
 * This affects every linked mention on the affected build. (On older builds,
 * e.g. v16.22, `create` used `innerHTML +=` and rendered correctly — which is
 * why it can look fine on an older dev bench but break in production.)
 *
 * The fix
 * -------
 * Override `MentionBlot.create` to render a proper, XSS-safe mention: build a
 * real <a> from the trusted `data.link` (the server-generated User/User Group
 * URL) and set the visible name via `textContent`, so any HTML in a person's
 * name stays inert. Every `data-*` attribute the rest of Frappe relies on is
 * preserved verbatim and byte-identical to core — notably `data-id`, which
 * `frappe.desk.notifications.extract_mentions` reads to notify mentioned users,
 * so tagging keeps working and the saved markup round-trips unchanged.
 *
 * Self-scoping
 * ------------
 * Applied once, the first time any text editor mounts, by reaching the shared
 * blot class through that editor's own Quill instance
 * (`quill.scroll.query("mention")`) -- this reuses Frappe's Quill rather than
 * bundling a second copy. It only engages when the running blot actually
 * escapes a linked value (probed at runtime), so on a Frappe build that has
 * fixed the upstream bug this is a no-op.
 */
frappe.provide("erpnext_enhancements");

(function () {
	// getItemData() wraps the display name in an (intentionally unclosed) anchor
	// when the mention has a link. Recover the bare name so we can re-emit a safe
	// anchor; a plain value (User Groups, or a fixed Frappe) passes through as-is.
	function strip_anchor(value) {
		if (typeof value !== "string") return value == null ? "" : String(value);
		return value.replace(/^\s*<a\b[^>]*>/i, "").replace(/<\/a>\s*$/i, "");
	}

	function build_patched_create(MentionBlot) {
		return function (data) {
			const node = document.createElement(MentionBlot.tagName || "SPAN");
			if (MentionBlot.className) node.classList.add(MentionBlot.className);

			const denotation = document.createElement("span");
			denotation.className = "ql-mention-denotation-char";
			denotation.textContent = data.denotationChar || "@";
			node.appendChild(denotation);

			const name = strip_anchor(data.value);
			if (data.link) {
				const link = document.createElement("a");
				link.className = "mention-link";
				link.setAttribute("href", data.link);
				link.setAttribute("target", "_blank");
				// textContent (not innerHTML): any markup in a user's name stays inert.
				link.textContent = name;
				node.appendChild(link);
			} else {
				const span = document.createElement("span");
				span.textContent = name;
				node.appendChild(span);
			}

			if (String(data.isGroup) === "true" && frappe.utils && frappe.utils.icon) {
				const icon = document.createElement("span");
				icon.innerHTML = frappe.utils.icon("users");
				node.appendChild(icon);
			}

			// Preserve every data-* attribute exactly as core does: extract_mentions
			// reads data-id / data-is-group, and MentionBlot.value() round-trips
			// data-value when saved content is reloaded into an editor.
			node.dataset.id = data.id;
			node.dataset.value = data.value;
			node.dataset.denotationChar = data.denotationChar;
			node.dataset.isGroup = data.isGroup;
			if (data.link) node.dataset.link = data.link;
			return node;
		};
	}

	// True when the live blot escapes a linked value (the v16.24.x bug). Lets the
	// patch stay a no-op on builds that already render mentions correctly.
	function blot_is_affected(MentionBlot) {
		try {
			const probe = MentionBlot.create({
				id: "probe",
				value: '<a class="mention-link" href="/app/user/probe" target="_blank">Probe',
				link: "/app/user/probe",
				denotationChar: "@",
				isGroup: "false",
			});
			return !(probe && probe.querySelector && probe.querySelector("a.mention-link"));
		} catch (e) {
			return false;
		}
	}

	function patch_mention_blot(quill) {
		if (erpnext_enhancements._mention_blot_patched) return;
		const MentionBlot =
			quill && quill.scroll && quill.scroll.query && quill.scroll.query("mention");
		if (!MentionBlot || MentionBlot._ee_patched) return;
		// Mark handled regardless, so we don't re-probe on every editor mount.
		erpnext_enhancements._mention_blot_patched = true;
		if (!blot_is_affected(MentionBlot)) return;
		MentionBlot.create = build_patched_create(MentionBlot);
		MentionBlot._ee_patched = true;
	}

	const TextEditor =
		frappe.ui && frappe.ui.form && frappe.ui.form.ControlTextEditor;
	if (TextEditor && TextEditor.prototype && TextEditor.prototype.make_quill_editor) {
		const original_make_quill_editor = TextEditor.prototype.make_quill_editor;
		TextEditor.prototype.make_quill_editor = function () {
			original_make_quill_editor.apply(this, arguments);
			try {
				patch_mention_blot(this.quill);
			} catch (e) {
				// eslint-disable-next-line no-console
				console.error("[erpnext_enhancements] mention blot patch failed", e);
			}
		};
	}
})();
