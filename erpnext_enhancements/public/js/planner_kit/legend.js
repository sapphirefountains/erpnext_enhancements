/*
 * Planner kit: the help and legend drawer, rendered from a description of what a page draws.
 *
 *   planner_kit.legend([
 *     { title: "Cards", note: "optional line under the heading", items: [
 *       { sample_html: '<span class="pp-chip pp-red">Conflict</span>', text: "Someone on it is ..." },
 *       { swatch: { color: "#2563eb", style: "dashed", fill: "#eff6ff" }, text: "..." },
 *       { chip: { text: "Pencil", tone: "plain" | "red" | "amber" | "green" | "blue" }, text: "..." },
 *     ] },
 *   ], { title: "How to read the planner", owner: "pp" });
 *
 * `text`, `title`, `note` and a chip's text are escaped here. `sample_html` is the page's own markup
 * (its real chip and card classes, so the legend shows exactly what the calendar draws) and is
 * trusted: a page builds it from fixed strings, never from a record. `legend_html` is pure, so
 * tests/test_planner_phase6a.py renders it under node.
 */

import { escape, safe_color } from "./escape.js";

const TONES = ["plain", "red", "amber", "green", "blue"];
const STYLES = ["solid", "dashed"];

function sample(item) {
	if (item.sample_html) return String(item.sample_html);
	if (item.swatch) {
		const swatch = item.swatch;
		const color = safe_color(swatch.color, "#94a3b8");
		const style = STYLES.includes(swatch.style) ? swatch.style : "solid";
		const fill = safe_color(swatch.fill, "");
		return `<i class="pk-swatch" style="border-left-color:${color};border-style:${style};${
			fill ? `background:${fill};` : ""
		}"></i>`;
	}
	if (item.chip) {
		const tone = TONES.includes(item.chip.tone) ? item.chip.tone : "plain";
		return `<span class="pk-chip pk-chip-${tone}">${escape(item.chip.text)}</span>`;
	}
	return "";
}

export function legend_html(sections) {
	const parts = (sections || [])
		.filter((section) => section && (section.items || []).length)
		.map((section) => {
			const items = section.items
				.filter((item) => item && item.text)
				.map(
					(item) =>
						`<li class="pk-legend-item"><span class="pk-legend-sample">${sample(item)}</span>` +
						`<span class="pk-legend-text">${escape(item.text)}</span></li>`
				)
				.join("");
			return (
				`<section class="pk-legend-section"><h4 class="pk-legend-title">${escape(section.title)}</h4>` +
				(section.note ? `<p class="pk-legend-note">${escape(section.note)}</p>` : "") +
				`<ul class="pk-legend-list">${items}</ul></section>`
			);
		});
	return `<div class="pk-legend">${parts.join("")}</div>`;
}

export function create_legend(env) {
	return function legend(sections, opts) {
		opts = opts || {};
		const open = () =>
			env.drawer.open({
				title: opts.title || env.t("How to read the planner"),
				subtitle: opts.subtitle || "",
				body: legend_html(sections),
				width: opts.width || 460,
				key: "legend",
				owner: opts.owner || null,
				reopen: () => open(),
			});
		return open();
	};
}
