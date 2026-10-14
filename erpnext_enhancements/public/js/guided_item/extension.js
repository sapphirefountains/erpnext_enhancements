/*
 * form kit P8.1 (ERPNext TASK-2026-02790): the Item flow's blocks on the guided page.
 *
 * Registered for "Item" in the form kit's extension registry (public/js/form_kit/extensions.js),
 * which is how the generic guided page draws a step's block without knowing what an Item is. The
 * steps name their blocks (form_kit/config/item.py):
 *
 *   item_naming    step 1: the code helper (vendor part number, CON-, PDT-####, SRV-###), the
 *                  name builder (POL-0602's seven segments, pick lists from the catalog) and the
 *                  naming guard's live preview. Only the guard's two refusals stop the create;
 *                  everything else the check finds is advice (advice.js).
 *   item_supplier  the default supplier, their part number and purchase link, the last price paid
 *   item_stock     the default bin, its reorder level and quantity, and the bin's QR label
 *   item_asset     the Asset for a fixed-asset item, made here so the two never come apart
 *
 * The code and name go into the step's own Item Code and Item Name fields (ctx.set_value), which
 * the page saves like any other. Everything else is written through api/item_flow.py's own
 * endpoints, each checking its own permissions; a block flushes the page's save first and has the
 * page take the record's new version after (ctx.save_first, ctx.record_changed), so the page's
 * next save is not a conflict. Every request goes through sf_kit.call quiet: reads GET, writes POST.
 *
 * The decisions are pure modules beside this one (naming.js, codes.js, advice.js, blocks.js), run
 * under node by tests/test_guided_item.py. This file is the DOM and frappe wiring only.
 */

import { register_extension } from "../form_kit/extensions.js";
import { SEGMENTS, assemble_name, split_name, part_options } from "./naming.js";
import { FAMILIES, CONSUMABLE_GROUPS, FAMILY_PREFIX, classify, build_code, code_problem } from "./codes.js";
import { verdict, answers } from "./advice.js";
import {
	label_url,
	new_client_id,
	supplier_start,
	supplier_fields,
	default_ticked,
	rate_to_send,
	stock_start,
	given,
} from "./blocks.js";

const API = "erpnext_enhancements.api.item_flow.";
const NAMING_CHECK = "erpnext_enhancements.inventory_enhancements.item_naming.check_item";
const PREVIEW_DELAY = 600;

const FAMILY_LABELS = {
	vendor: "Vendor part number",
	consumable: "CON- consumable",
	product: "PDT- product",
	service: "SRV- service",
};

function esc(value) {
	return frappe.utils.escape_html(value === null || value === undefined ? "" : String(value));
}

function plain(message) {
	// Server refusals arrive as HTML (the naming guard bolds the code): shown as text. Parsed in an
	// inert document, so nothing in it loads or runs (a created div's innerHTML is not inert: an
	// <img onerror> fires even though the div is never attached).
	const doc = new DOMParser().parseFromString(String(message || ""), "text/html");
	return ((doc.body && doc.body.textContent) || "").trim();
}

function link_control($parent, df) {
	const control = frappe.ui.form.make_control({ df, parent: $parent[0], render_input: true });
	control.refresh();
	return control;
}

function form_link(doctype, name) {
	return frappe.utils.get_form_link(doctype, name);
}

class ItemExtension {
	constructor() {
		this.naming = null; // naming_options, loaded once per record
		this.setup = null; // get_setup, the supplier/stock/asset blocks' view
		this.timer = null;
		this.preview_seq = 0;
		this.last_verdict = null;
		this.code_spec = { family: "vendor" };
		this.asset_id = null;
		this.$naming = null;
	}

	leave() {
		if (this.timer) window.clearTimeout(this.timer);
		this.timer = null;
		this.$naming = null;
	}

