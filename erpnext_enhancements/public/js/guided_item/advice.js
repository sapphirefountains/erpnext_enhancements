/*
 * form kit P8.1: what the name and code step says about the naming check's preview
 * (api.item_flow.preview_item, which is Stock Scan's preview_new_item). Pure: no frappe, no DOM;
 * run under node by tests/test_guided_item.py.
 *
 * POL-0602's guard refuses a new Item for two findings only, a code that duplicates an existing
 * one once case and punctuation are ignored and a name that is just the code, and only from its
 * go-live date. So only those, and only while the guard is in force (the preview's will_refuse),
 * are shown as refusals that stop "Create and continue". Every other finding, STOP severity
 * included, is advice: worth reading, never in the way.
 */

const ORDER = { STOP: 0, FIX: 1, NOTE: 2 };

function list(value) {
	return Array.isArray(value) ? value : [];
}

// { refusals: [message], advice: [{severity, message}], exists, similar, can_create, checked }
export function verdict(preview) {
	const p = preview || {};
	const blocking = list(p.blocking).map((finding) => String((finding || {}).message || "")).filter(Boolean);
	const enforced = !!p.will_refuse;
	const refusals = enforced ? blocking : [];
	const advice = list(p.advice)
		.filter((finding) => finding && finding.message)
		.map((finding) => ({ severity: finding.severity || "NOTE", message: String(finding.message) }));
	// Before the guard is in force the two findings still matter: said, not enforced.
	if (!enforced) blocking.forEach((message) => advice.unshift({ severity: "STOP", message }));
	const rank = (finding) => (finding.severity in ORDER ? ORDER[finding.severity] : 3);
	advice.sort((a, b) => rank(a) - rank(b));
	return {
		refusals,
		advice,
		exists: p.exists || null,
		similar: list(p.similar).filter((row) => row && row.item_code),
		can_create: refusals.length === 0,
		checked: !!p.checked,
	};
}

// Whether a preview answers the code and name now on screen (a slow answer to an earlier version
// must not paint over a newer one).
export function answers(asked, current) {
	const a = asked || {};
	const c = current || {};
	return String(a.item_code || "") === String(c.item_code || "") && String(a.item_name || "") === String(c.item_name || "");
}
