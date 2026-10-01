/**
 * Concept screens in sandboxed frames, and the click-through rules that join them.
 *
 * Every screen is drawn in <iframe sandbox="allow-same-origin">, NEVER with allow-scripts (that
 * pair lets a frame lift its own sandbox), carrying Content-Security-Policy: default-src 'none'.
 * The markup was sanitized on import (design_review/sanitize.py); the frame is the second layer.
 * The WI-079 spike proved in Chrome with real mouse input that this page can still measure every
 * part, receive clicks inside the frame and outline what is linked, and that an unsanitized
 * hostile screen ran no script and reached no host with the policy in place.
 *
 * The frame's document runs nothing. Everything here is this page acting on it: a <style> it
 * inserts, attributes it sets, listeners it registers.
 */

import { el } from "./dom.js";

const FONT = "/assets/erpnext_enhancements/fonts/big_noodle_titling.woff2";
const CSP = "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src 'self' data:";

// Inserted into every frame: scrolling inside a screen, the Links and Markup outlines, hover.
const FRAME_CSS = `
html,body{margin:0;overflow:hidden;background:#f8f8f8}
.ux-scroll{overflow:auto!important;scrollbar-width:thin}
body.rr-links [data-nav]{outline:2px dashed rgba(0,160,223,.85);outline-offset:-2px}
body.rr-flash [data-nav]{outline:3px solid #00a0df;outline-offset:-3px;background-color:rgba(0,160,223,.10)}
body:not(.rr-markup) .rr-hot{outline:2px solid #00a0df!important;outline-offset:-2px;cursor:pointer}
[data-nav]{cursor:pointer}
body.rr-markup [data-c]{outline:1px dashed rgba(0,160,223,.75);outline-offset:-1px;cursor:crosshair}
body.rr-markup [data-c]:hover{outline:2px solid #00a0df;background-color:rgba(0,160,223,.08)}
body.rr-markup [data-rr-has]{outline:2px solid #ffb819}
body.rr-markup [data-rr-sel]{outline:3px solid #ff6b35;background-color:rgba(255,107,53,.10)}
input,textarea,select{pointer-events:none}
`;

export function frameDoc(content, html) {
	return (
		'<!doctype html><html><head><meta charset="utf-8">' +
		`<meta http-equiv="Content-Security-Policy" content="${CSP}">` +
		`<style>@font-face{font-family:"Big Noodle Titling";src:url(${FONT}) format("woff2");font-weight:700}` +
		FRAME_CSS +
		(content.kit_css || "") +
		(content.stylesheet || "") +
		"</style></head><body>" +
		html +
		"</body></html>"
	);
}

export function makeFrame(content, screen, opts) {
	const f = el("iframe");
	f.setAttribute("sandbox", "allow-same-origin"); // never allow-scripts: see the header comment
	f.setAttribute("referrerpolicy", "no-referrer");
	f.setAttribute("title", `${screen.option} ${screen.name} (${screen.frame})`);
	if (opts && opts.inert) {
		f.setAttribute("tabindex", "-1");
		f.setAttribute("aria-hidden", "true");
		f.style.pointerEvents = "none";
	}
	f.style.cssText += `position:absolute;left:0;top:0;border:0;transform-origin:0 0;width:${screen.w}px;height:${screen.h}px;`;
	f.width = screen.w;
	f.height = screen.h;
	f.srcdoc = frameDoc(content, screen.html);
	return f;
}

// ---------------------------------------------------------------- click-through
//
// A bundle's `flow` says where a click goes (docs/design-review-bundle.md). Targets: "S04" (a
// screen of this track), "learner:S01" (another track, the option in the same position), "next"
// (the story's next screen, else the next in order), "up" (the screen's own back target, else
// browser Back), "end", "done:<message>" (shown, no move). Rules are resolved once per screen and
// written onto the frame's elements as data-nav, as the claude.ai Concept Viewer did, so hover and
// Links show what is clickable before anyone clicks.

const DOM_DEFAULTS = { icon: ".ux-iconbtn", step: ".ux-step", step_label: "span:not(.ux-stepnum):not(.ux-badge)", noinherit: "", inert: "", bars: [] };

function matches(pattern, text) {
	try {
		return new RegExp(pattern).test(text);
	} catch (e) {
		return false;
	}
}

function norm(node) {
	return (node.textContent || "").replace(/\s+/g, " ").trim().toUpperCase();
}

