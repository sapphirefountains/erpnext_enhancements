/**
 * Shared export/print helpers — `erpnext_enhancements.export_utils`.
 *
 * The bits every export surface needs and none of them should re-derive: a
 * download trigger, safe filenames, the site's letterhead resolved to an inline
 * data URI, and a branded print window.
 *
 * Consumers: `gantt_widget/gantt_export.js` (Gantt SVG/PNG/print) and
 * `project_enhancements/task_tree_manager.js` (Scope-tab task tree).
 *
 * WHY BROWSER PRINT AND NOT A SERVER-RENDERED PDF. Server-side PDF is
 * non-functional on this host — both backends fail, and it is an environment
 * problem this repo cannot fix (docs/pdf-generation.md: Debian's wkhtmltopdf
 * segfaults on a one-line HTML file, and the bench Chromium SIGTRAPs on
 * `--version`). `window.print()` needs neither, and the browser's own
 * print-to-PDF is currently the only working path to a PDF file. The same
 * reasoning is recorded in pick_routing_map.js and project_brief.js; this is
 * the third surface to reach it, hence the shared helper.
 */
frappe.provide("erpnext_enhancements.export_utils");

(function () {
	const NS = erpnext_enhancements.export_utils;
	if (NS.download_blob) {
		return; // already initialized (double bundle evaluation)
	}

	function escape_html(s) {
		return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => {
			return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
		});
	}

	function download_blob(blob, filename) {
		const url = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = url;
		a.download = filename;
		document.body.appendChild(a);
		a.click();
		document.body.removeChild(a);
		// Revoking synchronously cancels the download in Firefox.
		setTimeout(() => URL.revokeObjectURL(url), 4000);
	}

	/** Rebuild a base64 payload from the server into a downloaded file. */
	function download_payload(payload) {
		if (!payload || !payload.filecontent) {
			frappe.show_alert({ message: __("Nothing to export."), indicator: "orange" });
			return false;
		}
		const bytes = Uint8Array.from(atob(payload.filecontent), (c) => c.charCodeAt(0));
		download_blob(new Blob([bytes], { type: payload.content_type }), payload.filename);
		return true;
	}

	function safe_filename(s, fallback) {
		const cleaned = String(s || "")
			.replace(/[^\w\-. ]+/g, "")
			.trim()
			.replace(/\s+/g, "-")
			.slice(0, 80);
		return cleaned || fallback || "export";
	}

	function stamp() {
		const d = new Date();
		return `${d.getFullYear()}${String(d.getMonth() + 1).padStart(2, "0")}${String(
			d.getDate()
		).padStart(2, "0")}`;
	}

	/**
	 * The site's default letterhead image, inlined as a data URI.
	 *
	 * Inlining is not an optimisation, it is required in two places: an
	 * `<image href="/private/files/…">` inside a serialised SVG is not fetched
	 * when that SVG is rasterised through an `<img>` (no cookies, no same-origin
	 * document), so the logo would silently vanish from every PNG; and a
	 * print window opened with `window.open("")` is a fresh about:blank
	 * document whose relative URLs have nothing to resolve against.
	 *
	 * Resolved once per session. Any failure returns null and callers fall back
	 * to a text-only header — a missing logo must never fail an export.
	 */
	let _logo_promise = null;
	function brand_logo() {
		if (_logo_promise) {
			return _logo_promise;
		}
		_logo_promise = (async () => {
			try {
				const url = await letterhead_image_url();
				if (!url) {
					return null;
				}
				const res = await fetch(url, { credentials: "same-origin" });
				if (!res.ok) {
					return null;
				}
				const blob = await res.blob();
				const data = await new Promise((resolve, reject) => {
					const fr = new FileReader();
					fr.onload = () => resolve(fr.result);
					fr.onerror = reject;
					fr.readAsDataURL(blob);
				});
				const dims = await new Promise((resolve) => {
					const img = new Image();
					img.onload = () => resolve({ w: img.naturalWidth, h: img.naturalHeight });
					img.onerror = () => resolve({ w: 120, h: 34 });
					img.src = data;
				});
				return { data: data, width: Math.round(34 * (dims.w / (dims.h || 1))), height: 34 };
			} catch (e) {
				return null;
			}
		})();
		return _logo_promise;
	}

	async function letterhead_image_url() {
		const boot = frappe.boot || {};
		const from_boot =
			(boot.sysdefaults && boot.sysdefaults.letter_head_image) || boot.letter_head_image;
		if (from_boot) {
			return from_boot;
		}
		try {
			const r = await frappe.call({
				method: "frappe.client.get_value",
				args: { doctype: "Letter Head", filters: { is_default: 1 }, fieldname: "image" },
			});
			return (r && r.message && r.message.image) || null;
		} catch (e) {
			return null;
		}
	}

	function brand_company() {
		const boot = frappe.boot || {};
		return (
			(boot.sysdefaults && boot.sysdefaults.company) ||
			(frappe.defaults && frappe.defaults.get_default && frappe.defaults.get_default("company")) ||
			""
		);
	}

	/** `{ company, logo (data URI|null), logo_width }`; `{}` when branding is off. */
	async function build_brand(opts) {
		if (opts && opts.brand === false) {
			return {};
		}
		const logo = await brand_logo();
		return {
			company: brand_company(),
			logo: logo ? logo.data : null,
			logo_width: logo ? logo.width : 0,
		};
	}

	/** The branded header band, as HTML, for a print document. */
	function brand_header_html(meta) {
		meta = meta || {};
		const brand = meta.brand || {};
		const logo = brand.logo
			? `<img class="ee-print-logo" src="${brand.logo}" alt="">`
			: "";
		const right = [
			meta.range ? escape_html(meta.range) : "",
			brand.company ? escape_html(brand.company) : "",
		]
			.filter(Boolean)
			.join("<br>");
		return (
			`<header class="ee-print-head">` +
			`<div class="ee-print-head-left">${logo}` +
			`<div><div class="ee-print-title">${escape_html(meta.title || "")}</div>` +
			(meta.subtitle ? `<div class="ee-print-sub">${escape_html(meta.subtitle)}</div>` : "") +
			`</div></div>` +
			`<div class="ee-print-head-right">${right}</div>` +
			`</header>`
		);
	}

	const PRINT_CSS = `
		* { box-sizing: border-box; }
		html, body { margin: 0; padding: 0; background: #fff; color: #1a1a1a;
			font-family: Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
			font-size: 11px; }
		.ee-print-head { display: flex; justify-content: space-between; align-items: flex-start;
			gap: 16px; border-bottom: 2px solid #1a1a1a; padding-bottom: 8px; margin-bottom: 12px; }
		.ee-print-head-left { display: flex; align-items: center; gap: 12px; }
		.ee-print-logo { height: 34px; width: auto; }
		.ee-print-title { font-size: 15px; font-weight: 700; }
		.ee-print-sub { font-size: 11px; color: #6b7280; margin-top: 2px; }
		.ee-print-head-right { text-align: right; font-size: 11px; color: #6b7280; line-height: 1.5; }
		.ee-print-foot { margin-top: 10px; font-size: 9px; color: #9ca3af;
			display: flex; justify-content: space-between; }
		svg { display: block; width: 100%; height: auto; }
		table { width: 100%; border-collapse: collapse; }
		thead { display: table-header-group; }
		tr { page-break-inside: avoid; }
		th { text-align: left; font-size: 9px; text-transform: uppercase; letter-spacing: .04em;
			color: #6b7280; border-bottom: 1px solid #d1d5db; padding: 4px 6px; }
		td { padding: 4px 6px; border-bottom: 1px solid #ececf0; vertical-align: top; }
	`;

	/**
	 * Open a print window containing `body_html` under a branded header.
	 *
	 * `thead { display: table-header-group }` in PRINT_CSS is what repeats
	 * column headings on every printed page; `tr { page-break-inside: avoid }`
	 * stops a row being sliced in half at a page boundary. Both are print-only
	 * behaviours with no on-screen effect, which is why they are easy to lose.
	 */
	function print_document(body_html, meta) {
		meta = meta || {};
		const win = window.open("", "_blank", "width=1100,height=850");
		if (!win) {
			frappe.msgprint(__("Please allow pop-ups to print."));
			return null;
		}
		const generated = __("Generated {0} by {1}", [
			frappe.datetime.str_to_user(frappe.datetime.now_datetime()),
			frappe.session.user_fullname || frappe.session.user,
		]);
		win.document.write(
			`<!doctype html><html><head><meta charset="utf-8">` +
				`<title>${escape_html(meta.title || __("Print"))}</title>` +
				`<style>@page { size: ${meta.orientation || "portrait"}; margin: ${
					meta.margin || "12mm"
				}; }${PRINT_CSS}</style></head><body>` +
				brand_header_html(meta) +
				body_html +
				`<footer class="ee-print-foot"><span>${escape_html(generated)}</span>` +
				`<span>${escape_html(meta.footer || "")}</span></footer>` +
				`</body></html>`
		);
		win.document.close();
		// Printing before images decode yields a blank logo; onload fires after
		// the inline data-URI image is ready.
		win.onload = () => {
			win.focus();
			win.print();
		};
		return win;
	}

	NS.escape_html = escape_html;
	NS.download_blob = download_blob;
	NS.download_payload = download_payload;
	NS.safe_filename = safe_filename;
	NS.stamp = stamp;
	NS.brand_logo = brand_logo;
	NS.brand_company = brand_company;
	NS.build_brand = build_brand;
	NS.brand_header_html = brand_header_html;
	NS.print_document = print_document;
	NS.PRINT_CSS = PRINT_CSS;
})();
