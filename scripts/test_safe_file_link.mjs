#!/usr/bin/env node
/**
 * Escaping of record values in the browser's attachment and purchase-link HTML (v1.826.4),
 * executed against stubs rather than grepped.
 *
 *   1. global_enhancements/safe_file_link.js: `safeFileHref` answers an address unchanged when it
 *      starts with /files/, /private/files/, http:// or https://, and "" otherwise; the sidebar
 *      row and the comment box's link are built with every record value escaped, and an address
 *      that is not one of those is drawn as plain text, not as a link. Ordinary values read as
 *      they always did.
 *   2. procurement_links.js (a classic doctype_js script, run in a vm over a stub of frappe): the
 *      supplier button's name and address are escaped and linked only for http(s); the add and edit
 *      buttons carry item code, supplier and address in data- attributes with no inline handler,
 *      and one capture-phase click handler, bound once, passes them to edit_link unchanged.
 *
 * The module is an ES module in a package without "type": "module", so it is copied to a temp
 * folder beside one that says so, as scripts/test_new_item_prefill.mjs does.
 * Run: node scripts/test_safe_file_link.mjs
 */

import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath, pathToFileURL } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const JS = path.join(HERE, "..", "erpnext_enhancements", "public", "js");

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "ee-safe-file-link-"));
fs.writeFileSync(path.join(tmp, "package.json"), '{"type": "module"}');
fs.copyFileSync(path.join(JS, "global_enhancements", "safe_file_link.js"), path.join(tmp, "safe_file_link.js"));
const { safeFileHref, fileLinkHtml, sidebarAttachmentRowHtml } = await import(
	pathToFileURL(path.join(tmp, "safe_file_link.js")).href
);

// frappe.utils.escape_html: & < > " ' and the backtick.
const escape = (text) =>
	String(text).replace(/[&<>"'`]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;", "`": "&#96;" })[c]);

let passed = 0;
function check(label, fn) {
	try {
		fn();
		passed += 1;
	} catch (e) {
		console.error(`FAIL ${label}`);
		throw e;
	}
}

const HOSTILE = `"><img src=x onerror=alert(1)><b>&`;

// ---- 1. the module ---------------------------------------------------------------------------

check("site files and web pages are addresses; everything else is not", () => {
	for (const ok of [
		"/files/x.pdf",
		"/private/files/x.pdf",
		"/files/a b & c.png",
		"https://supplier.example/p/1?x=1&y=2",
		"http://supplier.example/p/1",
		"HTTPS://SUPPLIER.EXAMPLE/P",
		"  /files/x.pdf  ",
	]) {
		assert.equal(safeFileHref(ok), ok.trim(), ok);
	}
	for (const bad of [
		"javascript:alert(1)",
		"JaVaScRiPt:alert(1)",
		"data:text/html,<b>x</b>",
		"vbscript:x",
		"//evil.example/files/x",
		"\\\\evil.example\\files",
		"files/x.pdf",
		"/api/method/logout",
		"/app/file/1",
		"ftp://x.example/a",
		"",
		null,
		undefined,
	]) {
		assert.equal(safeFileHref(bad), "", String(bad));
	}
});

check("a sidebar row for an ordinary file reads as it always did", () => {
	for (const url of ["/files/plan.pdf", "/private/files/plan.pdf", "https://supplier.example/specs"]) {
		const row = sidebarAttachmentRowHtml("plan.pdf", url, "12 KB", "<svg></svg>", escape);
		assert.match(row, new RegExp(`<a href="${url.replace(/[./]/g, "\\$&")}" target="_blank"`));
		assert.match(row, /title="plan\.pdf"/);
		assert.match(row, /<span class="ellipsis">plan\.pdf<\/span>/);
		assert.match(row, />12 KB<\/span>/);
		assert.match(row, /<span class="m-r-1"><svg><\/svg><\/span>/);
	}
});

check("a name with & or quotes shows as text", () => {
	const row = sidebarAttachmentRowHtml(`Tom & "Jerry" <v2>.pdf`, "/files/t.pdf", "", "", escape);
	assert.ok(row.includes("Tom &amp; &quot;Jerry&quot; &lt;v2&gt;.pdf"));
	assert.ok(!row.includes("<v2>"));
	assert.ok(!row.includes(`"Jerry"`));
});

