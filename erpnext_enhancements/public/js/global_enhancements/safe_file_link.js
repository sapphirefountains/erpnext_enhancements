/*
 * Attachment links drawn from record values: the file name is shown as text and the address is
 * used as a link only when it points at a File on this site or at an http(s) page.
 *
 * The form sidebar's attachment list (erpnext_enhancements.js) and the comment box's upload
 * preview and attached-files list (global_comments.js) both build HTML strings from a File's
 * `file_name` and `file_url`. This module is the one place that decides how:
 *
 * - `escape` is `frappe.utils.escape_html` in the browser (a stub in scripts/test_safe_file_link.mjs),
 *   so a name with `&`, quotes or angle brackets shows as typed.
 * - `safeFileHref(url)` answers the address unchanged when it starts with `/files/`,
 *   `/private/files/`, `http://` or `https://`, and "" for anything else. A row with no usable
 *   address is drawn as plain text rather than as a link.
 *
 * Pure functions; imported by the global bundle. Tested by scripts/test_safe_file_link.mjs.
 */

const FILE_PATH = /^\/(?:private\/)?files\//;
const WEB_URL = /^https?:\/\//i;

export function safeFileHref(url) {
	const value = String(url == null ? "" : url).trim();
	return FILE_PATH.test(value) || WEB_URL.test(value) ? value : "";
}

/** The inner HTML of a link-or-text for one file: `<a href=...>name</a>` or `<span>name</span>`. */
export function fileLinkHtml(file_name, file_url, escape, attrs) {
	const name = escape(file_name == null ? "" : String(file_name));
	const href = safeFileHref(file_url);
	const extra = attrs ? " " + attrs : "";
	if (!href) return `<span${extra}>${name}</span>`;
	return `<a href="${escape(href)}" target="_blank" rel="noopener"${extra}>${name}</a>`;
}

/** One row of the form sidebar's attachment list. Every record value goes through `escape`. */
export function sidebarAttachmentRowHtml(file_name, file_url, size, icon, escape) {
	const name = escape(file_name == null ? "" : String(file_name));
	const href = safeFileHref(file_url);
	const tag = href ? "a" : "span";
	const link_attrs = href ? ` href="${escape(href)}" target="_blank" rel="noopener"` : "";
	return `
		<div class="sidebar-item" style="display: flex; align-items: center; justify-content: space-between; padding: 4px 0; font-size: var(--text-xs);">
			<${tag}${link_attrs} class="text-muted ellipsis" style="display: flex; align-items: center; max-width: 75%; text-decoration: none;" title="${name}">
				<span class="m-r-1">${icon}</span>
				<span class="ellipsis">${name}</span>
			</${tag}>
			<span class="text-muted" style="font-size: 10px; flex-shrink: 0;">${escape(size == null ? "" : String(size))}</span>
		</div>
	`;
}