	render(name, $box, ctx) {
		const draw = {
			item_naming: () => this.render_naming($box, ctx),
			item_supplier: () => this.render_with_setup($box, ctx, (setup) => this.render_supplier($box, ctx, setup)),
			item_stock: () => this.render_with_setup($box, ctx, (setup) => this.render_stock($box, ctx, setup)),
			item_asset: () => this.render_with_setup($box, ctx, (setup) => this.render_asset($box, ctx, setup)),
		}[name];
		if (draw) draw();
	}

	on_change(fieldname, value, ctx) {
		if (!this.$naming) return;
		if (fieldname === "item_name") this.fill_segments(ctx, value);
		if (["item_code", "item_name", "item_group", "stock_uom"].includes(fieldname)) this.schedule_preview(ctx);
	}

	// Create and continue waits for the naming guard's two refusals to be fixed (the server would
	// refuse the insert anyway, in the same words).
	before_create() {
		const v = this.last_verdict;
		if (v && v.refusals.length) return __("Fix the naming problem shown above first: it would be refused.");
		return "";
	}

	// ------------------------------------------------------------------ name & code

	render_naming($box, ctx) {
		this.$naming = $box;
		$box.html(`
			<section class="sf-card gi-card gi-code">
				<h3 class="sf-card-title gi-title"></h3>
				<div class="gi-code-body"></div>
			</section>
			<section class="sf-card gi-card gi-name">
				<h3 class="sf-card-title gi-title"></h3>
				<p class="gi-note gi-name-intro"></p>
				<div class="gi-segments"></div>
				<p class="gi-assembled" aria-live="polite"></p>
			</section>
			<section class="sf-card gi-card gi-verdict" aria-live="polite">
				<h3 class="sf-card-title gi-title"></h3>
				<div class="gi-verdict-body"></div>
			</section>`);
		$box.find(".gi-code .gi-title").text(__("Item Code"));
		$box.find(".gi-name .gi-title").text(__("Item Name"));
		$box.find(".gi-name-intro").text(
			__("Fill the segments that apply, in this order. Each list offers what the catalog already uses for the category you chose.")
		);
		$box.find(".gi-verdict .gi-title").text(__("Naming check"));
		if (ctx.is_new && ctx.can_write) this.render_code_helper($box.find(".gi-code-body"), ctx);
		else {
			$box.find(".gi-code-body").append(
				$('<p class="gi-note"></p>').text(
					ctx.is_new
						? __("You can read Items but not create them.")
						: __("The code is the Item's ID: {0}. It is changed by renaming the Item on its form.", [ctx.name])
				)
			);
		}
		this.render_segments($box.find(".gi-segments"), ctx);
		this.schedule_preview(ctx, 0);
	}

	render_code_helper($body, ctx) {
		const current = String((ctx.values() || {}).item_code || "");
		const family = classify(current);
		this.code_spec = { family: family === "unknown" ? "vendor" : family };
		if (family === "vendor") this.code_spec.vendor = current;
		$body.empty();
		const $choices = $('<div class="gi-families" role="radiogroup"></div>').attr("aria-label", __("Kind of code"));
		FAMILIES.forEach((key) => {
			$('<button type="button" class="sf-btn sf-btn--secondary gi-family"></button>')
				.attr("role", "radio")
				.attr("data-family", key)
				.text(__(FAMILY_LABELS[key]))
				.on("click", () => {
					this.code_spec = { family: key };
					this.paint_families($choices);
					this.render_family_inputs($inputs, ctx);
				})
				.appendTo($choices);
		});
		const $inputs = $('<div class="gi-family-inputs"></div>');
		$body.append($choices, $inputs);
		this.paint_families($choices);
		this.render_family_inputs($inputs, ctx);
	}

	paint_families($choices) {
		$choices.find(".gi-family").each((i, button) => {
			const on = $(button).attr("data-family") === this.code_spec.family;
			$(button).attr("aria-checked", on ? "true" : "false").toggleClass("is-on", on);
		});
	}

