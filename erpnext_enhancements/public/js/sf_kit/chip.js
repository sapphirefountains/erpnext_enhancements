/*
 * sf_kit: a chip. A pill with a word in it, in a pillar's colours or a status colour.
 *
 *   chip("Build", "build")          a pillar: fill = closing stop, 1px edge = opening stop
 *   chip("Failing", "danger")       a status: danger | warn | good
 *
 * The word is the meaning and the colour only repeats it, so a chip with no word is not drawn at
 * all (a coloured blob reads as nothing to a colour-blind reader or a screen reader). An unknown
 * tone is neutral rather than an error: tones often come from data.
 *
 * The pillar edge is not decoration: Build's closing stop is navy-800, the colour of a dark-theme
 * card, and without the edge a Build chip is a floating word (docs/app-design-system.md, Chip).
 */

import { esc } from "./esc.js";

export const PILLAR_TONES = ["design", "build", "service", "rent", "neutral"];
export const STATUS_TONES = ["danger", "warn", "good"];
export const CHIP_TONES = PILLAR_TONES.concat(STATUS_TONES);

export function chip_tone(tone) {
	return CHIP_TONES.includes(tone) ? tone : "neutral";
}

export function chip(label, tone) {
	const text = label === null || label === undefined ? "" : String(label).trim();
	if (!text) return "";
	return `<span class="sf-chip sf-chip--${chip_tone(tone)}">${esc(text)}</span>`;
}
