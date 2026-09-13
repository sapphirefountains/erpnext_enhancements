/*
 * `public/js/triton/keys.js` — the IME composition guard, executably.
 *
 * Run: node scripts/test_triton_keys.mjs
 *
 * WHY THIS FILE EXISTS, since it is four lines of source under test. `isComposingKey` used to
 * be exercised by `scripts/test_chat_client_logic.mjs`, which imported it from
 * `public/js/chat/dom.js` alongside six other SPA modules. v1.426.0 deleted the chat module
 * and that suite with it, and re-homed this one function — which would have left the rule with
 * no assertion on what it *returns*, only `test_triton_widget_guards.js`'s source-scan proving
 * the widget still calls it. "It is imported and called" and "it is correct" are different
 * claims, and a removal is exactly when the second one quietly stops being checked.
 *
 * Both halves of the check are load-bearing and neither is redundant. `ev.isComposing` is the
 * standard; `keyCode === 229` is what some IMEs and older WebKit send *instead*, with
 * `isComposing` unset. A composer that tests only one of them sends a half-typed word the
 * moment a Japanese, Chinese or Korean typist presses Enter to accept a candidate — and reads
 * as perfectly correct to anyone testing in English, because neither signal ever fires for
 * them. That is the failure this guards, and it is invisible to the people most likely to
 * review it.
 */

import assert from "node:assert/strict";
import { isComposingKey } from "../erpnext_enhancements/public/js/triton/keys.js";

let checks = 0;
function ok(name, fn) {
	try {
		fn();
		checks += 1;
		console.log("  ok    " + name);
	} catch (err) {
		console.error("  FAIL  " + name + "\n        " + (err && err.message));
		process.exitCode = 1;
	}
}

console.log("Triton composer IME guard\n");

ok("a plain Enter is not a composition", () => {
	assert.equal(isComposingKey({ key: "Enter", keyCode: 13 }), false);
});

ok("the standard signal is honoured", () => {
	assert.equal(isComposingKey({ key: "Enter", isComposing: true }), true);
});

ok("the legacy 229 keycode is honoured on its own", () => {
	// The half that is easy to drop: no `isComposing` anywhere on the event.
	assert.equal(isComposingKey({ key: "Enter", keyCode: 229 }), true);
});

ok("229 is honoured even when isComposing is explicitly false", () => {
	assert.equal(isComposingKey({ key: "Enter", isComposing: false, keyCode: 229 }), true);
});

ok("a truthy non-boolean isComposing still reads as composing", () => {
	assert.equal(isComposingKey({ isComposing: 1 }), true);
});

ok("a missing event is not a composition rather than a crash", () => {
	// The composer calls this from a keydown handler; a throw here would break Enter-to-send
	// outright rather than degrade it.
	assert.equal(isComposingKey(undefined), false);
	assert.equal(isComposingKey(null), false);
});

ok("it always returns a real boolean, never the raw signal", () => {
	// `!!ev.isComposing` matters: a caller writing `if (isComposingKey(e) === true)` must not
	// be defeated by a truthy 1 leaking through.
	for (const ev of [{}, { isComposing: 1 }, { keyCode: 229 }, { keyCode: 13 }]) {
		assert.equal(typeof isComposingKey(ev), "boolean");
	}
});

console.log(`\nTriton composer IME guard: ${checks} assertions passed`);