	// The inputs of the chosen family; each change writes the code into Item Code.
	render_family_inputs($inputs, ctx) {
		const spec = this.code_spec;
		$inputs.empty();
		const $problem = $('<p class="gi-problem" role="status"></p>');
		const apply = () => {
			const problem = code_problem(spec);
			$problem.text(problem ? __(problem) : "");
			const code = build_code(spec);
			if (!problem && code) {
				ctx.set_value("item_code", code);
				this.schedule_preview(ctx);
			}
		};
		const input = (label, key, opts) => {
			const id = `gi-code-${key}`;
			const $field = $('<div class="gi-field"></div>').appendTo($inputs);
			$('<label class="gi-label"></label>').attr("for", id).text(label).appendTo($field);
			const $input = $('<input type="text" class="form-control gi-input" autocomplete="off">')
				.attr("id", id)
				.attr("autocapitalize", (opts || {}).caps ? "characters" : "off")
				.val(spec[key] || "")
				.on("input", () => {
					spec[key] = $input.val();
					apply();
				})
				.appendTo($field);
			if ((opts || {}).hint) $('<p class="gi-hint"></p>').text(opts.hint).appendTo($field);
			return $input;
		};
		if (spec.family === "vendor") {
			input(__("Part number as printed"), "vendor", {
				hint: __("Exactly as the vendor prints it; a comma is dropped (keep the full number in the Description)."),
			});
		} else if (spec.family === "consumable") {
			const $field = $('<div class="gi-field"></div>').appendTo($inputs);
			$('<span class="gi-label"></span>').text(__("Group")).appendTo($field);
			const $groups = $('<div class="gi-families" role="radiogroup"></div>').attr("aria-label", __("Group")).appendTo($field);
			CONSUMABLE_GROUPS.forEach((group) => {
				$('<button type="button" class="sf-btn sf-btn--secondary gi-family"></button>')
					.attr("role", "radio")
					.attr("data-family", group)
					.attr("aria-checked", spec.group === group ? "true" : "false")
					.toggleClass("is-on", spec.group === group)
					.text(group)
					.on("click", (event) => {
						spec.group = group;
						$groups.find(".gi-family").attr("aria-checked", "false").removeClass("is-on");
						$(event.currentTarget).attr("aria-checked", "true").addClass("is-on");
						apply();
					})
					.appendTo($groups);
			});
			input(__("Descriptor"), "descriptor", { caps: true, hint: __("What it is, in a word or two: PAPER-TOWEL, ZIP-TIE.") });
			input(__("Size (optional)"), "size", { caps: true });
		} else {
			this.render_blocks($inputs, ctx, FAMILY_PREFIX[spec.family], apply);
		}
		$inputs.append($problem);
		if (spec.family === "vendor" && spec.vendor) apply();
	}

	// PDT and SRV: the blocks in use, each with its lowest free number; the person chooses one.
	render_blocks($inputs, ctx, prefix, apply) {
		const spec = this.code_spec;
		const $list = $('<div class="gi-blocks"></div>').appendTo($inputs);
		ctx.sf.state.render($list, "loading");
		ctx.sf
			.call(API + "code_blocks", { prefix }, { quiet: true, method: "GET" })
			.then((data) => {
				const blocks = (data.blocks || []).filter((block) => block.next_code);
				const choices = blocks.map((block) => ({ block, label: __("{0}: next free {1}", [block.label, block.next_code]) }));
				if (data.new_block) {
					choices.push({ block: data.new_block, label: __("A new block, {0}: starts at {1}", [data.new_block.label, data.new_block.next_code]) });
				}
				$list.empty();
				$('<p class="gi-hint"></p>')
					.text(__("Numbers are given out in blocks of {0}; products of one kind share a block. Choose the block this belongs in.", [data.block_size]))
					.appendTo($list);
				const $group = $('<div class="gi-block-list" role="radiogroup"></div>').attr("aria-label", __("Block")).appendTo($list);
				choices.forEach((choice) => {
					$('<button type="button" class="sf-btn sf-btn--secondary gi-block"></button>')
						.attr("role", "radio")
						.attr("aria-checked", "false")
						.text(choice.label)
						.on("click", (event) => {
							spec.number = choice.block.next_free;
							$group.find(".gi-block").attr("aria-checked", "false").removeClass("is-on");
							$(event.currentTarget).attr("aria-checked", "true").addClass("is-on");
							apply();
						})
						.appendTo($group);
				});
			})
			.catch((err) => ctx.sf.state.render($list, "error", { body: (err || {}).message }));
	}

