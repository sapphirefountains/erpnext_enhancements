/*
 * sf_kit: a grid of status tiles, the shape of a health dashboard (Integrations Health, the device
 * fleet). Each tile is an sf-card with a title, a status chip (the word AND the colour), an
 * optional big value, a detail line, label/value rows and links.
 *
 *   tiles_html([{
 *     key: "drive", title: "Google Drive", tone: "good", status_label: "Healthy",
 *     value: "12,408", detail: "Last sync 4 min ago",
 *     metrics: [{ label: "Queued", value: 0 }],
 *     links: [{ label: "Settings", href: "/desk/google-drive-settings" }],
 *   }])
 *
 * `tone` (or `status`) is a chip tone; integrations_health's green / amber / red are accepted as
 * good / warn / danger so that page can move over unchanged. With no `status_label`, a status
 * tone gets a default word; a tile with neither has no chip rather than a wordless one.
 *
 * Everything is escaped. A link goes in an href only when it is a site-relative /desk/ route or an
 * http(s) URL; anything else (javascript:, data:, //host, a bare word) is shown as plain text, so
 * a bad route in the data costs a link, not a script.
 */

import { esc } from "./esc.js";
import { t } from "./t.js";
import { chip, chip_tone } from "./chip.js";

const STATUS_ALIASES = { green: "good", amber: "warn", yellow: "warn", red: "danger" };

function default_label(tone) {
	if (tone === "good") return t("Healthy");
	if (tone === "warn") return t("Needs attention");
	if (tone === "danger") return t("Failing");
	return "";
}

export function tile_tone(value) {
	const tone = STATUS_ALIASES[value] || value;
	return chip_tone(tone);
}

// The href to write, or "" when the value must not become a link.
export function safe_href(value) {
	const href = String(value === null || value === undefined ? "" : value).trim();
	if (/[\s\u0000-\u001f\u007f\\]/.test(href)) return "";
	if (/^\/desk(?:[/?#]|$)/.test(href)) return href;
	if (/^https?:\/\/[^/?#]+/i.test(href)) return href;
	return "";
}

function blank(value) {
	return value === undefined || value === null || value === "";
}

function link_html(link) {
	if (!link || blank(link.label)) return "";
	const href = safe_href(link.href !== undefined ? link.href : link.route);
	if (!href) return `<span class="sf-tile-link sf-tile-link--plain">${esc(link.label)}</span>`;
	const external = /^https?:/i.test(href) ? ' target="_blank" rel="noopener noreferrer"' : "";
	return `<a class="sf-tile-link" href="${esc(href)}"${external}>${esc(link.label)}</a>`;
}

export function tile_html(item) {
	item = item || {};
	const raw_tone = item.tone !== undefined ? item.tone : item.status;
	const tone = tile_tone(raw_tone);
	const label = blank(item.status_label) ? (blank(raw_tone) ? "" : default_label(tone)) : item.status_label;
	const parts = [
		`<div class="sf-tile-head"><h3 class="sf-tile-title">${esc(item.title)}</h3>${chip(label, tone)}</div>`,
	];
	if (!blank(item.value)) parts.push(`<div class="sf-tile-value">${esc(item.value)}</div>`);
	if (!blank(item.detail)) parts.push(`<p class="sf-tile-detail">${esc(item.detail)}</p>`);
	const metrics = (item.metrics || [])
		.filter((m) => m && !blank(m.label))
		.map(
			(m) =>
				`<div class="sf-tile-metric"><dt>${esc(m.label)}</dt><dd>${esc(blank(m.value) ? "—" : m.value)}</dd></div>`
		);
	if (metrics.length) parts.push(`<dl class="sf-tile-metrics">${metrics.join("")}</dl>`);
	const links = (item.links || []).map(link_html).filter(Boolean);
	if (links.length) parts.push(`<div class="sf-tile-links">${links.join("")}</div>`);
	const key = blank(item.key) ? "" : ` data-key="${esc(item.key)}"`;
	return `<section class="sf-card sf-tile"${key}>${parts.join("")}</section>`;
}

export function tiles_html(items) {
	return `<div class="sf-tiles">${(items || []).map(tile_html).join("")}</div>`;
}