check("hostile name, address and size cannot add an element or an attribute", () => {
	for (const url of [HOSTILE, `/files/${HOSTILE}`, `https://x.example/${HOSTILE}`, "javascript:alert(1)"]) {
		const row = sidebarAttachmentRowHtml(HOSTILE, url, HOSTILE, "", escape);
		assert.ok(!row.includes("<img"), url);
		assert.ok(!row.includes("<b>"), url);
		assert.equal((row.match(/"/g) || []).length % 2, 0, "balanced quotes: " + url);
	}
});

check("an address that is not a site file or web page is drawn as text, not a link", () => {
	const row = sidebarAttachmentRowHtml("a.pdf", "javascript:alert(1)", "1 KB", "", escape);
	assert.ok(!row.includes("href"));
	assert.ok(!row.includes("<a "));
	assert.match(row, /<span class="text-muted ellipsis"/);
	assert.ok(row.includes(">a.pdf</span>"));
	const none = sidebarAttachmentRowHtml("a.pdf", undefined, "", "", escape);
	assert.ok(!none.includes("href"));
});

check("the comment box's link: an anchor for a good address, text for a bad one", () => {
	assert.equal(
		fileLinkHtml("a & b.pdf", "/files/a.pdf", escape),
		'<a href="/files/a.pdf" target="_blank" rel="noopener">a &amp; b.pdf</a>',
	);
	assert.equal(
		fileLinkHtml("a.pdf", "/files/a.pdf", escape, 'style="x"'),
		'<a href="/files/a.pdf" target="_blank" rel="noopener" style="x">a.pdf</a>',
	);
	assert.equal(fileLinkHtml("<i>", "javascript:alert(1)", escape), "<span>&lt;i&gt;</span>");
	assert.equal(fileLinkHtml(null, "/files/a.pdf", escape), '<a href="/files/a.pdf" target="_blank" rel="noopener"></a>');
});

// ---- 2. procurement_links.js ------------------------------------------------------------------

function loadPurchaseLinks() {
	const clicks = [];
	const sandbox = {
		console,
		document: { addEventListener: (type, handler, capture) => clicks.push({ type, handler, capture }) },
		$: () => ({}),
	};
	sandbox.window = sandbox;
	sandbox.frappe = {
		provide(dotted) {
			let node = sandbox;
			for (const part of dotted.split(".")) node = node[part] = node[part] || {};
		},
		ui: { form: { on() {} } },
		utils: { escape_html: escape },
		call() {},
		prompt() {},
		msgprint() {},
		show_alert() {},
	};
	vm.createContext(sandbox);
	vm.runInContext(fs.readFileSync(path.join(JS, "procurement_links.js"), "utf8"), sandbox);
	return { ns: sandbox.erpnext_enhancements.procurement, clicks };
}

function rowHtml(ns, { supplier, links, item_code = "ITEM-1", doctype = "Purchase Order" }) {
	const frm = { doc: { doctype, supplier, items: [] } };
	const links_ui = new ns.PurchaseLinks(frm);
	links_ui.cache = { [item_code]: links };
	const row = { item_code };
	links_ui.update_row_html(row);
	return row.purchase_links;
}

check("supplier buttons: ordinary links read as before, bad addresses are not links", () => {
	const { ns } = loadPurchaseLinks();
	const html = rowHtml(ns, {
		supplier: "Acme",
		links: [
			{ supplier: "Acme", url: "https://acme.example/p/1?a=1&b=2" },
			{ supplier: "Other", url: "javascript:alert(1)" },
		],
	});
	assert.ok(html.includes('<a href="https://acme.example/p/1?a=1&amp;b=2" target="_blank" rel="noopener" class="btn btn-xs btn-default"'));
	assert.ok(html.includes("Acme ↗"));
	assert.ok(!html.includes("javascript:"));
	assert.ok(html.includes("Other ↗"));
});

check("supplier, item code and address are escaped everywhere they are printed", () => {
	const { ns } = loadPurchaseLinks();
	for (const links of [[{ supplier: HOSTILE, url: `https://x.example/${HOSTILE}` }], []]) {
		const html = rowHtml(ns, { supplier: HOSTILE, links, item_code: HOSTILE });
		assert.ok(!html.includes("<img"));
		assert.ok(!html.includes("<b>"));
		assert.ok(!html.includes("onclick="));
		assert.equal((html.match(/"/g) || []).length % 2, 0, "balanced quotes");
	}
});

check("add and edit buttons carry their values in data- attributes", () => {
	const { ns } = loadPurchaseLinks();
	const add = rowHtml(ns, { supplier: "Acme", links: [] });
	assert.ok(add.includes('class="btn btn-xs btn-primary btn-purchase-link" data-item-code="ITEM-1" data-supplier="Acme" data-url=""'));
	assert.ok(add.includes('title="Add Link for Acme"'));
	const edit = rowHtml(ns, { supplier: "Acme", links: [{ supplier: "Acme", url: "https://acme.example/p?a=1&b=2" }] });
	assert.ok(edit.includes('data-url="https://acme.example/p?a=1&amp;b=2"'));
	assert.ok(edit.includes('title="Edit Link"'));
	assert.ok(!edit.includes("btn-primary"), "an existing link is edited, not added");
});

check("one capture-phase click handler passes the data- values to edit_link", () => {
	const { ns, clicks } = loadPurchaseLinks();
	rowHtml(ns, { supplier: "Acme", links: [] });
	rowHtml(ns, { supplier: "Acme", links: [] });
	assert.equal(clicks.length, 1, "bound once");
	assert.equal(clicks[0].type, "click");
	assert.equal(clicks[0].capture, true);
	const calls = [];
	ns.edit_link = (...args) => calls.push(args);
	const attrs = { "data-item-code": `IT"EM'1`, "data-supplier": "Acme & Sons", "data-url": "https://a.example/p?x=1&y=2" };
	let prevented = 0;
	const button = { getAttribute: (name) => attrs[name] };
	clicks[0].handler({
		target: { closest: (selector) => (selector === ".purchase-links .btn-purchase-link" ? button : null) },
		preventDefault: () => (prevented += 1),
	});
	assert.deepEqual(calls, [[`IT"EM'1`, "Acme & Sons", "https://a.example/p?x=1&y=2"]]);
	assert.equal(prevented, 1);
	// A click anywhere else is left alone.
	clicks[0].handler({ target: { closest: () => null }, preventDefault: () => (prevented += 1) });
	clicks[0].handler({ target: null, preventDefault: () => (prevented += 1) });
	assert.equal(calls.length, 1);
	assert.equal(prevented, 1);
});

console.log(`ok: ${passed} checks`);