	// The seven segments, each a text box with the catalog's choices for it.
	render_segments($segments, ctx) {
		const parts = split_name((ctx.values() || {}).item_name || "");
		this.parts = parts;
		$segments.empty();
		const editable = ctx.can_write;
		SEGMENTS.forEach((segment, index) => {
			const id = `gi-seg-${index}`;
			const $field = $('<div class="gi-field gi-segment"></div>').appendTo($segments);
			$('<label class="gi-label"></label>').attr("for", id).text(__(segment)).appendTo($field);
			const $input = $('<input type="text" class="form-control gi-input" autocomplete="off" autocapitalize="characters">')
				.attr("id", id)
				.attr("list", `${id}-list`)
				.attr("data-index", index)
				.prop("readonly", !editable)
				.val(parts[index] || "")
				.appendTo($field);
			$('<datalist></datalist>').attr("id", `${id}-list`).appendTo($field);
			$input.on("focus", () => this.fill_choices(ctx, index));
			$input.on("input", () => {
				this.parts[index] = String($input.val() || "");
				const name = assemble_name(this.parts);
				this.show_assembled(name);
				ctx.set_value("item_name", name);
				this.schedule_preview(ctx);
				// A new CATEGORY narrows every list after it.
				if (index === 0) this.fill_choices(ctx, 1);
			});
		});
		this.show_assembled(assemble_name(parts));
		if (!this.naming) {
			ctx.sf
				.call(API + "naming_options", {}, { quiet: true, method: "GET" })
				.then((data) => {
					this.naming = data || {};
					const focused = $segments.find("input:focus").attr("data-index");
					this.fill_choices(ctx, focused === undefined ? 0 : Number(focused));
				})
				.catch(() => {
					// No pick lists: the boxes still take anything typed.
				});
		}
	}

	fill_choices(ctx, index) {
		if (!this.$naming || !this.naming) return;
		const options = part_options(this.naming.catalog, index, this.parts, this.naming.categories);
		const $list = this.$naming.find(`#gi-seg-${index}-list`).empty();
		options.forEach((value) => $("<option></option>").attr("value", value).appendTo($list));
	}

	// The name typed straight into Item Name puts its segments back into the boxes.
	fill_segments(ctx, name) {
		if (!this.$naming) return;
		const typed = String(name || "");
		if (typed === assemble_name(this.parts || [])) return;
		this.parts = split_name(typed);
		this.$naming.find(".gi-segment input").each((i, input) => {
			if (document.activeElement !== input) $(input).val(this.parts[i] || "");
		});
		this.show_assembled(assemble_name(this.parts));
	}

	show_assembled(name) {
		if (!this.$naming) return;
		this.$naming.find(".gi-assembled").text(name ? __("Name: {0}", [name]) : __("The name appears here as you fill the segments."));
	}

	schedule_preview(ctx, delay) {
		if (this.timer) window.clearTimeout(this.timer);
		this.timer = window.setTimeout(() => this.run_preview(ctx), delay === undefined ? PREVIEW_DELAY : delay);
	}

