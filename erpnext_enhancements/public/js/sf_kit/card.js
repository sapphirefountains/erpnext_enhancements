/*
 * sf_kit: a card. Square, --sf-surface, a --sf-rule hairline, no shadow; a pillar card leads with
 * a band in the pillar's closing stop. Most pages only need the class (`sf-card`,
 * `sf-card--pillar-build`); this helper is for the simple title-and-body case.
 *
 *   card_html({ title: "Highlands Plaza", body_html: rows, pillar: "build" })
 *
 * The title is escaped. `body_html` is HTML the caller built, and the caller escaped what went
 * into it: that is what the `_html` in its name promises.
 */

import { esc } from "./esc.js";

export const CARD_PILLARS = ["design", "build", "service", "rent", "neutral"];

export function card_class(pillar) {
	return CARD_PILLARS.includes(pillar) ? `sf-card sf-card--pillar-${pillar}` : "sf-card";
}

export function card_html(opts) {
	opts = opts || {};
	const title =
		opts.title === undefined || opts.title === null || opts.title === ""
			? ""
			: `<h3 class="sf-card-title">${esc(opts.title)}</h3>`;
	const body = opts.body_html ? `<div class="sf-card-body">${opts.body_html}</div>` : "";
	return `<section class="${card_class(opts.pillar)}">${title}${body}</section>`;
}