export function targetOf(node, screen, flow) {
	const dom = Object.assign({}, DOM_DEFAULTS, flow.dom || {});
	const t = screen.track;
	const sid = screen.screen;
	const label = node.getAttribute("aria-label") || "";
	if (dom.icon && node.matches(dom.icon)) {
		for (const [pattern, tg, only] of flow.icon || []) if ((!only || only === t) && matches(pattern, label)) return tg;
		return null;
	}
	if (label) for (const [pattern, tg, only] of flow.aria || []) if ((!only || only === t) && matches(pattern, label)) return tg;
	for (const [part, tg] of Object.entries((flow.disabled || {})[t] || {})) {
		if (node.classList.contains("is-disabled") && node.closest(`[data-c="${CSS.escape(part)}"]`)) return tg;
	}
	if (node.tagName === "A" || node.tagName === "BUTTON") {
		const lb = dom.step && node.matches(dom.step) ? node.querySelector(dom.step_label) : null;
		const txt = norm(lb || node);
		for (const [pattern, tg, screens, options] of (flow.text || {})[t] || []) {
			if (screens && screens.indexOf(sid) < 0) continue;
			if (options && options.indexOf(screen.option) < 0) continue;
			if (matches(pattern, txt)) return tg;
		}
	}
	const name = node.getAttribute("data-c");
	let rule = name ? ((flow.nav || {})[t] || {})[name] : null;
	if (Array.isArray(rule)) {
		const txt = norm(node);
		const hit = rule.find(([pattern]) => matches(pattern, txt));
		return hit ? hit[1] : null;
	}
	if (rule && typeof rule === "object") rule = Object.prototype.hasOwnProperty.call(rule, sid) ? rule[sid] : rule["*"];
	return rule || null;
}

/** Where a target leads from `screen`: {track, screen} or {special: "back"|"end"|"done", message}. */
export function resolve(target, screen, flow, order) {
	if (!target) return null;
	if (target.indexOf("done:") === 0) return { special: "done", message: target.slice(5) };
	const t = screen.track;
	const sid = screen.screen;
	if (target === "up") {
		target = ((flow.backto || {})[t] || {})[sid];
		if (!target) return { special: "back" };
	}
	if (target === "next") target = ((flow.next || {})[t] || {})[sid] || "+1";
	if (target === "end") {
		const chain = (flow.chain || {})[`${t}:${sid}`];
		if (!chain) return { special: "end" };
		target = chain;
	}
	if (target === "+1" || target === "-1") {
		const i = order.indexOf(sid) + (target === "+1" ? 1 : -1);
		if (i < 0) return { special: "start" };
		if (i >= order.length) return { special: "end" };
		return { track: t, screen: order[i] };
	}
	if (target.indexOf(":") > 0) {
		const [tt, ss] = target.split(":");
		return { track: tt, screen: ss };
	}
	return { track: t, screen: target };
}

export function decorate(doc, screen, flow, order) {
	const dom = Object.assign({}, DOM_DEFAULTS, flow.dom || {});
	const style = doc.createElement("style");
	style.textContent = FRAME_CSS;
	doc.head.appendChild(style);
	const inert = (n) => dom.inert && n.closest(dom.inert);
	const here = (tg) => {
		if (/^(end|start)$/.test(tg) || tg.indexOf("done:") === 0) return false;
		const r = resolve(tg, screen, flow, order);
		return !!r && !r.special && r.track === screen.track && r.screen === screen.screen;
	};
	doc.querySelectorAll("a,button,[data-c]").forEach((n) => {
		if (inert(n)) return;
		const tg = targetOf(n, screen, flow);
		if (tg) n.setAttribute(here(tg) ? "data-here" : "data-nav", tg);
	});
	// An unmapped main button carries its bar's target; secondary ones do not.
	doc.querySelectorAll("a,button").forEach((c) => {
		if (c.hasAttribute("data-nav") || c.hasAttribute("data-here") || inert(c)) return;
		if (dom.noinherit && c.matches(dom.noinherit)) return;
		const up = c.parentElement && c.parentElement.closest("[data-nav]");
		if (up) c.setAttribute("data-nav", up.getAttribute("data-nav"));
	});
	// A bar hands its target only to its main button: its padding is not a door.
	(dom.bars || []).forEach((bar) => {
		doc.querySelectorAll(`[data-c="${CSS.escape(bar)}"]`).forEach((b) => {
			if (b.querySelector("a,button")) b.removeAttribute("data-nav");
		});
	});
}