	run_preview(ctx) {
		if (!this.$naming) return;
		const v = ctx.values() || {};
		const asked = { item_code: v.item_code || "", item_name: v.item_name || "" };
		const $body = this.$naming.find(".gi-verdict-body");
		if (!asked.item_code && !asked.item_name) {
			this.last_verdict = null;
			$body.html(`<p class="gi-note">${esc(__("Choose a code and build the name; the check runs as you go."))}</p>`);
			return;
		}
		const seq = ++this.preview_seq;
		const args = { item_code: asked.item_code, item_name: asked.item_name, item_group: v.item_group || "", stock_uom: v.stock_uom || "" };
		const request = ctx.is_new
			? ctx.sf.call(API + "preview_item", args, { quiet: true, method: "GET" })
			: ctx.sf
					.call(NAMING_CHECK, Object.assign({ mode: "record", existing: 1 }, args), { quiet: true, method: "GET" })
					.then((result) => ({ checked: true, advice: (result || {}).findings || [] }));
		request
			.then((preview) => {
				if (seq !== this.preview_seq || !this.$naming) return;
				const now = ctx.values() || {};
				if (!answers(asked, { item_code: now.item_code || "", item_name: now.item_name || "" })) return;
				this.last_verdict = verdict(preview);
				this.paint_verdict($body, this.last_verdict, ctx);
			})
			.catch((err) => {
				if (seq !== this.preview_seq) return;
				this.last_verdict = null;
				$body.html(`<p class="gi-note">${esc(__("The check could not run: {0}. The Item is still checked when it is saved.", [(err || {}).message || ""]))}</p>`);
			});
	}

	paint_verdict($body, result, ctx) {
		$body.empty();
		if (result.refusals.length) {
			const $refuse = $('<div class="gi-refusals" role="alert"></div>').appendTo($body);
			$('<p class="gi-refusal-head"></p>').text(__("This would be refused:")).appendTo($refuse);
			result.refusals.forEach((message) => $('<p class="gi-refusal"></p>').text(plain(message)).appendTo($refuse));
		}
		if (result.exists && ctx.is_new) {
			const $exists = $('<p class="gi-exists"></p>').appendTo($body);
			$exists.text(__("Already in ERPNext: {0} {1}. ", [result.exists.item_code, result.exists.item_name || ""]));
			$('<a></a>').attr("href", form_link("Item", result.exists.item_code)).text(__("Open it")).appendTo($exists);
		}
		if (result.advice.length) {
			$('<p class="gi-advice-head"></p>').text(__("Advice (does not stop the save):")).appendTo($body);
			const $ul = $('<ul class="gi-advice"></ul>').appendTo($body);
			result.advice.forEach((item) => {
				const tone = item.severity === "STOP" ? "warn" : "neutral";
				$("<li></li>").html(`${ctx.sf.chip(__(item.severity), tone)} ${esc(plain(item.message))}`).appendTo($ul);
			});
		}
		if (ctx.is_new && result.similar.length) {
			$('<p class="gi-advice-head"></p>').text(__("Similar items already in ERPNext:")).appendTo($body);
			const $ul = $('<ul class="gi-similar"></ul>').appendTo($body);
			result.similar.forEach((row) => {
				const $li = $("<li></li>").appendTo($ul);
				$('<a></a>').attr("href", form_link("Item", row.item_code)).text(`${row.item_code}: ${row.item_name || ""}`).appendTo($li);
			});
		}
		if (!result.refusals.length && !result.advice.length && !(result.exists && ctx.is_new)) {
			$('<p class="gi-ok"></p>').text(__("Nothing to fix.")).appendTo($body);
		}
	}

	// ------------------------------------------------------------------ the blocks on a saved Item

	render_with_setup($box, ctx, draw) {
		if (ctx.is_new || !ctx.name) return;
		ctx.sf.state.render($box, "loading");
		ctx.sf
			.call(API + "get_setup", { item_code: ctx.name }, { quiet: true, method: "GET" })
			.then((setup) => {
				this.setup = setup || {};
				$box.empty();
				draw(this.setup);
			})
			.catch((err) => ctx.sf.state.render($box, "error", { body: (err || {}).message, on_action: () => this.render_with_setup($box, ctx, draw) }));
	}

	// A write through one of api/item_flow.py's endpoints: the page's own save goes first, the
	// page takes the record's new version after, and the block redraws from the answer.
	write($box, ctx, method, args, done) {
		const $error = $box.find(".gi-error").first().text("");
		const $buttons = $box.find("button.gi-save").prop("disabled", true);
		return ctx
			.save_first()
			.then(() => ctx.sf.call(API + method, args, { quiet: true }))
			.then((result) => {
				ctx.record_changed();
				done(result || {});
			})
			.catch((err) => {
				$error.text(plain((err || {}).message) || __("It could not be saved."));
			})
			.finally(() => $buttons.prop("disabled", false));
	}

