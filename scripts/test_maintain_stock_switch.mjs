/**
 * The Switch Items to Maintain Stock page's decisions (v1.809.0), without a browser.
 *
 * `inventory_enhancements/page/maintain_stock_switch/maintain_stock_switch.js` keeps what it decides
 * in one plain object, MSS: which tab a route means, which rows a tab and the search box show, what
 * an apply would send, and the words of the run line. This loads the real file in a bare context
 * (a `frappe` that only has `pages`, a `__` that fills in {0}) and checks those. The rules that
 * matter (what is a part, which checks, what an apply queues) are the server's, and are tested in
 * tests/test_maintain_stock.py, which also runs this file.
 *
 * Run: node scripts/test_maintain_stock_switch.mjs
 */
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const file = path.join(
	here, "..", "erpnext_enhancements", "inventory_enhancements", "page", "maintain_stock_switch", "maintain_stock_switch.js"
);
const sandbox = {
	module: { exports: {} },
	frappe: { pages: { "maintain-stock-switch": {} } },
	__: (text, args) => String(text).replace(/\{(\d+)\}/g, (_, i) => String((args || [])[Number(i)])),
};
vm.runInNewContext(fs.readFileSync(file, "utf8"), sandbox, { filename: file });
const { MSS, MSS_VIEWS } = sandbox.module.exports;
const plain = (value) => JSON.parse(JSON.stringify(value));

// The page registered both hooks, and loading it built nothing.
assert.equal(typeof sandbox.frappe.pages["maintain-stock-switch"].on_page_load, "function");
assert.equal(typeof sandbox.frappe.pages["maintain-stock-switch"].on_page_show, "function");

// 1. A tab is a route, and a route is a tab: Back and Forward land on the tab they left.
assert.deepEqual(plain(MSS_VIEWS), ["switch", "stays", "blocked", "results"]);
for (const view of MSS_VIEWS) {
	assert.equal(MSS.view_from_route(MSS.route_for(view)), view, view);
}
assert.deepEqual(plain(MSS.route_for("switch")), ["maintain-stock-switch"]);
assert.deepEqual(plain(MSS.route_for("blocked")), ["maintain-stock-switch", "blocked"]);
assert.equal(MSS.view_from_route(["maintain-stock-switch", "nonsense"]), "switch");
assert.equal(MSS.view_from_route(undefined), "switch");

// 2. A tab shows its own outcome, narrowed by the search box (code, name or group, any case).
const rows = [
	{ item_code: "TST-PUMP-100", item_name: "Test pump", item_group: "Pumps", outcome: "switch" },
	{ item_code: "TST-TEE-200", item_name: "Test tee", item_group: "Plumbing", outcome: "switch", status: "Queued" },
	{ item_code: "TST-FEE-1", item_name: "Late fee", item_group: "All Item Groups", outcome: "stay" },
	{ item_code: "TST-VALVE", item_name: "Test valve", item_group: "Plumbing", outcome: "blocked" },
];
const codes = (list) => plain(list.map((row) => row.item_code));
assert.deepEqual(codes(MSS.visible(rows, "switch", "")), ["TST-PUMP-100", "TST-TEE-200"]);
assert.deepEqual(codes(MSS.visible(rows, "stays", "")), ["TST-FEE-1"]);
assert.deepEqual(codes(MSS.visible(rows, "blocked", "")), ["TST-VALVE"]);
assert.deepEqual(codes(MSS.visible(rows, "switch", "  PLUMB ")), ["TST-TEE-200"]);
assert.deepEqual(codes(MSS.visible(rows, "switch", "pump")), ["TST-PUMP-100"]);
assert.deepEqual(codes(MSS.visible(rows, "stays", "pump")), []);
assert.deepEqual(codes(MSS.visible(null, "switch", "")), []);

// 3. An apply sends the rows that will switch and are not already queued. The server decides again.
assert.deepEqual(plain(MSS.to_apply(rows)), ["TST-PUMP-100"]);
assert.deepEqual(plain(MSS.to_apply([])), []);

// 4. The choice box shows a person's choice, else "follow the rule".
assert.equal(MSS.choice({ decision: "Switch" }), "Switch");
assert.equal(MSS.choice({ decision: "Stay non-stock" }), "Stay non-stock");
assert.equal(MSS.choice({ decision: "" }), "");
assert.equal(MSS.choice({ decision: "anything else" }), "");

// 5. The run line: nothing before a run, progress during one, the tally after.
assert.equal(MSS.run_line(null), "");
assert.equal(MSS.run_line({ queued: 0, switched: 0, refused: 0 }), "");
assert.equal(MSS.run_line({ queued: 4, switched: 6, refused: 0 }), "Switching now: 4 waiting to switch, 6 switched.");
assert.equal(MSS.run_line({ queued: 0, switched: 9, refused: 1 }), "Last run: 9 switched, 1 refused (see Results).");

console.log("maintain_stock_switch: all checks passed");
