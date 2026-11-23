// The duty-point chart, the schematic and the one-line come from derive as SVG text (charts.duty_svg,
// schematic_svg, oneline_svg) and the page draws them as given: the server owns the drawing, so a chart
// on screen and the same chart on paper (phase G) cannot disagree.
//
// "As given" stops short of running anything. The text goes through an allow-list before it becomes
// markup: SVG drawing elements and their presentation attributes only, no script, no foreignObject, no
// event handler, no link that leaves the drawing, no style attribute (a url() in it can fetch). With a
// DOMParser (the browser) the check walks the parsed tree; without one (node) it falls back to a
// conservative text filter that refuses anything it cannot vouch for.

const ELEMENTS = new Set([
	"svg", "g", "defs", "title", "desc", "path", "line", "polyline", "polygon", "rect", "circle", "ellipse",
	"text", "tspan", "clippath", "marker", "lineargradient", "radialgradient", "stop", "pattern", "symbol",
]);

const ATTRIBUTES = new Set([
	"viewbox", "width", "height", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "d", "points",
	"fill", "fill-opacity", "fill-rule", "stroke", "stroke-width", "stroke-dasharray", "stroke-linecap",
	"stroke-linejoin", "stroke-opacity", "opacity", "transform", "class", "id", "role", "aria-label",
	"aria-hidden", "focusable", "font-size", "font-weight", "font-family", "text-anchor", "dominant-baseline",
	"dx", "dy", "clip-path", "marker-end", "marker-start", "marker-mid", "markerwidth", "markerheight", "refx",
	"refy", "orient", "offset", "stop-color", "stop-opacity", "gradientunits", "patternunits",
	"preserveaspectratio", "xmlns", "vector-effect", "data-point", "data-series",
]);

// An attribute value that points somewhere: only a reference inside the drawing ("url(#id)") passes.
function safe_value(name, value) {
	const text = String(value == null ? "" : value);
	if (/javascript:|data:|expression\(|@import/i.test(text)) return false;
	if (/url\(/i.test(text) && !/^\s*url\(\s*#[\w-]+\s*\)\s*$/i.test(text)) return false;
	return true;
}

// The last few drawings checked: the verdict panel is drawn again on every edit, and the same reply's
// chart need not be parsed and walked each time.
const MEMO = new Map();
const MEMO_SIZE = 6;

// The sanitized SVG text, or "" when there is nothing safe to draw.
export function safe_svg(text, parser) {
	if (typeof text !== "string") return "";
	const key = (parser ? "p:" : "f:") + text;
	if (MEMO.has(key)) return MEMO.get(key);
	const out = check(text, parser);
	MEMO.set(key, out);
	if (MEMO.size > MEMO_SIZE) MEMO.delete(MEMO.keys().next().value);
	return out;
}

function check(text, parser) {
	const source = text.trim();
	if (!/^(<\?xml[^>]*>\s*)?<svg[\s>]/i.test(source)) return "";
	if (parser) return parsed(source, parser);
	return filtered(source);
}

function parsed(source, parser) {
	let doc;
	try {
		doc = parser.parseFromString(source, "image/svg+xml");
	} catch (e) {
		return "";
	}
	const root = doc && doc.documentElement;
	if (!root || String(root.nodeName).toLowerCase() !== "svg" || doc.getElementsByTagName("parsererror").length) return "";
	const walk = (el) => {
		for (const child of Array.from(el.children || [])) {
			if (!ELEMENTS.has(String(child.nodeName).toLowerCase())) {
				child.remove();
				continue;
			}
			clean_attributes(child);
			walk(child);
		}
	};
	clean_attributes(root);
	walk(root);
	return root.outerHTML || "";
}

function clean_attributes(el) {
	for (const attr of Array.from(el.attributes || [])) {
		const name = String(attr.name).toLowerCase();
		if (!ATTRIBUTES.has(name) || !safe_value(name, attr.value)) el.removeAttribute(attr.name);
	}
}

// No parser: every tag must be an allowed element and every attribute an allowed one with a safe value,
// or the whole drawing is refused. Comments and the XML prolog are dropped.
function filtered(source) {
	const body = source.replace(/<\?xml[^>]*>/gi, "").replace(/<!--[\s\S]*?-->/g, "");
	if (/<!\[CDATA\[|<!DOCTYPE|<!ENTITY/i.test(body)) return "";
	const tags = body.match(/<[^>]*>/g) || [];
	for (const tag of tags) {
		const m = /^<\/?\s*([a-zA-Z][\w:-]*)([\s\S]*?)\/?>$/.exec(tag);
		if (!m) return "";
		if (!ELEMENTS.has(m[1].toLowerCase())) return "";
		const rest = m[2];
		const attr_re = /([^\s=]+)\s*=\s*("[^"]*"|'[^']*')/g;
		let a;
		let stripped = rest;
		while ((a = attr_re.exec(rest))) {
			const name = a[1].toLowerCase();
			if (!ATTRIBUTES.has(name) || !safe_value(name, a[2].slice(1, -1))) return "";
			stripped = stripped.replace(a[0], "");
		}
		if (stripped.trim()) return "";
	}
	return body;
}

// The figure the page draws: the chart, or a quiet line when there is none yet (or it was refused).
export function chart_html(svg, opts) {
	const o = opts || {};
	const esc = o.esc || ((v) => String(v == null ? "" : v));
	const clean = safe_svg(svg, o.parser);
	const label = esc(o.label || "");
	if (!clean) return `<p class="fd-chart-empty">${esc(o.empty || "")}</p>`;
	return `<figure class="fd-chart" role="group" aria-label="${label}">${clean}</figure>`;
}