	number_input($parent, label, id, value, hint) {
		const $field = $('<div class="gi-field"></div>').appendTo($parent);
		$('<label class="gi-label"></label>').attr("for", id).text(label).appendTo($field);
		const $input = $('<input type="number" min="0" step="any" inputmode="decimal" class="form-control gi-input">')
			.attr("id", id)
			.val(given(value) ? value : "")
			.appendTo($field);
		if (hint) $('<p class="gi-hint"></p>').text(hint).appendTo($field);
		return $input;
	}

	text_input($parent, label, id, value, opts) {
		const $field = $('<div class="gi-field"></div>').appendTo($parent);
		$('<label class="gi-label"></label>').attr("for", id).text(label).appendTo($field);
		const $input = $('<input class="form-control gi-input" autocomplete="off">')
			.attr("type", (opts || {}).type || "text")
			.attr("inputmode", (opts || {}).inputmode || null)
			.attr("id", id)
			.val(value || "")
			.appendTo($field);
		if ((opts || {}).hint) $('<p class="gi-hint"></p>').text(opts.hint).appendTo($field);
		return $input;
	}

	render_supplier($box, ctx, setup) {
		const start = supplier_start(setup);
		const can = setup.can || {};
		const $card = $('<section class="sf-card gi-card"></section>').appendTo($box);
		$('<h3 class="sf-card-title gi-title"></h3>').text(__("Who we buy it from")).appendTo($card);
		if ((setup.suppliers || []).length) {
			const $list = $('<ul class="gi-rows"></ul>').appendTo($card);
			setup.suppliers.forEach((row) => {
				const bits = [row.supplier_name || row.supplier];
				if (row.supplier_part_no) bits.push(__("part {0}", [row.supplier_part_no]));
				if (given(row.rate)) bits.push(format_currency(row.rate, setup.currency));
				const $li = $("<li></li>").appendTo($list);
				$('<span class="gi-row-text"></span>').text(bits.join(" · ")).appendTo($li);
				if (row.supplier === setup.default_supplier) $li.append(` ${ctx.sf.chip(__("Default"), "good")}`);
			});
		}
		if (!can.write) {
			$('<p class="gi-note"></p>').text(__("You can read this Item but not change it.")).appendTo($card);
			return;
		}
		const $form = $('<div class="gi-form"></div>').appendTo($card);
		const supplier = link_control($('<div class="gi-field"></div>').appendTo($form), {
			fieldtype: "Link",
			fieldname: "gi_supplier",
			label: __("Supplier"),
			options: "Supplier",
			get_query: () => ({ filters: { disabled: 0 } }),
		});
		const $part = this.text_input($form, __("Their part number"), "gi-part", start.supplier_part_no, {
			hint: __("As it appears on their quotes and invoices; a purchase order shows it to them."),
		});
		const $url = can.link
			? this.text_input($form, __("Purchase link"), "gi-url", start.purchase_url, {
					type: "url",
					inputmode: "url",
					hint: __("The product page on their website, up to 140 characters."),
				})
			: null;
		const $rate = can.price
			? this.number_input(
					$form,
					__("Last price paid, per {0}", [setup.stock_uom || __("unit")]),
					"gi-rate",
					start.rate,
					__("Recorded on {0} for this supplier; a purchase order offers it.", [setup.price_list || __("the buying price list")])
				)
			: null;
		if (!can.price) {
			$('<p class="gi-note"></p>').text(__("Recording a price needs the Purchase Master Manager role; the rest is saved without it.")).appendTo($form);
		}
		const $default = $('<label class="gi-tick"><input type="checkbox"> <span></span></label>').appendTo($form);
		$default.find("span").text(__("Reorders go to this supplier (the default supplier)"));
		$default.find("input").prop("checked", default_ticked(setup, start.supplier));
		// The rate shown for the supplier in the box: a save sends a rate only when it was changed.
		let shown_rate = start.rate;
		supplier.set_value(start.supplier || "");
		supplier.df.onchange = () => {
			const picked = supplier.get_value();
			// A supplier with no row yet starts blank: never the previous supplier's details.
			const fields = supplier_fields(this.setup, picked);
			$part.val(fields.supplier_part_no);
			if ($url) $url.val(fields.purchase_url);
			if ($rate) $rate.val(fields.rate);
			shown_rate = fields.rate;
			$default.find("input").prop("checked", default_ticked(this.setup, picked));
		};
		$('<p class="gi-error" role="alert"></p>').appendTo($form);
		$('<button type="button" class="sf-btn sf-btn--primary gi-save"></button>')
			.text(__("Save supplier"))
			.on("click", () => {
				const args = {
					item_code: ctx.name,
					supplier: supplier.get_value() || "",
					supplier_part_no: $part.val() || "",
					make_default: $default.find("input").prop("checked") ? 1 : 0,
				};
				if ($url) args.purchase_url = $url.val() || "";
				const rate = $rate ? rate_to_send(shown_rate, $rate.val()) : null;
				if (rate !== null) args.rate = rate;
				this.write($form, ctx, "save_supplier", args, (setup_now) => {
					ctx.sf.toast(__("Supplier saved."), { tone: "success", timeout: 3000 });
					this.setup = setup_now;
					$box.empty();
					this.render_supplier($box, ctx, setup_now);
				});
			})
			.appendTo($form);
	}

