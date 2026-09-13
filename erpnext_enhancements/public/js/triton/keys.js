/**
 * Keyboard helpers for the Triton widget's composer.
 *
 * This file is the surviving fragment of `public/js/chat/dom.js`, which went with the chat
 * module in v1.426.0. Everything else that file exported — the `el()` node builders,
 * `linkify`, `relativeTime`, `shouldGroup`, `initials`, `fileSize` — existed for the chat
 * transcript and lost its last consumer with it. `isComposingKey` did not: the Triton
 * composer has always used it, and `scripts/test_triton_widget_guards.js` asserts the widget
 * imports it from here rather than re-implementing the check inline.
 *
 * Kept as its own module rather than inlined into `triton_widget.js` for that reason. The
 * guard exists because the rule is easy to get half-right, and a half-right IME check is
 * invisible to anyone typing in English — see below.
 */

/**
 * Is this keydown part of an IME composition, rather than a finished keystroke?
 *
 * Both halves are load-bearing and neither is redundant. `ev.isComposing` is the standard
 * and is what modern browsers set; `keyCode === 229` is the legacy signal that some IMEs
 * (and older WebKit) send *instead*, with `isComposing` unset. A composer that checks only
 * one of them sends a half-typed word the moment a Japanese, Chinese or Korean typist
 * presses Enter to accept a candidate — and reads as perfectly correct to anyone testing in
 * English, because neither signal ever fires for them.
 *
 * @param {KeyboardEvent} ev
 * @returns {boolean}
 */
export function isComposingKey(ev) {
	if (!ev) return false;
	return !!ev.isComposing || ev.keyCode === 229;
}
