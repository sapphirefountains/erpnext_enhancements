#!/usr/bin/env node
/**
 * The realtime connection URL, and the namespace that makes it authenticate.
 *
 * WHY THIS FILE EXISTS
 * ====================
 *
 * The SPA received **no realtime events at all** — not another person's message, not
 * `@triton`'s reply, not even its own — and looked completely healthy doing it. Every symptom
 * pointed away from the cause:
 *
 * * the engine.io handshake succeeds, because it runs before any namespace middleware, so
 *   `/socket.io/?EIO=4&transport=polling` answers with a real `sid`;
 * * the rejection happens inside the Node process, so nothing reaches ERPNext's Error Log;
 * * `connect_error` set a status nobody surfaced.
 *
 * The cause was one missing path segment. Frappe's server namespaces every connection by site
 * and its auth middleware does:
 *
 *     let namespace = socket.nsp.name.slice(1);      // "" for the default namespace
 *     if (namespace != get_site_name(socket)) next(new Error("Invalid namespace"));
 *
 * Connecting to the bare origin lands in `/`, so the comparison is `"" != "site"` and the
 * connection is refused. `socketUrl` is pure, so the one thing that mattered is directly
 * assertable — which is the point of this file.
 */

import assert from "node:assert/strict";

import { socketUrl } from "../erpnext_enhancements/public/js/chat/socket.js";

let failures = 0;
let checks = 0;

function test(name, fn) {
	checks += 1;
	try {
		fn();
		console.log("  ok    " + name);
	} catch (err) {
		failures += 1;
		console.error("  FAIL  " + name + "\n        " + (err && err.message));
	}
}

const PROD = { protocol: "https:", hostname: "erp.example.com", host: "erp.example.com", port: "" };
const DEV = { protocol: "http:", hostname: "localhost", host: "localhost:8000", port: "8000" };

console.log("chat realtime connection URL\n");

test("THE BUG: the URL carries the site as a NAMESPACE, not just an origin", () => {
	const url = socketUrl({ site_name: "erp.example.com" }, PROD);
	assert.equal(url, "https://erp.example.com/erp.example.com");
	assert.ok(
		url.endsWith("/erp.example.com"),
		"without the trailing namespace the server compares \"\" against the site name and " +
			"refuses the connection as Invalid namespace"
	);
});

test("the bare origin is NOT what we send — that is the shape that was rejected", () => {
	assert.notEqual(socketUrl({ site_name: "erp.example.com" }, PROD), "https://erp.example.com");
});

test("a dev server keeps its explicit port AND gains the namespace", () => {
	// Behind the load balancer /socket.io/ is proxied on 443 so an explicit port is wrong; on a
	// dev port it is required. Both cases still need the namespace.
	assert.equal(
		socketUrl({ site_name: "dev.localhost", socketio_port: 9000 }, DEV),
		"http://localhost:9000/dev.localhost"
	);
});

test("no site name returns EMPTY, so connect() refuses instead of being silently rejected", () => {
	// Every value here would produce a connection the server rejects as Invalid namespace.
	// Returning "" lets connect() fail loudly at a point where the reason is still known.
	for (const boot of [{}, { site_name: "" }, { site_name: null }, null, undefined]) {
		assert.equal(socketUrl(boot, PROD), "", `expected "" for ${JSON.stringify(boot)}`);
	}
});

test("a dev port without a socketio_port still falls back to the page host", () => {
	assert.equal(socketUrl({ site_name: "dev.localhost" }, DEV), "http://localhost:8000/dev.localhost");
});

console.log("");
if (failures) {
	console.error(`chat realtime connection URL: ${failures} of ${checks} FAILED`);
	process.exit(2);
}
console.log(`chat realtime connection URL: ${checks} assertions passed`);