	render_stock($box, ctx, setup) {
		const start = stock_start(setup);
		const can = setup.can || {};
		const $card = $('<section class="sf-card gi-card"></section>').appendTo($box);
		$('<h3 class="sf-card-title gi-title"></h3>').text(__("Where it is kept")).appendTo($card);
		if (!setup.auto_reorder) {
			$('<p class="gi-note"></p>')
				.text(__("Automatic reordering is off in Stock Settings: a reorder level is recorded, but nothing is requested until it is turned on."))
				.appendTo($card);
		}
		const $form = $('<div class="gi-form"></div>').appendTo($card);
		const bin = link_control($('<div class="gi-field"></div>').appendTo($form), {
			fieldtype: "Link",
			fieldname: "gi_bin",
			label: __("Bin"),
			options: "Warehouse",
			read_only: can.write ? 0 : 1,
			description: __("The shelf location it lives in; Stock Scan lists it there."),
			get_query: () => ({ filters: { is_group: 0, disabled: 0, warehouse_type: ["!=", "Transit"] } }),
		});
		bin.set_value(start.warehouse || "");
		const $level = this.number_input($form, __("Reorder level"), "gi-level", start.reorder_level, __("When the bin falls to this many, buy more."));
		const $qty = this.number_input($form, __("Reorder quantity"), "gi-qty", start.reorder_qty, __("How many to buy each time."));
		if (!can.write) {
			$level.prop("readonly", true);
			$qty.prop("readonly", true);
		}
		$('<p class="gi-error" role="alert"></p>').appendTo($form);
		const $actions = $('<div class="gi-actions"></div>').appendTo($form);
		if (can.write) {
			$('<button type="button" class="sf-btn sf-btn--primary gi-save"></button>')
				.text(__("Save bin and reorder"))
				.on("click", () => {
					const args = { item_code: ctx.name, warehouse: bin.get_value() || "" };
					if (given($level.val()) || given($qty.val())) {
						args.reorder_level = $level.val();
						args.reorder_qty = $qty.val();
					}
					this.write($form, ctx, "save_stock", args, (setup_now) => {
						// The server says when the reorder rule moved bins, or when the old bin kept its own.
						const note = setup_now.note ? ` ${setup_now.note}` : "";
						ctx.sf.toast(__("Bin saved.") + note, { tone: setup_now.note ? "warning" : "success", timeout: note ? 8000 : 3000 });
						this.setup = setup_now;
						$box.empty();
						this.render_stock($box, ctx, setup_now);
					});
				})
				.appendTo($actions);
		}
		if (can.label && start.warehouse) {
			// The bin's own label, printed from the label page Stock Scan's labels come from.
			$('<button type="button" class="sf-btn sf-btn--secondary gi-label-print"></button>')
				.text(__("Print the bin label"))
				.attr("aria-label", __("Print the QR label for {0}", [start.warehouse]))
				.on("click", () => window.open(label_url(start.warehouse), "_blank", "noopener"))
				.appendTo($actions);
		}
	}

	render_asset($box, ctx, setup) {
		const can = setup.can || {};
		const $card = $('<section class="sf-card gi-card"></section>').appendTo($box);
		$('<h3 class="sf-card-title gi-title"></h3>').text(__("The Asset")).appendTo($card);
		const assets = setup.assets || [];
		if (assets.length) {
			const $list = $('<ul class="gi-rows"></ul>').appendTo($card);
			assets.forEach((row) => {
				const $li = $("<li></li>").appendTo($list);
				$('<a></a>').attr("href", form_link("Asset", row.name)).text(`${row.name}: ${row.asset_name || ""}`).appendTo($li);
				const bits = [row.location, row.docstatus ? row.status : __("Draft")].filter(Boolean);
				$('<span class="gi-row-text"></span>').text(` · ${bits.join(" · ")}`).appendTo($li);
			});
		}
		if (!setup.asset_category) {
			$('<p class="gi-note"></p>').text(__("Give the Item its Asset Category on the first step, then come back.")).appendTo($card);
			return;
		}
		if (!can.asset) {
			$('<p class="gi-note"></p>')
				.text(__("Creating the Asset needs Asset permission, which Accounts has. Ask them to create it from this Item."))
				.appendTo($card);
			return;
		}
		const $toggle = $('<button type="button" class="sf-btn sf-btn--secondary"></button>')
			.text(assets.length ? __("Add another Asset") : __("Create the Asset"))
			.appendTo($card);
		const $form = $('<div class="gi-form" hidden></div>').appendTo($card);
		$toggle.on("click", () => {
			$toggle.remove();
			$form.prop("hidden", false);
		});
		if (!assets.length) $toggle.trigger("click");
		const location = link_control($('<div class="gi-field"></div>').appendTo($form), {
			fieldtype: "Link",
			fieldname: "gi_location",
			label: __("Where it is kept"),
			options: "Location",
		});
		const date = link_control($('<div class="gi-field"></div>').appendTo($form), {
			fieldtype: "Date",
			fieldname: "gi_purchase_date",
			label: __("Purchase date"),
		});
		date.set_value(frappe.datetime.get_today());
		const $amount = this.number_input($form, __("What it cost"), "gi-amount", "", __("ERPNext needs the purchase amount for every Asset."));
		const $rent = $('<label class="gi-tick"><input type="checkbox"> <span></span></label>').appendTo($form);
		$rent.find("span").text(__("Available for event rental (a rental fountain)"));
		$('<p class="gi-error" role="alert"></p>').appendTo($form);
		// One id per Asset this form makes: a retry after a lost answer sends the same one.
		this.asset_id = this.asset_id || new_client_id(Date.now(), Math.random);
		$('<button type="button" class="sf-btn sf-btn--primary gi-save"></button>')
			.text(__("Create the Asset"))
			.on("click", () => {
				const args = {
					item_code: ctx.name,
					client_id: this.asset_id,
					location: location.get_value() || "",
					purchase_date: date.get_value() || "",
					net_purchase_amount: $amount.val() || "",
					rentable: $rent.find("input").prop("checked") ? 1 : 0,
				};
				this.write($form, ctx, "create_asset", args, (made) => {
					this.asset_id = null;
					ctx.sf.toast(made.deduped ? __("Asset {0} was already created.", [made.name]) : __("Asset {0} created as a draft.", [made.name]), {
						tone: "success",
						timeout: 5000,
					});
					this.render_with_setup($box, ctx, (setup_now) => this.render_asset($box, ctx, setup_now));
				});
			})
			.appendTo($form);
	}
}

register_extension("Item", () => new ItemExtension());
